import contextlib
import ssl
import time
import typing
import types

import h11

from ._content import Content
from ._headers import Headers
from ._network import Lock, NetworkBackend, Semaphore, NetworkStream
from ._response import Response
from ._request import Request
from ._streams import IterByteStream, Stream
from ._urls import URL


__all__ = [
    "Transport",
    "ConnectionPool",
    "Connection",
    "open_connection",
]


class Transport:
    @contextlib.contextmanager
    def send(self, request: Request) -> typing.Iterator[Response]:
        raise NotImplementedError()
        yield

    def close(self):
        pass

    def request(
        self,
        method: str,
        url: URL | str,
        headers: Headers | dict[str, str] | None = None,
        content: Content | Stream | bytes | None = None,
    ) -> Response:
        request = Request(method, url, headers=headers, content=content)
        with self.send(request) as response:
            response.read()
        return response

    @contextlib.contextmanager
    def stream(
        self,
        method: str,
        url: URL | str,
        headers: Headers | dict[str, str] | None = None,
        content: Content | Stream | bytes | None = None,
    ) -> typing.Iterator[Response]:
        request = Request(method, url, headers=headers, content=content)
        with self.send(request) as response:
            yield response


class ConnectionPool(Transport):
    def __init__(self, ssl_context: ssl.SSLContext | None = None, backend: NetworkBackend | None = None):
        if ssl_context is None:
            import truststore
            ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if backend is None:
            backend = NetworkBackend()

        self._connections: list[Connection] = []
        self._ssl_context = ssl_context
        self._network_backend = backend
        self._limit_concurrency = Semaphore(100)
        self._closed = False

    # Public API...
    @contextlib.contextmanager
    def send(self, request: Request) -> typing.Iterator[Response]:
        if self._closed:
            raise RuntimeError("ConnectionPool is closed.")

        with self._limit_concurrency:
            try:
                connection = self._get_connection(request)
                with connection.send(request) as response:
                    yield response
            finally:
                self._close_expired_connections()
                self._remove_closed_connections()

    def close(self):
        self._closed = True
        closing = list(self._connections)
        self._connections = []
        for conn in closing:
            conn.close()

    # Create or reuse connections as required...
    def _get_connection(self, request: Request) -> "Connection":
        # Attempt to reuse an existing connection.
        url = request.url
        origin = URL(scheme=url.scheme, host=url.host, port=url.port)
        now = time.monotonic()
        for conn in self._connections:
            if conn.origin() == origin and conn.is_idle() and not conn.is_expired(now):
                return conn

        # Or else create a new connection.
        conn = open_connection(
            origin,
            hostname=request.headers["Host"],
            ssl_context=self._ssl_context,
            backend=self._network_backend
        )
        self._connections.append(conn)
        return conn

    # Connection pool management...
    def _close_expired_connections(self) -> None:
        now = time.monotonic()
        for conn in list(self._connections):
            if conn.is_expired(now):
                conn.close()

    def _remove_closed_connections(self) -> None:
        for conn in list(self._connections):
            if conn.is_closed():
                self._connections.remove(conn)

    @property
    def connections(self) -> typing.List['Connection']:
        return [c for c in self._connections]

    def description(self) -> str:
        counts = {"active": 0}
        for status in [c.description() for c in self._connections]:
            counts[status] = counts.get(status, 0) + 1
        return ", ".join(f"{count} {status}" for status, count in counts.items())

    # Builtins...
    def __repr__(self) -> str:
        return f"<ConnectionPool [{self.description()}]>"

    def __del__(self):
        if not self._closed:
            import warnings
            warnings.warn("ConnectionPool was garbage collected without being closed.")

    def __enter__(self) -> "ConnectionPool":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: types.TracebackType | None = None,
    ) -> None:
        self.close()


