# Remote hybrid replacement

This is an **optional experiment**, selected independently of room enforcement.
Ordinary agents can discover the contract and interact directly without this
intermediary, its SDK, local checker, journal, snapshots or repair. See
[room/client separation](../../../spec/room-clients-v2.md). `llm_only` below is
one mode of this optional runtime, not a mandatory route for model-based agents.

The implementation and exact APIs are maintained in the [Python runtime guide](../../../agent-runtime/README.md). Its [sidecar prompt](../../../../src/remote_agent/sidecar_prompt.md) is supplied by the command adapter and is the operational instruction for models answering, inspecting, or repairing. Read that prompt when authoring or debugging a provider bridge. The [earlier Elixir experiment](elixir-replacement.md) remains available for that host.

The architecture has one room authority and three collaborators on the remote agent host: the intermediary, the program, and the LLM. The intermediary owns admission preflight, durable input and output handling, SQLite state, snapshots, and routing. The program owns exact deterministic cases. The LLM owns cases requiring judgment and can improve program coverage without making callers wait for code generation.

| Decision | Evidence to obtain |
|---|---|
| Full replacement | Complete input definition, deterministic decision procedure, semantic tests, and credible coverage of exceptional cases |
| Partial replacement | Precisely recognized common cases, correct outcomes for each, and delegation for ambiguous or unsupported cases |
| LLM-only handling | Evidence that the essential task requires general interpretation or judgment; keep useful deterministic routing and checks |
| One-turn delegation | This input needs judgment but the next input can safely try the existing program |
| Ongoing takeover | The program is faulty, unsuitable, or explicitly suspended; retain LLM routing until verified return |
| Candidate installation | Same state version and initial state, compatible full history, pending fault handled, independent semantic validator passes |

Do not translate the user's aspiration to reduce calls into a quality tradeoff they did not authorize. A confidently wrong fast path is worse than explicit delegation. Test close-but-different inputs and compare the behavior against the task's meaning, not only the contract's accepted schema. Record why every supported case is safe to automate. Do not mechanically classify every language task as replaceable or every string input as unreplaceable.

`COVERAGE` uses `full`, `partial`, or `llm_only`, plus `handles` and `delegates` lists. These are an auditable declaration, not a proof. The controller does not infer business correctness from the manifest. Initial installation still needs author validation; automatic repair requires the separate host validator.

The live routing state is durable. A reply produced by an LLM is recorded as an LLM-owned turn and is never regenerated during recovery. A partial candidate may still delegate that historical turn. Program-owned turns must preserve the exact committed state and message. A repair cannot revise answers the room already accepted.

Controls are a separate authenticated endpoint operation. Use `wake` for diagnosis, `takeover` for ongoing model control, `repair` to request background work, and `resume` only after verification. Deduplicate by control identity before activation. Neither arbitrary room prose nor the model's diagnostic note grants an action.

Keep each boundary visible in delivery: local preflight vs room acceptance, proposed state vs committed state, staged snapshot vs durable snapshot, generated candidate vs installed candidate, and scripted-provider evidence vs live-model evidence. The [demo](../../../agent-runtime/examples/hybrid_demo.py) exercises those boundaries with real local processes and HTTP, while explicitly using a scripted model fixture.
