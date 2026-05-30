# Production Repo Resolution

## Problem

Hook `cwd` is not always the repo that owns the work. A user can start Codex in
a parent workspace and then run tools inside a nested Git repository:

```text
hook cwd:        C:\Users\sumit\Downloads\Dora
actual workdir:  C:\Users\sumit\Downloads\Dora\agent-memory-orchestrator
```

If the production pipeline validates commits against the parent repo, work packets fail even though
the commits are real. If it accepts the parent path blindly, durable memory can
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

`repo_id` is the durable memory scope. `repo_path` is local support metadata.
The same repository cloned to two devices should resolve to the same `repo_id`
when it has the same normalized Git remote. Local paths are not allowed to be
canonical graph identity.

Canonical central keys use:

```text
commit      = repo_id + full_commit_sha
file        = repo_id + normalized_file_path
symbol      = repo_id + normalized_file_path + qualified_name
code_region = repo_id + normalized_file_path + ast_kind + qualified_name
```

This means one AMO home can capture sessions from many repositories without
mixing their central atoms, retrieval docs, graph views, or version-flow debug
surfaces.

## Runner Behavior

`evidence_view` resolves the repo root before commit truth is extracted. If the
resolved repo differs from the queued job path, the runner updates
`v2_session_jobs.repo_path` and records a `repo_resolved` event.

The runner also persists `v2_session_jobs.repo_id`. `central_version_merge`
plans, central merge locks, `GraphCommit`, and `GraphView` rows are scoped by
that `repo_id`, so two repositories do not race on one branch head or share one
active `GraphView`.

`work_packets` also has a recovery path for already-created evidence views. If
all commit facts are unresolved, it re-runs commit truth against the resolved
repo root before deciding whether to quarantine commits.

Session graph nodes retain both fields:

```text
repo_id   = canonical memory scope
repo_path = local path used for debug and operator display
```

Retrieval documents inherit `repo_id` from graph nodes. The dashboard and graph
workbench expose a repository selector backed by `/api/repos`; CLI maintenance
commands accept `--repo-id` for scoped rebuild, embedding, retrieval, and
version-flow inspection.

Install does not require per-repo setup. Hooks capture the current working
directory and tool payloads for every Codex/Claude session; production resolves the repo
when a closed-session job runs. New repositories get their own repo-scoped jobs,
central atoms, `GraphView`, retrieval docs, and vectors automatically.

## Validation

The required behavior is:

```text
parent repo exists
nested repo exists
hook cwd = parent
transcript workdir = nested
commit belongs to nested
Production job repo_path becomes nested
Production job repo_id becomes the nested repo's canonical id
work_packets produces commit-backed packets
central merge writes/reads GraphView for only that repo_id
retrieval can filter to only that repo_id
```

This prevents false `no_commit_backed_work_packets` failures while preserving
the safety rule that answer-grade graph output requires real Git commit support.
