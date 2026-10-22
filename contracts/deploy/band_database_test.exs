Code.require_file("band_database.exs", __DIR__)
ExUnit.start()

defmodule ContractQualification.DatabaseTest do
  use ExUnit.Case, async: true
  alias ContractQualification.Database

  @url "postgres://contractlab:not-a-secret@127.0.0.1:5488/contract_prod20261010_test"

  test "the installed Ecto URL scheme and PostgreSQL spellings name the same target" do
    for scheme <- ["ecto", "postgres", "postgresql"] do
      config = Database.configure!(url: String.replace(@url, "postgres://", scheme <> "://"))
      assert :ok == Database.verify!(config)
    end
  end

  test "a database name suffix does not pass the dedicated database guard" do
    assert_raise RuntimeError, "Dedicated qualification database required", fn ->
      Database.configure!(url: @url <> "_other")
    end
  end

  test "the expected address in a query does not authorize another destination" do
    assert_raise RuntimeError, "Dedicated qualification database required", fn ->
      Database.configure!(
        url:
          "postgres://contractlab:not-a-secret@other.invalid/main?label=127.0.0.1:5488/contract_prod20261010_test"
      )
    end
  end

  test "configuration does not inherit alternate connection targets or callbacks" do
    config =
      Database.configure!(
        url: @url,
        socket: "/other",
        socket_dir: "/other",
        endpoints: [{"other.invalid", 5432}],
        configure: {Other, :target, []}
      )

    refute Keyword.has_key?(config, :socket)
    refute Keyword.has_key?(config, :socket_dir)
    refute Keyword.has_key?(config, :endpoints)
    refute Keyword.has_key?(config, :configure)
    assert config[:hostname] == "127.0.0.1"
    assert config[:port] == 5488
    assert config[:database] == "contract_prod20261010_test"
    assert config[:username] == "contractlab"
    assert config[:pool] == DBConnection.ConnectionPool
    assert :ok == Database.verify!(config)

    assert_raise RuntimeError, "Dedicated qualification database required", fn ->
      Database.verify!(Keyword.put(config, :database, "other"))
    end

    assert_raise RuntimeError, "Dedicated qualification database required", fn ->
      Database.verify!(Keyword.put(config, :socket, "/other"))
    end
  end
end
