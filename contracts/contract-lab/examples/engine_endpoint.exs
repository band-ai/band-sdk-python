case System.argv() do
  [path] -> ContractLab.Endpoint.run(path)
  [path, format] -> ContractLab.Endpoint.run(path, format)
  _ -> raise "Expected contract path and optional header format"
end
