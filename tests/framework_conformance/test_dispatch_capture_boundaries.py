from __future__ import annotations

import inspect

from tests.framework_conformance import dispatch_capture


def test_langgraph_dispatch_uses_scripted_model_seam_not_graph_shortcut() -> None:
    source = inspect.getsource(dispatch_capture._drive_langgraph_script)

    assert "LangGraphAdapter(" in source
    assert "llm=model" in source
    assert "graph_factory=" not in source
    assert ".ainvoke(" not in source
    assert "bind_tools" in source
    assert "bound_tool_names" in source
    assert '"band_send_message"' in source


def test_codex_dispatch_uses_replay_client_not_internal_request_shortcut() -> None:
    source = inspect.getsource(dispatch_capture._drive_codex_replay)

    assert "ReplayCodexClient" in source
    assert "client_factory" in source
    assert "thread_start_dynamic_tool_names" in source
    assert '"band_send_message"' in source
    assert "_process_turn_events" not in source
    assert "_handle_server_request" not in source
    assert "RpcEvent(" not in source
