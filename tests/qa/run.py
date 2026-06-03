#!/usr/bin/env python3
"""
Unified QA runner for any adapter.

Usage:
    python tests/qa/run.py --adapter langgraph          # Core scenarios (A-C)
    python tests/qa/run.py --adapter langgraph --all    # Everything
    python tests/qa/run.py --all-adapters               # Full sweep, all adapters

See tests/qa/RUN.md for setup and full CLI reference.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import os
import sys
from pathlib import Path

import yaml

QA_DIR = Path(__file__).parent
REPO_ROOT = QA_DIR.parent.parent

sys.path.insert(0, str(QA_DIR))

from harness.api_client import AgentInfo, PlatformClient  # noqa: E402
from harness.agent_runner import AgentRunner  # noqa: E402
from harness.contact_requester import ContactRequester  # noqa: E402
from harness.reporter import ExampleReport, Reporter  # noqa: E402
from harness.scenario import ScenarioResult, Status  # noqa: E402
from scenarios import CORE_SCENARIOS  # noqa: E402
from scenarios.d_multi_participant import MultiParticipantScenario  # noqa: E402
from scenarios.e_memory_tools import MemoryToolsScenario  # noqa: E402
from scenarios.f_contact_disabled import ContactDisabledScenario  # noqa: E402
from scenarios.f_contact_callback import ContactCallbackScenario  # noqa: E402
from scenarios.f_contact_hub import ContactHubScenario  # noqa: E402
from scenarios.g_execution_emit import ExecutionEmitScenario  # noqa: E402
from scenarios.i_concurrent_rooms import ConcurrentRoomsScenario  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("qa")

EXPANDED_SCENARIO_CLASSES: dict[str, type] = {
    "E": MemoryToolsScenario,
    "F1": ContactDisabledScenario,
    "F2": ContactCallbackScenario,
    "F3": ContactHubScenario,
    "G": ExecutionEmitScenario,
    "I": ConcurrentRoomsScenario,
}

SCENARIO_LABELS: dict[str, str] = {
    "D": "Multi-participant",
    "E": "Memory tools",
    "F1": "Contact DISABLED",
    "F2": "Contact CALLBACK",
    "F3": "Contact HUB_ROOM",
    "G": "Execution emit",
    "I": "Concurrent rooms",
}


def load_adapter_config(adapter_name: str) -> dict:
    config_path = QA_DIR / "adapters" / adapter_name / "config.yaml"
    if not config_path.exists():
        available = [
            d.name for d in (QA_DIR / "adapters").iterdir()
            if d.is_dir() and (d / "config.yaml").exists()
        ]
        raise ValueError(
            f"No config for adapter '{adapter_name}'. "
            f"Available: {', '.join(sorted(available))}"
        )
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_credentials(config_file: str, adapter_dir: Path) -> dict:
    if os.path.isabs(config_file):
        path = Path(config_file)
    else:
        path = adapter_dir / config_file
    if not path.exists():
        path = REPO_ROOT / config_file
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def creds_ok(cfg: dict, key: str) -> bool:
    entry = cfg.get(key, {})
    return bool(entry.get("agent_id")) and bool(entry.get("api_key"))


def resolve_venv(adapter_cfg: dict) -> str | None:
    venv_name = adapter_cfg.get("venv")
    if not venv_name:
        return None
    venv_path = REPO_ROOT / venv_name
    if not (venv_path / "bin" / "python").exists():
        logger.warning(
            "venv '%s' not found at %s — falling back to uv run. "
            "Create it with: UV_PROJECT_ENVIRONMENT=%s uv sync --extra <extra>",
            venv_name, venv_path, venv_name,
        )
        return None
    return str(venv_path)


# ---------------------------------------------------------------------------
# Core scenarios (A-C per example)
# ---------------------------------------------------------------------------

async def run_examples(
    adapter_cfg: dict,
    client: PlatformClient,
    example_filter: list[str] | None,
) -> list[ExampleReport]:
    examples_cfg = adapter_cfg.get("examples", {})
    working_dir = str(REPO_ROOT / examples_cfg["working_dir"])
    config_file = REPO_ROOT / examples_cfg["config_file"]
    items = examples_cfg.get("items", {})

    with open(config_file) as f:
        agent_configs = yaml.safe_load(f)

    if example_filter:
        items = {
            k: v for k, v in items.items()
            if any(k.startswith(p) for p in example_filter)
        }

    reports: list[ExampleReport] = []

    for example_key, example_cfg in items.items():
        report = ExampleReport(
            example_name=example_key,
            framework=adapter_cfg["adapter"],
            agent_id=example_cfg["config_key"],
            llm_model=adapter_cfg.get("llm_model", ""),
        )

        venv = resolve_venv(adapter_cfg)
        runner = AgentRunner(example_cfg["file"], working_dir, venv=venv)

        try:
            logger.info("=" * 60)
            logger.info("Starting QA for: %s", example_key)
            logger.info("=" * 60)

            started = await runner.start(timeout=180.0)
            report.startup_ok = started
            report.startup_logs = runner.get_logs()

            if not started:
                report.errors.append(
                    f"Agent failed to start: {runner.get_stderr()[:500]}"
                )
                reports.append(report)
                continue

            creds = agent_configs.get(example_cfg["config_key"], {})
            actual_agent_id = creds["agent_id"]
            report.agent_id = actual_agent_id

            room_id = await client.create_room()
            agent_info = await client.add_participant(room_id, actual_agent_id)
            await asyncio.sleep(3.0)

            shared: dict = {}
            for scenario_cls in CORE_SCENARIOS:
                scenario = scenario_cls()
                logger.info("--- Running scenario: %s ---", scenario.name)
                try:
                    sr = await scenario.run(client, runner, agent_info, room_id, shared=shared)
                    sr.room_id = room_id
                    report.scenario_results.append(sr)
                    logger.info("Scenario %s: %s", scenario.name, sr.status.value)
                except Exception as e:
                    sr = ScenarioResult(
                        name=scenario.name,
                        description=scenario.description,
                        status=Status.FAIL,
                        error=str(e),
                        room_id=room_id,
                    )
                    report.scenario_results.append(sr)
                    logger.error("Scenario %s failed: %s", scenario.name, e)

        finally:
            await runner.stop()

        reports.append(report)

    return reports


# ---------------------------------------------------------------------------
# Expanded scenarios (E-I)
# ---------------------------------------------------------------------------

async def run_expanded(
    adapter_cfg: dict,
    client: PlatformClient,
    selected_scenarios: set[str],
) -> ExampleReport:
    adapter_name = adapter_cfg["adapter"]
    adapter_dir = QA_DIR / "adapters" / adapter_name
    expanded_cfg = adapter_cfg.get("expanded", {})
    scenario_defs = expanded_cfg.get("scenarios", {})
    contacts_cfg = expanded_cfg.get("contacts", {})

    creds = load_credentials(
        expanded_cfg.get("config_file", "agent_config.yaml"), adapter_dir
    )

    report = ExampleReport(
        example_name=f"{adapter_name}_expanded",
        framework=adapter_name,
        agent_id="(multiple)",
        llm_model=adapter_cfg.get("llm_model", ""),
        startup_ok=True,
    )

    requester: ContactRequester | None = None
    contacts_client: PlatformClient | None = None
    target_handle: str = ""
    need_contacts = selected_scenarios & {"F1", "F2", "F3"}

    if need_contacts and contacts_cfg:
        req_key = contacts_cfg.get("requester_config_key", "")
        if creds_ok(creds, req_key):
            requester = ContactRequester(creds[req_key]["api_key"], client.base_url)
            target_handle = contacts_cfg.get("target_handle", "")
            user_key_env = contacts_cfg.get("user_api_key_env", "")
            if user_key_env:
                alt_key = os.environ.get(user_key_env, "")
                if alt_key:
                    contacts_client = PlatformClient(client.base_url, alt_key)
                    logger.info("Using %s for contact scenario rooms", user_key_env)
                else:
                    logger.warning("%s not set — falling back to default user key", user_key_env)
        else:
            logger.warning(
                "%s credentials missing — skipping contact scenarios",
                req_key,
            )
            for sid in sorted(need_contacts):
                report.scenario_results.append(
                    ScenarioResult(
                        name=f"{sid}: {SCENARIO_LABELS.get(sid, '')}",
                        description="",
                        status=Status.SKIP,
                        error=f"{req_key} credentials not configured",
                    )
                )
            selected_scenarios -= need_contacts

    try:
        for scenario_id in sorted(selected_scenarios):
            sdef = scenario_defs.get(scenario_id)
            if not sdef:
                logger.warning("No definition for scenario %s — skipping", scenario_id)
                continue

            config_key = sdef["config_key"]
            if not creds_ok(creds, config_key):
                logger.warning(
                    "%s credentials missing — skipping %s", config_key, scenario_id
                )
                continue

            script = sdef["script"]
            agent_id = creds[config_key]["agent_id"]

            logger.info("=" * 60)
            logger.info("Running %s: %s", scenario_id, SCENARIO_LABELS.get(scenario_id, ""))
            logger.info("=" * 60)

            venv = resolve_venv(adapter_cfg)
            runner = AgentRunner(script, str(adapter_dir), venv=venv)
            scenario_room_id: str | None = None
            try:
                ok = await runner.start(timeout=180.0)
                if not ok:
                    report.scenario_results.append(
                        ScenarioResult(
                            name=f"{scenario_id}: {SCENARIO_LABELS.get(scenario_id, '')}",
                            description="",
                            status=Status.FAIL,
                            error=f"Agent {script} failed to start",
                        )
                    )
                    continue

                is_contact = scenario_id in ("F1", "F2", "F3")
                active_client = contacts_client if (is_contact and contacts_client) else client

                scenario_room_id = await active_client.create_room()
                agent_info = await active_client.add_participant(scenario_room_id, agent_id)
                await asyncio.sleep(3.0)

                scenario_cls = EXPANDED_SCENARIO_CLASSES[scenario_id]
                if is_contact and requester:
                    scenario = scenario_cls(requester, target_handle)
                else:
                    scenario = scenario_cls()

                sr = await scenario.run(active_client, runner, agent_info, scenario_room_id)
                sr.room_id = scenario_room_id
                report.scenario_results.append(sr)
                logger.info("Scenario %s: %s", scenario_id, sr.status.value)

            except Exception as exc:
                report.scenario_results.append(
                    ScenarioResult(
                        name=f"{scenario_id}: {SCENARIO_LABELS.get(scenario_id, '')}",
                        description="",
                        status=Status.FAIL,
                        error=str(exc),
                        room_id=scenario_room_id,
                    )
                )
            finally:
                await runner.stop()

    finally:
        if requester:
            await requester.close()
        if contacts_client:
            await contacts_client.close()

    return report


# ---------------------------------------------------------------------------
# Scenario D: Multi-Participant (cross-adapter)
# ---------------------------------------------------------------------------

async def run_multi_participant(
    adapter_cfg: dict,
    client: PlatformClient,
) -> ExampleReport | None:
    mp_cfg = adapter_cfg.get("multi_participant")
    if not mp_cfg:
        logger.info("No multi_participant config — skipping scenario D")
        return None

    report = ExampleReport(
        example_name=f"{adapter_cfg['adapter']}_multi_participant",
        framework=adapter_cfg["adapter"],
        agent_id="(multi)",
        llm_model=adapter_cfg.get("llm_model", ""),
        startup_ok=True,
    )

    a1 = mp_cfg["agent_1"]
    a2 = mp_cfg["agent_2"]

    a1_adapter_cfg = load_adapter_config(a1["adapter"])
    a2_adapter_cfg = load_adapter_config(a2["adapter"])

    a1_examples = a1_adapter_cfg.get("examples", {})
    a2_examples = a2_adapter_cfg.get("examples", {})

    a1_config_file = REPO_ROOT / a1_examples["config_file"]
    a2_config_file = REPO_ROOT / a2_examples["config_file"]

    with open(a1_config_file) as f:
        a1_creds = yaml.safe_load(f) or {}
    with open(a2_config_file) as f:
        a2_creds = yaml.safe_load(f) or {}

    a1_entry = a1_creds.get(a1["config_key"], {})
    a2_entry = a2_creds.get(a2["config_key"], {})

    if not a1_entry.get("agent_id") or not a2_entry.get("agent_id"):
        logger.warning("Missing credentials for multi-participant agents — skipping D")
        report.scenario_results.append(
            ScenarioResult(
                name="D: Multi-Participant",
                description="Two agents in one room",
                status=Status.SKIP,
                error="Missing agent credentials",
            )
        )
        return report

    a1_item = a1_examples["items"][a1["example"]]
    a2_item = a2_examples["items"][a2["example"]]

    a1_working_dir = str(REPO_ROOT / a1_examples["working_dir"])
    a2_working_dir = str(REPO_ROOT / a2_examples["working_dir"])

    runner1 = AgentRunner(a1_item["file"], a1_working_dir, venv=resolve_venv(a1_adapter_cfg))
    runner2 = AgentRunner(a2_item["file"], a2_working_dir, venv=resolve_venv(a2_adapter_cfg))

    try:
        logger.info("=" * 60)
        logger.info("Running D: Multi-Participant (%s + %s)", a1["adapter"], a2["adapter"])
        logger.info("=" * 60)

        s1, s2 = await asyncio.gather(
            runner1.start(timeout=180.0),
            runner2.start(timeout=180.0),
        )
        if not s1 or not s2:
            report.scenario_results.append(
                ScenarioResult(
                    name="D: Multi-Participant",
                    description="Two agents in one room",
                    status=Status.FAIL,
                    error=f"Agent startup failed: agent_1={s1}, agent_2={s2}",
                )
            )
            return report

        agent1_info = AgentInfo(agent_id=a1_entry["agent_id"])
        scenario = MultiParticipantScenario(
            second_agent_id=a2_entry["agent_id"],
            second_example_file=a2_item["file"],
            second_runner=runner2,
        )
        sr = await scenario.run(client, runner1, agent1_info, "unused")
        report.scenario_results.append(sr)
        logger.info("Scenario D: %s", sr.status.value)

    except Exception as exc:
        report.scenario_results.append(
            ScenarioResult(
                name="D: Multi-Participant",
                description="Two agents in one room",
                status=Status.FAIL,
                error=str(exc),
            )
        )
    finally:
        await asyncio.gather(runner1.stop(), runner2.stop())

    return report


# ---------------------------------------------------------------------------
# All-adapters sweep
# ---------------------------------------------------------------------------

def discover_adapters() -> list[str]:
    adapters_dir = QA_DIR / "adapters"
    return sorted(
        d.name
        for d in adapters_dir.iterdir()
        if d.is_dir() and (d / "config.yaml").exists()
    )


async def run_all_adapters(args: argparse.Namespace) -> None:
    from dotenv import load_dotenv

    adapters = discover_adapters()
    logger.info("Discovered adapters: %s", ", ".join(adapters))

    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.band.ai")
    user_api_key = os.environ.get("THENVOI_API_KEY_USER", "")
    if not user_api_key:
        raise ValueError("THENVOI_API_KEY_USER environment variable required")

    reports_dir = QA_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)

    cross_adapter_reports: dict[str, list[ExampleReport]] = {}

    for adapter_name in adapters:
        logger.info("\n%s", "=" * 70)
        logger.info("ADAPTER: %s", adapter_name)
        logger.info("=" * 70)

        adapter_cfg = load_adapter_config(adapter_name)

        env_file = adapter_cfg.get("env_file")
        if env_file:
            env_path = REPO_ROOT / env_file
            if env_path.exists():
                load_dotenv(env_path, override=True)

        client = PlatformClient(rest_url, user_api_key)
        adapter_reports: list[ExampleReport] = []

        try:
            # Core scenarios (A-C)
            core_reports = await run_examples(adapter_cfg, client, None)
            adapter_reports.extend(core_reports)
            for report in core_reports:
                path = reports_dir / f"{adapter_name}_{report.example_name}.md"
                path.write_text(Reporter.render(report))

            # Expanded scenarios (E-I)
            if adapter_cfg.get("expanded"):
                selected = set(EXPANDED_SCENARIO_CLASSES.keys())
                report = await run_expanded(adapter_cfg, client, selected)
                adapter_reports.append(report)
                path = reports_dir / f"{adapter_name}_expanded.md"
                path.write_text(Reporter.render(report))

            # Scenario D: Multi-Participant
            d_report = await run_multi_participant(adapter_cfg, client)
            if d_report:
                adapter_reports.append(d_report)
                path = reports_dir / f"{adapter_name}_multi_participant.md"
                path.write_text(Reporter.render(d_report))

        except Exception as exc:
            logger.error("Adapter %s failed: %s", adapter_name, exc)
        finally:
            await client.close()

        cross_adapter_reports[adapter_name] = adapter_reports
        print_summary(adapter_name, adapter_cfg, adapter_reports, reports_dir)

    # Cross-adapter summary
    print(f"\n{'=' * 70}")
    print("CROSS-ADAPTER SUMMARY")
    print(f"{'=' * 70}")

    summary_lines = [
        "# QA Cross-Adapter Summary",
        "",
        f"**Date:** {datetime.date.today().isoformat()}",
        "",
        "| Adapter | Status | Pass | Total |",
        "|---------|--------|------|-------|",
    ]

    for adapter_name, reports in cross_adapter_reports.items():
        total = sum(len(r.scenario_results) for r in reports)
        passed = sum(
            1 for r in reports for s in r.scenario_results if s.status == Status.PASS
        )
        failed = any(r.overall_status == Status.FAIL for r in reports)
        all_pass = all(r.overall_status == Status.PASS for r in reports)
        status = "PASS" if all_pass else ("FAIL" if failed else "PARTIAL")
        symbol = {"PASS": "+", "FAIL": "X", "PARTIAL": "~"}[status]

        summary_lines.append(f"| {adapter_name} | {status} | {passed} | {total} |")
        print(f"  [{symbol}] {adapter_name}: {status} ({passed}/{total})")

    summary_lines.append("")
    summary_path = reports_dir / "cross_adapter_summary.md"
    summary_path.write_text("\n".join(summary_lines))
    print(f"\nReports: {reports_dir}/")


# ---------------------------------------------------------------------------
# Auto-setup
# ---------------------------------------------------------------------------

def _run_setup(adapters: list[str] | None) -> None:
    """Run setup_agents.py to register agents and generate credential files."""
    import subprocess

    cmd = [sys.executable, str(QA_DIR / "setup_agents.py")]
    if adapters:
        cmd.extend(["--adapters", ",".join(adapters)])
    logger.info("Running agent setup: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        raise RuntimeError("Agent setup failed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="QA test runner for Thenvoi SDK adapters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--adapter",
        help="Adapter name (subdirectory of tests/qa/adapters/). Required unless --all-adapters.",
    )
    parser.add_argument(
        "--examples",
        help="Comma-separated example prefixes to run (e.g., 01,03). Default: all.",
    )
    parser.add_argument(
        "--expanded", action="store_true",
        help="Run expanded scenarios (E-I) instead of core examples.",
    )
    parser.add_argument(
        "--scenarios",
        help="Comma-separated expanded scenario IDs (e.g., D,E,G,I). Default: all.",
    )
    parser.add_argument(
        "--all", action="store_true", dest="run_all",
        help="Run both core examples and expanded scenarios.",
    )
    parser.add_argument(
        "--all-adapters", action="store_true", dest="all_adapters",
        help="Run all configured adapters sequentially with full test suite.",
    )
    parser.add_argument(
        "--setup", action="store_true",
        help="Register agents and generate credential files before running. "
             "Requires THENVOI_API_KEY_USER (in .env or environment).",
    )
    args = parser.parse_args()

    if not args.adapter and not args.all_adapters:
        parser.error("--adapter is required unless --all-adapters is specified")

    from dotenv import load_dotenv
    # Load root .env as base, then QA .env overrides
    root_env = REPO_ROOT / ".env"
    if root_env.exists():
        load_dotenv(root_env)
    qa_env = QA_DIR / ".env"
    if qa_env.exists():
        load_dotenv(qa_env, override=True)

    # Auto-setup: register agents and generate config files if needed
    if args.setup:
        _run_setup(
            [args.adapter] if args.adapter else None
        )
        if qa_env.exists():
            load_dotenv(qa_env, override=True)

    if args.all_adapters:
        await run_all_adapters(args)
        return

    adapter_cfg = load_adapter_config(args.adapter)

    env_file = adapter_cfg.get("env_file")
    if env_file:
        env_path = REPO_ROOT / env_file
        if env_path.exists():
            load_dotenv(env_path, override=True)

    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.band.ai")
    user_api_key = os.environ.get("THENVOI_API_KEY_USER", "")
    if not user_api_key:
        raise ValueError("THENVOI_API_KEY_USER environment variable required")

    client = PlatformClient(rest_url, user_api_key)

    reports_dir = QA_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)

    run_core = not args.expanded or args.run_all
    run_expanded_flag = args.expanded or args.run_all

    all_reports: list[ExampleReport] = []

    try:
        if run_core:
            example_filter = None
            if args.examples:
                example_filter = [p.strip() for p in args.examples.split(",")]

            reports = await run_examples(adapter_cfg, client, example_filter)
            all_reports.extend(reports)

            for report in reports:
                path = reports_dir / f"{args.adapter}_{report.example_name}.md"
                path.write_text(Reporter.render(report))

        if run_expanded_flag:
            if args.scenarios:
                selected = {s.strip().upper() for s in args.scenarios.split(",")}
            else:
                selected = set(EXPANDED_SCENARIO_CLASSES.keys())

            # Extract D from selected — it runs separately
            run_d = "D" in selected
            selected.discard("D")

            if selected:
                report = await run_expanded(adapter_cfg, client, selected)
                all_reports.append(report)

                path = reports_dir / f"{args.adapter}_expanded.md"
                path.write_text(Reporter.render(report))

            if run_d or args.run_all:
                d_report = await run_multi_participant(adapter_cfg, client)
                if d_report:
                    all_reports.append(d_report)
                    path = reports_dir / f"{args.adapter}_multi_participant.md"
                    path.write_text(Reporter.render(d_report))

    finally:
        await client.close()

    print_summary(args.adapter, adapter_cfg, all_reports, reports_dir)


def print_summary(
    adapter_name: str,
    adapter_cfg: dict,
    all_reports: list[ExampleReport],
    reports_dir: Path,
) -> None:
    summary_lines = [
        f"# QA Summary: {adapter_name}",
        "",
        f"**Date:** {all_reports[0].date if all_reports else 'N/A'}",
        f"**LLM:** {adapter_cfg.get('llm_model', 'N/A')}",
        "",
        "| Report | Status | Scenarios |",
        "|--------|--------|-----------|",
    ]
    for r in all_reports:
        count = len(r.scenario_results)
        pass_count = sum(1 for s in r.scenario_results if s.status == Status.PASS)
        summary_lines.append(
            f"| {r.example_name} | {r.overall_status.value} | {pass_count}/{count} |"
        )
    summary_lines.append("")

    summary_path = reports_dir / f"{adapter_name}_summary.md"
    summary_path.write_text("\n".join(summary_lines))

    print(f"\n{'=' * 60}")
    print(f"QA SUMMARY: {adapter_name}")
    print(f"{'=' * 60}")
    for r in all_reports:
        symbol = {"PASS": "+", "FAIL": "X", "PARTIAL": "~"}.get(
            r.overall_status.value, "?"
        )
        print(f"  [{symbol}] {r.example_name}: {r.overall_status.value}")
        for sr in r.scenario_results:
            s = {"PASS": "+", "FAIL": "X", "PARTIAL": "~", "SKIP": "-"}.get(
                sr.status.value, "?"
            )
            rooms = ", ".join(sr.all_rooms) if sr.all_rooms else "n/a"
            print(f"      [{s}] {sr.name}: {sr.status.value}  (room: {rooms})")
    print(f"\nReports: {reports_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
