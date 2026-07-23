# LLM setup

Configure a local or cloud LLM for pipeline import and Workspace chat.

**Most people only need the [Beginner (Ollama)](#beginner-ollama--one-model) section.** You pull **one** model; role-specific overrides are optional.

**Prerequisites:** Python venv and `pip install -r requirements.txt` from the [README Quick start](../../README.md#quick-start-beginner).

Back to [Documentation index](../INDEX.md).

---

## Beginner (Ollama — one model)

You do **not** need five models. One Ollama model powers import, classification, and chat.

### 1. Install Ollama

1. Download [Ollama](https://ollama.com) for your OS and install it.
2. Open the Ollama app so the local server is running (`http://127.0.0.1:11434`).

### 2. Pull one model

| Your machine | Command | Notes |
|--------------|---------|--------|
| **16GB+ RAM** (recommended) | `ollama pull qwen2.5:14b` | Best quality/speed balance |
| **8–16GB RAM** | `ollama pull qwen2.5:7b` | Faster; slightly less nuanced labels |

### 3. Copy the beginner config

```bash
cp config/.env.ollama.beginner config/.env
```

That preset sets `LLM_PROVIDER=ollama` and uses the **same** model for pipeline and chat. Advanced features (Learning Agent, classification audit) stay **off**.

If you pulled `qwen2.5:7b`, edit `config/.env` and set all three to `qwen2.5:7b`:

```env
OLLAMA_MODEL=qwen2.5:7b
PIPELINE_MODEL=qwen2.5:7b
CHAT_MODEL=qwen2.5:7b
```

### 4. Verify

```bash
curl http://127.0.0.1:11434/v1/models
```

You should see your pulled model in the JSON. Then start the app (`./start.sh`) and open http://127.0.0.1:8000.

### 5. First import

Upload [`samples/sample_transactions.csv`](../../samples/sample_transactions.csv) from the Import sidebar, or use your bank CSV (needs Date, Amount, Category, and a description column).

---

## Expert: which model?

| Model | Verdict |
|-------|---------|
| **qwen2.5:14b** (Ollama) | Default for beginners and most machines. |
| **qwen2.5:7b** / **qwen2.5:7b-instruct** | Faster for repeat runs once the description cache is warm. |
| **qwen2.5-14b-instruct-mlx** (LM Studio) | Best speed/quality on Apple Silicon via LM Studio. |
| **qwen2.5-coder-32b-instruct** | Optional stronger model for audit / learning — **not required** for day-to-day use. |

---

## Expert: role-specific models

Optional. If unset, each role falls back to `OLLAMA_MODEL` / `LM_STUDIO_MODEL` / `CHAT_MODEL` as documented in [config/.env.example](../../config/.env.example).

Precedence: role var → `LLM_MODEL` → provider `*_MODEL`.

| Variable | Role | Beginner need? |
|----------|------|----------------|
| `PIPELINE_MODEL` | Import descriptions + classification | No — same as base |
| `CHAT_MODEL` | Workspace chat, review suggest, edit insights | No — same as base |
| `CLASSIFICATION_AUDIT_MODEL` | Sampled audit spot-check | No — feature off in beginner preset |
| `LEARNING_AGENT_MODEL` | Decision Analyst scheduler | No — feature off in beginner preset |

Example (expert): keep routine work on 14B, use a larger model only when you enable Learning Agent:

```env
PIPELINE_MODEL=qwen2.5:14b
CHAT_MODEL=qwen2.5:14b
LEARNING_AGENT_ENABLED=1
LEARNING_AGENT_MODEL=qwen2.5-coder:32b
```

Full Ollama preset with audit/vocabulary tuning: `cp config/.env.ollama config/.env`

---

## Expert: LM Studio

The pipeline uses LM Studio’s **OpenAI-compatible API**.

1. Start LM Studio and load a model.
2. Enable **Serve on Local Network** (e.g. port `1234`).
3. Copy the preset or set:

| Setting | Example |
|---------|---------|
| `LLM_PROVIDER` | `lmstudio` |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` |
| `LM_STUDIO_MODEL` | `qwen2.5-14b-instruct-mlx` |

```bash
cp config/.env.lmstudio config/.env
```

---

## Expert: OpenAI cloud (optional)

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

See [config/.env.example](../../config/.env.example) for optional `OPENAI_MODEL` and base URL overrides.

---

## Expert: performance tips (Apple Silicon)

| Lever | Recommendation |
|-------|----------------|
| **Model** | `qwen2.5-14b-instruct-mlx` (LM Studio) or `qwen2.5:7b` (Ollama) for routine months |
| **Description cache** | Second runs are much faster once `description_lookup` in SQLite is warm |
| **Batch size** | Override with `LOCAL_BATCH_SIZE` / `DESCRIPTION_BATCH_SIZE` in `.env` on large-RAM machines |

Restart `./start.sh` (or uvicorn) after changing LLM settings.

---

## Config file cheat sheet

| File | Who it’s for |
|------|----------------|
| [`config/.env.ollama.beginner`](../../config/.env.ollama.beginner) | **Start here** — one model, extras off |
| [`config/.env.ollama`](../../config/.env.ollama) | Ollama + audit/tuning knobs |
| [`config/.env.lmstudio`](../../config/.env.lmstudio) | LM Studio |
| [`config/.env.example`](../../config/.env.example) | Full commented reference (all providers) |
