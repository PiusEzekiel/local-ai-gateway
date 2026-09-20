import {getGallery, getJobs, getPerformance, getSummary, getUsage, hasToken, setToken, setUnauthorizedHandler} from "./api.js?v=9c1-20260920";
import {state, update} from "./state.js?v=9c1-20260920";
import {compact, duration, renderFeed, selectJob} from "./jobs.js?v=9c1-20260920";
import {abortGalleryMedia, closeLightbox, initializeLightbox, renderGallery} from "./gallery.js?v=9c1-20260920";
import {clearAnalyticsError, renderAnalyticsError, renderPerformance, renderUsage} from "./analytics.js?v=9c1-20260920";
import {initializeDiagnostics, loadDiagnostics, inspectDiagnosticJob} from "./diagnostics.js?v=9c1-20260920";
import {initializeSettings, loadSettings, settingsHasUnsavedChanges, discardSettingsChanges, refreshSettingsView} from "./settings.js?v=9c1-20260920";
import {createLiveClient} from "./live.js?v=9c1-20260920";
import {createSyncStatus} from "./sync_status.js?v=9c1-20260920";

const $ = id => document.getElementById(id);
// Module 9B.5 — a display-only preference stored in this browser tab.
// Collapsing the navigation never stops streaming or closes an image preview.
const SIDEBAR_PREF = "gatewaySidebarCollapsed";
function setSidebarCollapsed(collapsed) {
  const isCollapsed = Boolean(collapsed);
  $("appShell").classList.toggle("sidebar-collapsed", isCollapsed);
  const control = $("sidebarToggle");
  control.setAttribute("aria-expanded", String(!isCollapsed));
  control.setAttribute("aria-label", isCollapsed ? "Expand sidebar" : "Collapse sidebar");
  control.title = isCollapsed ? "Expand sidebar" : "Collapse sidebar";
  sessionStorage.setItem(SIDEBAR_PREF, isCollapsed ? "1" : "0");
}
// The transport badge is inspectable, but it NEVER replaces gateway health.
const syncMonitor = createSyncStatus({
  badge: $("liveStatus"), toggle: $("syncDetailsToggle"), panel: $("syncDetails"),
  description: $("syncDescription"), snapshot: $("syncLastSnapshot"),
  event: $("syncLastEvent"), transport: $("syncTransport"),
  close: $("syncDetailsClose"), refresh: $("syncNow"),
  onRefresh() { void refresh({includeJobs: true}); if (state.page === "diagnostics") loadDiagnostics({reset: true, silent: true}); },
});
let refreshTimer;
let countdownTimer;
let searchTimer;
let gallerySearchTimer;
let galleryListController = null;
let galleryRequestGeneration = 0;
let galleryLoadMoreInFlight = false;

function cancelGalleryList() {
  ++galleryRequestGeneration;
  galleryListController?.abort();
  galleryListController = null;
  galleryLoadMoreInFlight = false;
  $("galleryLoadMore").disabled = false;
}
let diagnosticsTimer;
let reconciliationTimer;
let changeTimer;
let queued = {jobs: false, analytics: false, gallery: false, diagnostics: false,
  settings: false, selected: false, full: false};
let lastLiveStatus = "stopped";
let summaryGeneration = 0; // newer API snapshots must never be overwritten by slower older requests
const terminalStatuses = new Set(["completed", "failed"]);


function formatUptime(seconds) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function setConnected(connected, message = "") {
  update({connected});
  syncMonitor.setGateway(connected);
  $("healthDot").className = `status-dot ${connected ? "healthy" : "error"}`;
  $("sideDot").className = `status-dot ${connected ? "healthy" : "error"}`;
  $("healthText").textContent = connected ? "Healthy" : "Disconnected";
  $("sideStatus").textContent = connected ? "Gateway healthy" : (message || "Connection lost");
}

function quotaWindowName(window, fallback) {
  const minutes = window.window_duration_mins;
  if (minutes === null || minutes === undefined) return fallback;
  if (minutes < 60) return `${minutes}m window`;
  if (minutes % 1440 === 0) return `${minutes / 1440}d window`;
  if (minutes % 60 === 0) return `${minutes / 60}h window`;
  return `${minutes}m window`;
}

