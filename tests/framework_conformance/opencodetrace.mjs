import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { once } from "node:events";
import streamTrace from "../../.github/scripts/opencode-trace.mjs";

function barrier() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function fixture(t, handle) {
  const directory = mkdtempSync(join(tmpdir(), "opencode-trace-"));
  const path = join(directory, "trace.jsonl");
  const hooks = await streamTrace({}, { path });
  const config = { provider: { opencode: { options: {} } } };
  await hooks.config(config);
  const server = createServer(handle);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    await hooks.dispose();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    rmSync(directory, { recursive: true });
  });
  return {
    hooks, fetch: config.provider.opencode.options.fetch,
    url: `http://127.0.0.1:${server.address().port}/chat/completions?secret-query`,
    trace: () => readFileSync(path, "utf8").trim().split("\n").map(JSON.parse),
    raw: () => readFileSync(path, "utf8"),
  };
}

const CORRELATION = { "x-opencode-session": "ses-test", "x-opencode-request": "msg-test" };

test("partial SSE bytes, request, status and headers survive tracing without content", async (t) => {
  const release = barrier();
  const comment = Buffer.from(": keepalive\r\n\n");
  const generation = Buffer.from('data: {"text":"secret-generation-δ"}\n\ndata: [DONE]\n\n');
  let received;
  const peer = await fixture(t, async (req, res) => {
    const body = [];
    for await (const chunk of req) body.push(chunk);
    received = { body: Buffer.concat(body).toString(), auth: req.headers.authorization, url: req.url };
    res.writeHead(200, { "content-type": "text/event-stream", "x-request-id": "upstream-id" });
    res.write(comment);
    await release.promise;
    const split = generation.indexOf(Buffer.from("δ")) + 1;
    res.write(generation.subarray(0, split));
    res.end(generation.subarray(split));
  });
  const response = await peer.fetch(peer.url, {
    method: "POST", headers: { ...CORRELATION, Authorization: "Bearer secret-credential" },
    body: "secret-prompt",
  });
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.deepEqual(Buffer.from(first.value), comment);
  assert.equal(response.headers.get("x-request-id"), "upstream-id");
  assert.equal(peer.trace().find((e) => e.phase === "provider.headers").provider_request, "upstream-id");
  assert.equal(response.status, 200);
  assert.deepEqual(received, { body: "secret-prompt", auth: "Bearer secret-credential",
    url: "/chat/completions?secret-query" });
  assert.equal(peer.trace().find((e) => e.phase === "provider.first_byte").data_lines, 0);
  release.resolve();
  const remainder = [];
  for (;;) {
    const part = await reader.read();
    if (part.done) break;
    remainder.push(Buffer.from(part.value));
  }
  assert.deepEqual(Buffer.concat(remainder), generation);
  const end = peer.trace().find((e) => e.phase === "provider.end");
  assert.equal(end.session, CORRELATION["x-opencode-session"]);
  assert.equal(end.message, CORRELATION["x-opencode-request"]);
  assert.equal(end.bytes, comment.length + generation.length);
  assert.equal(end.data_lines, 2);
  assert.equal(end.comment_lines, 1);
  assert.ok(!peer.raw().includes("secret-"));
});

test("abort before provider headers preserves its reason and closes the request", async (t) => {
  const entered = barrier();
  const closed = barrier();
  const peer = await fixture(t, (req, res) => {
    res.on("close", closed.resolve);
    entered.resolve();
  });
  const controller = new AbortController();
  const reason = new Error("secret-abort-reason");
  const pending = peer.fetch(peer.url, { headers: CORRELATION, signal: controller.signal });
  await entered.promise;
  controller.abort(reason);
  await assert.rejects(pending, (error) => error === reason);
  await closed.promise;
  assert.deepEqual(peer.trace().filter((e) => e.phase.startsWith("provider.")).map((e) => e.phase),
    ["provider.dispatch", "provider.abort"]);
  assert.ok(!peer.raw().includes(reason.message));
});

test("cancelling a partial stream closes the upstream socket", async (t) => {
  const closed = barrier();
  const peer = await fixture(t, (req, res) => {
    res.on("close", closed.resolve);
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(": heartbeat\n\n");
  });
  const response = await peer.fetch(peer.url, { headers: CORRELATION });
  const reader = response.body.getReader();
  await reader.read();
  await reader.cancel("secret-cancel-reason");
  await closed.promise;
  assert.equal(peer.trace().at(-1).phase, "provider.cancel");
  assert.ok(!peer.raw().includes("secret-cancel-reason"));
});

test("provider failure status and body reach the native caller unchanged", async (t) => {
  const peer = await fixture(t, (req, res) => {
    res.writeHead(429, { "content-type": "application/json", "retry-after": "9" });
    res.end('{"error":"secret-provider-error"}');
  });
  const response = await peer.fetch(peer.url);
  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "9");
  assert.equal(await response.text(), '{"error":"secret-provider-error"}');
  assert.equal(peer.trace().find((e) => e.phase === "provider.headers").status, 429);
  assert.ok(!peer.raw().includes("secret-provider-error"));
});

test("abort while awaiting provider bytes closes the original transport", async (t) => {
  const closed = barrier();
  const peer = await fixture(t, (req, res) => {
    res.on("close", closed.resolve);
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(": heartbeat\n\n");
  });
  const controller = new AbortController();
  const response = await peer.fetch(peer.url, { headers: CORRELATION, signal: controller.signal });
  const reader = response.body.getReader();
  await reader.read();
  const pending = reader.read();
  controller.abort(new Error("secret-interrupt-reason"));
  await assert.rejects(pending);
  await closed.promise;
  assert.equal(peer.trace().at(-1).phase, "provider.abort");
  assert.ok(!peer.raw().includes("secret-interrupt-reason"));
});

test("a disconnected provider after partial bytes remains a failed stream", async (t) => {
  const disconnect = barrier();
  const peer = await fixture(t, async (req, res) => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(": heartbeat\n\n");
    await disconnect.promise;
    res.destroy();
  });
  const response = await peer.fetch(peer.url, { headers: CORRELATION });
  const reader = response.body.getReader();
  await reader.read();
  disconnect.resolve();
  await assert.rejects(reader.read());
  assert.equal(peer.trace().at(-1).phase, "provider.stream_error");
});

test("native progress records the first event while retaining every permission and terminal event", async (t) => {
  const peer = await fixture(t, (req, res) => res.end());
  for (const type of ["message.part.delta", "message.part.delta", "permission.asked",
    "permission.asked", "session.error", "session.idle", "message.part.delta"]) {
    await peer.hooks.event({ event: { type, properties: { sessionID: "ses-test",
      delta: "secret-generation", metadata: { apiKey: "secret-credential" } } } });
  }
  assert.deepEqual(peer.trace().filter((e) => e.phase === "native.event").map((e) => e.type),
    ["message.part.delta", "permission.asked", "permission.asked", "session.error",
      "session.idle", "message.part.delta"]);
  assert.ok(!peer.raw().includes("secret-"));
});
