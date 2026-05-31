"""GraphView domain boundary.

`views.py` remains as a compatibility import path. New code should prefer this
module name because it matches the versioning architecture vocabulary.
"""

from __future__ import annotations

from .views import GraphViewRef
from .views import GraphViewStore
from .views import graph_view_id
from .views import resolve_graph_view

__all__ = ["GraphViewRef", "GraphViewStore", "graph_view_id", "resolve_graph_view"]