function relativeResetTime(resetsAt) {
  if (!Number.isFinite(resetsAt)) return "Reset time unavailable";
  const seconds = Math.max(0, Math.floor(resetsAt - Date.now() / 1000));
  if (!seconds) return "Due now";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (days) return `In ${days}d ${hours}h`;
  if (hours) return `In ${hours}h ${minutes}m`;
  if (minutes) return `In ${minutes}m ${secs}s`;
  return `In ${secs}s`;
}

function resetSchedule(resetsAt) {
  if (!Number.isFinite(resetsAt)) return "Reset time unavailable";
  const resetAt = new Date(resetsAt * 1000);
  if (Number.isNaN(resetAt.getTime())) return "Reset time unavailable";
  const formatted = new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(resetAt);
  return `Resets ${formatted}`;
}

function updateQuotaCountdowns() {
  document.querySelectorAll("[data-reset-at]").forEach(node => {
    const resetsAt = Number(node.dataset.resetAt);
    const value = resetSchedule(resetsAt);
    node.textContent = node.dataset.quotaLeft ? `${node.dataset.quotaLeft} · ${value}` : value;
    node.title = relativeResetTime(resetsAt);
  });
}

function renderQuota(quota) {
  const status = quota?.status || "unavailable";
  const buckets = Array.isArray(quota?.buckets) ? quota.buckets : [];
  const windows = buckets.flatMap(bucket => [bucket.primary, bucket.secondary].filter(Boolean));
  const mostConstrained = windows.reduce((best, item) => (
    !best || item.remaining_percent < best.remaining_percent ? item : best
  ), null);
  const notice = $("quotaNotice");
  notice.className = `notice quota-notice ${status}`;
  $("quotaTitle").textContent = status === "healthy" ? "Codex quota healthy" :
    status === "warning" ? "Codex quota getting low" :
    status === "critical" ? "Codex quota critical" :
    status === "exhausted" ? "Codex quota exhausted" : "Quota telemetry unavailable";
  $("quotaSubtitle").textContent = status === "unavailable" ?
    "Generation remains available; no missing windows are inferred." :
    `${buckets.length} reported quota bucket${buckets.length === 1 ? "" : "s"}`;
  $("quotaObserved").textContent = quota?.observed_at ?
    `Observed ${new Date(quota.observed_at).toLocaleTimeString()}` : "Not observed";
  const top = $("topQuota");
  if (mostConstrained) {
    const left = `${mostConstrained.remaining_percent}% left`;
    top.dataset.quotaLeft = left;
    if (Number.isFinite(mostConstrained.resets_at)) top.dataset.resetAt = String(mostConstrained.resets_at);
    else delete top.dataset.resetAt;
    top.textContent = Number.isFinite(mostConstrained.resets_at) ? `${left} · ${resetSchedule(mostConstrained.resets_at)}` : left;
  } else {
    delete top.dataset.quotaLeft;
    delete top.dataset.resetAt;
    top.textContent = status === "unavailable" ? "Unavailable" : status;
  }

  const container = $("quotaBuckets");
  container.replaceChildren();
  buckets.forEach(bucket => {
    const card = document.createElement("div"); card.className = "quota-bucket";
    const heading = document.createElement("div"); heading.className = "quota-bucket-head";
    const name = document.createElement("strong"); name.textContent = bucket.limit_name || bucket.limit_id;
    const plan = document.createElement("span"); plan.textContent = bucket.plan_type || "Plan unavailable";
    heading.append(name, plan); card.append(heading);
    const reported = [["Primary", bucket.primary], ["Secondary", bucket.secondary]].filter(([, value]) => value);
    if (!reported.length) {
      const missing = document.createElement("span"); missing.className = "quota-missing"; missing.textContent = "Window data unavailable"; card.append(missing);
    }
    reported.forEach(([fallback, window]) => {
      const row = document.createElement("div"); row.className = "quota-window";
      const labels = document.createElement("div"); labels.className = "quota-window-labels";
      const label = document.createElement("span"); label.textContent = quotaWindowName(window, fallback);
      const value = document.createElement("strong"); value.textContent = `${window.remaining_percent}% left`;
      labels.append(label, value);
      const track = document.createElement("div"); track.className = "quota-track";
      const bar = document.createElement("span"); bar.style.width = `${Math.max(0, Math.min(100, window.remaining_percent))}%`; track.append(bar);
      const reset = document.createElement("small");
      if (Number.isFinite(window.resets_at)) reset.dataset.resetAt = String(window.resets_at);
      reset.textContent = resetSchedule(window.resets_at);
      row.append(labels, track, reset); card.append(row);
    });
    container.append(card);
  });
  const resetCredits = quota?.reset_credits?.available_count;
  if (Number.isInteger(resetCredits)) {
    const credit = document.createElement("div"); credit.className = "quota-credit";
    credit.textContent = `${resetCredits} earned reset credit${resetCredits === 1 ? "" : "s"} available`;
    container.append(credit);
  }
  updateQuotaCountdowns();
}

