from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True, frozen=True)
class ExtensionDescriptor:
    name: str
    extension_type: str
    version: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ExtensionRegistry:
    """In-memory registry for local extension instances.

    Loading executable extension code is deliberately separate from registry
    bookkeeping so private algorithms can remain local-only and opt-in.
    """

    def __init__(self) -> None:
        self._extensions: dict[tuple[str, str], object] = {}
        self._descriptors: dict[tuple[str, str], ExtensionDescriptor] = {}

    def register(self, extension: object, descriptor: ExtensionDescriptor) -> None:
        key = _key(descriptor.extension_type, descriptor.name)
        self._extensions[key] = extension
        self._descriptors[key] = descriptor

    def get(self, extension_type: str, name: str) -> object | None:
        return self._extensions.get(_key(extension_type, name))

    def descriptors(self, *, extension_type: str = "") -> list[ExtensionDescriptor]:
        if not extension_type:
            return sorted(self._descriptors.values(), key=lambda item: (item.extension_type, item.name))
        normalized = extension_type.strip().lower()
        return sorted(
            (item for item in self._descriptors.values() if item.extension_type.strip().lower() == normalized),
            key=lambda item: item.name,
        )


def _key(extension_type: str, name: str) -> tuple[str, str]:
    clean_type = extension_type.strip().lower()
    clean_name = name.strip()
    if not clean_type:
        raise ValueError("extension_type is required")
    if not clean_name:
        raise ValueError("extension name is required")
    return clean_type, clean_name


__all__ = ["ExtensionDescriptor", "ExtensionRegistry"]
