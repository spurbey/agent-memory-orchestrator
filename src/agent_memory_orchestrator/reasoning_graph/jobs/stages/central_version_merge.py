from __future__ import annotations

import sys

from ....application.pipeline.stages import central_version_merge as _impl

sys.modules[__name__] = _impl
