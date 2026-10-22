defmodule ContractQualification.Database do
  @moduledoc false

  def configure!(repo) do
    url = URI.parse(repo[:url] || "")

    unless url.scheme in ["ecto", "postgres", "postgresql"] and url.host == "127.0.0.1" and
             url.port == 5488 and url.path == "/contract_prod20261010_test" and
             url.query == nil and url.fragment == nil and is_binary(url.userinfo),
           do: fail!()

    case String.split(url.userinfo, ":", parts: 2) do
      ["contractlab", password] when byte_size(password) > 0 ->
        # Construct a closed connection configuration. Do not carry socket,
        # endpoints, configure callbacks or URL query options into the lab.
        [
          hostname: "127.0.0.1",
          port: 5488,
          database: "contract_prod20261010_test",
          username: "contractlab",
          password: URI.decode(password),
          pool: DBConnection.ConnectionPool,
          pool_size: 12
        ]

      _ ->
        fail!()
    end
  end

  def verify!(config) do
    unless config[:hostname] == "127.0.0.1" and config[:port] == 5488 and
             config[:database] == "contract_prod20261010_test" and
             config[:username] == "contractlab" and
             Enum.all?(
               [:url, :socket, :socket_dir, :endpoints, :configure],
               &(Keyword.get(config, &1) == nil)
             ),
           do: fail!()

    :ok
  end

  defp fail!, do: raise("Dedicated qualification database required")
end
