import os
import typing


__all__ = ["ByteStream", "FileStream", "IterByteStream", "Stream"]


def humanize_size(b: int) -> str:
    B = float(b)
    KB = float(1024)
    MB = float(KB**2)  # 1,048,576
    GB = float(KB**3)  # 1,073,741,824
    TB = float(KB**4)  # 1,099,511,627,776

    if B < KB:
        return "{0:.0f}B".format(B)
    elif KB <= B < MB:
        return "{0:.0f}KB".format(B / KB)
    elif MB <= B < GB:
        return "{0:.0f}MB".format(B / MB)
    elif GB <= B < TB:
        return "{0:.0f}GB".format(B / GB)
    return "{0:.0f}TB".format(B / TB)


class Stream:
    @property
    def size(self) -> int | None:
        raise NotImplementedError()

    def __iter__(self):
        raise NotImplementedError()


class ByteStream(Stream):
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __eq__(self, other) -> bool:
        return (
            self._content == other._content
            if isinstance(other, ByteStream)
            else False
        )

    @property
    def size(self):
        return len(self._content)

    def __iter__(self) -> typing.Iterator[bytes]:
        yield self._content

    def __repr__(self) -> str:
        size = humanize_size(self.size)
        return f"<ByteStream [{size}]>"


class IterByteStream(Stream):
    def __init__(
        self,
        iterator: typing.Iterator[bytes],
        size: int | None = None
    ) -> None:
        self._iterator = iterator
        self._size = size
        self._consumed = 0

    @property
    def size(self) -> int | None:
        return self._size

    def __iter__(self) -> typing.Iterator[bytes]:
        self._consumed = 0
        for part in self._iterator:
            self._consumed += len(part)
            if self._size is not None and self._consumed > self._size:
                raise ValueError("IterByteStream returned more data than expected size")
            yield part

        if self._size is not None and self._consumed < self._size:
            raise ValueError("IterByteStream returned less data than expected size")
        self._size = self._consumed

    def __repr__(self) -> str:
        if self._size is None:
            percent_of_size = "0% of ???" if self._consumed == 0 else "???% of ???"
        else:
            percent = self._consumed * 100 // self._size
            size = humanize_size(self._size)
            percent_of_size = f"{percent}% of {size}"

        return f"<IterByteStream [{percent_of_size}]>"


class FileStream(Stream):
    def __init__(self, path: str):
        self._path = path

    @property
    def size(self) -> int | None:
        return os.path.getsize(self._path)

    def __iter__(self) -> typing.Iterator[bytes]:
        BUFFER_SIZE = 64 * 1024
        with open(self._path, "rb") as fin:
            while buffer := fin.read(BUFFER_SIZE):
                yield buffer

    def __repr__(self):
        size = humanize_size(self.size)
        return f"<FileStream [{self._path!r} {size}]>"
