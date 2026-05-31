from __future__ import annotations

import sys

from ....application.pipeline.stages import retrieval_projection as _impl

sys.modules[__name__] = _impl
