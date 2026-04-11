import contextlib
import logging
import time

import h11

from ._content import Text
from ._request import Request
from ._response import Response
from ._network import NetworkBackend, sleep
from ._streams import IterByteStream

__all__ = [
    "serve_http",
]

logger = logging.getLogger("httpx.server")


class ConnectionClosed(Exception):
    pass


class HTTPConnection:
    def __init__(self, stream, endpoint):
        self._stream = stream
        self._endpoint = endpoint
        self._state = h11.Connection(our_role=h11.SERVER)
        self._keepalive_duration = 5.0
        self._idle_expiry = time.monotonic() + self._keepalive_duration

    # API entry points...
    def handle_requests(self):
        try:
            method, url, headers = self._recv_head()
            stream = IterByteStream(self._recv_body())
            # TODO: Handle endpoint exceptions
            try:
                request = Request(method, url, headers=headers, content=stream)
                response = self._endpoint(request)
            except Exception as exc:
                logger.error("Internal Server Error", exc_info=True)
                content = Text("Internal Server Error")
                response = Response(code=500, content=content)
                self._send_head(response)
                self._send_body(response)
            else:
                try:
                    self._send_head(response)
                    self._send_body(response)
                except Exception as exc:
                    logger.error("Internal Server Error", exc_info=True)
            finally:
                status_line = f"{request.method} {request.url.target} [{response.status_code} {response.reason_phrase}]"
                logger.info(status_line)
        except ConnectionClosed:
            pass
        finally:
            self._cycle_complete()

    def close(self):
        if self._state.our_state in (h11.DONE, h11.IDLE, h11.MUST_CLOSE):
            event = h11.ConnectionClosed()
            self._state.send(event)

        self._stream.close()

    # Receive the request...
    def _recv_head(self) -> tuple[str, str, list[tuple[str, str]]]:
        while True:
            event = self._recv_event()
            if isinstance(event, h11.Request):
                method = event.method.decode('ascii')
                target = event.target.decode('ascii')
                headers = [
                    (k.decode('latin-1'), v.decode('latin-1'))
                    for k, v in event.headers.raw_items()
                ]
                return (method, target, headers)
            elif isinstance(event, h11.ConnectionClosed):
                raise ConnectionClosed()

    def _recv_body(self):
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

    # Return the response...
    def _send_head(self, response: Response):
        event = h11.Response(
            status_code=response.status_code,
            headers=list(response.headers.items())
        )
        self._send_event(event)

    def _send_body(self, response: Response):
        for data in response.stream:
            self._send_event(h11.Data(data=data))
        self._send_event(h11.EndOfMessage())

    def _send_event(self, event: h11.Event) -> None:
        data = self._state.send(event)
        if data is not None:
            self._stream.write(data)

    # Start it all over again...
    def _cycle_complete(self):
        if self._state.our_state is h11.DONE and self._state.their_state is h11.DONE:
            self._state.start_next_cycle()
            self._idle_expiry = time.monotonic() + self._keepalive_duration
        else:
            self.close()


class HTTPServer:
    def __init__(self, host, port):
        self.url = f"http://{host}:{port}/"

    def wait(self):
        while(True):
            sleep(1)


@contextlib.contextmanager
def serve_http(endpoint):
    def handler(stream):
        connection = HTTPConnection(stream, endpoint)
        connection.handle_requests()

    logging.basicConfig(
        format="%(levelname)s [%(asctime)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.DEBUG
    )

    backend = NetworkBackend()
    with backend.serve("127.0.0.1", 8080, handler) as server:
        server = HTTPServer(server.host, server.port)
        logger.info(f"Serving on {server.url}")
        yield server
