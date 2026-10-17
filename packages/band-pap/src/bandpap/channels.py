"""Advertised PAP channels with credential-bound destinations and host approvals."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlencode, urlsplit
from uuid import uuid4

from pydantic import SecretStr

from bandpap.auth import AuthError
from bandpap.discovery import ApiDiscovery, OperationsDiscovery, validate_https_url
from bandpap.http import ProtocolError, Response
from bandpap.oauth import OAuthClient
from bandpap.operations import ApprovalVerifier, OperationError, copy_operation
from bandpap.storage import Session
from bandpap.wire import ConfirmOperation, Operation


def checked(
    response: Response, statuses: frozenset[int] = frozenset({200})
) -> Response:
    if response.status not in statuses:
        raise ProtocolError(response)
    return response


def advertised(oauth: OAuthClient, api: ApiDiscovery, kind: str) -> None:
    if api.type != kind or api not in oauth.discovery.document.apis:
        raise ValueError("Channel must exactly match an advertised API")


class OperationsClient:
    """Confirm only host-verified approval; retry uncertainty using the same ID.

    Network exceptions are intentionally exposed. Callers read or confirm that
    same ID after a lost response, rather than replacing the proposed action.
    """

    def __init__(self, oauth: OAuthClient, *, approvals: ApprovalVerifier) -> None:
        self.oauth, self.approvals = oauth, approvals
        extension = oauth.discovery.document.extensions.get("operations")
        if extension is None:
            raise ValueError("Operations extension was not advertised")
        self.endpoint = OperationsDiscovery.model_validate(extension)

    def _url(self, operation_id: str, suffix: str = "") -> str:
        if not operation_id or operation_id in {".", ".."}:
            raise ValueError("Operation ID must be nonempty")
        endpoint = self.endpoint.endpoint
        if urlsplit(endpoint).query:
            raise ValueError("Operations endpoint cannot contain a query")
        return endpoint.rstrip("/") + "/" + quote(operation_id, safe="") + suffix

    def _projection(self, response: Response, operation_id: str) -> Operation:
        value = Operation.model_validate(checked(response).json())
        if value.operation_id != operation_id:
            raise ValueError("Operations response changed operation ID")
        return value

    async def read(self, operation_id: str) -> Operation:
        response = await self.oauth.request(
            "GET", self._url(operation_id), resource=self.endpoint.resource
        )
        return self._projection(response, operation_id)

    async def confirm(
        self,
        session: Session,
        operation_id: str,
        request: ConfirmOperation,
        *,
        evidence: object,
    ) -> Operation:
        request = ConfirmOperation.model_validate(request)
        await self.oauth.token(resource=self.endpoint.resource)
        _, record = self.oauth.record()
        if session.scope != self.oauth.scope or session.session_id != record.get(
            "session"
        ):
            raise AuthError("invalid_session")
        current = await self.read(operation_id)
        if current.revision != request.revision:
            raise OperationError("terms_changed", 409, current)
        if current.user_approval_required and request.approved_by != "user":
            raise OperationError("user_approval_required", 403)
        if current.state != "proposed":
            return current

        def check_session() -> None:
            _, state = self.oauth.record()
            if session.scope != self.oauth.scope or session.session_id != state.get(
                "session"
            ):
                raise AuthError("invalid_session")

        async def authorize_dispatch() -> None:
            check_session()
            await self.approvals.verify(
                session, copy_operation(current), request, evidence
            )
            check_session()

        response = await self.oauth.request(
            "POST",
            self._url(operation_id, "/confirm"),
            resource=self.endpoint.resource,
            document=request.wire(),
            authorize=authorize_dispatch,
        )
        result = self._projection(response, operation_id)
        if result.revision != request.revision:
            raise ValueError("Operations response changed confirmed revision")
        if (
            result.confirmation is not None
            and result.confirmation.approved_by != request.approved_by
        ):
            raise ValueError("Operations response changed approval provenance")
        if (
            result.summary != current.summary
            or result.terms != current.terms
            or result.user_approval_required != current.user_approval_required
        ):
            raise ValueError("Operations response changed approved terms")
        return result

    async def cancel(self, operation_id: str) -> Operation:
        return self._projection(
            await self.oauth.request(
                "POST",
                self._url(operation_id, "/cancel"),
                resource=self.endpoint.resource,
                document={},
            ),
            operation_id,
        )


class OpenAPIClient:
    """JSON OpenAPI subset with explicit host allowlists.

    Hosts approve server URLs and operation IDs independently of untrusted API
    descriptions. External references, server variables, non-JSON bodies and
    unsupported parameter locations fail explicitly rather than expanding access.
    """

    def __init__(
        self,
        oauth: OAuthClient,
        api: ApiDiscovery,
        *,
        allowed_servers: frozenset[str],
        allowed_operations: frozenset[str],
    ) -> None:
        advertised(oauth, api, "openapi")
        for server in allowed_servers:
            validate_https_url(server)
            if urlsplit(server).query:
                raise ValueError("API server URL cannot contain a query")
        self.oauth, self.api = oauth, api
        self.allowed_servers, self.allowed_operations = (
            allowed_servers,
            allowed_operations,
        )
        self.schema: dict[str, Any] | None = None

    async def load(self) -> dict[str, Any]:
        response = await self.oauth.request(
            "GET",
            self.api.url,
            resource=self.api.resource,
            extra_endpoints=(self.api.url,),
        )
        schema = checked(response).json()
        if not str(schema.get("openapi", "")).startswith(
            ("3.0.", "3.1.")
        ) or not isinstance(schema.get("paths"), dict):
            raise ValueError("Expected an OpenAPI 3.0 or 3.1 JSON description")
        self.schema = json.loads(json.dumps(schema))
        return json.loads(json.dumps(schema))

    def _operation(
        self, operation_id: str
    ) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        if operation_id not in self.allowed_operations:
            raise PermissionError("Host has not approved this API operation")
        if self.schema is None:
            raise ValueError("Load the API description before calling it")
        matches = []
        for path, item in self.schema["paths"].items():
            if not isinstance(item, dict):
                continue
            for method in (
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            ):
                operation = item.get(method)
                if (
                    isinstance(operation, dict)
                    and operation.get("operationId") == operation_id
                ):
                    matches.append((method.upper(), path, item, operation))
        if len(matches) != 1:
            raise ValueError(
                "Operation ID must identify exactly one documented operation"
            )
        return matches[0]

    async def call(
        self,
        operation_id: str,
        *,
        server: str,
        path_parameters: dict[str, str] | None = None,
        query: dict[str, str | int | bool | list[str]] | None = None,
        document: dict[str, Any] | None = None,
    ) -> Response:
        method, path, item, operation = self._operation(operation_id)
        assert self.schema is not None
        servers = operation.get(
            "servers", item.get("servers", self.schema.get("servers", []))
        )
        if not isinstance(servers, list):
            raise ValueError("OpenAPI servers must be an array")
        if server not in self.allowed_servers or not any(
            isinstance(entry, dict)
            and entry.get("url") == server
            and not entry.get("variables")
            for entry in servers
        ):
            raise PermissionError(
                "API server must be declared and approved by the host"
            )
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or any(part in {".", ".."} for part in unquote(path).split("/"))
            or "?" in path
            or "#" in path
            or "\\" in path
        ):
            raise ValueError("Unsafe API path")
        path_parameters, query = dict(path_parameters or {}), dict(query or {})
        parameters: dict[tuple[str, str], dict[str, Any]] = {}
        for parameter in [
            *item.get("parameters", []),
            *operation.get("parameters", []),
        ]:
            if (
                not isinstance(parameter, dict)
                or "$ref" in parameter
                or parameter.get("in") not in {"path", "query"}
                or not isinstance(parameter.get("name"), str)
            ):
                raise ValueError("Unsupported API parameter")
            parameters[(parameter["in"], parameter["name"])] = parameter
        allowed_path = {name for location, name in parameters if location == "path"}
        allowed_query = {name for location, name in parameters if location == "query"}
        if set(path_parameters) != allowed_path or not set(query) <= allowed_query:
            raise ValueError("Only documented API parameters may be supplied")
        for (location, name), parameter in parameters.items():
            if parameter.get("required") and name not in (
                path_parameters if location == "path" else query
            ):
                raise ValueError("Required API parameter is missing")
        for name, value in path_parameters.items():
            if (
                not isinstance(value, str)
                or not value
                or any(character in value for character in ("/", "\\", "%"))
                or value in {".", ".."}
            ):
                raise ValueError("Path parameters must be single safe segments")
            if "{" + name + "}" not in path:
                raise ValueError("Path parameter is absent from path template")
            path = path.replace("{" + name + "}", quote(value, safe=""))
        if "{" in path or "}" in path:
            raise ValueError("Undocumented path template parameter")
        body = operation.get("requestBody")
        if body is not None and (
            not isinstance(body, dict)
            or "$ref" in body
            or "application/json" not in body.get("content", {})
        ):
            raise ValueError("Only documented JSON request bodies are supported")
        if document is not None and body is None:
            raise ValueError("Operation does not document a request body")
        if body and body.get("required") and document is None:
            raise ValueError("Required JSON request body is missing")
        url = server.rstrip("/") + path
        if query:
            url += "?" + urlencode(query, doseq=True)
        return checked(
            await self.oauth.request(
                method,
                url,
                resource=self.api.resource,
                document=document,
                extra_endpoints=(server,),
            ),
            frozenset(range(200, 300)),
        )


class MCPError(ValueError):
    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"MCP JSON-RPC error {code}")


class MCPClient:
    """MCP 2025-06-18 Streamable HTTP JSON and bounded SSE responses.

    Session loss is surfaced and never retries a tool call automatically. A new
    initialize is explicit; all bearer requests remain at the advertised URL.
    """

    def __init__(
        self, oauth: OAuthClient, api: ApiDiscovery, *, allowed_tools: frozenset[str]
    ) -> None:
        advertised(oauth, api, "mcp")
        self.oauth, self.api, self.allowed_tools = oauth, api, allowed_tools
        self.session_id: str | None = None
        self.initialized = False
        self.tools_supported = False
        self.protocol = "2025-06-18"

    async def _send(self, method: str, document: dict[str, Any] | None) -> Response:
        headers = {"Accept": "application/json, text/event-stream"}
        if self.initialized:
            headers["MCP-Protocol-Version"] = self.protocol
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        response = await self.oauth.request(
            method,
            self.api.url,
            resource=self.api.url,
            bearer=True,
            document=document,
            headers=headers,
        )
        if response.status == 404 and self.session_id is not None:
            self.session_id, self.initialized, self.tools_supported = None, False, False
            raise ValueError("MCP session expired; initialize a new session explicitly")
        return response

    @staticmethod
    def _result(response: Response, request_id: str) -> dict[str, Any]:
        checked(response)
        media_type = (
            response.headers.get("content-type", "application/json")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if media_type == "text/event-stream":
            messages = []
            data: list[str] = []
            for line in [*response.body.decode().replace("\r\n", "\n").split("\n"), ""]:
                if line == "":
                    if data:
                        messages.append(json.loads("\n".join(data)))
                        data = []
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
        elif media_type == "application/json" or media_type.endswith("+json"):
            messages = [response.json()]
        else:
            raise ValueError("Unsupported MCP response media type")
        matches = [
            message
            for message in messages
            if isinstance(message, dict) and message.get("id") == request_id
        ]
        if len(matches) != 1 or matches[0].get("jsonrpc") != "2.0":
            raise ValueError("MCP response must match exactly one request")
        message = matches[0]
        if "error" in message:
            error = message["error"]
            if not isinstance(error, dict) or not isinstance(error.get("code"), int):
                raise ValueError("Malformed MCP JSON-RPC error")
            raise MCPError(error["code"])
        if not isinstance(message.get("result"), dict):
            raise ValueError("MCP response must contain an object result")
        return message["result"]

    async def _rpc(
        self, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], Response]:
        identifier = uuid4().hex
        response = await self._send(
            "POST",
            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params},
        )
        return self._result(response, identifier), response

    async def initialize(self) -> dict[str, Any]:
        if self.initialized:
            raise ValueError("MCP client is already initialized")
        result, response = await self._rpc(
            "initialize",
            {
                "protocolVersion": self.protocol,
                "capabilities": {},
                "clientInfo": {"name": "bandpap", "version": "0.1.0"},
            },
        )
        if result.get("protocolVersion") != self.protocol:
            raise ValueError("Unsupported negotiated MCP protocol version")
        session_id = response.headers.get("mcp-session-id")
        if session_id is not None and (
            not session_id
            or len(session_id) > 256
            or any(
                ord(character) < 33 or ord(character) > 126 for character in session_id
            )
        ):
            raise ValueError("Invalid MCP session header")
        self.session_id = session_id
        self.initialized = True
        self.tools_supported = (
            isinstance(result.get("capabilities"), dict)
            and "tools" in result["capabilities"]
        )
        try:
            checked(
                await self._send(
                    "POST", {"jsonrpc": "2.0", "method": "notifications/initialized"}
                ),
                frozenset({202}),
            )
        except BaseException:
            self.initialized = False
            raise
        return result

    def _ready(self) -> None:
        if not self.initialized or not self.tools_supported:
            raise ValueError("Initialize an MCP server supporting tools first")

    async def list_tools(self, *, cursor: str | None = None) -> dict[str, Any]:
        self._ready()
        result, _ = await self._rpc(
            "tools/list", {"cursor": cursor} if cursor is not None else {}
        )
        if not isinstance(result.get("tools"), list):
            raise ValueError("Malformed MCP tool listing")
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ready()
        if name not in self.allowed_tools:
            raise PermissionError("Host has not approved this MCP tool")
        result, _ = await self._rpc(
            "tools/call", {"name": name, "arguments": arguments}
        )
        return result

    async def close(self) -> None:
        if self.session_id is not None:
            try:
                checked(
                    await self._send("DELETE", None), frozenset({200, 202, 204, 405})
                )
            finally:
                self.session_id, self.initialized, self.tools_supported = (
                    None,
                    False,
                    False,
                )
        else:
            self.initialized, self.tools_supported = False, False


@dataclass(frozen=True)
class BrowserCommand:
    """Private form-post handoff; never render the assertion in chat or a URL."""

    endpoint: str
    assertion: SecretStr = field(repr=False)
    return_to: str
    method: str = field(default="POST", init=False)
    top_level: bool = field(default=True, init=False)

    def form(self) -> dict[str, str]:
        """Only the controlled browser executor may consume this protected body."""
        return {"assertion": self.assertion.get_secret_value()}


class ControlledBrowser(Protocol):
    async def post_form(self, command: BrowserCommand) -> None:
        """Use a Personal Agent-controlled browser, never the User's browser.

        Submit command.form() as a top-level application/x-www-form-urlencoded
        POST to command.endpoint; keep the assertion out of URLs, logs and chat.
        """
        ...


class BrowserClient:
    def __init__(self, oauth: OAuthClient, *, browser: ControlledBrowser) -> None:
        self.oauth, self.browser = oauth, browser
        web = oauth.discovery.document.web
        if web is None or web.browser_session_endpoint is None:
            raise ValueError("Session browser channel was not advertised")
        self.endpoint = web.browser_session_endpoint

    async def command(self, return_to: str) -> BrowserCommand:
        validate_https_url(return_to)
        domain = self.oauth.discovery.document.organization.domain.lower()
        host = urlsplit(return_to).hostname
        if host is None or not (
            host.lower() == domain or host.lower().endswith("." + domain)
        ):
            raise ValueError("Browser return URL must be on the company's domain")
        await self.oauth.token()
        _, record = self.oauth.record()
        session_id = record.get("session")
        if not isinstance(session_id, str) or not session_id:
            raise AuthError("invalid_session")
        assertion = self.oauth.identity.assertion(
            self.oauth.scope.client,
            self.oauth.scope.user,
            self.endpoint,
            typ="poppy-browser+jwt",
            session_id=session_id,
            return_to=return_to,
        )
        return BrowserCommand(self.endpoint, SecretStr(assertion), return_to)

    async def join(self, return_to: str) -> None:
        await self.browser.post_form(await self.command(return_to))
