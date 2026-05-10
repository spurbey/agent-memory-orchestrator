from __future__ import annotations

import sys

from .app import hook as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())

sys.modules[__name__] = _impl
