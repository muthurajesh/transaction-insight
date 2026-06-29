# LLM setup

Configure a local or cloud LLM for pipeline import and Workspace chat.

**Prerequisites:** completed [README Quick start](../../README.md#quick-start) (venv, `pip install`, `config/.env` copied).

Back to [Documentation index](../INDEX.md).

---

## Ollama (common default)

1. Install and start [Ollama](https://ollama.com).
2. Pull a model:

```bash
ollama pull qwen2.5:14b
```

3. In `config/.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen2.5:14b
```

Verify: `curl http://127.0.0.1:11434/v1/models`

Good picks: `qwen2.5:14b`, `qwen2.5:7b-instruct` (faster after lookups exist), `llama3.1:8b`.

Or copy the preset: `cp config/.env.ollama config/.env`

---

## LM Studio

The pipeline uses LM Studio’s **OpenAI-compatible API** on your LAN.

1. Start LM Studio and load a model.
2. Enable **Serve on Local Network** (e.g. port `1234`).
3. In `config/.env`:

| Setting | Example |
|---------|---------|
| `LLM_PROVIDER` | `lmstudio` |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` |
| `LM_STUDIO_MODEL` | `qwen2.5-14b-instruct-mlx` |

Or copy the preset: `cp config/.env.lmstudio config/.env`

---

## Which model to use?

| Model | Verdict |
|-------|---------|
| **qwen2.5-coder-32b-instruct** | Best quality for nuanced rules; slower. Good for first-time seeding. |
| **qwen2.5-14b-instruct-mlx** | Best speed/quality balance on Apple Silicon. |
| **qwen2.5:7b-instruct** (Ollama) | Fastest for repeat runs once description cache is warm. |

---

## Role-specific models

Optional overrides in `config/.env` (precedence: role var → `LLM_MODEL` → provider `*_MODEL`):

| Variable | Role |
|----------|------|
| `PIPELINE_MODEL` | Import descriptions + classification (fast) |
| `CHAT_MODEL` | Workspace chat, review suggest, edit insights |
| `CLASSIFICATION_AUDIT_MODEL` | Sampled audit spot-check (defaults to pipeline) |
| `LEARNING_AGENT_MODEL` | Decision Analyst scheduler (defaults to chat) |

Example: `LEARNING_AGENT_MODEL=qwen2.5-coder:32b` for pattern analysis while keeping `PIPELINE_MODEL` on 14B.

---

## Performance tips (Apple Silicon)

| Lever | Recommendation |
|-------|----------------|
| **Model** | `qwen2.5-14b-instruct-mlx` or `qwen2.5:7b` for routine months |
| **Description cache** | Second runs are much faster once `description_lookup` in SQLite is warm |
| **Batch size** | Override with `LOCAL_BATCH_SIZE` / `DESCRIPTION_BATCH_SIZE` in `.env` on large-RAM machines |

---

## OpenAI cloud (optional)

Set in `config/.env`:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

See [config/.env.example](../../config/.env.example) for optional `OPENAI_MODEL` and base URL overrides.

Restart `./start.sh` (or uvicorn) after changing LLM settings.
