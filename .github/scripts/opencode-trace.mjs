import { appendFileSync } from "node:fs";
import { randomUUID } from "node:crypto";

const PROGRESS_INTERVAL_MS = 30_000;
const DATA_PREFIX = "data:";
const EVENTS = new Set([
  "message.updated", "message.part.updated", "message.part.delta",
  "permission.asked", "permission.replied", "question.asked",
  "session.error", "session.idle",
]);

function identifier(value) {
  return typeof value === "string" && /^[\w-]{1,100}$/.test(value) ? value : null;
}

// Only correlation IDs and counters are recorded, never credentials or content.
export default async function streamTrace(_context, { path }) {
  const seen = new Map();
  const active = new Set();
  const record = (phase, fields = {}) => appendFileSync(
    path, JSON.stringify({ timestamp: new Date().toISOString(), phase, ...fields }) + "\n",
  );
  record("plugin.loaded");
  return {
    async config(config) {
      const options = config.provider?.opencode?.options;
      if (!options) throw new Error("OpenCode provider configuration missing");
      const fetchProvider = options.fetch ?? globalThis.fetch;
      options.fetch = async (input, init) => {
        const headers = new Headers(init?.headers);
        const correlation = {
          request: randomUUID(),
          session: identifier(headers.get("x-opencode-session")),
          message: identifier(headers.get("x-opencode-request")),
        };
        const started = performance.now();
        let chunks = 0;
        let bytes = 0;
        let dataLines = 0;
        let commentLines = 0;
        let lastByteMs = null;
        let readPending = false;
        let line = "";
        const decoder = new TextDecoder();
        let finished = false;
        const progress = (phase) => record(phase, {
          ...correlation, elapsed_ms: Math.round(performance.now() - started),
          chunks, bytes, data_lines: dataLines, comment_lines: commentLines,
          last_byte_ms: lastByteMs,
          read_pending: readPending,
        });
        const finish = (phase) => {
          if (finished) return;
          finished = true;
          clearInterval(timer);
          init?.signal?.removeEventListener("abort", aborted);
          active.delete(dispose);
          progress(phase);
        };
        const aborted = () => finish("provider.abort");
        const dispose = () => finish("plugin.dispose");
        const timer = setInterval(() => progress("provider.progress"), PROGRESS_INTERVAL_MS);
        timer.unref();
        active.add(dispose);
        init?.signal?.addEventListener("abort", aborted, { once: true });
        progress("provider.dispatch");
        try {
          if (init?.signal?.aborted) aborted();
          const response = await fetchProvider(input, init);
          record("provider.headers", { ...correlation, status: response.status,
            provider_request: identifier(response.headers.get("x-request-id")),
            edge_request: identifier(response.headers.get("cf-ray")),
            elapsed_ms: Math.round(performance.now() - started) });
          if (!response.body) {
            finish("provider.end");
            return response;
          }
          const reader = response.body.getReader();
          const body = new ReadableStream({
            async pull(controller) {
              try {
                readPending = true;
                const part = await reader.read();
                readPending = false;
                if (part.done) {
                  finish("provider.end");
                  reader.releaseLock();
                  controller.close();
                  return;
                }
                chunks += 1;
                bytes += part.value.byteLength;
                lastByteMs = Math.round(performance.now() - started);
                // Count SSE line prefixes without retaining generation content.
                for (const character of decoder.decode(part.value, { stream: true })) {
                  if (character === "\n") {
                    if (line.startsWith(DATA_PREFIX)) dataLines += 1;
                    if (line.startsWith(":")) commentLines += 1;
                    line = "";
                  } else if (line.length < DATA_PREFIX.length) {
                    line += character;
                  }
                }
                if (chunks === 1) progress("provider.first_byte");
                controller.enqueue(part.value);
              } catch (error) {
                readPending = false;
                finish("provider.stream_error");
                reader.releaseLock();
                controller.error(error);
              }
            },
            async cancel(reason) {
              finish("provider.cancel");
              try { await reader.cancel(reason); }
              finally { reader.releaseLock(); }
            },
          });
          return new Response(body, { status: response.status,
            statusText: response.statusText, headers: response.headers });
        } catch (error) {
          finish("provider.fetch_error");
          throw error;
        }
      };
      record("plugin.configured");
    },
    async event({ event }) {
      if (!EVENTS.has(event.type)) return;
      const props = event.properties ?? {};
      const session = identifier(props.sessionID ?? props.info?.sessionID ?? props.part?.sessionID);
      if (!session) return;
      const first = seen.get(session) ?? new Set();
      if (!event.type.startsWith("message.") || !first.has(event.type)) {
        record("native.event", { session, type: event.type });
      }
      first.add(event.type);
      seen.set(session, first);
      if (event.type === "session.idle") seen.delete(session);
    },
    async dispose() {
      for (const finish of active) finish();
      seen.clear();
    },
  };
}
