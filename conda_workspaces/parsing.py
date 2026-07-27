"""Resource limits shared by repository-controlled document parsers."""

from __future__ import annotations

import os
import stat
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


MAX_DOCUMENT_DEPTH = 128
MAX_DOCUMENT_COLLECTION_ITEMS = 100_000
MAX_DOCUMENT_ITEMS = 1_000_000


def decode_limited_text(
    content: str | bytes,
    *,
    maximum_bytes: int,
    label: str,
) -> str:
    """Return UTF-8 *content* after enforcing a byte limit."""
    if isinstance(content, bytes):
        if len(content) > maximum_bytes:
            raise ValueError(
                f"{label} exceeds the maximum size of {maximum_bytes:,} bytes"
            )
        return content.decode("utf-8")

    if len(content) > maximum_bytes:
        raise ValueError(f"{label} exceeds the maximum size of {maximum_bytes:,} bytes")
    encoded = content.encode("utf-8")
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{label} exceeds the maximum size of {maximum_bytes:,} bytes")
    return content


def read_limited_text(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> str:
    """Read UTF-8 text from *path* without reading beyond *maximum_bytes*."""
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(maximum_bytes + 1)
            final = os.fstat(stream.fileno())
        if (
            (final.st_dev, final.st_ino, final.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or (final.st_mtime_ns, final.st_ctime_ns)
            != (opened.st_mtime_ns, opened.st_ctime_ns)
            or (len(content) <= maximum_bytes and len(content) != opened.st_size)
        ):
            raise ValueError(f"{label} changed while it was read")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return decode_limited_text(
        content,
        maximum_bytes=maximum_bytes,
        label=label,
    )


def validate_document_limits(
    value: object,
    *,
    label: str,
    maximum_depth: int = MAX_DOCUMENT_DEPTH,
    maximum_collection_items: int = MAX_DOCUMENT_COLLECTION_ITEMS,
    maximum_items: int = MAX_DOCUMENT_ITEMS,
) -> int:
    """Reject excessive documents and return their collection-item count.

    The iterative walk avoids adding its own recursion risk. Repeated YAML
    aliases are counted as references but traversed once, while cyclic aliases
    are rejected.
    """
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active: set[int] = set()
    visited: set[int] = set()
    item_count = 0

    while stack:
        current, depth, exiting = stack.pop()
        if exiting:
            active.remove(id(current))
            visited.add(id(current))
            continue

        if isinstance(current, Mapping):
            collection_size = len(current)
        elif isinstance(current, Collection) and not isinstance(
            current,
            (str, bytes, bytearray),
        ):
            collection_size = len(current)
        else:
            continue

        item_count += collection_size
        if collection_size > maximum_collection_items:
            raise ValueError(
                f"{label} contains a collection with more than"
                f" {maximum_collection_items:,} items"
            )
        if item_count > maximum_items:
            raise ValueError(
                f"{label} contains more than {maximum_items:,} collection items"
            )
        if depth > maximum_depth:
            raise ValueError(
                f"{label} exceeds the maximum nesting depth of {maximum_depth}"
            )

        identity = id(current)
        if identity in active:
            raise ValueError(f"{label} contains a cyclic collection")
        if identity in visited:
            continue
        active.add(identity)
        stack.append((current, depth, True))
        if isinstance(current, Mapping):
            children = (item for pair in current.items() for item in pair)
        else:
            children = iter(current)
        stack.extend((child, depth + 1, False) for child in children)

    return item_count
