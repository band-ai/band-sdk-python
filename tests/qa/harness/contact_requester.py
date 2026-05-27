from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class ContactRequester:
    """Send contact requests FROM a requester agent TO a target agent via the
    agent-level REST API (X-API-Key is the *requester's* api_key).
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def send_request(self, target_handle: str, message: str = "") -> dict:
        payload: dict[str, str] = {"handle": target_handle}
        if message:
            payload["message"] = message
        resp = await self._client.post("/api/v1/agent/contacts/add", json=payload)
        resp.raise_for_status()
        data = resp.json().get("data", resp.json())
        logger.info("Sent contact request to %s → id=%s", target_handle, data.get("id", "?"))
        return data

    async def list_contacts(self) -> list[dict]:
        resp = await self._client.get("/api/v1/agent/contacts")
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def list_requests(self, direction: str = "sent") -> list[dict]:
        params: dict[str, str] = {}
        if direction == "sent":
            params["sent_status"] = "pending"
        resp = await self._client.get(
            "/api/v1/agent/contacts/requests",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        if direction == "sent":
            return data.get("sent", [])
        return data.get("received", [])

    async def cancel_request(self, request_id: str) -> None:
        resp = await self._client.post(
            "/api/v1/agent/contacts/requests/respond",
            json={"action": "cancel", "request_id": request_id},
        )
        resp.raise_for_status()
        logger.info("Cancelled request %s", request_id)

    async def remove_contact(self, contact_id: str) -> None:
        resp = await self._client.post(
            "/api/v1/agent/contacts/remove",
            json={"contact_id": contact_id},
        )
        resp.raise_for_status()
        logger.info("Removed contact %s", contact_id)

    async def respond_to_request(self, request_id: str, action: str) -> None:
        resp = await self._client.post(
            "/api/v1/agent/contacts/requests/respond",
            json={"action": action, "request_id": request_id},
        )
        resp.raise_for_status()
        logger.info("Responded %s to request %s", action, request_id)

    async def cleanup_all(self, target_handle: str) -> None:
        """Remove contact + cancel pending requests involving *target_handle*.
        Idempotent — silently ignores 404s and already-processed requests.
        """
        try:
            for c in await self.list_contacts():
                h = c.get("handle", "")
                if target_handle.lstrip("@") in h.lstrip("@"):
                    try:
                        await self.remove_contact(c["id"])
                    except httpx.HTTPStatusError:
                        pass
        except Exception as exc:
            logger.debug("cleanup contacts: %s", exc)

        try:
            for r in await self.list_requests("sent"):
                if r.get("status") == "pending":
                    try:
                        await self.cancel_request(r["id"])
                    except httpx.HTTPStatusError:
                        pass
        except Exception as exc:
            logger.debug("cleanup requests: %s", exc)
