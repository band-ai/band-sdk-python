/**
 * Drives the real room view against a fake MCP App host.
 *
 * The view is a browser script, so what it does with an in-flight watch, an
 * overtaking refresh, or a room that changes underneath it cannot be reached
 * from Python at all. This runs the shipped asset in a Node VM with a stub DOM
 * and a scripted host, and prints one JSON object per scenario; the pytest that
 * invokes it owns the assertions.
 *
 * Usage: node roomview.mjs <path to room-view.js>
 */

import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";

const flush = () => new Promise(resolve => setImmediate(resolve));

/** Only what the view actually touches; anything else is a genuine surprise. */
function stubElement() {
  return {
    textContent: "",
    title: "",
    hidden: false,
    scrollTop: 0,
    scrollHeight: 100,
    offsetHeight: 20,
    attributes: {},
    handlers: {},
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(type, callback) { (this.handlers[type] ||= []).push(callback); },
    click() {
      for (const callback of this.handlers.click || []) {
        callback({ target: this, currentTarget: this });
      }
    },
    append() {},
    appendChild() {},
    replaceChildren() {}
  };
}

function mountView(source) {
  const sent = [];
  const answered = new Set();
  const inbox = [];
  const timers = [];
  const elements = new Map();
  const parent = { postMessage: message => sent.push(message) };
  const byId = id => {
    if (!elements.has(id)) elements.set(id, stubElement());
    return elements.get(id);
  };

  const document = {
    hidden: false,
    documentElement: { scrollWidth: 400, scrollHeight: 300 },
    body: { classList: { toggle() {} } },
    getElementById: byId,
    createElement: stubElement,
    addEventListener() {}
  };
  const sandbox = {
    console: { log() {} },
    document,
    window: {
      parent,
      document,
      addEventListener(type, callback) {
        if (type === "message") inbox.push(callback);
      },
      setTimeout(callback, delayMs) {
        timers.push({ callback, delayMs });
        return timers.length;
      },
      clearTimeout() {}
    }
  };
  createContext(sandbox);
  runInContext(source, sandbox);

  const deliver = message => {
    for (const listener of inbox) listener({ source: parent, data: message });
  };
  const waiting = (method, matches = () => true) =>
    sent.find(
      item =>
        item.method === method &&
        item.id !== undefined &&
        !answered.has(item.id) &&
        matches(item)
    );
  const answer = (message, result = {}) => {
    answered.add(message.id);
    deliver({ jsonrpc: "2.0", id: message.id, result });
  };

  /** Let the view's promises run, answering the calls a host always answers. */
  const settle = async () => {
    for (let round = 0; round < 12; round += 1) {
      await flush();
      const routine = waiting("ui/update-model-context") || waiting("ui/message");
      if (!routine) return;
      answer(routine);
    }
  };

  return {
    sent,
    deliver,
    answer,
    settle,
    room: () => byId("room").textContent,
    toolCall: name => waiting("tools/call", item => item.params.name === name),
    contextUpdates: () =>
      sent.filter(item => item.method === "ui/update-model-context"),
    click: id => byId(id).click(),
    // The RPC deadlines share this queue, so only the short restart delay runs.
    tick: () => {
      for (const timer of timers.splice(0).filter(item => item.delayMs <= 1000)) {
        timer.callback();
      }
    }
  };
}

const VIEWER = { id: "agent-1", name: "tom" };

function transcript(chatId, messages, nextSince, pending = []) {
  return {
    chat_id: chatId,
    viewer: VIEWER,
    participants: [],
    messages,
    pending_requests: pending,
    role_briefing: "briefing",
    next_since: nextSince,
    refreshed_at: nextSince,
    transport: { role: "follower" },
    host: {}
  };
}

function said(id, insertedAt, extra = {}) {
  return {
    id,
    content: id,
    sender_id: "peer-1",
    sender_name: "Jerry",
    sender_type: "Agent",
    message_type: "text",
    inserted_at: insertedAt,
    ...extra
  };
}

