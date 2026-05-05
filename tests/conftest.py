from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path() -> Path:
    root = Path(".tmp") / "test-runs"
    path = root / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