function renderSummary(summary) {
  const {gateway, workers, today} = summary;
  $("topModel").textContent = gateway.model;
  $("topRunning").textContent = `${workers.active} / ${workers.concurrency}`;
  $("topQueued").textContent = `${workers.queued} / ${workers.max_queue}`;
  $("topUptime").textContent = formatUptime(gateway.uptime_seconds);
  $("metricJobs").textContent = compact(today.jobs);
  $("metricSuccess").textContent = today.success_rate === null ? "—" : `${today.success_rate}%`;
  $("metricFailures").textContent = `${today.failed} failures`;
  $("metricTokens").textContent = compact(today.total_tokens);
  $("metricAverage").textContent = duration(today.average_latency_ms);
  $("metricP95").textContent = `P95 ${duration(today.p95_latency_ms)}`;
  $("metricCompute").textContent = duration(today.codex_compute_ms);
  $("metricCumulative").textContent = `Cumulative ${duration(today.cumulative_task_ms)}`;
  $("metricImages").textContent = compact(today.images);
  $("workerActive").textContent = String(workers.active);
  $("workerCapacity").textContent = String(workers.concurrency);
  $("workerQueue").textContent = `${workers.queued} / ${workers.max_queue}`;
  $("updatedAt").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  renderQuota(summary.quota);
  const failures = $("recentFailures");
  failures.replaceChildren();
  if (!today.recent_failures.length) failures.append(Object.assign(document.createElement("div"), {className: "empty-state", textContent: "No failures today"}));
  today.recent_failures.forEach(failure => {
    const item = document.createElement("button"); item.type = "button"; item.className = "failure-item";
    item.title = "Inspect this failure in Diagnostics";
    item.addEventListener("click", () => { go("diagnostics"); inspectDiagnosticJob(failure.id); });
    const title = document.createElement("strong"); title.textContent = failure.request_id;
    const detail = document.createElement("span"); detail.textContent = failure.error_type || "Unknown failure";
    item.append(title, detail); failures.append(item);
  });
}

// Module 9B.4 — this is a *browser-tab* disconnect, not bearer-token
// rotation and not a gateway shutdown. A reload discards retained job details,
// blob URLs and module-local state that cannot safely be cleared individually.
let sessionClosing = false;
function disconnectSession({expired = false} = {}) {
  if (sessionClosing) return;
  if (!expired && settingsHasUnsavedChanges() &&
      !window.confirm("Disconnect and discard unsaved settings changes?")) return;
  sessionClosing = true;
  ++summaryGeneration; // an outstanding snapshot must not reveal the old view
  clearTimeout(searchTimer);
  clearTimeout(gallerySearchTimer);
  clearTimeout(changeTimer);
  liveClient.stop();
  syncMonitor.hide();
  closeLightbox();
  cancelGalleryList();
  abortGalleryMedia(); // abort authenticated images and revoke blob URLs
  setToken("");       // clear both the module-local token and sessionStorage
  $("token").value = "";
  $("appShell").hidden = true;
  $("authScreen").hidden = false;
  $("authError").textContent = expired ?
    "The gateway token is no longer valid. Connect again." : "";
  if (expired) sessionStorage.setItem("gatewayAuthNotice", "expired");
  else sessionStorage.removeItem("gatewayAuthNotice");
  window.location.reload();
}

