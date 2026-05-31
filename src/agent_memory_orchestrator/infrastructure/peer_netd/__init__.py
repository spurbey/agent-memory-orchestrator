"""Infrastructure adapters for the AMO peer-netd sidecar."""

from __future__ import annotations

from .client import PeerNetdClient
from .client import PeerNetdError
from .runtime import REQUIRED_NETD_FLAGS
from .runtime import REQUIRED_NETD_PROTOCOL_CAPABILITIES
from .runtime import PeerNetdLaunchOptions
from .runtime import PeerNetdRuntime
from .runtime import PeerNetdRuntimeError
from .runtime import binary_name
from .runtime import platform_binary_dir_name
from .service import PeerNetdServiceOptions
from .service import install_service
from .service import install_service_plan
from .service import service_status
from .service import uninstall_service

__all__ = [
    "REQUIRED_NETD_FLAGS",
    "REQUIRED_NETD_PROTOCOL_CAPABILITIES",
    "PeerNetdClient",
    "PeerNetdError",
    "PeerNetdLaunchOptions",
    "PeerNetdRuntime",
    "PeerNetdRuntimeError",
    "PeerNetdServiceOptions",
    "binary_name",
    "install_service",
    "install_service_plan",
    "platform_binary_dir_name",
    "service_status",
    "uninstall_service",
]
