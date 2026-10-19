# Band mixin for Claude Code

Connect a sandboxed Claude Code agent to a Band room using its existing Claude
authentication. The Claude workload supplies the executable, Anthropic networking,
and API-key or OAuth support. This V3 mixin adds `band-claude`, a Band SDK bridge,
plus Band network access and a proxy-managed Band credential request.

```text
Band room <---- WebSocket + REST ----> Band bridge
                                          |
                                   Claude Agent SDK
                                          |
                              workload's Claude Code
                                          |
                                  mounted workspace
```

The bridge receives mentions over an outbound WebSocket, runs a Claude session
per room, and sends replies and tool events through Band's REST API. Band does
not need an inbound connection to the sandbox. Installing MCP tools alone would
not provide the listener.

## Run from source

Requires macOS/Linux with Docker Sandboxes, Docker's build engine, a Claude
credential configured in SBX, and an existing Band agent UUID and agent key.
This source prototype was exercised with SBX 0.48.0 and the V3 Claude workload
below. Its tag reports Claude Code 2.1.276. The floating Claude `latest` required
an additional capability unavailable in that SBX version; use a workload release
compatible with your CLI. Do not combine this V3 mixin with the V2 `claude` shortcut.

From this directory, create the combined sandbox with a dedicated workspace:

```bash
sbx run --detach --name band-claude \
  docker.io/docker/sbx-kit-claude:2.1.278 \
  --kit . /absolute/path/to/workspace
```

The mixin leaves the workload's entrypoint and project configuration intact. The
bridge is started explicitly, so opening interactive Claude does not silently
start another Band receiver.

## Bind the Band credential

The mixin requests service `band`, with `BAND_API_KEY` as the proxy-managed
environment variable and `X-API-Key` injection restricted to `app.band.ai`.
The request is optional for interactive Claude and `--check-claude`; the bridge
requires it when started. Actual keys stay on the host.

The [example environment](sbxenv.example.yaml) combines the workload and mixin
with a host secret source and an explicit binding approving that host. Replace
its example vault reference with your own supported secret source (`ref` or
`command`), and set its workspace. The example `op://` source requires an
authenticated 1Password CLI on the host. It is a choice, not a kit dependency;
another supported secret manager can supply the same existing Band key. Never
put a literal key into the environment file or a mounted file.

Review the configuration without applying it, then create the environment:

```bash
sbx env plan ./sbxenv.example.yaml
sbx env run --detach ./sbxenv.example.yaml
```

Use this environment route instead of the direct create command when provisioning
the declared service binding. A changed creation-time credential configuration
needs a new sandbox. The earlier `set-custom` Band credential path can coexist
with the optional request for existing installations, but is not a substitute for
testing the declared service binding before publication.

Anthropic authentication belongs to the Claude workload. An existing SBX OAuth
login works through Claude Code and the Agent SDK; it is not passed as an
Anthropic API key to an ordinary Python HTTP client. No extra Anthropic policy
rule or separate Anthropic key is needed for this OAuth route.

## Check and connect

The Claude check makes one small model request. The Band check only reads the
authenticated identity and rejects a key belonging to a different agent UUID:

```bash
sbx exec --user agent band-claude band-claude --check-claude
sbx exec --user agent band-claude band-claude \
  --agent-id YOUR_BAND_AGENT_UUID --check-band
```

Stop any other runtime using that Band identity. Keep the bridge in its own
terminal for as long as it should receive messages:

```bash
sbx exec --user agent band-claude band-claude \
  --agent-id YOUR_BAND_AGENT_UUID --workspace /absolute/path/to/workspace
```

Add that agent to a Band room and mention its handle. Tool calls and results are
sent to the room. Ctrl-C stops the bridge gracefully. Re-run the same command
to reconnect after a sandbox restart.

Interactive Claude remains available through `sbx run --name band-claude`. It and
the bridge use separate conversations with the same mounted workspace. Avoid
concurrent edits. The bridge uses native Claude tools and `bypassPermissions`
inside the sandbox, matching the Claude workload's sandbox mode; it does not
preserve Repo Doctor's former four-tool restriction. Mount only the intended
workspace. Startup is explicit and is not automatically resumed after a restart.

## Validation boundary

The source prototype built and composed with the official Claude workload on
SBX 0.48.0. Existing host OAuth passed both a direct Claude request and an Agent
SDK request using the workload's executable. An SDK native shell-tool loop also
passed. The Band identity check passed, and the bridge authenticated WebSocket
subscriptions and joined an existing room using the existing custom-secret
binding. No new Anthropic key was provisioned.

The PR version also built and composed successfully, and its OAuth and Band
identity checks passed with that existing custom binding. Twelve focused mixin
tests and eleven existing publishing-workflow checks passed. Ruff, formatting,
type checking, shell syntax, lock checks, and a direct secret scan passed. The
broader non-integration/non-E2E suite is not yet a completed validation result.

Before publishing an immutable mixin image, verify a real room mention/reply and
restart/reconnection on the exact final source, plus the declared `band` service
binding. These remain acceptance checks; authentication alone does not establish
them. No published mixin reference is claimed here. The existing Python workload
and its release pipeline are unchanged.

Dependencies are pinned in `uv.lock`; Dependabot checks this directory weekly.
Review the managed Python version, Dockerfile, frontend digest, and compatible
Claude workload monthly and repeat the checks above after a dependency refresh.

References: [Band coding agents](https://docs.band.ai/integrations/sdks/tutorials/coding-agents),
[Claude SDK subscription support](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan),
[Docker tool mixins](https://docs.docker.com/ai/sandboxes/customize/author/tool-mixins/).
