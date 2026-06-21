(() => {
  const caption = document.querySelector(".caption");
  const committedEl = document.querySelector(".committed");
  const partialEl = document.querySelector(".partial");

  let ws = null;
  let reconnectTimer = null;
  let retryMs = 250;
  let lastCommitted = "";
  let lastPartial = "";

  function maxLines() {
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--cap-max-lines");
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 3;
  }

  function websocketUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/ws`;
  }

  function clearReconnect() {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function scheduleReconnect() {
    clearReconnect();
    reconnectTimer = window.setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 5000);
  }

  function setUpdatedFlag() {
    caption.dataset.updated = "false";
    window.requestAnimationFrame(() => {
      caption.dataset.updated = "true";
    });
  }

  function renderCommitted(lines) {
    const visible = lines.slice(-maxLines());
    const nextCommitted = visible.join("\n");
    if (nextCommitted === lastCommitted) return;

    committedEl.replaceChildren();
    visible.forEach((line, index) => {
      if (index > 0) committedEl.appendChild(document.createElement("br"));
      committedEl.appendChild(document.createTextNode(line));
    });
    lastCommitted = nextCommitted;
    setUpdatedFlag();
  }

  function renderPartial(text) {
    if (text === lastPartial) return;
    partialEl.textContent = text;
    lastPartial = text;
    setUpdatedFlag();
  }

  function renderCaption(message) {
    if (message.type !== "caption") return;
    const committed = Array.isArray(message.committed) ? message.committed : [];
    const partial = typeof message.partial === "string" ? message.partial : "";
    renderCommitted(committed);
    renderPartial(partial);
    caption.dataset.empty = committed.length === 0 && partial === "" ? "true" : "false";
  }

  function connect() {
    clearReconnect();
    ws = new WebSocket(websocketUrl());

    ws.addEventListener("open", () => {
      retryMs = 250;
    });

    ws.addEventListener("message", (event) => {
      try {
        renderCaption(JSON.parse(event.data));
      } catch (_error) {
        // Ignore malformed messages and keep the overlay alive.
      }
    });

    ws.addEventListener("close", scheduleReconnect);
    ws.addEventListener("error", () => {
      if (ws !== null) ws.close();
    });
  }

  window.addEventListener("beforeunload", () => {
    clearReconnect();
    if (ws !== null) ws.close();
  });

  connect();
})();
