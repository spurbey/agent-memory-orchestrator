from __future__ import annotations

import sys

from ....application.pipeline.stages import session_graph_write as _impl

sys.modules[__name__] = _impl