async function refresh({showAuthError = false, includeJobs = true} = {}) {
  if (!hasToken()) return;
  const generation = ++summaryGeneration;
  try {
    const [summary, jobs] = await Promise.all([getSummary(), includeJobs ? getJobs({limit: 50}) : Promise.resolve(null)]);
    if (generation !== summaryGeneration) return; // discard stale in-flight results
    update(jobs ? {summary, recentJobs: jobs.jobs} : {summary});
    renderSummary(summary);
    if (jobs) {
      const feed = $("recentJobs");
      const scroll = feed.scrollTop;
      renderFeed(feed, jobs.jobs.slice(0, 25));
      feed.scrollTop = scroll;
    }
    syncMonitor.recordSnapshot();
    setConnected(true);
    $("authScreen").hidden = true;
    $("appShell").hidden = false;
    $("authError").textContent = "";
    if (!document.hidden) liveClient.start();
  } catch (error) {
    if (generation !== summaryGeneration) return;
    setConnected(false, error.message);
    if (error.status === 401) {
      disconnectSession({expired: true});
      return;
    }
    if (showAuthError) {
      $("authScreen").hidden = false;
      $("appShell").hidden = true;
      $("authError").textContent = error.message;
    }
  }
}

function go(page) {
  if (state.page === "settings" && page !== "settings" && settingsHasUnsavedChanges()) {
    if (!window.confirm("Discard your unsaved settings changes?")) return;
    discardSettingsChanges();
  }
  closeLightbox(); // keep sidebar navigation usable while a Gallery image is open
  if (state.page === "gallery") {
    clearTimeout(gallerySearchTimer);
    cancelGalleryList();
    abortGalleryMedia();
  }
  update({page});
  document.querySelectorAll(".page").forEach(node => node.classList.toggle("active", node.id === `page-${page}`));
  document.querySelectorAll(".nav-item").forEach(node => {
    const active = node.dataset.page === page;
    node.classList.toggle("active", active);
    if (active) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  if (page === "jobs") loadJobs(true);
  if (page === "gallery") loadGallery(true);
  if (page === "usage") loadUsage();
  if (page === "performance") loadPerformance();
  if (page === "diagnostics") loadDiagnostics({reset: true});
  if (page === "settings") loadSettings();
}

function analyticsQuery() {
  return {range: state.analyticsRange, start: state.analyticsStart, end: state.analyticsEnd};
}

async function loadUsage() {
  clearAnalyticsError("usage");
  try { renderUsage(await getUsage(analyticsQuery())); }
  catch (error) { renderAnalyticsError("usage", error.message); }
}

async function loadPerformance() {
  clearAnalyticsError("performance");
  try { renderPerformance(await getPerformance(analyticsQuery())); }
  catch (error) { renderAnalyticsError("performance", error.message); }
}

function syncRangeButtons() {
  document.querySelectorAll("[data-analytics-range]").forEach(button => {
    button.classList.toggle("active", button.dataset.analyticsRange === state.analyticsRange);
  });
}

function loadCurrentAnalytics() {
  if (state.page === "usage") loadUsage();
  if (state.page === "performance") loadPerformance();
}

async function loadJobs(reset = false) {
  const query = {limit: 50, status: $("jobStatus").value, task: $("jobTask").value, search: $("jobSearch").value.trim()};
  if (!reset && state.nextCursor) query.cursor = state.nextCursor;
  try {
    const data = await getJobs(query);
    const allJobs = reset ? data.jobs : [...state.allJobs, ...data.jobs];
    update({allJobs, nextCursor: data.next_cursor});
    const feed = $("allJobs");
    const scroll = feed.scrollTop;
    renderFeed(feed, allJobs, state.selectedJobId);
    feed.scrollTop = scroll;
    $("loadMore").hidden = !data.next_cursor;
  } catch (error) {
    $("allJobs").textContent = error.message;
  }
}

async function loadGallery(reset = false) {
  if (!hasToken() || state.page !== "gallery") return;
  // Prevent a repeated Load more click from inserting the same page twice.
  if (!reset && (galleryLoadMoreInFlight || !state.galleryCursor)) return;
  cancelGalleryList(); // abort prior reset/load-more before starting the next query
  const generation = galleryRequestGeneration;
  const controller = new AbortController();
  galleryListController = controller;
  if (!reset) galleryLoadMoreInFlight = true;
  $("galleryLoadMore").disabled = true;
  const query = {
    limit: 48, category: state.galleryCategory,
    search: $("gallerySearch").value.trim(),
  };
  if (!reset) query.cursor = state.galleryCursor;
  try {
    const data = await getGallery(query, {signal: controller.signal});
    if (controller.signal.aborted || generation !== galleryRequestGeneration || state.page !== "gallery") return;
    const galleryItems = reset ? data.items : [...state.galleryItems, ...data.items];
    update({galleryItems, galleryCursor: data.next_cursor});
    renderGallery($("galleryGrid"), galleryItems);
    $("galleryLoadMore").hidden = !data.next_cursor;
    $("galleryCount").textContent = `${galleryItems.length} shown`;
  } catch (error) {
    if (controller.signal.aborted || generation !== galleryRequestGeneration || state.page !== "gallery") return;
    $("galleryGrid").textContent = error.message;
  } finally {
    if (generation === galleryRequestGeneration) {
      galleryListController = null;
      galleryLoadMoreInFlight = false;
      $("galleryLoadMore").disabled = false;
    }
  }
}

// The SSE stream carries hints, never the source-of-truth job record. Coalesce
// storms into a single authoritative GET; hidden views do not fetch or repaint.
function queueReconcile(flags = {}) {
  for (const [key, value] of Object.entries(flags)) if (value) queued[key] = true;
  if (changeTimer !== undefined) return;
  changeTimer = setTimeout(async () => {
    changeTimer = undefined;
    if (!hasToken() || document.hidden) return;
    const pending = queued;
    queued = {jobs: false, analytics: false, gallery: false, diagnostics: false,
      settings: false, selected: false, full: false};
    await refresh({includeJobs: pending.jobs || pending.full});
    if (pending.jobs && state.page === "jobs" && state.allJobs.length <= 50) loadJobs(true);
    if (pending.selected && state.page === "jobs" && state.selectedJobId) selectJob(state.selectedJobId);
    if (pending.gallery && state.page === "gallery" && state.galleryItems.length <= 48) loadGallery(true);
    if (pending.analytics) loadCurrentAnalytics();
    if (pending.diagnostics && state.page === "diagnostics") loadDiagnostics({reset: true, silent: true});
    if (pending.settings && state.page === "settings" && !settingsHasUnsavedChanges()) loadSettings();
  }, 180);
}

function showLiveStatus(next) {
  lastLiveStatus = next;
  syncMonitor.setTransport(next);
}

const liveClient = createLiveClient({
  onStatus: showLiveStatus,
  onUnauthorized() {
    // Stop retrying an expired token and wipe the browser tab's old data.
    disconnectSession({expired: true});
  },
  onEvent(type, data) {
    syncMonitor.recordEvent(type);
    if (type === "ready" || type === "resync_required") {
      queueReconcile({full: true, jobs: true, gallery: true, analytics: true, diagnostics: true, settings: true});
      return;
    }
    if (type === "quota.changed" || type === "workers.changed" || type === "model.changed") {
      queueReconcile({}); // summary only; avoid unnecessary gallery and job requests
      if (type === "model.changed" && state.page === "settings" && !settingsHasUnsavedChanges())
        queueReconcile({settings: true});
      return;
    }
    if (type === "storage.changed") {
      queueReconcile({gallery: true, diagnostics: true, settings: true, jobs: true});
      return;
    }
    if (type.startsWith("job.")) {
      const terminal = terminalStatuses.has(data?.status);
      const selected = Boolean(data?.job_id && data.job_id === state.selectedJobId);
      queueReconcile({jobs: true, selected, gallery: terminal, diagnostics: data?.status === "failed",
        analytics: terminal || type === "job.usage"});
    }
  },
});

// Live -> one reconciliation each minute. Offline -> 15-second fallback, not
// the old unconditional 5-second requests. Visibility resumes with a snapshot.
function pollingInterval() {
  if (!hasToken() || document.hidden) return;
  if (lastLiveStatus !== "live") refresh();
}

$("sidebarToggle").addEventListener("click", () =>
  setSidebarCollapsed(!$("appShell").classList.contains("sidebar-collapsed")));
$("disconnectButton").addEventListener("click", () => disconnectSession());
$("authForm").addEventListener("submit", event => {
  event.preventDefault();
  liveClient.stop();
  setToken($("token").value);
  refresh({showAuthError: true});
});
document.querySelectorAll(".nav-item").forEach(node => node.addEventListener("click", () => go(node.dataset.page)));
document.querySelectorAll("[data-go]").forEach(node => node.addEventListener("click", () => go(node.dataset.go)));
$("refreshButton").addEventListener("click", () => { refresh(); if (state.page === "diagnostics") loadDiagnostics({reset: true, silent: true}); if (state.page === "settings") refreshSettingsView(); });
$("loadMore").addEventListener("click", () => loadJobs(false));
$("galleryLoadMore").addEventListener("click", () => loadGallery(false));
[$("jobStatus"), $("jobTask")].forEach(node => node.addEventListener("change", () => loadJobs(true)));
$("jobSearch").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadJobs(true), 250); });
$("gallerySearch").addEventListener("input", () => {
  cancelGalleryList(); // invalidate immediately, not after the search debounce
  clearTimeout(gallerySearchTimer);
  gallerySearchTimer = setTimeout(() => loadGallery(true), 250);
});
document.querySelectorAll("[data-gallery-category]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll("[data-gallery-category]").forEach(node => node.classList.toggle("active", node === button));
  cancelGalleryList();
  update({galleryCategory: button.dataset.galleryCategory});
  loadGallery(true);
}));
document.querySelectorAll("[data-analytics-range]").forEach(button => button.addEventListener("click", () => {
  const toolbar = button.closest(".analytics-toolbar");
  const custom = toolbar.querySelector(".custom-range");
  if (button.dataset.analyticsRange === "custom") {
    custom.hidden = false;
    return;
  }
  document.querySelectorAll(".custom-range").forEach(node => { node.hidden = true; });
  update({analyticsRange: button.dataset.analyticsRange, analyticsStart: "", analyticsEnd: ""});
  syncRangeButtons(); loadCurrentAnalytics();
}));
document.querySelectorAll("[data-custom-apply]").forEach(button => button.addEventListener("click", () => {
  const toolbar = button.closest(".analytics-toolbar");
  const start = toolbar.querySelector("[data-custom-start]").value;
  const end = toolbar.querySelector("[data-custom-end]").value;
  if (!start || !end) return renderAnalyticsError(toolbar.dataset.analyticsPage, "Choose both custom timestamps.");
  update({analyticsRange: "custom", analyticsStart: new Date(start).toISOString(), analyticsEnd: new Date(end).toISOString()});
  syncRangeButtons(); loadCurrentAnalytics();
}));
document.addEventListener("gallery:inspect-job", event => { go("jobs"); selectJob(event.detail); });
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  const pages = ["overview", "jobs", "gallery", "usage", "performance", "diagnostics", "settings"];
  const index = Number(event.key) - 1;
  if (pages[index]) go(pages[index]);
});

