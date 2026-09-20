/**
 * Module 6D — read-only Diagnostics workspace.
 *
 * All data comes from the authenticated /dashboard/api/diagnostics endpoints.
 * No raw subprocess output, credentials, prompts, or external reference URLs
 * are retrieved or constructed here. Server-side redaction remains essential.
 */
import {
  getDiagnostics, getDiagnosticsSummary, getDiagnostic,
  getDiagnosticBundle, requestBlob,
} from "./api.js?v=9c1-20260920";
import {compact, duration} from "./jobs.js?v=9c1-20260920";

const $ = id => document.getElementById(id);
const node = (tag, className = "", value) => {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (value !== undefined && value !== null) result.textContent = String(value);
  return result;
};

const view = {
  range: "7d", start: "", end: "", cursor: null,
  failures: [], selectedId: null, listVersion: 0, detailVersion: 0,
  busy: false, initialized: false, objectUrls: [], searchTimer: null,
};

function dateLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function rangeQuery() {
  return {range: view.range, start: view.start, end: view.end};
}

function readQuery() {
  return {
    ...rangeQuery(),
    task: $("diagTask").value,
    operation: $("diagOperation").value,
    model: $("diagModel").value,
    error_type: $("diagErrorType").value,
    search: $("diagSearch").value.trim(),
  };
}

function filtersActive(query) {
  return Boolean(query.task || query.operation || query.model || query.error_type || query.search);
}

function setError(message = "") {
  const error = $("diagError");
  error.hidden = !message;
  error.textContent = message;
}

function setStatus(message) {
  $("diagFilterNote").textContent = message;
}

function syncRange() {
  document.querySelectorAll("[data-diagnostic-range]").forEach(button => {
    button.classList.toggle("active", button.dataset.diagnosticRange === view.range);
    button.setAttribute("aria-pressed", String(button.dataset.diagnosticRange === view.range));
  });
  $("diagCustomRange").hidden = view.range !== "custom";
}

// Rebuild facets using the unfiltered summary. Preserve the current selection
// even when its category is outside the API's bounded top-20 breakdown.
function populateSelect(id, entries, key) {
  const select = $(id);
  const previous = select.value;
  const defaultOption = select.options[0].cloneNode(true);
  select.replaceChildren(defaultOption);
  const options = new Set();
  for (const entry of entries || []) {
    const value = entry?.[key];
    if (typeof value !== "string" || !value || options.has(value)) continue;
    options.add(value);
    const option = document.createElement("option");
    option.value = value;
    option.textContent = `${value} (${entry.failures ?? 0})`;
    select.append(option);
  }
  if (previous && !options.has(previous)) {
    const option = document.createElement("option");
    option.value = previous;
    option.textContent = previous;
    select.append(option);
  }
  select.value = previous;
}

