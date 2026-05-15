# Leiden Community Detection

## Depends on
- ../graph_model/edge-types.md
- ../modules/kuzu-graph-store.md

## Used by
- ../modules/graph-validation.md

## Related docs
- ../graph_model/node-types.md
- ../examples/code-query-flow.md

## Purpose

Leiden groups related central graph nodes for navigation and future retrieval scoping. It does not decide truth, validity, or ranking by itself.

## Input Projection

Read central answer/support graph edges from Kuzu and create an undirected weighted graph.

Example query:

```cypher
MATCH (a)-[r]->(b)
RETURN a.id, b.id, r.weight, r.kind
```

## Edge Weights

Default weights:

```text
CAUSED_BY          0.9
SUPERSEDES         0.8
REFINES            0.8
REVERTS            0.8
PRODUCED_CHANGE_IN 0.7
VALIDATED_BY       0.7
MODIFIES           0.5
SAME_FILE          0.5
FOLLOWED_BY        0.2
```

Raw-only provenance edges should be excluded unless explicitly running a provenance community job.

## Implementation Pattern

```python
G = nx.Graph()
for row in kuzu_rows:
    G.add_edge(row["a.id"], row["b.id"], weight=row["r.weight"])

ig_graph = ig.Graph.from_networkx(G)
partition = leidenalg.find_partition(
    ig_graph,
    leidenalg.ModularityVertexPartition,
    weights="weight",
)

for node_index, community_id in enumerate(partition.membership):
    node_id = ig_graph.vs[node_index]["_nx_name"]
    store_community(node_id, community_id)
```

## Community Labels

Community labels are navigation metadata. They are generated after membership is fixed and must never alter community membership, node status, or answer ranking.

### Deterministic Top-Term Label Algorithm

Inputs:

- member node `kind`, `label`, `summary`, `metadata.entities`, `file_path`, and internal community degree
- final Leiden `community_id`

Collection:

1. Collect text from each member node:
   - `label`
   - `summary`
   - normalized file path stem and parent directory names
   - explicit entity fields such as package names, class names, function names, dependency names, and config keys
2. Exclude raw/support-only node kinds from label scoring unless they are the only members:
   - `RawEvidenceRef`
   - `Prompt`
   - `ToolResult`
   - `Session`
   - `Repo`
   - `Branch`
3. Tokenize with lowercase ASCII normalization and split on whitespace, punctuation, path separators, camel-case boundaries, underscores, and hyphens.
4. Drop tokens that are not label material:
   - stopwords: `the`, `and`, `for`, `with`, `from`, `that`, `this`, `into`, `when`, `after`, `before`
   - generic graph words: `node`, `edge`, `graph`, `session`, `latest`, `update`, `changed`, `work`, `result`, `summary`
   - raw ids and hashes: `raw_*`, UUID-like strings, hex strings length `>= 7`
   - path noise: `src`, `lib`, `test`, `tests`, `docs`, `file`, `files`, `index`, `main`, `init`
   - tokens shorter than 3 characters unless they are known technical keys such as `ndk`, `sdk`, `api`, `ui`

Weights:

```text
Decision, Fix, Bug, Blocker, OpenQuestion  3.0
CodeNode, CodeHunk                         3.0
DecisionThread, TestRun                    2.5
WorkChange                                 2.0
File                                       1.5
Community, App                             1.0
Raw/support-only nodes                     0.0 by default
```

Field weights:

```text
metadata.entities     1.4
file path stem        1.3
label                 1.2
summary               1.0
parent directory      0.8
```

Degree weighting:

```text
degree_weight = 1.0 + log1p(internal_degree)
```

Term score:

```text
score(term) = sum(kind_weight * field_weight * degree_weight)
```

Add a repetition bonus when the term appears in multiple member nodes:

```text
score(term) += 0.5 * distinct_member_count(term)
```

Selection:

1. Sort terms by score descending.
2. Break ties by higher distinct member count.
3. Break remaining ties lexicographically for stable output.
4. Prefer file/entity terms that appear in at least two member nodes.
5. Choose top `2-5` terms.
6. Render title case only at display time. Store normalized tokens plus rendered label.
7. If no useful terms remain, label is `Community <community_id>`.

Example:

```text
Members:
- Decision: "Pin NDK to 27 because Sentry requires it"
- CodeNode: "build.gradle.kts ndk block"
- TestRun: "Flutter build passed after NDK pin"

Top terms:
- ndk
- sentry
- build.gradle

Label:
NDK Sentry Build.gradle
```

### Qwen Label Improvement

Qwen may propose a shorter human label only after the deterministic label exists. The Qwen label is accepted only if the community label contract in `../modules/qwen-contracts.md` passes schema validation, confidence threshold, and timeout limits.

Qwen label output may update:

- community display label
- label diagnostic reason

Qwen label output must never update:

- community membership
- graph node status
- edge weights
- answer-grade truth
- retrieval rank by itself

## Tests

- Weighted graph creates stable community ids for fixture graph.
- Raw-only support nodes are excluded by default.
- Community id is written to nodes.
- Deterministic labels are stable across repeated runs.
- Hashes, raw ids, and generic graph terms cannot dominate labels.
- Qwen label failure falls back to deterministic label.
