from __future__ import annotations

import sys

from ...application.pipeline.evaluation import production_eval as _impl

sys.modules[__name__] = _impl
