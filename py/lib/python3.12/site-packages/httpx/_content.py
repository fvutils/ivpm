import json
import os
import typing

from ._streams import Stream, ByteStream, FileStream, IterByteStream
from ._urlencode import urldecode, urlencode

__all__ = [
    "Content",
    "Form",
    "File",
    "Files",
    "JSON",
    "MultiPart",
    "Text",
    "HTML",
]

# https://github.com/nginx/nginx/blob/master/conf/mime.types
_content_types = {
    ".json": "application/json",
    ".js": "application/javascript",
    ".html": "text/html",
    ".css": "text/css",
    ".png": "image/png",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
}


class Content:
    def encode(self) -> tuple[Stream, str]:
        raise NotImplementedError()


class Form(typing.Mapping[str, str], Content):
    """
    HTML form data, as an immutable multi-dict.
    Form parameters, as a multi-dict.
    """

    def __init__(
        self,
        form: (
            typing.Mapping[str, str | typing.Sequence[str]]
            | typing.Sequence[tuple[str, str]]
            | str
            | None
        ) = None,
    ) -> None:
        d: dict[str, list[str]] = {}

        if form is None:
            d = {}
        elif isinstance(form, str):
            d = urldecode(form)
        elif isinstance(form, typing.Mapping):
            # Convert dict inputs like:
            #    {"a": "123", "b": ["456", "789"]}
            # To dict inputs where values are always lists, like:
            #    {"a": ["123"], "b": ["456", "789"]}
            d = {k: [v] if isinstance(v, str) else list(v) for k, v in form.items()}
        else:
            # Convert list inputs like:
            #     [("a", "123"), ("a", "456"), ("b", "789")]
            # To a dict representation, like:
            #     {"a": ["123", "456"], "b": ["789"]}
            for k, v in form:
                d.setdefault(k, []).append(v)

        self._dict = d

    # Content API

    def encode(self) -> tuple[Stream, str]:
        stream = ByteStream(str(self).encode("ascii"))
        content_type = "application/x-www-form-urlencoded"
        return (stream, content_type)
    
    # Dict operations

    def keys(self) -> typing.KeysView[str]:
        return self._dict.keys()

    def values(self) -> typing.ValuesView[str]:
        return {k: v[0] for k, v in self._dict.items()}.values()

    def items(self) -> typing.ItemsView[str, str]:
        return {k: v[0] for k, v in self._dict.items()}.items()

    def get(self, key: str, default: typing.Any = None) -> typing.Any:
        if key in self._dict:
            return self._dict[key][0]
        return default

    # Multi-dict operations

    def multi_items(self) -> list[tuple[str, str]]:
        multi_items: list[tuple[str, str]] = []
        for k, v in self._dict.items():
            multi_items.extend([(k, i) for i in v])
        return multi_items

    def multi_dict(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._dict.items()}

    def get_list(self, key: str) -> list[str]:
        return list(self._dict.get(key, []))

    # Update operations

    def copy_set(self, key: str, value: str) -> "Form":
        d = self.multi_dict()
        d[key] = [value]
        return Form(d)

    def copy_append(self, key: str, value: str) -> "Form":
        d = self.multi_dict()
        d[key] = d.get(key, []) + [value]
        return Form(d)

    def copy_remove(self, key: str) -> "Form":
        d = self.multi_dict()
        d.pop(key, None)
        return Form(d)

    # Accessors & built-ins

    def __getitem__(self, key: str) -> str:
        return self._dict[key][0]

    def __contains__(self, key: typing.Any) -> bool:
        return key in self._dict

    def __iter__(self) -> typing.Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self._dict)

    def __bool__(self) -> bool:
        return bool(self._dict)

    def __hash__(self) -> int:
        return hash(str(self))

    def __eq__(self, other: typing.Any) -> bool:
        return (
            isinstance(other, Form) and
            sorted(self.multi_items()) == sorted(other.multi_items())
        )

    def __str__(self) -> str:
        return urlencode(self.multi_dict())

    def __repr__(self) -> str:
        return f"<Form {self.multi_items()!r}>"


class File(Content):
    """
    Wrapper class used for files in uploads and multipart requests.
    """

    def __init__(self, path: str):
        self._path = path

    def name(self) -> str:
        return os.path.basename(self._path)

    def size(self) -> int:
        return os.path.getsize(self._path)

    def content_type(self) -> str:
        _, ext = os.path.splitext(self._path)
        ct = _content_types.get(ext, "application/octet-stream")
        if ct.startswith('text/'):
            ct += "; charset='utf-8'"
        return ct

    def encode(self) -> tuple[Stream, str]:
        stream = FileStream(self._path)
        content_type = self.content_type()
        return (stream, content_type)

    def __lt__(self, other: typing.Any) -> bool:
        return isinstance(other, File) and other._path < self._path

    def __eq__(self, other: typing.Any) -> bool:
        return isinstance(other, File) and other._path == self._path

    def __repr__(self) -> str:
        return f"<File {self._path!r}>"


