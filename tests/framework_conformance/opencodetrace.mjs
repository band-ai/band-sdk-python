import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
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
  const raw = () => readFileSync(path, "utf8");
  const trace = () => raw().trim().split("\n").map(JSON.parse);
  const records = (phase) => trace().filter((event) => event.phase === phase);
  return {
    hooks, fetch: config.provider.opencode.options.fetch,
    url: `http://127.0.0.1:${server.address().port}/chat/completions?secret-query`,
    record: (phase) => records(phase)[0],
    records,
    removeArtifact: () => rmSync(directory, { recursive: true }),
    restoreArtifact: () => mkdirSync(directory),
    lastPhase: () => trace().at(-1).phase,
    providerPhases: () => trace().filter((event) => event.phase.startsWith("provider."))
      .map((event) => event.phase),
    nativeEventTypes: () => trace().filter((event) => event.phase === "native.event")
      .map((event) => event.type),
    raw,
  };
}

const CORRELATION = { "x-opencode-session": "ses-test", "x-opencode-request": "msg-test" };
const PROGRESS_TICK_MS = 30_000;

for (const separator of ["\n", "\r", "\r\n"]) {
  test(`SSE counters preserve ${JSON.stringify(separator)} line boundaries across chunks`, async (t) => {
    const release = barrier();
    const prefix = `: heartbeat${separator}${separator}data: secret-generation`;
    const suffix = `${separator}${separator}data: [DONE]${separator}${separator}`;
    const split = separator === "\r\n" ? 1 : 0;
    const peer = await fixture(t, async (req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write(prefix + suffix.slice(0, split));
      await release.promise;
      res.end(suffix.slice(split));
    });
    const response = await peer.fetch(peer.url);
    const reader = response.body.getReader();
    const first = await reader.read();
    release.resolve();
    const chunks = [Buffer.from(first.value)];
    for (;;) {
      const part = await reader.read();
      if (part.done) break;
      chunks.push(Buffer.from(part.value));
    }
    assert.equal(Buffer.concat(chunks).toString(), prefix + suffix);
    assert.equal(peer.record("provider.end").data_lines, 2);
    assert.equal(peer.record("provider.end").comment_lines, 1);
    assert.ok(!peer.raw().includes("secret-generation"));
  });
}

test("partial SSE bytes, request, status and headers survive tracing without content", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
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
  assert.equal(peer.record("provider.headers").provider_request, "upstream-id");
  assert.equal(response.status, 200);
  assert.deepEqual(received, { body: "secret-prompt", auth: "Bearer secret-credential",
    url: "/chat/completions?secret-query" });
  assert.equal(peer.record("provider.first_byte").data_lines, 0);
  release.resolve();
  const remainder = [];
  for (;;) {
    const part = await reader.read();
    if (part.done) break;
    remainder.push(Buffer.from(part.value));
  }
  assert.deepEqual(Buffer.concat(remainder), generation);
  const end = peer.record("provider.end");
  assert.equal(end.session, CORRELATION["x-opencode-session"]);
  assert.equal(end.message, CORRELATION["x-opencode-request"]);
  assert.equal(end.bytes, comment.length + generation.length);
  assert.equal(end.data_lines, 2);
  assert.equal(end.comment_lines, 1);
  assert.ok(!peer.raw().includes("secret-"));
  const records = peer.raw();
  t.mock.timers.tick(PROGRESS_TICK_MS);
  assert.equal(peer.raw(), records);
});

test("artifact failure still cancels the upstream stream", async (t) => {
  const closed = barrier();
  const peer = await fixture(t, (req, res) => {
    res.on("close", closed.resolve);
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(": heartbeat\n\n");
  });
  const response = await peer.fetch(peer.url);
  const reader = response.body.getReader();
  await reader.read();
  peer.removeArtifact();
  try {
    await assert.rejects(reader.cancel(), { code: "ENOENT" });
    await closed.promise;
  } finally {
    peer.restoreArtifact();
  }
});

test("periodic progress distinguishes provider waits from consumer backpressure", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const release = barrier();
  const peer = await fixture(t, async (req, res) => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(": heartbeat\n\n");
    await release.promise;
    res.write("data: secret-generation\n\n");
  });
  const response = await peer.fetch(peer.url);
  const reader = response.body.getReader();
  await reader.read();
  await new Promise(setImmediate);
  t.mock.timers.tick(PROGRESS_TICK_MS);
  const waiting = peer.records("provider.progress").at(-1);
  assert.equal(waiting.read_pending, true);
  assert.equal(waiting.comment_lines, 1);
  assert.equal(waiting.data_lines, 0);
  release.resolve();
  let queued;
  do {
    await new Promise(setImmediate);
    t.mock.timers.tick(PROGRESS_TICK_MS);
    queued = peer.records("provider.progress").at(-1);
  } while (queued.data_lines === 0);
  assert.equal(queued.read_pending, false);
  assert.ok(queued.bytes > waiting.bytes);
  await reader.cancel();
  const records = peer.raw();
  t.mock.timers.tick(PROGRESS_TICK_MS);
  assert.equal(peer.raw(), records);
});

test("disposal releases every progress timer despite an artifact failure", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const peer = await fixture(t, (req, res) => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(": heartbeat\n\n");
  });
  const responses = await Promise.all([peer.fetch(peer.url), peer.fetch(peer.url)]);
  const readers = responses.map((response) => response.body.getReader());
  await Promise.all(readers.map((reader) => reader.read()));
  peer.removeArtifact();
  try {
    await assert.rejects(peer.hooks.dispose(), { code: "ENOENT" });
  } finally {
    peer.restoreArtifact();
  }
  await peer.hooks.event({ event: { type: "session.idle", properties: { sessionID: "ses-test" } } });
  const records = peer.raw();
  t.mock.timers.tick(PROGRESS_TICK_MS);
  assert.equal(peer.raw(), records);
  await Promise.all(readers.map((reader) => reader.cancel()));
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
  assert.deepEqual(peer.providerPhases(),
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
  assert.equal(peer.lastPhase(), "provider.cancel");
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
  assert.equal(peer.record("provider.headers").status, 429);
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
  assert.equal(peer.lastPhase(), "provider.abort");
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
  assert.equal(peer.lastPhase(), "provider.stream_error");
});

test("native progress records the first event while retaining every permission and terminal event", async (t) => {
  const peer = await fixture(t, (req, res) => res.end());
  for (const type of ["message.part.delta", "message.part.delta", "permission.asked",
    "permission.asked", "session.error", "session.idle", "message.part.delta"]) {
    await peer.hooks.event({ event: { type, properties: { sessionID: "ses-test",
      delta: "secret-generation", metadata: { apiKey: "secret-credential" } } } });
  }
  assert.deepEqual(peer.nativeEventTypes(),
    ["message.part.delta", "permission.asked", "permission.asked", "session.error",
      "session.idle", "message.part.delta"]);
  assert.ok(!peer.raw().includes("secret-"));
});
