"""Antelligent desktop companion lifecycle helpers."""

from .doctor import doctor
from .install import install_antelligent, uninstall_antelligent
from .launch_config import write_launch_config
from .process import start_antelligent, status_antelligent, stop_antelligent
from .startup import install_startup, startup_status, uninstall_startup

__all__ = [
    "doctor",
    "install_antelligent",
    "install_startup",
    "start_antelligent",
    "startup_status",
    "status_antelligent",
    "stop_antelligent",
    "uninstall_antelligent",
    "uninstall_startup",
    "write_launch_config",
]
