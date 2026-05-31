from __future__ import annotations

import sys

from ....application.pipeline.stages import code_graph as _impl

sys.modules[__name__] = _impl
