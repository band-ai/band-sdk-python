# NOOA reproduction evidence

Base revision: `69e15a20fcd69da8a1241a004062aa28637bcd41`.
Environment: Python 3.12.12, NOOA 0.0.10, Pydantic 2.14.1, pytest 9.1.1.
The tests run real NOOA generation/execution with scripted model responses.
Band transport is replaced with existing test doubles; no live model or platform
was used. Production code is unchanged.

## Focused result

```sh
PYTHONPATH=src python -m pytest tests/adapters/nooa -q -o addopts='' --tb=short
```

**48 passed, 6 failed.** The original 38 cases pass. The 16 added cases comprise
10 passing controls/characterizations and six failing regressions for two defects.
The failing cases are deliberately not skipped or marked `xfail`.

| Test file | Cases | Result and interpretation |
|---|---|---|
| [test_validation_aliases.py](test_validation_aliases.py) | 9 | Five alias variants fail before the handler runs. Plain fields, ordinary aliases, serialization-only aliases, and strict rejection of an invalid integer pass. |
| [test_bootstrap_retry.py](test_bootstrap_retry.py) | 2 | Normal bootstrap passes. After a constructor fails once, the next successful agent loses earlier history because the coordinator already committed bootstrap context. |
| [test_resource_lifecycle.py](test_resource_lifecycle.py) | 3 | Passing characterizations: an owned worker remains running after room removal, session replacement, or adapter shutdown. These document the absent teardown convention, not a promised universal `close()` API. Test teardown stops every worker. |
| [test_thought_cancellation.py](test_thought_cancellation.py) | 2 | Passing characterizations: an unscoped fake thought send stalls until released; real `AgentTools` bound to a terminated scope cancel before REST dispatch. The fake-only stall is not proof of a production shutdown defect. |

The alias failures share this pytest prefix:

```text
tests/adapters/nooa/test_validation_aliases.py::TestValidationAliases::test_advertised_field_name_calls_handler_once_with_validated_input
```

| Parameter | Observed error |
|---|---|
| `[validation-alias]` | `orderId: Field required`; handler receives zero calls |
| `[alias-choices]` | `orderId: Field required`; handler receives zero calls |
| `[alias-path]` | `order.ids.0: Field required`; handler receives zero calls |
| `[choices-with-path]` | `order.ids.0: Field required`; handler receives zero calls |
| `[distinct-validation-and-serialization]` | `orderId: Field required`; handler receives zero calls |

The sixth failure is:

```text
tests/adapters/nooa/test_bootstrap_retry.py::TestBootstrapRetry::test_history_reaches_first_successful_room_agent[constructor-retry]
```

It expects the earlier note `The deployment window is named BLUE-PINE.` in the
model prompt. That assertion fails after the constructor retry. This case uses
the real Band coordinator, preprocessor, execution context, and NOOA adapter;
it does not assert live-server automatic redelivery behavior.

## Other checks and limits

Checks used Ruff 0.17.0 and Pyrefly 1.3.2 in a minimal NOOA review environment.

| Check | Outcome |
|---|---|
| Ruff check of the five added Python files | Passed |
| Repository-wide `ruff format --check .` | Passed; 1,400 Python files already formatted |
| Repository-wide `ruff check .` | One finding in unchanged `tests/runtime/test_tools.py:906`: an unused `DTZ001` noqa under Ruff 0.17.0 |
| `pyrefly check` | Not passing: 180 diagnostics, including 157 missing imports; this environment lacks the full development extras. Tests are excluded by the repository's Pyrefly configuration. |
| Unit suite excluding `tests/integration/` and `tests/e2e/` | Stopped during collection with 90 errors in the minimal environment, including missing `mcp`; no full-suite pass is claimed |
| Live integration / E2E | Not run |

The focused suite's failures reproduce the defects. The broad checks above do not
establish a clean repository-wide validation baseline. The reproduction branch
is intended for review before implementation fixes and is not merge-ready.
