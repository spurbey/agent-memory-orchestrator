from __future__ import annotations

import sys

from ....application.pipeline.stages import qwen_reasoning as _impl

sys.modules[__name__] = _impl
