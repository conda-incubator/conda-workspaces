"""Resource limits shared by repository-controlled document parsers."""

from __future__ import annotations

import os
import stat
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, cast

from ruamel.yaml import YAML
from ruamel.yaml.composer import Composer
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.events import MappingStartEvent, SequenceStartEvent
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from ruamel.yaml.nodes import Node


MAX_DOCUMENT_DEPTH = 128
MAX_DOCUMENT_COLLECTION_ITEMS = 100_000
MAX_DOCUMENT_ITEMS = 1_000_000


@dataclass
class DocumentStringBudget:
    """Bound scalar processing across one document or a batch of fragments.

    Count every string reference in visited containers, including mapping keys,
    because dictionary lookups can compare aliased strings outside our memo.
    """

    remaining: int

    def consume(self, value: str, *, label: str) -> None:
        """Charge one string reference without scanning or encoding its text."""
        self.remaining -= len(value)
        if self.remaining < 0:
            raise ValueError(f"{label} exceeds the maximum expanded string content")


@dataclass
class DocumentExpansionBudget:
    """Bound work for consumers that expand aliases into TOML or JSON.

    Document readers share this budget across referenced files. Memoized subtree
    sizes avoid expanding the graph just to determine its serialized size.
    """

    items: int
    strings: int

    def consume(self, value: object, *, label: str) -> None:
        sizes: dict[int, tuple[int, int]] = {}
        active: set[int] = set()
        stack = [(value, False)]
        while stack:
            current, exiting = stack.pop()
            identity = id(current)
            if identity in sizes:
                continue
            if isinstance(current, Mapping):
                children = [item for pair in current.items() for item in pair]
                count = len(current)
            elif isinstance(current, list):
                children = current
                count = len(current)
            else:
                sizes[identity] = (0, len(current) if isinstance(current, str) else 0)
                continue
            if not exiting:
                if identity in active:
                    raise ValueError(f"{label} contains a cyclic collection")
                active.add(identity)
                stack.append((current, True))
                stack.extend((child, False) for child in children)
                continue
            active.remove(identity)
            strings = 0
            for child in children:
                child_items, child_strings = sizes[id(child)]
                count += child_items
                strings += child_strings
                if count > self.items:
                    raise ValueError(
                        f"{label} exceeds the maximum expanded collection items"
                    )
                if strings > self.strings:
                    raise ValueError(
                        f"{label} exceeds the maximum expanded string content"
                    )
            sizes[identity] = (count, strings)
        items, strings = sizes[id(value)]
        if items > self.items:
            raise ValueError(f"{label} exceeds the maximum expanded collection items")
        if strings > self.strings:
            raise ValueError(f"{label} exceeds the maximum expanded string content")
        self.items -= items
        self.strings -= strings


class _BoundedComposer(Composer):
    """Reject oversized collections before composing their next child node."""

    maximum_depth: int
    maximum_collection_items: int
    remaining_items: int
    label: str
    depth: int

    def compose_node(self, parent: Node | None, index: object) -> Node:
        if isinstance(parent, SequenceNode) or (
            isinstance(parent, MappingNode) and index is None
        ):
            if len(parent.value) >= self.maximum_collection_items:
                raise ValueError(f"{self.label} exceeds the maximum collection items")
            self.remaining_items -= 1
            if self.remaining_items < 0:
                raise ValueError(f"{self.label} exceeds the maximum collection items")
        self.depth += 1
        try:
            if self.depth > self.maximum_depth and self.parser.check_event(
                SequenceStartEvent, MappingStartEvent
            ):
                raise ValueError(f"{self.label} exceeds the maximum nesting depth")
            return cast("Node", super().compose_node(parent, index))
        finally:
            self.depth -= 1


