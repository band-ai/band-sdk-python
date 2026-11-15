"""Operations routes authenticate and obtain approval outside protocol content."""

from __future__ import annotations

from pathlib import Path

from bandpap.storage import StateStore

from tests.pap.gatewaykit import RESOURCE, Evidence, GatewayHarness
from tests.pap.operationkit import Approvals, Executor, proposal, service


class TestGatewayOperations:
    async def test_confirmation_body_cannot_self_certify_approval(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "gateway.db")
        approvals, executor = Approvals(), Executor()
        engine = service(store, approvals=approvals, executor=executor)

        evidence = Evidence(approvals.token)
        kit = GatewayHarness(
            tmp_path / "gateway.db", store=store, operations=engine, approvals=evidence
        )
        access = kit.access()
        await engine.propose(
            access.session, proposal(), execution_ref="host-private-effect"
        )
        discovery = (await kit.client.get("/.well-known/poppy.json")).json()
        assert discovery["extensions"]["operations"]["version"] == "1"
        assert discovery["extensions"]["operations"]["resource"] == RESOURCE
        path = "/poppy/operations/op_one"
        body = {
            "revision": 1,
            "approved_by": "user",
            "evidence": "yes, approved",
            "room": "another-room",
        }
        denied = await kit.request(access, "POST", path + "/confirm", value=body)
        assert denied.status_code == 403 and executor.calls == []
        evidence.granted = True
        result = await kit.request(access, "POST", path + "/confirm", value=body)
        assert result.status_code == 200 and result.json()["state"] == "succeeded"
        assert len(executor.calls) == 1
        assert "host-private-effect" not in result.text
        other = kit.access("bob")
        assert (await kit.request(other, "GET", path)).status_code == 404
        assert (await kit.client.get(path)).status_code == 401
        await kit.close()

    async def test_native_operation_id_uses_exact_encoded_request_target(
        self, tmp_path: Path
    ) -> None:
        store = StateStore(tmp_path / "gateway.db")
        approvals = Approvals()
        engine = service(store, approvals=approvals)
        kit = GatewayHarness(
            tmp_path / "gateway.db",
            store=store,
            operations=engine,
            approvals=Evidence(approvals.token),
        )
        access = kit.access()
        await engine.propose(
            access.session, proposal(operation_id="order 1"), execution_ref="private"
        )
        result = await kit.request(access, "GET", "/poppy/operations/order%201")
        assert result.status_code == 200 and result.json()["operation_id"] == "order 1"
        await kit.close()
