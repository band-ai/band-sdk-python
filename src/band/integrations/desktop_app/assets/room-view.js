// @ts-check
/**
 * The Band room view: a live display of one room inside Claude Desktop.
 *
 * It renders the transcript, mirrors it into the model's context, and — when
 * user activation allows it — accelerates Claude with a `ui/message` for the
 * mentions the server's wake ledger hands it. Claude's monitor loop is the
 * guarantee; the host only accepts `ui/message` with user activation.
 *
 * The payload shapes below mirror the Pydantic models in
 * `band/integrations/desktop_app/room.py`; keep them in step.
 *
 * Sections: host RPC · room state · rendering · model context sync ·
 * wake accelerator · live watch loop · host wiring.
 */

/**
 * @typedef {object} RoomMessage
 * @property {string} [id]
 * @property {string} [content]
 * @property {string} [sender_id]
 * @property {string} [sender_name]
 * @property {string} [sender_type]
 * @property {string} [message_type]
 * @property {string} [inserted_at]
 * @property {boolean} [addressed_to_viewer]
 */

/**
 * @typedef {object} RoomParticipant
 * @property {string} [id]
 * @property {string} [name]
 * @property {string} [handle]
 * @property {string} [type]
 * @property {string} [role]
 */

/**
 * @typedef {object} AgentIdentity
 * @property {string} [id]
 * @property {string} [name]
 * @property {string} [handle]
 * @property {string} [description]
 */

/**
 * @typedef {object} RelayStatus
 * @property {"starting"|"leader"|"follower"} [role]
 * @property {boolean} [websocket_connected]
 * @property {number} [events_received]
 * @property {string|null} [last_error]
 */

/**
 * @typedef {object} RoomTranscript
 * @property {string} [chat_id]
 * @property {AgentIdentity} [viewer]
 * @property {RoomParticipant[]} [participants]
 * @property {RoomMessage[]} messages
 * @property {string} [role_briefing]
 * @property {string} [next_since]
 * @property {RelayStatus} [transport]
 * @property {object} [host]
 * @property {RoomMessage[]} [pending_requests]
 * @property {RoomMessage[]} [wake_requests]
 * @property {string} [wake_prompt]
 * @property {string} [refreshed_at]
 * @property {boolean} [event_received]
 */

/**
 * A CallToolResult as the host relays it.
 * @typedef {object} ToolResult
 * @property {RoomTranscript} [structuredContent]
 * @property {{type: string, text?: string}[]} [content]
 * @property {boolean} [isError]
 */

/**
 * @typedef {object} Waiter
 * @property {(value: any) => void} resolve
 * @property {(error: Error) => void} reject
 */