class _BoundedSafeConstructor(SafeConstructor):
    """Reserve merge entries before ruamel expands them, once per source node."""

    remaining_items: int
    maximum_collection_items: int
    remaining_strings: int
    label: str
    flattened: dict[int, int]

    def flatten_mapping(self, node: MappingNode) -> None:
        if id(node) in self.flattened:
            return
        size = len(node.value)
        strings = sum(
            len(item.value)
            for key, value in node.value
            if key.tag != "tag:yaml.org,2002:merge"
            for item in (key, value)
            if isinstance(item, ScalarNode)
        )
        for key, value in node.value:
            if key.tag != "tag:yaml.org,2002:merge":
                continue
            sources = value.value if isinstance(value, SequenceNode) else [value]
            for source in sources:
                if isinstance(source, MappingNode):
                    self.flatten_mapping(source)
                    size += len(source.value)
                    strings += self.flattened[id(source)]
                    if size > self.maximum_collection_items:
                        raise ValueError(
                            f"{self.label} exceeds the maximum collection items"
                        )
                    if size > self.remaining_items:
                        raise ValueError(
                            f"{self.label} exceeds the maximum collection items"
                        )
                    if strings > self.remaining_strings:
                        raise ValueError(
                            f"{self.label} exceeds the maximum expanded string content"
                        )
        self.remaining_items -= size
        if self.remaining_items < 0:
            raise ValueError(f"{self.label} exceeds the maximum collection items")
        self.remaining_strings -= strings
        if self.remaining_strings < 0:
            raise ValueError(
                f"{self.label} exceeds the maximum expanded string content"
            )
        super().flatten_mapping(node)
        self.flattened[id(node)] = strings


@dataclass
class BoundedYamlLoader:
    """Use ruamel's safe YAML semantics with budgets before merge construction.

    Conda's round-trip loader has no construction limits and retains serializer
    metadata. Lockfiles and imported manifests both require plain data here.
    """

    maximum_bytes: int
    maximum_depth: int
    maximum_collection_items: int
    maximum_items: int
    label: str

    def load(
        self, content: str | bytes, *, string_budget: DocumentStringBudget | None = None
    ) -> Any:
        text = decode_limited_text(
            content, maximum_bytes=self.maximum_bytes, label=self.label
        )
        parser = YAML(typ="safe", pure=True)
        parser.Composer = _BoundedComposer
        parser.Constructor = _BoundedSafeConstructor
        composer = cast("_BoundedComposer", parser.composer)
        composer.maximum_depth = self.maximum_depth
        composer.maximum_collection_items = self.maximum_collection_items
        composer.remaining_items = self.maximum_items
        composer.label = self.label
        composer.depth = -1
        try:
            node = parser.compose(text)
            self.validate_nodes(node)
            constructor = cast("_BoundedSafeConstructor", parser.constructor)
            constructor.remaining_items = self.maximum_items
            constructor.remaining_strings = self.maximum_bytes
            constructor.maximum_collection_items = self.maximum_collection_items
            constructor.label = self.label
            constructor.flattened = {}
            data = constructor.construct_document(node) if node is not None else None
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Invalid {self.label.removesuffix(' YAML').lower()} YAML"
            ) from exc
        validate_document_limits(
            data,
            label=self.label,
            maximum_depth=self.maximum_depth,
            maximum_collection_items=self.maximum_collection_items,
            maximum_items=self.maximum_items,
            string_budget=string_budget or DocumentStringBudget(self.maximum_bytes),
        )
        return data

    def validate_nodes(self, root: Node | None) -> None:
        """Reject cycles and excessive graphs before constructors can recurse."""
        stack = [(root, 0, False)]
        active: set[int] = set()
        visited: set[int] = set()
        remaining = self.maximum_items
        strings = DocumentStringBudget(self.maximum_bytes)
        while stack:
            node, depth, exiting = stack.pop()
            if node is None:
                continue
            identity = id(node)
            if exiting:
                active.remove(identity)
                visited.add(identity)
                continue
            if isinstance(node, ScalarNode):
                strings.consume(node.value, label=self.label)
                continue
            if identity in active:
                raise ValueError(f"{self.label} contains a cyclic collection")
            if depth > self.maximum_depth:
                raise ValueError(f"{self.label} exceeds the maximum nesting depth")
            if identity in visited:
                continue
            if len(node.value) > self.maximum_collection_items:
                raise ValueError(f"{self.label} exceeds the maximum collection items")
            remaining -= len(node.value)
            if remaining < 0:
                raise ValueError(f"{self.label} exceeds the maximum collection items")
            if isinstance(node, MappingNode):
                if any(
                    not isinstance(key, ScalarNode)
                    or key.tag
                    not in {"tag:yaml.org,2002:str", "tag:yaml.org,2002:merge"}
                    for key, _ in node.value
                ):
                    raise ValueError(f"{self.label} mapping keys must be strings")
                children = [item for pair in node.value for item in pair]
            else:
                children = node.value
            active.add(identity)
            stack.append((node, depth, True))
            stack.extend((child, depth + 1, False) for child in children)


