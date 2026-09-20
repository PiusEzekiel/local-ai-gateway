/**
 * 8C — presentation-only sync state. The API remains authoritative: an SSE
 * notification is NEVER interpreted as proof that a job completed or that the
 * gateway is healthy. No payload, credential or prompt enters this module.
 */
const LABELS = {
  live: "Live", connecting: "Connecting…", retrying: "Polling fallback",
  unauthorized: "Sign in required", stopped: "Paused",
};
const DESCRIPTIONS = {
  live: "Live notifications are connected. API snapshots verify the displayed data.",
  connecting: "Connecting to live updates. Regular API snapshots remain available.",
  retrying: "The event stream is unavailable. Dashboard data refreshes every 15 seconds while the tab is visible.",
  unauthorized: "Authentication is required before live updates can resume.",
  stopped: "Live updates are paused while this tab is hidden or disconnected.",
};
const EVENTS = {
  ready: "Live connection established", resync_required: "Resynchronized",
  "job.created": "Job created", "job.updated": "Job updated", "job.usage": "Usage recorded",
  "workers.changed": "Workers changed", "model.changed": "Model changed",
  "quota.changed": "Quota changed", "storage.changed": "Storage changed",
};

export function ageLabel(timestamp, now = Date.now()) {
  if (!Number.isFinite(timestamp)) return "Not yet";
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  if (seconds < 5) return "Just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

export function createSyncStatus({badge, toggle, panel, description, snapshot, event,
  transport, close, refresh, onRefresh = () => {}, now = () => Date.now()}) {
  let mode = "stopped";
  let apiConnected = false;
  let lastSnapshot = null;
  let lastEvent = null;
  let eventName = "";
  let opened = false;

  function paint() {
    const age = lastSnapshot === null ? null : Math.max(0, now() - lastSnapshot);
    const stale = Boolean(apiConnected && age !== null && age > 120000);
    const label = stale && mode === "live" ? "Live · stale data" : (LABELS[mode] || "Syncing");
    badge.textContent = label;
    badge.className = `live-status ${mode}${stale ? " stale" : ""}`;
    badge.title = stale ? "The last successful gateway snapshot is over 2 minutes old" : DESCRIPTIONS[mode];
    toggle.title = badge.title + ". Open sync details.";
    description.textContent = !apiConnected && lastSnapshot !== null
      ? "The last gateway API request failed. Displayed data may be old."
      : stale ? "The gateway snapshot is stale. Live transport alone does not confirm fresh data."
        : DESCRIPTIONS[mode];
    snapshot.textContent = lastSnapshot === null ? "Waiting for data" : ageLabel(lastSnapshot, now());
    event.textContent = lastEvent === null ? "No events yet" : `${eventName} · ${ageLabel(lastEvent, now())}`;
    transport.textContent = !apiConnected && lastSnapshot !== null ? `${LABELS[mode]} · API unavailable` : (LABELS[mode] || "Syncing");
  }
  function hide() {
    opened = false;
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Show live synchronization details");
  }
  function show() {
    opened = true;
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Hide live synchronization details");
    paint();
  }
  toggle.addEventListener("click", () => opened ? hide() : show());
  close.addEventListener("click", () => {hide();toggle.focus();});
  refresh.addEventListener("click", () => {hide();onRefresh();});
  document.addEventListener("pointerdown", e => {
    if (opened && !panel.contains(e.target) && !toggle.contains(e.target)) hide();
  });
  document.addEventListener("keydown", e => {
    if (opened && e.key === "Escape") {hide();toggle.focus();}
  });
  paint();
  return {
    setTransport(next) { mode = next; paint(); },
    setGateway(value) { apiConnected = Boolean(value); paint(); },
    recordSnapshot() { lastSnapshot = now(); apiConnected = true; paint(); },
    recordEvent(type) {if (Object.hasOwn(EVENTS, type)) {eventName = EVENTS[type];lastEvent = now();paint();}},
    tick: paint,
    hide,
  };
}
