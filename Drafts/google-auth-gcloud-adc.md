# Gemini/ADK adapters: support gcloud Application Default Credentials

## Problem

The Gemini and Google ADK adapters only document and validate API key auth (`GOOGLE_API_KEY` / `GEMINI_API_KEY`). When initialization fails, the error message only suggests setting an API key — it doesn't mention the gcloud ADC path.

Developers using Google Cloud (especially on Vertex AI) authenticate via `gcloud auth application-default login` rather than managing raw API keys. The underlying `google-genai` SDK already supports this — we just don't surface it.

## Scope

**Adapter error message** (`src/thenvoi/adapters/gemini.py`):
- Update the `ValueError` on client init failure to mention both auth paths: API key and Vertex AI mode (`GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT`)

**Example docs** (4 files):
- `examples/gemini/01_basic_agent.py` — update "Requires" docstring
- `examples/google_adk/01_basic_agent.py` — update "Requires" docstring
- `examples/google_adk/02_custom_instructions.py` — same
- `examples/google_adk/03_custom_tools.py` — same

**SDK docs** (`AGENTS.md`):
- Add `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT` to environment variables section

## Out of scope

- QA harness `.env.example` and `setup_agents.py` gcloud detection — those ship with the QA harness branch
- Actual code changes to the adapter's auth flow (the `google-genai` SDK already handles ADC natively; this is a docs/DX issue)

## Acceptance criteria

- [ ] `GeminiAdapter` init error mentions both API key and Vertex AI auth
- [ ] All Gemini and ADK example docstrings show both auth methods
- [ ] `AGENTS.md` env vars section includes the 3 Google env vars

## Files to change

| File | Change |
|------|--------|
| `src/thenvoi/adapters/gemini.py` | Error message text |
| `examples/gemini/01_basic_agent.py` | Docstring |
| `examples/google_adk/01_basic_agent.py` | Docstring |
| `examples/google_adk/02_custom_instructions.py` | Docstring |
| `examples/google_adk/03_custom_tools.py` | Docstring |
| `AGENTS.md` | Env vars table |
