# Local Models

AMO uses local models only. Normal retrieval should not silently download models.

## Defaults

| Purpose | Default |
| --- | --- |
| Reasoning extraction | `qwen3.5:9b` through Ollama |
| Text embeddings | `BAAI/bge-m3` |
| Cross-encoder reranking | `BAAI/bge-reranker-base` |
| Vector cache | FAISS when available |

## Install Optional Runtime Packages

```bash
pip install -e ".[models]"
```

## Ollama Qwen

```bash
ollama pull qwen3.5:9b
```

Production reasoning extraction targets `qwen3.5:9b`. Smaller Qwen models are smoke-test fallbacks only.

## Presets

```bash
amo-cli models list
```

| Preset | Use |
| --- | --- |
| `cpu-light` | Low memory smoke tests |
| `cpu-balanced` | Default local production profile |
| `gpu-quality` | GPU/high-RAM profile |

Check local cache:

```bash
amo-cli models status --preset cpu-balanced
```

Download/cache intentionally:

```bash
amo-cli models download --preset cpu-balanced
```

Require local load success:

```bash
amo-cli models preflight --preset cpu-balanced
```

If you change embedding models, rebuild vectors:

```bash
amo-cli rebuild-indexes --force-vectors
```
