import typing
import json as libjson

from ._content import Content
from ._streams import ByteStream, Stream
from ._headers import Headers
from ._urls import URL

__all__ = ["Request"]


class Request:
    def __init__(
        self,
        method: str,
        url: URL | str,
        headers: Headers | typing.Mapping[str, str] | None = None,
        content: Content | Stream | bytes | None = None,
    ):
        self.method = method
        self.url = URL(url)
        self.headers = Headers(headers)
        self.stream: Stream = ByteStream(b"")

        # https://datatracker.ietf.org/doc/html/rfc2616#section-14.23
        # RFC 2616, Section 14.23, Host.
        #
        # A client MUST include a Host header field in all HTTP/1.1 request messages.
        if "Host" not in self.headers:
            self.headers = self.headers.copy_set("Host", self.url.netloc)

        if content is not None:
            if isinstance(content, bytes):
                self.stream = ByteStream(content)
            elif isinstance(content, Stream):
                self.stream = content
            elif isinstance(content, Content):
                assert isinstance(content, Content)
                # Eg. Request("POST", "https://www.example.com", content=Form(...))
                stream, content_type = content.encode()
                self.headers = self.headers.copy_set("Content-Type", content_type)
                self.stream = stream
            else:
                raise TypeError(f'Expected `Content | Stream | bytes | None` got {type(content)}')

            # https://datatracker.ietf.org/doc/html/rfc2616#section-4.3
            # RFC 2616, Section 4.3, Message Body.
            #
            # The presence of a message-body in a request is signaled by the
            # inclusion of a Content-Length or Transfer-Encoding header field in
            # the request's message-headers.
            content_length: int | None = self.stream.size
            if content_length is None:
                self.headers = self.headers.copy_set("Transfer-Encoding", "chunked")
            elif content_length > 0:
                self.headers = self.headers.copy_set("Content-Length", str(content_length))

    @property
    def body(self) -> bytes:
        if not hasattr(self, '_body'):
            raise RuntimeError("'.body' cannot be accessed without calling '.read()'")
        return self._body

    def read(self) -> bytes:
        if not hasattr(self, '_body'):
            self._body = b"".join([part for part in self.stream])
            self.stream = ByteStream(self._body)
        return self._body

    def __repr__(self):
        return f"<Request [{self.method} {str(self.url)!r}]>"
