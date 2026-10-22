Code.require_file("../examples/room_recovery.exs", __DIR__)

defmodule ContractLab.RoomRecoveryTest do
  use ExUnit.Case, async: false

  test "real room delivery generates a repair, resumes state, and publishes each total once" do
    dir = Path.join(System.tmp_dir!(), "room-recovery-#{System.unique_integer([:positive])}")
    on_exit(fn -> File.rm_rf!(dir) end)
    result = ContractLab.Examples.RoomRecovery.run(dir)
    assert result["totals"] == [1, 3, 16]
    assert result["ordinary_sidecar_calls"] == 0
    assert result["repair_sidecar_calls"] == 1
    assert result["state_after_restart"] == %{"total" => 16}
    assert result["replayed_after_snapshot"] == 1
    assert result["repeated_delivery"] == "duplicate"
    assert result["restart_publications"] == 0
    assert result["completed_inputs"] == 3
    assert result["generated_releases"] == 1
    assert result["snapshot_at_fault"] == %{"total" => 3}
    assert result["control_activation"] == "repaired"
    assert result["control_duplicate_calls"] == 0
    assert result["unauthorized_control"] == "illegal_message"
    assert result["control_contract_documentation"] =~ "Wake the sidecar"
    assert File.regular?(result["journal_path"])
  end
end
