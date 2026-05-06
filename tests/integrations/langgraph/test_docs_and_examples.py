"""Docs and example drift tests for LangGraph."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
LANGGRAPH_EXAMPLES = ROOT / "examples" / "langgraph"


def test_documented_langgraph_example_paths_exist() -> None:
    docs = [
        ROOT / "README.md",
        LANGGRAPH_EXAMPLES / "README.md",
        ROOT / "docker-compose.yml",
    ]
    text = "\n".join(path.read_text() for path in docs)
    referenced = sorted(set(re.findall(r"examples/langgraph/[\w_]+\.py", text)))

    missing = [path for path in referenced if not (ROOT / path).exists()]

    assert missing == []


def test_langgraph_docs_use_current_adapter_names() -> None:
    examples_readme = (LANGGRAPH_EXAMPLES / "README.md").read_text()
    root_readme = (ROOT / "README.md").read_text()
    langgraph_section = root_readme.split("### LangGraph (`examples/langgraph/`)", 1)[1]
    langgraph_section = langgraph_section.split("### Pydantic AI", 1)[0]

    assert "custom_instructions" not in examples_readme
    assert "custom_instructions" not in langgraph_section
    assert "custom_section" in examples_readme


def test_graph_as_tool_docs_show_required_input_schema() -> None:
    examples_readme = (LANGGRAPH_EXAMPLES / "README.md").read_text()
    graph_as_tool_section = examples_readme.split("## Wrapping a Graph as a Tool", 1)[1]
    graph_as_tool_section = graph_as_tool_section.split("---", 1)[0]

    assert "graph_as_tool(" in graph_as_tool_section
    assert "input_schema=" in graph_as_tool_section


def test_langgraph_config_keys_are_documented() -> None:
    example_files = sorted(LANGGRAPH_EXAMPLES.glob("*.py"))
    used_keys: set[str] = set()
    for path in example_files:
        used_keys.update(
            re.findall(r'load_agent_config\("([^"]+)"\)', path.read_text())
        )

    config_example = (ROOT / "agent_config.yaml.example").read_text()
    documented_keys = set(
        re.findall(r"^([a-zA-Z0-9_]+):\n\s+agent_id:", config_example, re.M)
    )

    assert used_keys <= documented_keys


def test_langgraph_readme_lists_all_base_platform_tools() -> None:
    readme = (LANGGRAPH_EXAMPLES / "README.md").read_text()
    expected_tools = {
        "thenvoi_send_message",
        "thenvoi_add_participant",
        "thenvoi_remove_participant",
        "thenvoi_lookup_peers",
        "thenvoi_get_participants",
        "thenvoi_create_chatroom",
        "thenvoi_send_event",
    }

    missing = [tool for tool in sorted(expected_tools) if tool not in readme]

    assert missing == []
