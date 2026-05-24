# V2 Repo Resolution

## Problem

Hook `cwd` is not always the repo that owns the work. A user can start Codex in
a parent workspace and then run tools inside a nested Git repository:

```text
hook cwd:        C:\Users\sumit\Downloads\Dora
actual workdir:  C:\Users\sumit\Downloads\Dora\agent-memory-orchestrator
```

If V2 validates commits against the parent repo, work packets fail even though
the commits are real. If V2 accepts the parent path blindly, durable memory can
attach commits, files, symbols, and decisions to the wrong `repo_id`.

## Rule

Repo scope is chosen by commit ownership, not by first hook cwd.

```text
raw records + transcript tool calls
-> candidate paths
-> candidate Git roots
-> count resolved commit ids per root
-> choose the Git root that owns the most session commits
```

## Inputs

- hook record paths: `cwd`, `repo_path`, `repo_root`, `workspace`, `workspace_root`
- transcript tool call paths: `workdir`, `cwd`, `repo_path`, `repo_root`
- absolute paths embedded in tool commands
- commit ids from git commit output and git log output

## Output

`SessionRepoResolution`:

```json
{
  "repo_root": "...",
  "source": "commit_resolution|git_root_candidate|unresolved",
  "fallback_repo_path": "...",
  "candidate_count": 0,
  "commit_count": 0,
  "resolved_commit_count": 0,
  "candidates": [],
  "commit_ids_sample": []
}
```

## Runner Behavior

`evidence_view` resolves the repo root before commit truth is extracted. If the
resolved repo differs from the queued job path, the runner updates
`v2_session_jobs.repo_path` and records a `repo_resolved` event.

`work_packets` also has a recovery path for already-created evidence views. If
all commit facts are unresolved, it re-runs commit truth against the resolved
repo root before deciding whether to quarantine commits.

## Validation

The required behavior is:

```text
parent repo exists
nested repo exists
hook cwd = parent
transcript workdir = nested
commit belongs to nested
V2 job repo_path becomes nested
work_packets produces commit-backed packets
```

This prevents false `no_commit_backed_work_packets` failures while preserving
the safety rule that answer-grade graph output requires real Git commit support.
