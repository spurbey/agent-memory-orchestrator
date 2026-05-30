"""Compatibility wrapper for the runtime daemon client."""

from __future__ import annotations

import sys

from ..runtime.daemon import client as _impl

sys.modules[__name__] = _impl