async function open(source, chatId, payload) {
  const view = mountView(source);
  await view.settle();
  view.answer(view.sent.find(item => item.method === "ui/initialize"), {
    hostInfo: { name: "fake-desktop" },
    hostCapabilities: {}
  });
  await view.settle();
  view.deliver({
    jsonrpc: "2.0",
    method: "ui/notifications/tool-input",
    params: { arguments: { chat_id: chatId } }
  });
  view.deliver({
    jsonrpc: "2.0",
    method: "ui/notifications/tool-result",
    params: { structuredContent: payload }
  });
  await view.settle();
  return view;
}

const SCENARIOS = {
  /** A watch answered after the room changed must not drag the view back. */
  async staleRoomResult(source) {
    const view = await open(
      source,
      "room-a",
      transcript("room-a", [said("a-1", "2026-01-01T00:00:01Z")], "2026-01-01T00:00:01Z")
    );
    const watch = view.toolCall("band_wait_for_room_event");
    const updatesBeforeSwitch = view.contextUpdates().length;

    view.deliver({
      jsonrpc: "2.0",
      method: "ui/notifications/tool-input",
      params: { arguments: { chat_id: "room-b" } }
    });
    view.answer(watch, {
      structuredContent: transcript(
        "room-a",
        [said("a-2", "2026-01-01T00:00:02Z")],
        "2026-01-01T00:00:02Z"
      )
    });
    await view.settle();

    return {
      room: view.room(),
      contextUpdatesAfterSwitch: view.contextUpdates().length - updatesBeforeSwitch
    };
  },

  /** A refresh overtaken by a watch must not rewind the resume cursor. */
  async cursorRewind(source) {
    const view = await open(
      source,
      "room-a",
      transcript("room-a", [said("m-1", "2026-01-01T00:00:05Z")], "2026-01-01T00:00:05Z")
    );
    const watch = view.toolCall("band_wait_for_room_event");
    view.click("refresh");
    await flush();
    const refresh = view.toolCall("band_refresh_room_view");

    view.answer(watch, {
      structuredContent: transcript(
        "room-a",
        [said("m-2", "2026-01-01T00:00:09Z")],
        "2026-01-01T00:00:09Z"
      )
    });
    await view.settle();
    view.answer(refresh, {
      structuredContent: transcript("room-a", [], "2026-01-01T00:00:05Z")
    });
    await view.settle();
    view.tick();
    await view.settle();

    return {
      refreshAskedFrom: refresh.params.arguments.since,
      resumedFrom: view.toolCall("band_wait_for_room_event").params.arguments.since
    };
  },

  /** A tick carrying nothing still moves the cursor, or monitoring stalls. */
  async quietTick(source) {
    const view = await open(
      source,
      "room-a",
      transcript("room-a", [said("m-1", "2026-01-01T00:00:01Z")], "2026-01-01T00:00:01Z")
    );

    view.answer(view.toolCall("band_wait_for_room_event"), {
      structuredContent: transcript("room-a", [], "2026-01-01T00:00:20Z")
    });
    await view.settle();
    view.tick();
    await view.settle();

    return {
      resumedFrom: view.toolCall("band_wait_for_room_event").params.arguments.since
    };
  },

  /** Whose turn it is to act is the server's call, relayed as delivered. */
  async pendingIsTheServers(source) {
    const view = await open(
      source,
      "room-a",
      transcript(
        "room-a",
        [
          said("ask", "2026-01-01T00:00:01Z", { addressed_to_viewer: true }),
          said("agent-reply", "2026-01-01T00:00:02Z", { sender_id: VIEWER.id })
        ],
        "2026-01-01T00:00:02Z",
        [said("ask", "2026-01-01T00:00:01Z", { addressed_to_viewer: true })]
      )
    );
    const latest = view.contextUpdates().at(-1).params;

    return {
      pending: latest.structuredContent.band_room.pending_requests.map(item => item.id),
      text: latest.content[0].text
    };
  },

  /** How long to block is configured on the server, so the view never says. */
  async watchTiming(source) {
    const view = await open(
      source,
      "room-a",
      transcript("room-a", [], "2026-01-01T00:00:01Z")
    );

    return {
      arguments: Object.keys(view.toolCall("band_wait_for_room_event").params.arguments)
    };
  }
};

const source = readFileSync(process.argv[2], "utf8");
const results = {};
for (const [name, scenario] of Object.entries(SCENARIOS)) {
  results[name] = await scenario(source);
}
process.stdout.write(JSON.stringify(results));