class Files(typing.Mapping[str, File], Content):
    """
    File parameters, as a multi-dict.
    """

    def __init__(
        self,
        files: (
            typing.Mapping[str, File | typing.Sequence[File]]
            | typing.Sequence[tuple[str, File]]
            | None
        ) = None,
    ) -> None:
        d: dict[str, list[File]] = {}

        if files is None:
            d = {}
        elif isinstance(files, typing.Mapping):
            d = {k: [v] if isinstance(v, File) else list(v) for k, v in files.items()}
        else:
            d = {}
            for k, v in files:
                d.setdefault(k, []).append(v)

        self._dict = d

    # Standard dict interface
    def keys(self) -> typing.KeysView[str]:
        return self._dict.keys()

    def values(self) -> typing.ValuesView[File]:
        return {k: v[0] for k, v in self._dict.items()}.values()

    def items(self) -> typing.ItemsView[str, File]:
        return {k: v[0] for k, v in self._dict.items()}.items()

    def get(self, key: str, default: typing.Any = None) -> typing.Any:
        if key in self._dict:
            return self._dict[key][0]
        return None

    # Multi dict interface
    def multi_items(self) -> list[tuple[str, File]]:
        multi_items: list[tuple[str, File]] = []
        for k, v in self._dict.items():
            multi_items.extend([(k, i) for i in v])
        return multi_items

    def multi_dict(self) -> dict[str, list[File]]:
        return {k: list(v) for k, v in self._dict.items()}

    def get_list(self, key: str) -> list[File]:
        return list(self._dict.get(key, []))

    # Content interface
    def encode(self) -> tuple[Stream, str]:
        return MultiPart(files=self).encode()

    # Builtins
    def __getitem__(self, key: str) -> File:
        return self._dict[key][0]

    def __contains__(self, key: typing.Any) -> bool:
        return key in self._dict

    def __iter__(self) -> typing.Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self._dict)

    def __bool__(self) -> bool:
        return bool(self._dict)
 
    def __eq__(self, other: typing.Any) -> bool:
        return (
            isinstance(other, Files) and
            sorted(self.multi_items()) == sorted(other.multi_items())
        )

    def __repr__(self) -> str:
        return f"<Files {self.multi_items()!r}>"


class JSON(Content):
    def __init__(self, data: typing.Any) -> None:
        self._data = data

    def encode(self) -> tuple[Stream, str]:
        content = json.dumps(
            self._data,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False
        ).encode("utf-8")
        stream = ByteStream(content)
        content_type = "application/json"
        return (stream, content_type)

    def __repr__(self) -> str:
        return f"<JSON {self._data!r}>"


class Text(Content):
    def __init__(self, text: str) -> None:
        self._text = text

    def encode(self) -> tuple[Stream, str]:
        stream = ByteStream(self._text.encode("utf-8"))
        content_type = "text/plain; charset='utf-8'"
        return (stream, content_type)

    def __repr__(self) -> str:
        return f"<Text {self._text!r}>"


class HTML(Content):
    def __init__(self, text: str) -> None:
        self._text = text

    def encode(self) -> tuple[Stream, str]:
        stream = ByteStream(self._text.encode("utf-8"))
        content_type = "text/html; charset='utf-8'"
        return (stream, content_type)

    def __repr__(self) -> str:
        return f"<HTML {self._text!r}>"


class MultiPart(Content):
    def __init__(
        self,
        form: (
            Form
            | typing.Mapping[str, str | typing.Sequence[str]]
            | typing.Sequence[tuple[str, str]]
            | str
            | None
        ) = None,
        files: (
            Files
            | typing.Mapping[str, File | typing.Sequence[File]]
            | typing.Sequence[tuple[str, File]]
            | None
        ) = None,
        boundary: str | None = None
    ):
        self._form = form if isinstance(form , Form) else Form(form)
        self._files = files if isinstance(files, Files) else Files(files)
        self._boundary = os.urandom(16).hex() if boundary is None else boundary

    @property
    def form(self) -> Form:
        return self._form

    @property
    def files(self) -> Files:
        return self._files

    def encode(self) -> tuple[Stream, str]:
        stream = IterByteStream(self.iter_bytes())
        content_type = f"multipart/form-data; boundary={self._boundary}"
        return (stream, content_type)

    def iter_bytes(self) -> typing.Iterator[bytes]:
        for key, value in self._form.multi_items():
            # See https://html.spec.whatwg.org/ - LF, CR, and " must be percent escaped.
            name = key.translate({10: "%0A", 13: "%0D", 34: "%22"})
            yield (
                f"--{self._boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n'
                f"\r\n"
                f"{value}\r\n"
            ).encode("utf-8")

        for key, file in self._files.multi_items():
            # See https://html.spec.whatwg.org/ - LF, CR, and " must be percent escaped.
            name = key.translate({10: "%0A", 13: "%0D", 34: "%22"})
            filename = file.name().translate({10: "%0A", 13: "%0D", 34: "%22"})
            stream, _ = file.encode()
            yield (
                f"--{self._boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"\r\n"
            ).encode("utf-8")
            for buffer in stream:
                yield buffer
            yield "\r\n".encode("utf-8")

        yield f"--{self._boundary}--\r\n".encode("utf-8")

    def __repr__(self) -> str:
        return f"<MultiPart form={self._form.multi_items()!r}, files={self._files.multi_items()!r}>"
