/**
 * Module 8B — authenticated, bounded SSE reader for /dashboard/api/events.
 *
 * EventSource cannot carry the gateway Bearer header. Only api.js opens the
 * stream, using fetch, and no token is placed in a URL or an event payload.
 * Events are hints, NOT authoritative state: the controller must refetch the
 * existing dashboard endpoints after ready/reconnect and resync_required.
 */
import {hasToken, openEventStream} from "./api.js?v=9c2-20260920";

const MAX_FRAME = 128 * 1024; // hard bound in case a buggy proxy omits delimiters
const EVENT_TYPES = new Set([
  "ready", "resync_required", "job.created", "job.updated", "job.usage",
  "workers.changed", "model.changed", "quota.changed", "storage.changed",
]);

/** Incremental UTF-8-decoded SSE parser. Accepts split data/event lines,
 * CRLF, comments, and multiple frames per transport chunk. */
export function createSSEParser(emit) {
  let pending = "";
  let event = "message";
  let data = [];
  function flush() {
    if (data.length && EVENT_TYPES.has(event)) {
      const value = data.join("\n");
      try { emit(event, JSON.parse(value)); }
      catch { /* malformed notification; periodic snapshots still reconcile */ }
    }
    event = "message";
    data = [];
  }
  return {
    push(chunk) {
      pending += chunk;
      if (pending.length > MAX_FRAME) throw new Error("Live notification exceeds size limit");
      let index;
      while ((index = pending.indexOf("\n")) >= 0) {
        let line = pending.slice(0, index);
        pending = pending.slice(index + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        if (line === "") { flush(); continue; }
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const key = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (key === "event") event = value;
        if (key === "data") data.push(value);
      }
      if (data.join("\n").length + pending.length > MAX_FRAME) throw new Error("Live frame exceeds size limit");
    },
    finish() { if (pending) this.push("\n"); flush(); },
  };
}

export function createLiveClient({onEvent, onStatus, onUnauthorized, fetchStream = openEventStream,
  initialDelay = 2000, maximumDelay = 30000} = {}) {
  let running = false;
  let epoch = 0; // invalidates an old loop if stop/start happen within one JS turn
  let controller = null;
  let attempt = 0;
  let status = "stopped";
  let retryTimer = null;
  let retryWake = null;
  function signal(next) {
    if (status !== next) { status = next; onStatus?.(next); }
  }
  async function wait(delay) {
    await new Promise(resolve => {
      retryWake = resolve;
      retryTimer = setTimeout(() => { retryTimer = null; retryWake = null; resolve(); }, delay);
    });
  }
  async function loop(generation) {
    while (running && generation === epoch && hasToken()) {
      const current = new AbortController();
      controller = current;
      signal(attempt ? "retrying" : "connecting");
      try {
        const response = await fetchStream(current.signal);
        if (!running || generation !== epoch || current.signal.aborted) break;
        if (response.status === 401) {
          running = false;
          signal("unauthorized");
          onUnauthorized?.();
          break;
        }
        if (!response.ok || !response.body ||
            !(response.headers.get("Content-Type") || "").toLowerCase().includes("text/event-stream")) {
          throw new Error(`Live stream unavailable (${response.status})`);
        }
        const decoder = new TextDecoder();
        const parser = createSSEParser((type, data) => {
          if (!running || generation !== epoch || current.signal.aborted) return;
          if (type === "ready") { attempt = 0; signal("live"); }
          onEvent?.(type, data);
        });
        const reader = response.body.getReader();
        try {
          while (running && generation === epoch && !current.signal.aborted) {
            const {done, value} = await reader.read();
            if (done) break;
            parser.push(decoder.decode(value, {stream: true}));
          }
          if (running && generation === epoch && !current.signal.aborted) {
            parser.push(decoder.decode());
            parser.finish();
          }
        } finally {
          try { await reader.cancel(); } catch { /* aborted stream */ }
          reader.releaseLock();
        }
      } catch (error) {
        // A disconnect is normal; the polling fallback covers missed events.
        // Do not log request headers, URLs containing credentials, or stream body.
        if (!running || generation !== epoch || current.signal.aborted) break;
      } finally {
        if (controller === current) controller = null;
      }
      if (!running || generation !== epoch) break;
      attempt += 1;
      signal("retrying");
      await wait(Math.min(maximumDelay, initialDelay * 2 ** Math.min(10, attempt - 1)));
    }
  }
  return {
    start() {
      if (running || !hasToken()) return;
      running = true;
      attempt = 0;
      void loop(++epoch);
    },
    stop() {
      running = false;
      epoch += 1;
      controller?.abort();
      controller = null;
      if (retryTimer !== null) clearTimeout(retryTimer);
      retryTimer = null;
      retryWake?.();
      retryWake = null;
      signal("stopped");
    },
    get status() { return status; },
  };
}
