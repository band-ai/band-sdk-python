defmodule ContractQualification.Command do
  @moduledoc false

  def consume(root, index, apply_command, remove \\ &File.rm!/1) do
    command = Path.join(root, "command-#{index}.json")
    done = Path.join(root, "command-done-#{index}.json")

    with {:ok, request} <- read(command),
         %{"id" => id, "operation" => operation} when is_binary(id) and id != "" <- request,
         true <- operation in ["revoke", "restore"] do
      unless read(done) == {:ok, %{"id" => id}} do
        apply_command.(request)
        temp = done <> ".#{System.unique_integer([:positive])}.tmp"

        try do
          File.write!(temp, :json.encode(%{"id" => id}))
          File.chmod!(temp, 0o600)
          File.rename!(temp, done)
        after
          File.rm(temp)
        end
      end

      remove.(command)
      :done
    else
      {:error, :enoent} -> :idle
      _ -> :pending
    end
  end

  defp read(path) do
    with {:ok, bytes} <- File.read(path), do: {:ok, :json.decode(bytes)}
  rescue
    _ in [ArgumentError, ErlangError] -> {:error, :invalid_json}
  end
end
