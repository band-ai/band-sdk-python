# WebSocket authentication and diagnostics

Band sends the API key in the `x-api-key` WebSocket handshake header on initial
connections, reconnects, and refused-upgrade diagnostic probes. Credential query
parameters are removed by the Phoenix transport. A rejected header does not
activate query authentication. Agent identity and conflict policy remain ordinary
query parameters.

Trusted proxies can replace a proxy-managed sentinel in the authentication header;
they do not need to rewrite the URL. Deployments must support header authentication
before adopting this transport behavior.

The connection-owned Phoenix logger sanitizes handshake and lifecycle records before
handlers receive them. Transport URLs, headers, frame payloads, identities and
untrusted server details are omitted from dynamic diagnostic arguments. Records
retain static lifecycle descriptions, native connection states and exception types,
without raw exception objects, socket metadata, or formatting arguments. Upgrade-error messages, codes,
and correlation identifiers use the same policy before becoming retained errors
or disconnect reasons. Raw HTTP upgrade exceptions are not chained into errors
returned to callers because they can retain response bodies.

Wire payloads are unchanged. Caller-visible errors redact credentials but may still
contain personal data. Applications remain responsible for logs they produce
outside the connection-owned diagnostic boundary.