(() => {
  /** Every number the view runs on, in one place. */
  const TUNING = {
    // The ceiling the monitor's schema advertises (MAX_ROOM_EVENT_TIMEOUT_S).
    // The server picks the actual wait from its own configuration, so the view
    // never sends one — it only has to outlast the longest it could choose.
    maxWatchS: 30,
    requestTimeoutMs: 15000,
    watchRestartDelayMs: 250,
    expandedMaxHeightPx: 520,
    contextMessages: 30,
    contextMessageChars: 2000
  };

  // ── Host RPC ──────────────────────────────────────────────────────────

  /** @type {Map<number, Waiter>} */
  const pending = new Map();
  let requestId = 1;
  /** @type {{info: unknown, capabilities: {logging?: unknown}}} */
  let host = { info: null, capabilities: {} };

  /** @param {object} message */
  const send = message => window.parent.postMessage(message, "*");

  /**
   * Send a JSON-RPC request to the host. Every request is bounded: a host that
   * never answers (or an iframe the browser froze mid-flight) must not wedge
   * the watch loop forever.
   * @param {string} method
   * @param {object} params
   * @param {number} [timeoutMs]
   * @returns {Promise<any>}
   */
  const request = (method, params, timeoutMs = TUNING.requestTimeoutMs) =>
    new Promise((resolve, reject) => {
      const id = requestId++;
      const timer = window.setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, timeoutMs);
      /**
       * @param {(value: any) => void} callback
       * @returns {(value: any) => void}
       */
      const settle = callback => value => {
        window.clearTimeout(timer);
        callback(value);
      };
      pending.set(id, { resolve: settle(resolve), reject: settle(reject) });
      send({ jsonrpc: "2.0", id, method, params });
    });

  /**
   * @param {string} method
   * @param {object} [params]
   */
  const notify = (method, params) => {
    /** @type {{jsonrpc: string, method: string, params?: object}} */
    const message = { jsonrpc: "2.0", method };
    if (params !== undefined) message.params = params;
    send(message);
  };

  /**
   * @param {"debug"|"info"|"warning"|"error"} level
   * @param {object} data
   */
  const log = (level, data) => {
    console.log("[band-room-view]", level, data);
    if (host.capabilities.logging) {
      notify("notifications/message", { level, logger: "band-room-view", data });
    }
  };

  /**
   * @param {ToolResult|undefined|null} result
   * @returns {RoomTranscript|null}
   */
  function toolPayload(result) {
    if (result?.structuredContent) return result.structuredContent;
    const text = result?.content?.find(item => item.type === "text")?.text;
    if (!text) return null;
    try { return JSON.parse(text); } catch (_) { return null; }
  }

  // ── Room state ────────────────────────────────────────────────────────

  /** @type {Map<string, RoomMessage>} */
  const messages = new Map();
  let chatId = "";
  /** @type {AgentIdentity} */
  let viewer = {};
  /** @type {RoomParticipant[]} */
  let participants = [];
  let roleBriefing = "";
  /** @type {RelayStatus} */
  let transport = {};
  /** What the server saw Desktop declare at connect. @type {object} */
  let mcpHost = {};
  /**
   * The server's resume cursor, taken from every payload's `next_since`.
   *
   * Not the newest message's timestamp: the server advances this even when the
   * room is quiet, which is what lets it prove a later tick needs no REST read
   * and keeps two successive monitor calls from looking identical.
   * @type {string|null}
   */
  let cursor = null;
  let eventCount = 0;

  /** @returns {RoomMessage[]} */
  function orderedMessages() {
    return Array.from(messages.values()).sort((left, right) =>
      String(left.inserted_at || "").localeCompare(String(right.inserted_at || "")) ||
      String(left.id || "").localeCompare(String(right.id || ""))
    );
  }

  /**
   * @param {RoomMessage[]} newMessages
   * @returns {number} how many messages not already displayed arrived
   */
  function merge(newMessages) {
    let fresh = 0;
    for (const message of newMessages) {
      const key = message.id ||
        `${message.inserted_at}:${message.sender_id}:${message.content}`;
      if (!messages.has(key)) fresh += 1;
      messages.set(key, message);
    }
    return fresh;
  }

  // ── Rendering ─────────────────────────────────────────────────────────

  let collapsed = false;
  let unseen = 0;

  /**
   * The element with this id. Missing means the markup and script disagree,
   * which is a bug worth failing on rather than silently doing nothing.
   * @param {string} id
   * @returns {HTMLElement}
   */
  const element = id => {
    const found = document.getElementById(id);
    if (found === null) throw new Error(`room view is missing #${id}`);
    return found;
  };

  /**
   * @param {string} text
   * @param {boolean} [error]
   */
  function setStatus(text, error = false) {
    const status = element("status");
    status.textContent = text;
    status.classList.toggle("error", error);
  }

  /** Tell the host how tall the view wants to be for its current shape. */
  function reportSize() {
    notify("ui/notifications/size-changed", {
      width: document.documentElement.scrollWidth,
      height: collapsed
        ? element("topbar").offsetHeight
        : Math.min(document.documentElement.scrollHeight, TUNING.expandedMaxHeightPx)
    });
  }

  /**
   * Collapse to the header status line, or expand back to the transcript.
   * @param {boolean} value
   */
  function setCollapsed(value) {
    collapsed = value;
    document.body.classList.toggle("collapsed", collapsed);
    const toggle = element("toggle");
    toggle.textContent = collapsed ? "▸" : "▾";
    toggle.title = collapsed ? "Expand" : "Collapse to status line";
    // The glyph is the button's only content, so it would otherwise be read
    // out as the button's name.
    toggle.setAttribute("aria-label", toggle.title);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    if (!collapsed) {
      unseen = 0;
      renderBadge();
      const container = element("messages");
      container.scrollTop = container.scrollHeight;
    }
    reportSize();
  }

  /** The unseen-messages pill, visible only while collapsed. */
  function renderBadge() {
    const badge = element("badge");
    badge.hidden = !collapsed || unseen === 0;
    badge.textContent = unseen > 99 ? "99+" : String(unseen);
  }

  function renderDiagnostics() {
    const diagnostics = element("events");
    const live = transport.role === "follower" || transport.websocket_connected;
    const wakes = `${wakeCount} wakes${lastWake ? ` · ${lastWake}` : ""}`;
    diagnostics.textContent = live
      ? `WebSocket · ${transport.role || "starting"} · ${eventCount} events · ${wakes}`
      : `WebSocket down · polling · ${wakes}`;
    diagnostics.classList.toggle("warn", !live);
    diagnostics.title = JSON.stringify({
      transport,
      mcpHost,
      hostInfo: host.info,
      hostCapabilities: host.capabilities
    });
  }

  /**
   * @param {string|undefined} name
   * @returns {string}
   */
  function initials(name) {
    return String(name || "?").split(/\s+/).slice(0, 2)
      .map(part => part[0] || "").join("").toUpperCase();
  }

  /**
   * @param {string|undefined} value
   * @returns {string}
   */
  function formatTime(value) {
    const date = new Date(value ?? "");
    return Number.isNaN(date.valueOf()) ? "" : date.toLocaleTimeString([], {
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
  }

  /**
   * @param {RoomMessage} message
   * @returns {HTMLElement}
   */
  function messageRow(message) {
    const row = document.createElement("article");
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = initials(message.sender_name || message.sender_type);
    const body = document.createElement("div");
    const meta = document.createElement("div");
    meta.className = "meta";
    const sender = document.createElement("span");
    sender.className = "sender";
    sender.textContent = message.sender_name || message.sender_type || "Unknown";
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = formatTime(message.inserted_at);
    meta.append(sender, time);
    if (message.message_type && message.message_type !== "text") {
      const kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = message.message_type;
      meta.appendChild(kind);
    }
    const content = document.createElement("div");
    content.className = "content";
    content.textContent = message.content || "";
    body.append(meta, content);
    row.append(avatar, body);
    return row;
  }

  function render() {
    const container = element("messages");
    const ordered = orderedMessages();
    container.replaceChildren();
    if (!ordered.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No messages in this room.";
      container.appendChild(empty);
    }
    for (const message of ordered) container.appendChild(messageRow(message));
    element("count").textContent = `${ordered.length} messages`;
    container.scrollTop = container.scrollHeight;
    reportSize();
  }

  /**
   * Point the view at a room, discarding whatever belonged to the previous one.
   *
   * The server serves whichever room a call names, so a switch can arrive from
   * a tool input or a payload at any time. Everything below the room id — the
   * transcript, the resume cursor, the briefing, the unread count — is that
   * room's alone, so carrying it across would mix one room's messages into
   * another's display and into the model's context.
   * @param {string|undefined} id
   */
  function enterRoom(id) {
    if (!id || id === chatId) return;
    chatId = id;
    messages.clear();
    participants = [];
    roleBriefing = "";
    cursor = null;
    retryWakes = [];
    unseen = 0;
    element("room").textContent = chatId;
    renderBadge();
    render();
  }

  /**
   * Whether a result still describes the room on screen.
   *
   * A watch call stays outstanding for up to half a minute, so a room switch
   * can land while one is in flight. Its answer describes the room it asked
   * about, and absorbing it would drag both the display and the model's
   * context back there.
   * @param {string|undefined} id
   * @returns {boolean}
   */
  const isActiveRoom = id => !chatId || !id || id === chatId;

  // ── Model context sync ────────────────────────────────────────────────

  /**
   * @param {RoomTranscript} payload
   * @returns {Promise<any>}
   */
  function syncModelContext(payload) {
    const recent = orderedMessages().slice(-TUNING.contextMessages).map(message => ({
      id: message.id,
      sender_id: message.sender_id,
      sender_name: message.sender_name,
      sender_type: message.sender_type,
      message_type: message.message_type,
      content: String(message.content || "").slice(0, TUNING.contextMessageChars),
      inserted_at: message.inserted_at,
      addressed_to_viewer: Boolean(message.addressed_to_viewer)
    }));
    const transcript = recent.map(message =>
      `[${message.inserted_at || "unknown time"}] ` +
      `${message.sender_name || message.sender_type || "Unknown"}: ${message.content}`
    ).join("\n");
    // The briefing is authored server-side so the join summary, this context
    // update, and the monitoring tool all describe the room identically.
    return request("ui/update-model-context", {
      content: [{ type: "text", text: `${roleBriefing}\n\n${transcript}` }],
      structuredContent: {
        band_room: {
          chat_id: chatId,
          joined: true,
          operating_as: viewer,
          participants: participants,
          refreshed_at: payload.refreshed_at,
          messages: recent,
          // Whose turn it is to act is the server's call. A second opinion
          // recomputed from the displayed window would disagree with the tool
          // result the model is acting on.
          pending_requests: payload.pending_requests || []
        }
      }
    });
  }

  // ── Wake accelerator ──────────────────────────────────────────────────

  let wakeCount = 0;
  let lastWake = "";
  /** Wakes the host refused, returned to the server for re-offer. @type {string[]} */
  let retryWakes = [];

  /**
   * Ask the host to start a Claude turn for the server-claimed mentions.
   * Measured on this host: `ui/message` succeeds only with user activation (a
   * click), so an autonomous wake is expected to be rejected — Claude's own
   * monitor loop is the guarantee, and this is an accelerator that fires when
   * activation happens to exist. A rejection is deterministic and is dropped;
   * only a transport failure is re-offered to the server for retry.
   *
   * The message itself is authored server-side and arrives as `wake_prompt`:
   * the view relays model-facing text, it never writes any.
   * @param {RoomMessage[]} wakeRequests
   * @param {string} text
   * @returns {Promise<void>}
   */
  async function wake(wakeRequests, text) {
    const shapes = /** @type {[string, object][]} */ ([
      ["object", { role: "user", content: { type: "text", text } }],
      ["array", { role: "user", content: [{ type: "text", text }] }]
    ]);
    let transportFailure = false;
    for (const [shape, params] of shapes) {
      try {
        const result = await request("ui/message", params);
        if (!result?.isError) {
          wakeCount += 1;
          lastWake = "accepted";
          setStatus("Claude woken");
          renderDiagnostics();
          return;
        }
        lastWake = `rejected (${shape})`;
        log("info", { stage: "wake", shape, result });
      } catch (error) {
        transportFailure = true;
        lastWake = `failed (${shape})`;
        log("error", { stage: "wake", shape, detail: String(error) });
      }
    }
    if (transportFailure) {
      retryWakes = retryWakes.concat(
        wakeRequests.map(item => String(item.id || "")).filter(Boolean)
      );
    }
    renderDiagnostics();
  }

  // ── Live watch loop ───────────────────────────────────────────────────

  let refreshing = false;
  let watching = false;
  let stopped = false;
  /** @type {Promise<void>} */
  let ingesting = Promise.resolve();

  /**
   * Every payload — join notification, manual refresh, live event — lands here,
   * serialized, so a redelivered payload is merely idempotent.
   * @param {RoomTranscript|null|undefined} payload
   * @returns {Promise<void>}
   */
  function ingest(payload) {
    if (!payload || !Array.isArray(payload.messages)) return ingesting;
    ingesting = ingesting
      .then(() => absorb(payload))
      .catch(error => log("error", { stage: "ingest", detail: String(error) }));
    return ingesting;
  }

  /**
   * Copy a payload's room facts into view state.
   * @param {RoomTranscript} payload
   * @returns {number} how many new messages it carried
   */
  function absorbState(payload) {
    enterRoom(payload.chat_id);
    viewer = payload.viewer || viewer;
    participants = payload.participants || participants;
    transport = payload.transport || transport;
    mcpHost = payload.host || mcpHost;
    roleBriefing = payload.role_briefing || roleBriefing;
    // The cursor only moves forward. A refresh started before an event can
    // answer after the watch that saw it, and rewinding to its older cursor
    // would have the server re-read and redeliver what is already displayed.
    if (payload.next_since && payload.next_since > (cursor || "")) {
      cursor = payload.next_since;
    }
    return merge(payload.messages);
  }

  /**
   * Reflect current state in the transcript, badge, and status line.
   * @param {number} fresh
   * @param {string|undefined} refreshedAt
   */
  function renderAll(fresh, refreshedAt) {
    element("room").textContent = chatId;
    if (fresh || messages.size === 0) render();
    if (collapsed && fresh) {
      unseen += fresh;
      renderBadge();
    }
    renderDiagnostics();
    element("updated").textContent = formatTime(refreshedAt);
  }

  /**
   * One payload, in order: state, display, model context, wake.
   * @param {RoomTranscript} payload
   * @returns {Promise<void>}
   */
  async function absorb(payload) {
    if (!isActiveRoom(payload.chat_id)) return;
    renderAll(absorbState(payload), payload.refreshed_at);
    try {
      await syncModelContext(payload);
      setStatus("Live · synced");
    } catch (error) {
      setStatus("Context sync failed", true);
      log("error", { stage: "update-model-context", detail: String(error) });
    }
    // The wake decision is the server's (its ledger hands each mention out
    // exactly once); a failed context sync must not swallow it.
    const wakes = Array.isArray(payload.wake_requests) ? payload.wake_requests : [];
    if (wakes.length && payload.wake_prompt) await wake(wakes, payload.wake_prompt);
  }

  /** @returns {Promise<void>} */
  async function refresh() {
    if (!chatId || refreshing) return;
    refreshing = true;
    setStatus("Refreshing");
    try {
      /** @type {ToolResult} */
      const result = await request("tools/call", {
        name: "band_refresh_room_view",
        arguments: { chat_id: chatId, since: cursor }
      });
      if (result?.isError) throw new Error("Refresh failed");
      await ingest(toolPayload(result));
    } catch (error) {
      setStatus("Refresh failed", true);
      log("error", { stage: "refresh", detail: String(error) });
    } finally {
      refreshing = false;
    }
  }

  /** @returns {Promise<void>} */
  async function watchRoom() {
    if (!chatId || watching || stopped) return;
    watching = true;
    const offered = retryWakes.splice(0);
    try {
      /** @type {ToolResult} */
      const result = await request("tools/call", {
        name: "band_wait_for_room_event",
        arguments: { chat_id: chatId, since: cursor, retry_wakes: offered }
      }, TUNING.maxWatchS * 1000 + TUNING.requestTimeoutMs);
      if (result?.isError) throw new Error("Room event wait failed");
      const payload = toolPayload(result);
      if (payload?.event_received) eventCount += 1;
      renderDiagnostics();
      if (!stopped) await ingest(payload);
    } catch (error) {
      retryWakes = offered.concat(retryWakes);
      if (!stopped) {
        setStatus("Live event wait failed", true);
        log("error", { stage: "wait-for-room-event", detail: String(error) });
      }
    } finally {
      watching = false;
      if (!stopped) window.setTimeout(watchRoom, TUNING.watchRestartDelayMs);
    }
  }

  // ── Host wiring ───────────────────────────────────────────────────────

  window.addEventListener("message", event => {
    if (event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== "2.0") return;
    if (Object.prototype.hasOwnProperty.call(message, "id") && !message.method) {
      const waiter = pending.get(message.id);
      if (!waiter) return;
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result);
      return;
    }
    if (message.method === "ui/notifications/tool-input") {
      enterRoom(message.params?.arguments?.chat_id);
    } else if (message.method === "ui/notifications/tool-result") {
      ingest(toolPayload(message.params)).then(watchRoom);
    } else if (message.method === "ui/resource-teardown") {
      stopped = true;
      notify("ui/notifications/teardown-complete");
    }
  });

  // The wait tool reads REST before it blocks, so restarting the watch loop
  // already covers everything a refresh would have fetched.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) watchRoom();
  });
  element("refresh").addEventListener("click", refresh);
  element("toggle").addEventListener("click", () => setCollapsed(!collapsed));
  element("topbar").addEventListener("click", event => {
    if (collapsed && event.target === event.currentTarget) setCollapsed(false);
  });

  request("ui/initialize", {
    appInfo: { name: "Band room", version: "1.0.0" },
    appCapabilities: {},
    protocolVersion: "2026-01-26"
  }).then(result => {
    host = {
      info: result?.hostInfo || null,
      capabilities: result?.hostCapabilities || {}
    };
    notify("ui/notifications/initialized");
    setStatus("Waiting for room");
    renderDiagnostics();
    log("info", { stage: "initialized", host });
  }).catch(error => {
    setStatus("Could not connect", true);
    console.log("[band-room-view] initialize failed", error);
  });
})();
