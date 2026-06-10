# Gemini / Google ADK: Dual Authentication Support

## Problem

The Gemini and Google ADK adapters currently document `GOOGLE_API_KEY` / `GEMINI_API_KEY` as the only authentication method. Developers using gcloud CLI (common in Google Cloud environments, local dev setups, and CI/CD with service accounts) have no documented path.

The google-genai SDK supports two backends:
| Backend | Auth method | Env vars |
|---------|------------|----------|
| **Gemini Developer API** (default) | API key only | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| **Vertex AI** | Application Default Credentials (ADC) | `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT` |

ADC is resolved from (in order): `GOOGLE_APPLICATION_CREDENTIALS` file, gcloud CLI (`gcloud auth application-default login`), GCE metadata server, or Workload Identity.

## Current State

| Component | API key | gcloud/ADC | Notes |
|-----------|---------|-----------|-------|
| `GeminiAdapter` | Works | Works (with env vars) | `genai.Client(api_key=None)` falls through to env lookup |
| `GoogleADKAdapter` | Works | Works (with env vars) | ADK creates its own `genai.Client()` internally |
| Example docstrings | Documented | Not documented | Only mention API key |
| QA harness `setup_agents.py` | Passes through | Auto-detects gcloud | New: `detect_gcloud_adc()` |
| SDK README / docs | API key only | Not mentioned | |

## What Works Today (no adapter code changes needed)

Both adapters already work with Vertex AI / gcloud — the google-genai SDK reads `GOOGLE_GENAI_USE_VERTEXAI` and `GOOGLE_CLOUD_PROJECT` from the environment automatically. The adapters don't hardcode API-key-only mode.

The only gap is **documentation and developer ergonomics**.

## Proposed Changes

### P0: Documentation (done in this branch)

- [x] Example file docstrings updated to show both auth methods
- [x] `setup_agents.py` auto-detects gcloud ADC and writes Vertex AI env vars
- [ ] SDK README: add "Authentication" section covering both paths
- [ ] CLAUDE.md: add `GOOGLE_GENAI_USE_VERTEXAI` and `GOOGLE_CLOUD_PROJECT` to env vars table

### P1: Better error messages

The GeminiAdapter's `_ensure_client` currently shows:
> "Gemini client initialization failed. Provide GEMINI_API_KEY or pass api_key explicitly."

Should also mention the Vertex AI / gcloud path:
> "Gemini client initialization failed. Either set GOOGLE_API_KEY / GEMINI_API_KEY, or enable Vertex AI mode (GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_CLOUD_PROJECT)."

**Files:** `src/thenvoi/adapters/gemini.py` line ~338

### P2: Explicit `vertexai` parameter on adapters

Add an optional `vertexai: bool | None = None` parameter to `GeminiAdapter.__init__()`:
- `None` (default): let the SDK auto-detect from env vars (current behavior)
- `True`: force Vertex AI mode, pass `vertexai=True` to `genai.Client()`
- `False`: force Gemini Developer API mode

This gives developers explicit control without relying on env vars:

```python
# Vertex AI with explicit project
adapter = GeminiAdapter(
    model="gemini-2.5-flash",
    vertexai=True,
    project="my-project-id",
)
```

**Files:** `src/thenvoi/adapters/gemini.py` (GeminiAdapter), `src/thenvoi/adapters/google_adk.py` (GoogleADKAdapter)

**Complexity:** Low for GeminiAdapter (we create the `genai.Client` ourselves). Medium for GoogleADKAdapter (ADK creates its own client — need to check if ADK's Agent constructor supports vertexai passthrough or if we need to set env vars before creating the runner).

### P3: `google_project` in adapter config YAML

Allow setting the Google Cloud project in `agent_config.yaml` or adapter config:

```yaml
gemini_agent:
  agent_id: "..."
  api_key: "..."
  google_project: "my-project-id"  # optional, for Vertex AI
```

**Complexity:** Medium — requires `config/loader.py` changes and adapter constructor wiring.

## Trade-offs

| Approach | Pros | Cons |
|----------|------|------|
| Env-var only (current + docs) | Zero code changes, standard Google pattern | Developers must know to set 2 env vars |
| Explicit adapter params (P2) | Clear in code, IDE-discoverable | More constructor params, ADK passthrough may be fragile |
| YAML config (P3) | Centralized config | Yet another config surface, only useful for Vertex AI |

## Recommendation

**Ship P0 + P1 now** (docs + error message). Defer P2/P3 unless users report friction — the env-var approach follows Google's standard pattern and works across both adapters without SDK changes.

## Testing

- QA harness: gemini and google_adk adapters now test with Vertex AI + gcloud ADC (auto-detected by `setup_agents.py`)
- Unit tests: add a test for `detect_gcloud_adc()` returning None when gcloud is absent
- The Gemini Developer API path continues to work for users with API keys — `GOOGLE_API_KEY` takes precedence over `GOOGLE_GENAI_USE_VERTEXAI` when both are set
