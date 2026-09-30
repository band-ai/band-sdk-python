# LangGraph Adapter

`LangGraphAdapter` invokes your graph with `thread_id` set to the Band room id.
Runnable scripts: [examples/langgraph/](../../examples/langgraph/).

- **The graph must call `band_send_message` to reply.** The adapter only
  narrates tool events and never posts the graph's output, so a plain final
  message reaches nobody.
- **Memory is the checkpointer's.** After a room's bootstrap only the new turn
  is sent, so earlier turns come from the checkpointer's state for that thread.
  The simple `llm=` pattern defaults to an in-memory saver that is lost on
  restart. At bootstrap, platform history is injected only when the
  checkpointer holds no messages for the thread.
- **`graph_factory` runs on every message.** It receives that room's Band tools
  plus `additional_tools`. Keep the Band tools in the returned graph, and build
  the checkpointer outside the factory so state survives between calls.
- **`graph=` gets none of that.** A static graph is never given Band tools or
  `additional_tools`, so it can reply only if it already calls Band tools itself.
- **`additional_tools` also takes `(InputModel, handler)` tuples**, the form
  every other adapter accepts, alongside ready-made LangChain tools.
