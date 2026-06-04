"""Compatibility facade for domain-owned peer identity models."""

from __future__ import annotations

from ..domain.peer.models import DEFAULT_CAPABILITIES
from ..domain.peer.models import PeerConfig
from ..domain.peer.models import PeerNode

__all__ = ["DEFAULT_CAPABILITIES", "PeerConfig", "PeerNode"]