// Only a constant reason flag survives an expired session — never the token.
if (sessionStorage.getItem("gatewayAuthNotice") === "expired")
  $("authError").textContent = "The gateway token is no longer valid. Connect again.";
sessionStorage.removeItem("gatewayAuthNotice");
setSidebarCollapsed(sessionStorage.getItem(SIDEBAR_PREF) === "1");
$("token").value = sessionStorage.getItem("gatewayToken") || "";
document.querySelectorAll("[data-custom-end]").forEach(node => { node.value = new Date().toISOString().slice(0, 16); });
document.querySelectorAll("[data-custom-start]").forEach(node => { node.value = new Date(Date.now() - 86400000).toISOString().slice(0, 16); });
initializeLightbox();
initializeDiagnostics();
initializeSettings();
setUnauthorizedHandler(() => disconnectSession({expired: true}));
if (hasToken()) refresh({showAuthError: true});
refreshTimer = setInterval(pollingInterval, 15000);
reconciliationTimer = setInterval(() => {
  if (hasToken() && lastLiveStatus === "live" && !document.hidden) {
    queueReconcile({full: true, jobs: true});
  }
}, 60000);
countdownTimer = setInterval(() => { updateQuotaCountdowns(); syncMonitor.tick(); }, 1000);
// Preserve feed scroll and inspector selection; do not refresh hidden diagnostics pages.
diagnosticsTimer = setInterval(() => {
  if (hasToken() && state.connected && state.page === "diagnostics" && !document.hidden && lastLiveStatus !== "live") {
    loadDiagnostics({reset: true, silent: true});
  }
}, 30000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    liveClient.stop();
    syncMonitor.hide();
  } else if (hasToken()) {
    queueReconcile({full: true, jobs: true});
    if (state.page === "diagnostics") loadDiagnostics({reset: true, silent: true});
    liveClient.start();
  }
});
window.addEventListener("beforeunload", () => {
  cancelGalleryList();
  liveClient.stop();
  clearInterval(refreshTimer); clearInterval(countdownTimer); clearInterval(diagnosticsTimer);
  clearInterval(reconciliationTimer); clearTimeout(changeTimer);
});
