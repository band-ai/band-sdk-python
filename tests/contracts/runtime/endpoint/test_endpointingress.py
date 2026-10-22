from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.contracts.runtime.endpoint.kit import EndpointIngressFixture


@pytest.mark.contract_posix
class TestEndpointIngress(EndpointIngressFixture):
    def test_slow_unauthenticated_headers_cannot_hold_the_only_connection_slot(
        self,
    ) -> None:
        self.assert_slow_ingress_releases_capacity(b"POST /events HTTP/1.1\r\nX-Slow: ")

    def test_slow_authenticated_body_cannot_extend_the_ingress_deadline(self) -> None:
        self.assert_slow_ingress_releases_capacity(
            b"POST /events HTTP/1.1\r\nAuthorization: Bearer "
            + b"r" * 32
            + b"\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{"
        )

    def test_completed_ingress_allows_a_longer_admitted_callback(self) -> None:
        with ThreadPoolExecutor(max_workers=1) as executor:
            response = executor.submit(self.post, {"slow": True})
            try:
                self.assertTrue(self.callback_started.wait(5))
                deadline = self.clock.await_timer()
                self.assertTrue(deadline.cancelled)
                self.clock.advance(100)
                self.assertFalse(response.done())
                self.release_callback.set()
                self.assertEqual(response.result(timeout=5), {"status": "completed"})
            finally:
                self.release_callback.set()
        self.assertEqual(self.received, [{"slow": True}])