function renderSummary(data, overall, query) {
  const summary = data.summary || {};
  const total = summary.failures ?? 0;
  $("diagTotal").textContent = compact(total);
  $("diagImageCount").textContent = compact(summary.image_failures ?? 0);
  $("diagTextCount").textContent = compact(summary.text_failures ?? 0);
  const mostCommon = (summary.error_types || [])[0];
  $("diagTopError").textContent = mostCommon?.error_type || "—";
  $("diagTopErrorCount").textContent = mostCommon
    ? `${mostCommon.failures} failure${mostCommon.failures === 1 ? "" : "s"} in this category`
    : "No error categories in this selection";
  const period = view.range === "custom" ? "Custom range"
    : view.range === "today" ? "Today · UTC"
      : `Last ${view.range}`;
  $("diagSummaryContext").textContent = `${period}${filtersActive(query) ? " · filtered" : ""}`;
  $("diagResultCount").textContent = `${compact(total)} total`;
  populateSelect("diagErrorType", overall.summary?.error_types, "error_type");
  populateSelect("diagModel", overall.summary?.models, "model");
  setStatus(data.memory_only
    ? "History is limited to the current session because persistent telemetry is unavailable."
    : `Failed jobs only · ${filtersActive(query) ? "filters applied" : "all categories"} · newest first`);
  $("diagUpdatedAt").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function failureRow(failure) {
  const row = node("button", `diag-failure${failure.id === view.selectedId ? " selected" : ""}`);
  row.type = "button";
  row.dataset.diagnosticId = failure.id;
  row.setAttribute("aria-pressed", String(failure.id === view.selectedId));

  const heading = node("span", "diag-failure-heading");
  heading.append(node("span", "diag-failure-task", (failure.operation || failure.task || "job").toUpperCase()),
    node("span", "diag-failure-time", dateLabel(failure.completed_at || failure.created_at)));

  const name = node("strong", "diag-failure-name", failure.request_id || "Unknown request");
  const problem = node("span", "diag-failure-error", failure.error?.type || "unknown_error");
  const message = node("span", "diag-failure-message",
    failure.diagnostic_preview || failure.error?.message || "No diagnostic preview was recorded.");
  const foot = node("span", "diag-failure-foot");
  foot.append(node("span", "", failure.model || "Unknown model"),
    node("span", "", duration(failure.elapsed_ms)),
    node("span", "", failure.exit_code === null || failure.exit_code === undefined
      ? "No exit code" : `Exit ${failure.exit_code}`));
  row.append(heading, name, problem, message, foot);
  row.addEventListener("click", () => inspectDiagnosticJob(failure.id));
  return row;
}

function renderList({silent = false} = {}) {
  const feed = $("diagFeed");
  const previousScroll = silent ? feed.scrollTop : 0;
  feed.replaceChildren();
  if (!view.failures.length) {
    const empty = node("div", "empty-state");
    empty.append(node("strong", "", "No failed jobs found"),
      node("span", "", "Try a longer time range or clear the filters. Successful jobs are shown in Jobs."));
    feed.append(empty);
  } else {
    const fragment = document.createDocumentFragment();
    for (const job of view.failures) fragment.append(failureRow(job));
    feed.append(fragment);
  }
  feed.scrollTop = previousScroll;
  $("diagLoadMore").hidden = !view.cursor;
}

function detailLine(label, value) {
  const line = node("div", "diag-detail-line");
  line.append(node("span", "", label), node("strong", "", value ?? "—"));
  return line;
}

function detailSection(label) {
  const section = node("section", "diag-detail-section");
  section.append(node("h3", "", label));
  return section;
}

function revokeInspectorImages() {
  for (const url of view.objectUrls) URL.revokeObjectURL(url);
  view.objectUrls = [];
}

function renderReferenceImages(references, host, version) {
  if (!references?.length) return;
  const section = detailSection("Reference downloads");
  const strip = node("div", "diag-reference-strip");
  for (const reference of references) {
    const card = node("div", "diag-reference-card");
    const label = node("span", "", reference.reference_id || `Reference ${reference.ordinal}`);
    if (reference.thumbnail_url) {
      const image = node("img");
      image.alt = `Reference ${reference.ordinal}: ${reference.reference_id || "image"}`;
      image.loading = "lazy";
      requestBlob(reference.thumbnail_url).then(blob => {
        if (version !== view.detailVersion) return;
        const url = URL.createObjectURL(blob);
        view.objectUrls.push(url);
        image.src = url;
      }).catch(() => {
        if (version === view.detailVersion) image.alt = `Reference ${reference.ordinal} preview unavailable`;
      });
      card.append(image);
    } else card.append(node("div", "diag-reference-missing", "Not downloaded"));
    card.append(label);
    strip.append(card);
  }
  section.append(strip);
  host.append(section);
}

async function copyBundle(jobId, textArea, status, button) {
  button.disabled = true;
  status.textContent = "Preparing a fresh redacted bundle…";
  try {
    const response = await getDiagnosticBundle(jobId);
    if (view.selectedId !== jobId) return;
    const json = JSON.stringify(response.bundle, null, 2);
    textArea.value = json;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(json);
      status.textContent = "Diagnostic bundle copied.";
    } catch {
      // Local HTTP, browser permissions and Firefox may block clipboard.writeText.
      // Always leave the JSON visible/selectable when automatic copying fails.
      textArea.focus();
      textArea.select();
      let copied = false;
      try { copied = Boolean(document.execCommand?.("copy")); } catch { /* unsupported */ }
      status.textContent = copied ? "Diagnostic bundle copied."
        : "Automatic copy blocked. Press Ctrl+C (or Cmd+C) to copy the selected JSON.";
    }
  } catch (error) {
    if (view.selectedId === jobId) status.textContent = `Could not retrieve bundle: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function renderDetail(data, version) {
  const job = data.job || {};
  const inspector = $("diagInspector");
  revokeInspectorImages();
  inspector.replaceChildren();

  const head = node("header", "diag-inspector-head");
  head.append(node("p", "kicker", `${job.operation || job.task || "job"} · failed`),
    node("h2", "", job.request_id || "Unknown request"),
    node("span", "diag-error-badge", job.error?.type || "unknown_error"));
  inspector.append(head);

  const summary = detailSection("Failure summary");
  summary.append(node("p", "diag-error-description", job.error?.message || "No error message recorded."));
  const grid = node("div", "diag-detail-grid");
  [
    ["Model", job.model], ["Exit code", job.exit_code ?? "Unavailable"],
    ["Last successful stage", job.last_successful_stage || "Not recorded"],
    ["Duration", duration(job.elapsed_ms)], ["Queue wait", duration(job.queue_ms)],
    ["Codex time", duration(job.codex_ms)],
    ["References", `${job.references_downloaded ?? 0} / ${job.reference_count ?? 0} downloaded`],
    ["Attempt", job.attempt ?? "—"], ["Created", dateLabel(job.created_at)],
    ["Completed", dateLabel(job.completed_at)],
  ].forEach(([label, value]) => grid.append(detailLine(label, value)));
  summary.append(grid);
  inspector.append(summary);

  const preview = detailSection("Redacted CLI diagnostic");
  const pre = node("pre", "diag-preview", job.diagnostic_preview || "No stderr or explicit error event was recorded.");
  preview.append(pre);
  inspector.append(preview);
  renderReferenceImages(data.references, inspector, version);

  const timelineSection = detailSection("Execution timeline");
  const timeline = node("div", "diag-timeline");
  if (!data.events?.length) timeline.append(node("p", "diag-muted", "No execution events were recorded."));
  for (const event of data.events || []) {
    const allowedLevel = ["error", "warning", "info"].includes(event.level) ? event.level : "info";
    const item = node("div", `diag-timeline-row ${allowedLevel}`);
    const ms = Number(event.elapsed_ms);
    item.append(node("span", "diag-event-clock", Number.isFinite(ms) ? `${(ms / 1000).toFixed(3)}s` : "—"),
      node("span", "diag-event-marker"), node("span", "", event.stage || "Unknown stage"));
    timeline.append(item);
  }
  timelineSection.append(timeline);
  inspector.append(timelineSection);

  const bundleSection = detailSection("Share a safe diagnostic bundle");
  bundleSection.append(node("p", "diag-muted",
    "This bundle includes execution metadata—not the full prompt, source URLs or unredacted stderr. Review before sharing."));
  const copy = node("button", "primary diag-copy", "Copy diagnostic bundle");
  copy.type = "button";
  const status = node("p", "diag-copy-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const textArea = node("textarea", "diag-bundle");
  textArea.readOnly = true;
  textArea.setAttribute("aria-label", "Redacted diagnostic bundle JSON");
  textArea.value = JSON.stringify(data.bundle || {}, null, 2);
  copy.addEventListener("click", () => copyBundle(job.id, textArea, status, copy));
  bundleSection.append(copy, status, textArea);
  inspector.append(bundleSection);
}

export async function inspectDiagnosticJob(id) {
  if (!id) return;
  const version = ++view.detailVersion;
  view.selectedId = id;
  $("diagFeed").querySelectorAll("[data-diagnostic-id]").forEach(row => {
    const selected = row.dataset.diagnosticId === id;
    row.classList.toggle("selected", selected);
    row.setAttribute("aria-pressed", String(selected));
  });
  const inspector = $("diagInspector");
  revokeInspectorImages();
  inspector.replaceChildren(node("div", "empty-state", "Loading failure details…"));
  try {
    const data = await getDiagnostic(id);
    if (version !== view.detailVersion) return;
    renderDetail(data, version);
  } catch (error) {
    if (version !== view.detailVersion) return;
    inspector.replaceChildren(node("div", "diag-detail-error", `Could not load failure: ${error.message}`));
  }
}

/**
 * Reset refreshes the first page and summary. Load-more appends a bounded page.
 * Version stamps discard stale responses when a user changes filters quickly.
 */
export async function loadDiagnostics({reset = true, silent = false} = {}) {
  if (!view.initialized) return;
  // Do not silently discard pages a user explicitly loaded while investigating.
  // Manual Refresh still resets to the newest first page.
  if (reset && silent && view.failures.length > 50) return;
  if (!reset && (view.busy || !view.cursor)) return;
  const version = ++view.listVersion;
  const query = readQuery();
  const cursor = reset ? null : view.cursor;
  view.busy = true;
  $("diagLoadMore").disabled = true;
  if (!silent) setError();
  if (reset && !silent && !view.failures.length) $("diagFeed").replaceChildren(node("div", "empty-state", "Loading failures…"));
  try {
    const tasks = [getDiagnostics({...query, limit: 50, cursor: cursor || ""})];
    if (reset) {
      tasks.push(getDiagnosticsSummary(rangeQuery()));
      if (filtersActive(query)) tasks.push(getDiagnosticsSummary(query));
    }
    const [page, overall, filtered] = await Promise.all(tasks);
    if (version !== view.listVersion) return;
    view.failures = reset ? page.failures : [...view.failures, ...page.failures];
    view.cursor = page.next_cursor;
    renderList({silent});
    if (reset) renderSummary(filtered || overall, overall, query);
    else $("diagResultCount").textContent = `${view.failures.length} shown`;
  } catch (error) {
    if (version !== view.listVersion) return;
    setError(`Could not load diagnostics: ${error.message}`);
    if (!view.failures.length) $("diagFeed").replaceChildren(node("div", "empty-state", "Diagnostics could not be loaded. Check the gateway connection and try Refresh."));
  } finally {
    if (version === view.listVersion) {
      view.busy = false;
      $("diagLoadMore").disabled = false;
    }
  }
}

function setRange(range) {
  if (range === "custom") {
    $("diagCustomRange").hidden = false;
    $("diagStart").focus();
    return;
  }
  view.range = range;
  view.start = "";
  view.end = "";
  syncRange();
  loadDiagnostics({reset: true});
}

function applyCustom() {
  const start = new Date($("diagStart").value);
  const end = new Date($("diagEnd").value);
  if (!$("diagStart").value || !$("diagEnd").value || Number.isNaN(start.getTime()) ||
    Number.isNaN(end.getTime()) || start >= end) {
    setError("Choose a valid custom range with Start before End.");
    return;
  }
  view.range = "custom";
  view.start = start.toISOString();
  view.end = end.toISOString();
  syncRange();
  loadDiagnostics({reset: true});
}

export function initializeDiagnostics() {
  if (view.initialized) return;
  view.initialized = true;
  const now = new Date();
  const dayAgo = new Date(now.getTime() - 86400000);
  // datetime-local expects local clock values (not UTC ISO timestamp slices).
  const localInput = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $("diagStart").value = localInput(dayAgo);
  $("diagEnd").value = localInput(now);
  syncRange();
  $("diagFeed").replaceChildren(node("div", "empty-state", "Choose Diagnostics to load the latest failures."));

  $("diagRefresh").addEventListener("click", () => loadDiagnostics({reset: true}));
  $("diagLoadMore").addEventListener("click", () => loadDiagnostics({reset: false}));
  $("diagApplyCustom").addEventListener("click", applyCustom);
  document.querySelectorAll("[data-diagnostic-range]").forEach(button =>
    button.addEventListener("click", () => setRange(button.dataset.diagnosticRange)));
  ["diagTask", "diagOperation", "diagModel", "diagErrorType"].forEach(id =>
    $(id).addEventListener("change", () => loadDiagnostics({reset: true})));
  $("diagSearch").addEventListener("input", () => {
    clearTimeout(view.searchTimer);
    view.searchTimer = setTimeout(() => loadDiagnostics({reset: true}), 320);
  });
  $("diagClear").addEventListener("click", () => {
    clearTimeout(view.searchTimer);
    ["diagTask", "diagOperation", "diagModel", "diagErrorType", "diagSearch"].forEach(id => { $(id).value = ""; });
    setError();
    loadDiagnostics({reset: true});
  });
}
