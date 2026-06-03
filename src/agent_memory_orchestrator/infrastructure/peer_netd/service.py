"""OS startup-service infrastructure adapter for peer-netd."""

from __future__ import annotations

from ...peer.netd_service import PeerNetdServiceOptions
from ...peer.netd_service import install_service
from ...peer.netd_service import install_service_plan
from ...peer.netd_service import service_status
from ...peer.netd_service import uninstall_service

__all__ = [
    "PeerNetdServiceOptions",
    "install_service",
    "install_service_plan",
    "service_status",
    "uninstall_service",
]
