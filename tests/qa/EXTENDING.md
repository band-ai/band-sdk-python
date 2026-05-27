# Adding a New Adapter to the QA Harness

## Steps

### 1. Create adapter directory

```
tests/qa/adapters/<name>/
├── config.yaml
└── agent_config.yaml.example
```

### 2. Write config.yaml

Follow this template (see `langgraph/config.yaml` or `google_adk/config.yaml` for real examples):

```yaml
adapter: <name>
llm_model: <model-id>
env_file: examples/<name>/.env     # path relative to repo root

# Core scenarios (A-C) — run per example file
examples:
  working_dir: examples/<name>     # cwd when spawning the example process
  config_file: examples/<name>/agent_config.yaml
  items:
    01_basic_agent:
      file: 01_basic_agent.py
      config_key: <config_key_in_agent_config_yaml>

# Scenario D: Multi-Participant (optional)
# Specify which two agents to pair in the same room
multi_participant:
  agent_1:
    adapter: <name>
    example: 01_basic_agent
    config_key: <key>
  agent_2:
    adapter: <other_adapter>
    example: 01_simple_agent
    config_key: <key>

# Expanded scenarios E-I (optional — requires purpose-built agent scripts)
expanded:
  config_file: agent_config.yaml   # relative to adapter dir
  scenarios:
    E:
      script: agents/memory_agent.py
      config_key: <key>_memory_test
    G:
      script: agents/full_agent.py
      config_key: <key>_full_test
    I:
      script: agents/full_agent.py
      config_key: <key>_full_test
  contacts:
    requester_config_key: requester_test
    target_handle: "admin1/<handle>"
```

### 3. Write agent_config.yaml.example

List every `config_key` referenced in `config.yaml`:

```yaml
# Copy to agent_config.yaml and fill in credentials.
<config_key>:
  agent_id: ""
  api_key: ""
```

### 4. Create expanded agent scripts (optional)

If adding expanded scenarios (E-I), create scripts under `tests/qa/adapters/<name>/agents/`:

| Script | For Scenarios | Features Required |
|--------|---------------|-------------------|
| `memory_agent.py` | E | Memory tools enabled |
| `contacts_disabled.py` | F1 | Contact strategy DISABLED |
| `contacts_callback.py` | F2 | Contact strategy CALLBACK |
| `contacts_hub.py` | F3 | Contact strategy HUB_ROOM |
| `full_agent.py` | G, I | Execution reporting enabled |

Each script is ~50 lines: load env, create adapter with features, create agent from config, run. Copy from an existing adapter and swap the adapter class + model.

### 5. Register agents and fill credentials

```bash
# Register each agent on the platform
curl -X POST $THENVOI_REST_URL/api/v1/me/agents/register \
  -H "X-API-Key: $THENVOI_API_KEY_USER" \
  -H "Content-Type: application/json" \
  -d '{"agent": {"name": "QA-<name>-basic", "description": "QA test agent"}}'

# Save agent_id + api_key into agent_config.yaml
cp tests/qa/adapters/<name>/agent_config.yaml.example \
   tests/qa/adapters/<name>/agent_config.yaml
```

### 6. Run and verify

```bash
# Core only
python tests/qa/run.py --adapter <name>

# Everything
python tests/qa/run.py --adapter <name> --all
```

## Running Against a PR

```bash
git checkout <pr-branch>
git cherry-pick feat/qa-test-harness   # if harness not on branch yet
python tests/qa/run.py --adapter <name> --all
```
