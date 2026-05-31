from __future__ import annotations

import sys

from ....application.pipeline.stages import evidence_packets as _impl

sys.modules[__name__] = _impl
