[{module, _bytes}] = Code.compile_file(hd(System.argv()))
"1" = module.state_version()
{:ok, %{"total" => 0} = initial} = module.init(%{})

Enum.reduce([1, 2, 13], initial, fn by, state ->
  expected = state["total"] + by

  {:ok, %{"total" => ^expected} = next,
   [%{"type" => "room", "kind" => "total", "payload" => %{"value" => ^expected}}]} =
    module.handle(%{"kind" => "increment", "payload" => %{"by" => by}}, state)

  next
end)

{:unhandled, _} = module.handle(%{"kind" => "unknown"}, initial)
