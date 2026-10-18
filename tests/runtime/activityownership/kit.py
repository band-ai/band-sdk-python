from __future__ import annotations

import httpx

from tests.runtime.helpers import ALICE, LifecyclePlatform, page


class ActivityPlatform(LifecyclePlatform):
    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "participants":
            return page([{**ALICE, "role": "member", "status": "active"}])
        if request.method == "POST" and tail == "messages":
            return httpx.Response(
                201, json={"data": {"id": "m-1", "success": True, "recipients": []}}
            )
        return super().answer(request)
