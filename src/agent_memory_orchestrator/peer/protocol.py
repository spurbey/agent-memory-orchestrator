"""Compatibility facade for the domain-owned peer message protocol."""

from __future__ import annotations

from ..domain.peer.protocol import SYSTEM_MESSAGE_TYPES
from ..domain.peer.protocol import PeerMessage
from ..domain.peer.protocol import is_conversation_message
from ..domain.peer.protocol import normalize_citations
from ..domain.peer.protocol import normalize_recipients

__all__ = [
    "SYSTEM_MESSAGE_TYPES",
    "PeerMessage",
    "is_conversation_message",
    "normalize_citations",
    "normalize_recipients",
]
