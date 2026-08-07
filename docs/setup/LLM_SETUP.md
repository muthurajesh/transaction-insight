# LLM setup

Configure a local or cloud LLM for pipeline import and Workspace chat.

**macOS beginners:** use the [README one-liner installer](../../README.md#setup-macos--one-liner) — it sets up Python and asks for **Local LLM** (Ollama `qwen2.5:7b`) or **Cloud LLM** (OpenAI).

**Most people only need the [Ollama](#ollama--one-model) section.** You pull **one** model; role-specific overrides are optional.

**Prerequisites:** Python venv and `pip install -r requirements.txt` from the [README](../../README.md) (or run `bash scripts/install_macos.sh` on macOS).

Back to [Documentation index](../INDEX.md).

---

## Config presets (one file per provider)

| File | Who it’s for |
|------|----------------|
| [`config/.env.ollama`](../../config/.env.ollama) | **Local (default)** — Ollama `qwen2.5:7b`; common app settings; extras off |
| [`config/.env.lmstudio`](../../config/.env.lmstudio) | LM Studio — same common block; LM Studio LLM vars only |
| [`config/.env.openai`](../../config/.env.openai) | OpenAI cloud — same common block; set `OPENAI_API_KEY` |
| [`config/.env.example`](../../config/.env.example) | Superset reference — Ollama active; LM Studio / OpenAI commented |
| `config/.env` | Active file the app reads (copy a preset; do not commit secrets) |

Each provider preset shares the same common app settings; only the LLM section differs (no `LM_STUDIO_*` in `.env.ollama`, no `OLLAMA_*` in `.env.openai`, etc.).

---

## Ollama — one model

You do **not** need five models. One Ollama model powers import, classification, and chat.

### 1. Install Ollama

1. Download [Ollama](https://ollama.com) for your OS and install it.
2. Open the Ollama app so the local server is running (`http://127.0.0.1:11434`).

### 2. Pull one model

| Your machine | Command | Notes |
|--------------|---------|--------|
| **Default (installer + `.env.ollama`)** | `ollama pull qwen2.5:7b` | Works on most laptops |
| **16GB+ RAM** (optional upgrade) | `ollama pull qwen2.5:14b` | Better quality; set all three model vars in `.env` |

### 3. Copy the Ollama preset

```bash
cp config/.env.ollama config/.env
```

That preset sets `LLM_PROVIDER=ollama` and uses **`qwen2.5:7b`** for pipeline and chat. Learning Agent and classification audit stay **off** (uncomment / set to `1` when you want them).

If you pulled `qwen2.5:14b`, edit `config/.env`:

```env
OLLAMA_MODEL=qwen2.5:14b
PIPELINE_MODEL=qwen2.5:14b
CHAT_MODEL=qwen2.5:14b
```

### 4. Verify

```bash
curl http://127.0.0.1:11434/v1/models
```

You should see your pulled model in the JSON. Then start the app (`./start.sh`) and open http://127.0.0.1:8000.

### 5. First import

Upload [`samples/sample_transactions.csv`](../../samples/sample_transactions.csv) from the Import sidebar, or use your bank CSV (needs Date, Amount, Category, and a description column).

---

## OpenAI cloud

```bash
cp config/.env.openai config/.env
```

Set `OPENAI_API_KEY` in that file (the macOS installer can prompt for it). Default model is `gpt-4o-mini` (OpenAI has no local 7B equivalent).

---

## Expert: which model?

| Model | Verdict |
|-------|---------|
| **qwen2.5:7b** (Ollama) | Default for local / macOS installer. |
| **qwen2.5:14b** (Ollama) | Optional upgrade on 16GB+ RAM. |
| **qwen2.5:7b-instruct** | Alternate 7B tag if the base pull name differs. |
| **qwen2.5-14b-instruct-mlx** (LM Studio) | Best speed/quality on Apple Silicon via LM Studio. |
| **qwen2.5-coder-32b-instruct** | Optional stronger model for audit / learning — **not required** for day-to-day use. |
| **gpt-4o-mini** (OpenAI) | Default cloud model in `.env.openai`. |

---

## Expert: role-specific models

Optional. If unset, each role falls back to `OLLAMA_MODEL` / `LM_STUDIO_MODEL` / `CHAT_MODEL` as documented in [config/.env.example](../../config/.env.example).

Precedence: role var → `LLM_MODEL` → provider `*_MODEL`.

| Variable | Role | Needed by default? |
|----------|------|---------------------|
| `PIPELINE_MODEL` | Import descriptions + classification | No — same as base |
| `CHAT_MODEL` | Workspace chat, review suggest, edit insights | No — same as base |
| `CLASSIFICATION_AUDIT_MODEL` | Sampled audit spot-check | No — feature off in presets |
| `LEARNING_AGENT_MODEL` | Decision Analyst scheduler | No — feature off in presets |

Example: keep routine work on 14B, use a larger model only when you enable Learning Agent:

```env
PIPELINE_MODEL=qwen2.5:14b
CHAT_MODEL=qwen2.5:14b
LEARNING_AGENT_ENABLED=1
LEARNING_AGENT_MODEL=qwen2.5-coder:32b
```

---

## Expert: LM Studio

The pipeline uses LM Studio’s **OpenAI-compatible API**.

1. Start LM Studio and load a model.
2. Enable **Serve on Local Network** (e.g. port `1234`).
3. Copy the preset:

```bash
cp config/.env.lmstudio config/.env
```

| Setting | Example |
|---------|---------|
| `LLM_PROVIDER` | `lmstudio` |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` |
| `LM_STUDIO_MODEL` | `qwen2.5-14b-instruct-mlx` |

---

## Expert: performance tips (Apple Silicon)

| Lever | Recommendation |
|-------|----------------|
| **Model** | `qwen2.5-14b-instruct-mlx` (LM Studio) or `qwen2.5:7b` (Ollama) for routine months |
| **Description cache** | Second runs are much faster once `description_lookup` in SQLite is warm |
| **Batch size** | Override with `LOCAL_BATCH_SIZE` / `DESCRIPTION_BATCH_SIZE` in `.env` on large-RAM machines |

Restart `./start.sh` (or uvicorn) after changing LLM settings.
