"""Infrastructure adapters for the AMO peer-netd sidecar."""

from __future__ import annotations

from .artifacts import DEFAULT_PEER_NETD_MANIFEST_URL
from .artifacts import PeerNetdArtifact
from .artifacts import install_peer_netd_artifact
from .client import PeerNetdClient
from .client import PeerNetdError
from .relay_bootstrap import DEFAULT_RELAY_BOOTSTRAP_URL
from .relay_bootstrap import ManagedRelayProfile
from .relay_bootstrap import load_managed_relay_profile
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
    "DEFAULT_PEER_NETD_MANIFEST_URL",
    "DEFAULT_RELAY_BOOTSTRAP_URL",
    "REQUIRED_NETD_FLAGS",
    "REQUIRED_NETD_PROTOCOL_CAPABILITIES",
    "ManagedRelayProfile",
    "PeerNetdArtifact",
    "PeerNetdClient",
    "PeerNetdError",
    "PeerNetdLaunchOptions",
    "PeerNetdRuntime",
    "PeerNetdRuntimeError",
    "PeerNetdServiceOptions",
    "binary_name",
    "install_peer_netd_artifact",
    "install_service",
    "load_managed_relay_profile",
    "install_service_plan",
    "platform_binary_dir_name",
    "service_status",
    "uninstall_service",
]