def plain_document(
    value: Any,
    *,
    maximum_items: int = MAX_DOCUMENT_ITEMS,
    maximum_collection_items: int = MAX_DOCUMENT_COLLECTION_ITEMS,
    string_budget: DocumentStringBudget | None = None,
) -> Any:
    """Copy visible document values without YAML serializer-only attributes.

    Shared lockfile consumers need this projection for both parsed documents and
    library-supplied round-trip objects. Preserve aliases without invoking their
    deepcopy hooks, including for already-constructed cyclic input.
    """
    memo: dict[int, Any] = {}
    pending = [value]
    containers: list[Any] = []
    while pending:
        current = pending.pop()
        if isinstance(current, str) and string_budget is not None:
            string_budget.consume(current, label="Document")
        identity = id(current)
        if identity in memo:
            continue
        if isinstance(current, (Mapping, list, tuple)):
            maximum_items -= len(current)
            if maximum_items < 0 or len(current) > maximum_collection_items:
                raise ValueError("Document exceeds the maximum collection items")
        if isinstance(current, Mapping):
            memo[identity] = {}
            containers.append(current)
            for key, child in current.items():
                if not isinstance(key, str):
                    raise ValueError("Document mapping keys must be strings")
                pending.extend((key, child))
        elif isinstance(current, (list, tuple)):
            memo[identity] = []
            containers.append(current)
            pending.extend(current)
        elif isinstance(current, str):
            memo[identity] = str(current)
        elif isinstance(current, bool) or current is None:
            memo[identity] = current
        elif isinstance(current, int):
            memo[identity] = int(current)
        elif isinstance(current, float):
            memo[identity] = float(current)
        elif type(current) in (date, datetime, bytes):
            memo[identity] = current
        else:
            raise ValueError("Unsupported document value")
    for current in containers:
        result = memo[id(current)]
        if isinstance(current, Mapping):
            result.update(
                (memo[id(key)], memo[id(child)]) for key, child in current.items()
            )
        else:
            result.extend(memo[id(child)] for child in current)
    return memo[id(value)]


def document_values_equal(
    left: object,
    right: object,
    *,
    equal_value_pairs: dict[tuple[int, int], tuple[object, object]] | None = None,
) -> bool:
    """Compare parsed document values without revisiting aliases.

    Successful comparisons extend *equal_value_pairs*. The memo retains the
    values so their identity keys remain valid across later comparisons.
    """
    stack = [(left, right)]
    root_identities = (id(left), id(right))
    compared: set[tuple[int, int]] = set()
    new_equal_value_pairs: dict[tuple[int, int], tuple[object, object]] = {}

    while stack:
        current_left, current_right = stack.pop()
        if current_left is current_right:
            continue
        identities = (id(current_left), id(current_right))
        if identities in compared or (
            equal_value_pairs is not None and identities in equal_value_pairs
        ):
            continue
        compared.add(identities)
        if identities != root_identities:
            new_equal_value_pairs[identities] = (current_left, current_right)

        if isinstance(current_left, Mapping):
            if not isinstance(current_right, Mapping):
                return False
            left_mapping = cast("Mapping[object, object]", current_left)
            right_mapping = cast("Mapping[object, object]", current_right)
            if len(left_mapping) != len(right_mapping):
                return False
            for key, value in left_mapping.items():
                if key not in right_mapping:
                    return False
                stack.append((value, right_mapping[key]))
            continue
        if isinstance(current_right, Mapping):
            return False

        if isinstance(current_left, list):
            if not isinstance(current_right, list):
                return False
            if len(current_left) != len(current_right):
                return False
            stack.extend(zip(current_left, current_right))
            continue
        if isinstance(current_right, list):
            return False

        if current_left != current_right:
            return False

    if equal_value_pairs is not None:
        equal_value_pairs.update(new_equal_value_pairs)
    return True


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
    string_budget: DocumentStringBudget | None = None,
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

        if isinstance(current, str):
            if string_budget is not None:
                string_budget.consume(current, label=label)
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
