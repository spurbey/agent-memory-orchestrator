"""Compatibility facade for domain-owned peer admission policy."""

from __future__ import annotations

from ..domain.peer.policy import PeerPolicy
from ..domain.peer.policy import PolicyDecision

__all__ = ["PeerPolicy", "PolicyDecision"]
