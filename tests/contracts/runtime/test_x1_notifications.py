from __future__ import annotations

from unittest.mock import Mock

import pytest

from tests.contracts.runtime.x1notifications import NotifyOnlyIngressFixture


@pytest.mark.contract_posix
class TestNotifyOnlyIngress(NotifyOnlyIngressFixture):
    def test_notifications_use_only_the_explicit_callback_on_both_ingresses(
        self,
    ) -> None:
        for ingress in ("endpoint", "service"):
            with self.subTest(ingress=ingress):
                notify = Mock(return_value={"status": "notified"})
                agent, server, path = self.make(ingress, notify)
                for _ in range(2):
                    self.assertEqual(
                        self.post(server, path, self.event),
                        (200, {"status": "notified"}),
                    )
                self.assertEqual(notify.call_count, 2)
                notify.assert_called_with(self.event)
                agent.receive.assert_not_called()
                agent.recover.assert_not_called()
                notify.return_value = {"status": "ignored", "reason": "self_authored"}
                self.assertEqual(
                    self.post(server, path, self.event),
                    (200, {"status": "ignored", "reason": "self_authored"}),
                )
                agent.receive.assert_not_called()

    def test_missing_notification_consumer_fails_closed_without_receive_fallback(
        self,
    ) -> None:
        for ingress in ("endpoint", "service"):
            with self.subTest(ingress=ingress):
                agent, server, path = self.make(ingress, None, configured=False)
                self.assertEqual(self.post(server, path, self.event)[0], 503)
                agent.receive.assert_not_called()
                agent.recover.assert_not_called()