class Connection(Transport):
    def __init__(self, stream: "NetworkStream", origin: URL | str):
        self._stream = stream
        self._origin = URL(origin)
        self._state = h11.Connection(our_role=h11.CLIENT)
        self._keepalive_duration = 5.0
        self._idle_expiry = time.monotonic() + self._keepalive_duration
        self._request_lock = Lock()

    # API for connection pool management...
    def origin(self) -> URL:
        return self._origin

    def is_idle(self) -> bool:
        return self._state.our_state is h11.IDLE

    def is_expired(self, when: float) -> bool:
        return self._state.our_state is h11.IDLE and when > self._idle_expiry

    def is_closed(self) -> bool:
        return self._state.our_state in (h11.CLOSED, h11.ERROR)

    def description(self) -> str:
        return {
            h11.IDLE: "idle",
            h11.SEND_BODY: "active",
            h11.DONE: "active",
            h11.MUST_CLOSE: "closing",
            h11.CLOSED: "closed",
            h11.ERROR: "error",
            h11.MIGHT_SWITCH_PROTOCOL: "upgrading",
            h11.SWITCHED_PROTOCOL: "upgraded",
        }[self._state.our_state]

    # API entry points...
    @contextlib.contextmanager
    def send(self, request: Request) -> typing.Iterator[Response]:
        with self._request_lock:
            try:
                self._send_head(request)
                self._send_body(request)
                code, headers = self._recv_head()
                stream = IterByteStream(self._recv_body())
                yield Response(code, headers=headers, content=stream)
            finally:
                self._cycle_complete()

    def close(self) -> None:
        with self._request_lock:
            self._close()

    # Top-level API for working directly with a connection.
    def request(
        self,
        method: str,
        url: URL | str,
        headers: Headers | typing.Mapping[str, str] | None = None,
        content: Content | Stream | bytes | None = None,
    ) -> Response:
        url = self._origin.join(url)
        request = Request(method, url, headers=headers, content=content)
        with self.send(request) as response:
            response.read()
        return response

    @contextlib.contextmanager
    def stream(
        self,
        method: str,
        url: URL | str,
        headers: Headers | typing.Mapping[str, str] | None = None,
        content: Content | Stream | bytes | None = None,
    ) -> typing.Iterator[Response]:
        url = self._origin.join(url)
        request = Request(method, url, headers=headers, content=content)
        with self.send(request) as response:
            yield response

    # Send the request...
    def _send_head(self, request: Request) -> None:
        event = h11.Request(
            method=request.method,
            target=request.url.target,
            headers=list(request.headers.items()),
        )
        self._send_event(event)

    def _send_body(self, request: Request) -> None:
        for data in request.stream:
            self._send_event(h11.Data(data=data))
        self._send_event(h11.EndOfMessage())

    def _send_event(self, event: h11.Event) -> None:
        data = self._state.send(event)
        if data is not None:
            self._stream.write(data)

    # Receive the response...
    def _recv_head(self) -> tuple[int, Headers]:
        while True:
            event = self._recv_event()
            if isinstance(event, h11.Response):
                code = event.status_code
                headers = Headers([
                    (k.decode("latin-1"), v.decode("latin-1")) for k, v in event.headers
                ])
                return (code, headers)

    def _recv_body(self) -> typing.Iterator[bytes]:
        while True:
            event = self._recv_event()
            if isinstance(event, h11.Data):
                yield bytes(event.data)
            elif isinstance(event, (h11.EndOfMessage, h11.PAUSED)):
                break

    def _recv_event(self) -> h11.Event | type[h11.PAUSED]:
        while True:
            event = self._state.next_event()

            if event is h11.NEED_DATA:
                data = self._stream.read()
                self._state.receive_data(data)
            else:
                return event  # type: ignore[return-value]

    # Request / response cycle complete...
    def _cycle_complete(self) -> None:
        if self._state.our_state is h11.DONE and self._state.their_state is h11.DONE:
            self._state.start_next_cycle()
            self._idle_expiry = time.monotonic() + self._keepalive_duration
        else:
            self._close()

    def _close(self) -> None:
        if self._state.our_state in (h11.DONE, h11.IDLE, h11.MUST_CLOSE):
            event = h11.ConnectionClosed()
            self._state.send(event)

        self._stream.close()

    # Builtins...
    def __repr__(self) -> str:
        return f"<Connection [{self._origin} {self.description()}]>"

    def __enter__(self) -> "Connection":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: types.TracebackType | None = None,
    ):
        self.close()


def open_connection(
        url: URL | str,
        hostname: str = '',
        ssl_context: ssl.SSLContext | None = None,
        backend: NetworkBackend | None = None,
    ) -> Connection:

    if isinstance(url, str):
        url = URL(url)

    if url.scheme not in ("http", "https"):
        raise ValueError("URL scheme must be 'http://' or 'https://'.")
    if backend is None:
        backend = NetworkBackend()

    host = url.host
    port = url.port or {"http": 80, "https": 443}[url.scheme]
    hostname = hostname or url.host

    stream = backend.connect(host, port)
    if url.scheme == "https":
        if ssl_context is None:
            import truststore
            ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        stream.start_tls(ssl_context, hostname=hostname)

    return Connection(stream, url)
