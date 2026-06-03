#!/usr/bin/env python3
"""
Register QA agents on the platform and generate credential files.

Reads each adapter's config.yaml to discover required config_keys,
registers an agent for each unique key, and writes:
  - tests/qa/.env
  - tests/qa/adapters/<adapter>/agent_config.yaml
  - examples/<adapter>/agent_config.yaml  (for core scenarios)

Usage:
    python tests/qa/setup_agents.py                # all adapters
    python tests/qa/setup_agents.py --adapters langgraph,anthropic
    python tests/qa/setup_agents.py --dry-run      # show what would be registered
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import shutil
import subprocess

import httpx
import yaml

QA_DIR = Path(__file__).parent
REPO_ROOT = QA_DIR.parent.parent

ADAPTER_PREFIX_MAP = {
    "langgraph": "LG",
    "anthropic": "Anth",
    "crewai": "Crew",
    "gemini": "Gem",
    "google_adk": "ADK",
    "pydantic_ai": "PAI",
    "claude_sdk": "ClaudeSDK",
    "letta": "Letta",
    "parlant": "Parlant",
}


def discover_adapters() -> list[str]:
    adapters_dir = QA_DIR / "adapters"
    return sorted(
        d.name
        for d in adapters_dir.iterdir()
        if d.is_dir() and (d / "config.yaml").exists()
    )


def collect_agent_specs(
    adapter_name: str, adapter_cfg: dict
) -> list[dict]:
    """Collect all agent registrations needed for an adapter."""
    specs: list[dict] = []
    seen_keys: set[str] = set()
    prefix = ADAPTER_PREFIX_MAP.get(adapter_name, adapter_name)

    # Core examples
    examples_cfg = adapter_cfg.get("examples", {})
    for item_cfg in examples_cfg.get("items", {}).values():
        key = item_cfg.get("config_key", "")
        if key and key not in seen_keys:
            seen_keys.add(key)
            specs.append({
                "config_key": key,
                "name": f"QA-{prefix}-{key}",
                "description": f"QA test agent ({adapter_name}/{key})",
                "target": "examples",
            })

    # Expanded scenarios
    expanded_cfg = adapter_cfg.get("expanded", {})
    for scenario_id, scenario_cfg in expanded_cfg.get("scenarios", {}).items():
        key = scenario_cfg.get("config_key", "")
        if key and key not in seen_keys:
            seen_keys.add(key)
            specs.append({
                "config_key": key,
                "name": f"QA-{prefix}-{key}",
                "description": f"QA test agent ({adapter_name}/{key}, scenario {scenario_id})",
                "target": "expanded",
            })

    # Contact requester
    contacts_cfg = expanded_cfg.get("contacts", {})
    req_key = contacts_cfg.get("requester_config_key", "")
    if req_key and req_key not in seen_keys:
        seen_keys.add(req_key)
        specs.append({
            "config_key": req_key,
            "name": f"QA-{prefix}-requester",
            "description": f"QA contact requester ({adapter_name})",
            "target": "expanded",
        })

    # Multi-participant agent_2
    mp_cfg = adapter_cfg.get("multi_participant", {})
    for agent_key in ("agent_1", "agent_2"):
        agent_mp = mp_cfg.get(agent_key, {})
        key = agent_mp.get("config_key", "")
        if key and key not in seen_keys:
            seen_keys.add(key)
            a2_adapter = agent_mp.get("adapter", adapter_name)
            a2_prefix = ADAPTER_PREFIX_MAP.get(a2_adapter, a2_adapter)
            specs.append({
                "config_key": key,
                "name": f"QA-{a2_prefix}-{key}",
                "description": f"QA test agent ({a2_adapter}/{key}, multi-participant)",
                "target": "examples",
            })

    return specs


def register_agent(
    client: httpx.Client, name: str, description: str
) -> dict:
    """Register an agent and return {agent_id, api_key}."""
    resp = client.post(
        "/api/v1/me/agents/register",
        json={"agent": {"name": name, "description": description}},
    )
    if resp.status_code == 422:
        error = resp.json().get("error", {})
        details = error.get("details", {})
        if "has already been taken" in str(details.get("name", "")):
            raise ValueError(f"Agent name '{name}' already taken")
    resp.raise_for_status()
    data = resp.json()["data"]
    return {
        "agent_id": data["agent"]["id"],
        "api_key": data["credentials"]["api_key"],
    }


def get_handle_from_example(adapter_dir: Path, config_key: str) -> str | None:
    """Read handle from agent_config.yaml.example if present."""
    example_path = adapter_dir / "agent_config.yaml.example"
    if not example_path.exists():
        return None
    with open(example_path) as f:
        data = yaml.safe_load(f) or {}
    entry = data.get(config_key, {})
    return entry.get("handle")


def scan_example_config_keys(examples_dir: Path) -> dict[str, str]:
    """Scan example .py files for Agent.from_config("key") calls.
    Returns {config_key: filename}."""
    import re
    keys: dict[str, str] = {}
    if not examples_dir.is_dir():
        return keys
    for py_file in sorted(examples_dir.glob("*.py")):
        text = py_file.read_text()
        for m in re.finditer(r'from_config\(\s*["\']([^"\']+)["\']', text):
            keys[m.group(1)] = py_file.name
    return keys


def detect_gcloud_adc() -> str | None:
    """Detect gcloud Application Default Credentials and return the project ID.

    Returns the project ID if gcloud is installed, authenticated, and ADC is
    available. Returns None otherwise.
    """
    gcloud = shutil.which("gcloud")
    if not gcloud:
        return None
    try:
        result = subprocess.run(
            [gcloud, "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        result = subprocess.run(
            [gcloud, "config", "get-value", "project"],
            capture_output=True, text=True, timeout=10,
        )
        project = result.stdout.strip()
        if result.returncode == 0 and project:
            return project
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def write_qa_env(rest_url: str, ws_url: str, user_key: str, llm_keys: dict) -> None:
    """Write tests/qa/.env with platform and LLM credentials."""
    env_path = QA_DIR / ".env"
    lines = [
        f"BAND_REST_URL={rest_url}",
        f"BAND_WS_URL={ws_url}",
        f"BAND_API_KEY_USER={user_key}",
        "",
    ]
    for k, v in sorted(llm_keys.items()):
        if v:
            lines.append(f"{k}={v}")
    lines.append("")
    env_path.write_text("\n".join(lines))
    print(f"  Wrote {env_path}")


def write_agent_config(path: Path, entries: dict) -> None:
    """Write an agent_config.yaml file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(entries, f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adapters",
        help="Comma-separated adapter names (default: all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be registered without making API calls",
    )
    args = parser.parse_args()

    # Load credentials from .env (handle worktrees)
    env_vars: dict[str, str] = {}
    main_env = None
    for candidate in [REPO_ROOT / ".env", Path.home() / "band" / "thenvoi-sdk-python" / ".env"]:
        if candidate.exists():
            main_env = candidate
            break
    if not main_env:
        print(f"ERROR: .env not found in {REPO_ROOT} or ~/band/thenvoi-sdk-python/")
        sys.exit(1)

    with open(main_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

    # Also try to load user key from known locations
    user_key = env_vars.get("BAND_API_KEY_USER", "")
    main_repo = Path.home() / "band" / "thenvoi-sdk-python"
    if not user_key:
        for candidate in [
            REPO_ROOT / ".env.userkey",
            main_repo / ".env.userkey",
            *main_repo.glob(".claude/worktrees/*/.env.userkey"),
        ]:
            if candidate.exists():
                with open(candidate) as f:
                    for line in f:
                        if line.startswith("BAND_API_KEY_USER="):
                            user_key = line.strip().split("=", 1)[1]
                            break
            if user_key:
                break

    if not user_key:
        print("ERROR: BAND_API_KEY_USER not found in .env or .env.userkey")
        sys.exit(1)

    rest_url = env_vars.get("BAND_REST_URL", "https://app.band.ai")
    ws_url = env_vars.get("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")

    llm_keys = {
        k: env_vars.get(k, "")
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                   "LETTA_API_KEY", "LETTA_BASE_URL", "MCP_SERVER_URL")
    }

    # Auto-detect gcloud ADC for Gemini/Google ADK when no API key is set
    if not llm_keys.get("GOOGLE_API_KEY"):
        gcloud_project = detect_gcloud_adc()
        if gcloud_project:
            llm_keys["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
            llm_keys["GOOGLE_CLOUD_PROJECT"] = gcloud_project
            print(f"  [gcloud] Using Vertex AI with project: {gcloud_project}")

    all_adapters = discover_adapters()
    selected = (
        [a.strip() for a in args.adapters.split(",")]
        if args.adapters else all_adapters
    )

    # Collect all needed registrations per adapter
    adapter_specs: dict[str, list[dict]] = {}
    for adapter_name in selected:
        cfg_path = QA_DIR / "adapters" / adapter_name / "config.yaml"
        with open(cfg_path) as f:
            adapter_cfg = yaml.safe_load(f)
        adapter_specs[adapter_name] = collect_agent_specs(adapter_name, adapter_cfg)

    # Deduplicate: same agent name across adapters shares one registration
    unique_agents: dict[str, dict] = {}  # name → first spec
    for adapter_name, specs in adapter_specs.items():
        for spec in specs:
            if spec["name"] not in unique_agents:
                unique_agents[spec["name"]] = spec

    # Show plan
    print(f"\nUnique agents to register: {len(unique_agents)}")
    for adapter_name, specs in adapter_specs.items():
        print(f"\n  {adapter_name}:")
        for s in specs:
            print(f"    {s['config_key']:25s} → {s['name']}")

    if args.dry_run:
        print("\n[DRY RUN] No changes made.")
        return

    print(f"\nRegistering {len(unique_agents)} agents on {rest_url}...")

    client = httpx.Client(
        base_url=rest_url,
        headers={"X-API-Key": user_key, "Content-Type": "application/json"},
        timeout=30.0,
    )

    # Register unique agents: name → {agent_id, api_key}
    name_to_creds: dict[str, dict] = {}

    for name, spec in unique_agents.items():
        desc = spec["description"]
        try:
            creds = register_agent(client, name, desc)
            print(f"  [+] {name} → {creds['agent_id'][:8]}...")
            name_to_creds[name] = creds
        except ValueError as e:
            print(f"  [!] {name}: {e}")
            import time
            suffix = str(int(time.time()))[-4:]
            alt_name = f"{name}-{suffix}"
            try:
                creds = register_agent(client, alt_name, desc)
                print(f"  [+] {alt_name} → {creds['agent_id'][:8]}...")
                name_to_creds[name] = creds
            except Exception as e2:
                print(f"  [X] {alt_name}: {e2}")
        except Exception as e:
            print(f"  [X] {name}: {e}")

    # Map back: (adapter_name, config_key) → creds
    registered: dict[tuple[str, str], dict] = {}
    for adapter_name, specs in adapter_specs.items():
        for spec in specs:
            creds = name_to_creds.get(spec["name"])
            if creds:
                registered[(adapter_name, spec["config_key"])] = creds

    client.close()

    # Write .env
    print("\nWriting credential files...")
    write_qa_env(rest_url, ws_url, user_key, llm_keys)

    # Write per-adapter agent_config.yaml files
    for adapter_name in selected:
        cfg_path = QA_DIR / "adapters" / adapter_name / "config.yaml"
        with open(cfg_path) as f:
            adapter_cfg = yaml.safe_load(f)

        adapter_dir = QA_DIR / "adapters" / adapter_name

        # Collect entries for QA adapter config (expanded scenarios)
        qa_entries: dict[str, dict] = {}
        # Collect entries for examples config (core scenarios)
        examples_entries: dict[str, dict] = {}

        for spec in adapter_specs[adapter_name]:
            key = spec["config_key"]
            creds = registered.get((adapter_name, key))
            if not creds:
                continue

            entry = {
                "agent_id": creds["agent_id"],
                "api_key": creds["api_key"],
            }

            handle = get_handle_from_example(adapter_dir, key)
            if handle:
                entry["handle"] = handle

            if spec["target"] == "examples":
                examples_entries[key] = entry
            qa_entries[key] = entry

        # Write QA adapter config
        if qa_entries:
            write_agent_config(adapter_dir / "agent_config.yaml", qa_entries)

        # Write examples config
        examples_cfg = adapter_cfg.get("examples", {})
        config_file = examples_cfg.get("config_file", "")
        if config_file and examples_entries:
            examples_path = REPO_ROOT / config_file
            # Merge with existing if present
            existing = {}
            if examples_path.exists():
                with open(examples_path) as f:
                    existing = yaml.safe_load(f) or {}
            existing.update(examples_entries)

            # Scan example scripts for additional config keys (aliases)
            # e.g., 02_custom_tools.py uses "custom_tools_agent" but QA
            # config maps it to "simple_agent" — add alias entries
            examples_dir = REPO_ROOT / examples_cfg.get("working_dir", "")
            script_keys = scan_example_config_keys(examples_dir)
            primary_creds = next(iter(examples_entries.values()), None)
            if primary_creds:
                for key in script_keys:
                    if key not in existing:
                        existing[key] = {
                            "agent_id": primary_creds["agent_id"],
                            "api_key": primary_creds["api_key"],
                        }

            write_agent_config(examples_path, existing)

    print(f"\nDone. Registered {len(registered)} agents.")
    print("Run the harness with: python tests/qa/run.py --all-adapters")


if __name__ == "__main__":
    main()
