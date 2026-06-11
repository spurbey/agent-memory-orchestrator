from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import parse_unified_diff_hunks


def test_parse_unified_diff_hunks_extracts_file_and_ranges() -> None:
    diff = """diff --git a/src/auth.py b/src/auth.py
index 1111111..2222222 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,2 +10,3 @@ def login():
 old line
-bad
+good
+extra
"""

    hunks = parse_unified_diff_hunks(diff)

    assert len(hunks) == 1
    assert hunks[0].file_path == "src/auth.py"
    assert hunks[0].old_range.start == 10
    assert hunks[0].old_range.count == 2
    assert hunks[0].new_range.start == 10
    assert hunks[0].new_range.count == 3
    assert "@@ -10,2 +10,3 @@" in hunks[0].text


def test_parse_unified_diff_hunks_defaults_missing_counts_to_one() -> None:
    diff = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -5 +5 @@ def login():
-bad
+good
"""

    hunks = parse_unified_diff_hunks(diff)

    assert hunks[0].old_range.count == 1
    assert hunks[0].new_range.count == 1


def test_parse_unified_diff_hunks_uses_new_file_path() -> None:
    diff = """diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+def created():
+    return True
"""

    hunks = parse_unified_diff_hunks(diff)

    assert len(hunks) == 1
    assert hunks[0].file_path == "src/new.py"
    assert hunks[0].old_range.start == 0
    assert hunks[0].old_range.count == 0
    assert hunks[0].new_range.start == 1
    assert hunks[0].new_range.count == 2


def test_parse_unified_diff_hunks_handles_multiple_files_and_hunks() -> None:
    diff = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-a
+b
@@ -10 +10 @@
-c
+d
diff --git a/src/b.py b/src/b.py
--- a/src/b.py
+++ b/src/b.py
@@ -2 +2 @@
-x
+y
"""

    hunks = parse_unified_diff_hunks(diff)

    assert [hunk.file_path for hunk in hunks] == ["src/a.py", "src/a.py", "src/b.py"]
    assert [hunk.old_range.start for hunk in hunks] == [1, 10, 2]
