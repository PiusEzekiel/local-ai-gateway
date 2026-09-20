/** Module 7D: Settings and System UI. Never build user-controlled HTML. */
import {
  getSettings, patchSettings, getPrivacyStorage, purgePrivacy,
  getStoragePreview, getStorageHealth, getStorageInventory, runStorageCleanup, getSystemInfo,
} from "./api.js?v=9c3-20260920";

const $ = id => document.getElementById(id);
const sections = [
  ["model", "Default model", "The model used unless the n8n request selects another."],
  ["execution", "Execution", "These settings are applied when the gateway starts a new worker pool."],
  ["timeouts", "Timeouts", "Text and image requests have separate limits; per-request overrides still work."],
  ["quota", "Quota", "Monitor frequency and capacity warnings. No quota value is inferred by this page."],
  ["privacy", "Privacy", "Choose which new content is retained after your next gateway restart."],
  ["retention", "Retention", "Age, capacity and cleanup policies. Automatic cleanup remains off unless enabled."],
];
const help = {
  model: "Takes effect immediately and also updates future gateway requests.",
  max_concurrency: "Maximum number of concurrently executing Codex requests.",
  max_queue: "Additional requests allowed to wait beyond active workers.",
  default_timeout_seconds: "Default time limit for text and research requests (seconds).",
  max_timeout_seconds: "Maximum allowed text/research time limit (seconds).",
  image_timeout_seconds: "Default limit for image generation (seconds).",
  quota_monitor_enabled: "A separate Codex app-server monitors quota; generation is independent.",
  quota_poll_seconds: "Seconds between quota snapshot refreshes.",
  quota_warning_remaining_percent: "Warning when remaining capacity falls to or below this percentage.",
  quota_critical_remaining_percent: "Critical threshold; must be below the warning threshold.",
  store_prompts: "Opt in to saving request prompts as plaintext in SQLite.",
  store_outputs: "Opt in to saving generated text as plaintext in SQLite.",
  store_diagnostics: "Keep bounded, redacted CLI failure previews for investigations.",
  auto_cleanup_enabled: "If enabled, scheduled cleanup begins after the configured interval, not on startup.",
  history_retention_days: "Completed/failed jobs older than this can be deleted, including their associated metadata.",
  artifact_retention_days: "Age limit for image, reference and thumbnail files; job statistics remain.",
  quota_snapshot_retention_days: "Age limit for historical Codex quota measurements.",
  max_artifact_storage_mb: "Best-effort total artifact storage limit in MiB; active jobs are protected.",
  cleanup_interval_hours: "Hours between automatic sweeps, if enabled.",
};
const view = {
  loaded: false, loading: false, busy: false, dirty: false,
  snapshot: null, baseline: {}, preview: null, includeOrphans: false,
  purgeScope: "text", initialized: false,
};
const node = (tag, className = "", text) => {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
};
const formatBytes = bytes => {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "Unavailable";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes, i = -1;
  do { value /= 1024; i += 1; } while (value >= 1024 && i < units.length - 1);
  return `${value.toLocaleString(undefined, {maximumFractionDigits: 2})} ${units[i]}`;
};

function status(message, kind = "") {
  const el = $("settingsStatus");
  el.className = `settings-status ${kind}`;
  el.textContent = message;
}
function showPending() {
  $("settingsRestartNotice").hidden = !view.snapshot?.pending_restart;
}
function controlValue(field, key) {
  const el = $(`setting-${key}`);
  return typeof field.value === "boolean" ? el.checked :
    typeof field.value === "number" ? (el.value.trim() === "" ? null : Number(el.value)) : el.value;
}
function editableValue(field) {
  return field.saved !== null && !field.environment_override ? field.saved : field.value;
}
function currentPatch() {
  const patch = {};
  for (const [key, field] of Object.entries(view.snapshot?.fields || {})) {
    if (field.environment_override) continue;
    const value = controlValue(field, key);
    if (!Object.is(value, view.baseline[key])) patch[key] = value;
  }
  return patch;
}
function syncDirty() {
  if (!view.snapshot || view.busy) return;
  view.dirty = Object.keys(currentPatch()).length > 0;
  $("settingsSave").disabled = !view.dirty;
  $("settingsDiscard").disabled = !view.dirty;
  if (view.dirty) status("Unsaved changes. Save settings or discard changes before leaving this page.");
  else status(view.snapshot.pending_restart ? "Saved configuration is waiting for a Pinokio restart." : "Settings are up to date.");
}
function fieldControl(key, field) {
  const wrapper = node("div", "settings-field-control");
  let control;
  if (typeof field.value === "boolean") {
    const label = node("label", "settings-toggle");
    control = document.createElement("input");
    control.type = "checkbox";
    control.checked = Boolean(editableValue(field));
    label.append(control, node("span", "", control.checked ? "Enabled" : "Disabled"));
    control.addEventListener("change", () => { label.lastChild.textContent = control.checked ? "Enabled" : "Disabled"; syncDirty(); });
    wrapper.append(label);
  } else if (field.choices?.length) {
    control = document.createElement("select");
    for (const choice of field.choices) {
      const option = node("option", "", choice);
      option.value = choice;
      control.append(option);
    }
    control.value = editableValue(field);
    control.addEventListener("change", syncDirty);
    wrapper.append(control);
  } else {
    control = document.createElement("input");
    control.type = "number";
    control.step = "1";
    if (field.minimum !== null) control.min = String(field.minimum);
    if (field.maximum !== null) control.max = String(field.maximum);
    control.value = String(editableValue(field));
    control.addEventListener("input", syncDirty);
    wrapper.append(control);
  }
  control.id = `setting-${key}`;
  control.disabled = Boolean(field.environment_override);
  if (field.environment_override) wrapper.append(node("small", "settings-flag override", "Environment override · edit the launcher configuration instead."));
  else if (field.pending_restart) wrapper.append(node("small", "settings-flag pending", "Saved value · gateway restart required"));
  else wrapper.append(node("small", "settings-flag", field.restart_required ? "Applies after restart" : "Applies immediately"));
  return wrapper;
}
function renderSettings(data) {
  view.snapshot = data;
  view.baseline = {};
  for (const [key, field] of Object.entries(data.fields || {})) view.baseline[key] = editableValue(field);
  view.dirty = false;
  $("settingsSave").disabled = true;
  $("settingsDiscard").disabled = true;
  showPending();
  const container = $("settingsGroups");
  container.replaceChildren();
  for (const [group, title, description] of sections) {
    const fields = Object.entries(data.fields || {}).filter(([, f]) => f.group === group);
    if (!fields.length) continue;
    const section = node("section", "surface settings-section");
    section.id = `settings-${group}`;
    const head = node("div", "section-head");
    const heading = document.createElement("div");
    heading.append(node("p", "kicker", "Configuration"), node("h2", "", title), node("p", "", description));
    head.append(heading);
    section.append(head);
    const list = node("div", "settings-fields");
    for (const [key, field] of fields) {
      const row = node("div", "settings-field");
      const info = document.createElement("div");
      const label = node("label", "", field.label || key);
      label.htmlFor = `setting-${key}`;
      info.append(label, node("p", "", help[key] || ""));
      const now = field.value;
      const desired = editableValue(field);
      const active = node("small", "settings-current", `Active: ${typeof now === "boolean" ? (now ? "Enabled" : "Disabled") : now}${!Object.is(now, desired) ? ` · saved: ${typeof desired === "boolean" ? (desired ? "Enabled" : "Disabled") : desired}` : ""}`);
      info.append(active);
      row.append(info, fieldControl(key, field));
      list.append(row);
    }
    section.append(list);
    if (group === "privacy") section.append(node("div", "settings-group-note", "Turning retention off does not erase text already saved. Use Privacy storage below for explicit deletion."));
    if (group === "retention") section.append(node("div", "settings-group-note", "Automatic cleanup is OFF by default. Manual preview and cleanup operate on currently active policies; restart to apply pending retention settings."));
    container.append(section);
  }
  status(data.pending_restart ? "Saved changes are pending restart. Your current running values are shown beside each setting." : "Settings loaded. Changes are not applied until you save.", data.pending_restart ? "" : "success");
}
export async function loadSettings({force = false} = {}) {
  if (view.loading || view.busy) return;
  if (view.dirty && force && !window.confirm("Discard your unsaved settings changes?")) return;
  if (view.dirty && !force) return;
  view.loading = true;
  try {
    const data = await getSettings();
    renderSettings(data);
    view.loaded = true;
    await Promise.all([refreshPrivacy(), refreshSystem(), refreshStorage({quiet: true}), refreshArtifactHealth()]);
  } catch (error) {
    status(`Settings could not be loaded: ${error.message}`, "error");
  } finally { view.loading = false; }
}
async function saveSettings() {
  if (view.busy || !view.dirty) return;
  const patch = currentPatch();
  // Client validation improves UX; the server still validates all relationships.
  for (const [key, value] of Object.entries(patch)) {
    const field = view.snapshot.fields[key];
    if (typeof field.value === "number" && (!Number.isSafeInteger(value) || value < field.minimum || value > field.maximum)) {
      status(`${field.label} must be an integer from ${field.minimum} to ${field.maximum}.`, "error");
      $(`setting-${key}`).focus();
      return;
    }
  }
  view.busy = true;
  $("settingsSave").disabled = true;
  $("settingsDiscard").disabled = true;
  try {
    const data = await patchSettings(patch);
    renderSettings(data);
    view.preview = null;
    $("settingsCleanup").disabled = true;
    status(data.pending_restart ? "Saved. Restart through Pinokio when current jobs finish to apply pending settings." : "Saved. The selected model is active now.", "success");
    await refreshStorage({quiet: true});
  } catch (error) {
    status(`Could not save settings: ${error.message}`, "error");
    $("settingsSave").disabled = false;
    $("settingsDiscard").disabled = false;
  } finally { view.busy = false; }
}
async function refreshPrivacy() {
  try {
    const response = await getPrivacyStorage();
    const rows = response?.retained_text;
    $("privacyPrompts").textContent = rows?.prompts ?? "—";
    $("privacyOutputs").textContent = rows?.outputs ?? "—";
    $("privacyRows").textContent = rows?.rows ?? "—";
  } catch {
    ["privacyPrompts", "privacyOutputs", "privacyRows"].forEach(id => { $(id).textContent = "Unavailable"; });
  }
}
async function refreshStorage({quiet = false} = {}) {
  if (!quiet) status("Calculating a read-only storage preview…");
  view.preview = null;
  $("settingsCleanup").disabled = true;
  try {
    const data = await getStoragePreview($("settingsIncludeOrphans").checked);
    view.preview = data;
    const used = data.storage || {}, candidates = data.candidates || {};
    $("storageUsed").textContent = formatBytes(used.bytes);
    $("storageLimit").textContent = `Configured limit: ${formatBytes(used.limit_bytes)}`;
    $("storageRegistered").textContent = formatBytes(used.registered_file_bytes);
    $("storageRecoverable").textContent = formatBytes(candidates.estimated_artifact_bytes);
    $("storageUnregistered").textContent = used.accounting_consistent === false
      ? "Temporarily unavailable" : formatBytes(used.unregistered_or_other_bytes);
    $("storageOverage").textContent = formatBytes(used.over_limit_bytes);
    $("storageProjected").textContent = formatBytes(used.estimated_post_cleanup_bytes);
    const overage = used.estimated_remaining_over_limit_bytes || 0;
    $("storageBudgetNote").textContent = used.accounting_consistent === false
      ? "File and database measurements changed during inspection; refresh when jobs finish. No data deleted."
      : (overage > 0
        ? `Best-effort limit: this preview would still leave about ${formatBytes(overage)} above the limit. Protected/recent registered files, unregistered files, and unknown files are NOT automatically deleted. Repeat later or inspect before making any cleanup decision.`
        : `Best-effort limit: ${used.over_limit_bytes ? "previewed reclaim may bring usage under the limit" : "current managed usage is under the limit"}. The accounting includes unregistered files and staging files, excludes Codex-owned images, and is read-only.`);
    $("storageJobs").textContent = candidates.job_count ?? "—";
    $("storageArtifacts").textContent = candidates.artifact_count ?? "—";
    $("storageQuota").textContent = candidates.quota_snapshot_count ?? "—";
    const pendingRetention = Object.entries(view.snapshot?.fields || {}).some(([, field]) => field.group === "retention" && field.pending_restart);
    const count = (candidates.job_count || 0) + (candidates.artifact_count || 0) + (candidates.quota_snapshot_count || 0) + (candidates.orphan_file_count || 0);
    $("settingsCleanup").disabled = !count || pendingRetention;
    $("storageNote").textContent = pendingRetention
      ? "Retention settings are pending a restart. Cleanup is disabled until you restart through Pinokio."
      : `Dry-run only · ${candidates.orphan_file_count || 0} eligible orphan files · ${data.truncated ? "Preview is batch-limited; repeat after a run." : "No data deleted."}`;
    if (!quiet) status("Read-only storage preview updated.", "success");
  } catch (error) {
    $("storageNote").textContent = `Preview unavailable: ${error.message}`;
    $("storageBudgetNote").textContent = "Storage-limit accounting unavailable. Nothing deleted.";
    if (!quiet) status(`Storage preview failed: ${error.message}`, "error");
  }
}
async function refreshArtifactHealth() {
  const note = $("healthNote");
  const samples = $("healthSamples");
  note.textContent = "Inspecting managed artifact files…";
  samples.replaceChildren();
  try {
    const report = await getStorageHealth();
    const r = report.records || {}, u = report.unregistered || {}, scan = report.scan || {};
    $("healthOriginals").textContent = (r.originals_missing || 0) + (r.originals_unsafe || 0)
      + (r.originals_unreadable || 0) + (r.originals_empty || 0);
    $("healthThumbnails").textContent = (r.thumbnails_missing || 0)
      + (r.thumbnails_unsafe || 0) + (r.thumbnails_unreadable || 0);
    $("healthOrphans").textContent = u.scan_complete ? (u.eligible_files || 0) : "Scan incomplete";
    note.textContent = `Inspected ${r.scanned ?? 0} / ${r.total ?? 0} registered artifacts. `
      + (scan.records_complete && scan.directory_complete
        ? `Eligible orphan space: ${formatBytes(u.eligible_bytes)}. Recent unregistered files: ${u.recent_unregistered_files || 0}.`
        : "Scan limit reached; do not treat counts as complete. Nothing deleted.");
    const findings = report.samples || [];
    for (const finding of findings) {
      const line = node("div", "artifact-health-finding");
      line.append(node("span", "", `${finding.file === "thumbnail" ? "Thumbnail" : "Original"}: ${finding.status.replaceAll("_", " ")}`),
        node("code", "", `Job ${finding.job_id} · ${finding.artifact_id}`));
      samples.append(line);
    }
    if (!findings.length) samples.append(node("p", "settings-muted", "No missing or unsafe files found in the scanned registered records."));
  } catch (error) {
    ["healthOriginals", "healthThumbnails", "healthOrphans"].forEach(id => { $(id).textContent = "Unavailable"; });
    note.textContent = `Artifact health unavailable: ${error.message}`;
  }
}

const inventoryAgeLabels = {
  under_5_minutes: "Under 5 min",
  "5_minutes_to_1_hour": "5 min–1 hour",
  "1_to_24_hours": "1–24 hours",
  older_than_24_hours: "Over 24 hours",
};

async function refreshStorageInventory() {
  const button = $("settingsInventoryRefresh");
  const note = $("inventoryNote"), rows = $("inventoryRows"), samples = $("inventorySamples");
  button.disabled = true;
  note.textContent = "Inspecting directory metadata… No files are being opened or modified.";
  rows.replaceChildren();
  samples.replaceChildren();
  try {
    const report = await getStorageInventory();
    const scan = report.scan || {}, sum = report.summary || {};
    $("inventoryRecent").textContent = scan.complete ? (sum.recent_managed_unregistered ?? "—") : "Incomplete";
    $("inventoryEligible").textContent = scan.complete ? (sum.eligible_managed_unregistered ?? "—") : "Incomplete";
    $("inventoryBytes").textContent = formatBytes(sum.scanned_bytes);
    const categories = report.categories || {};
    for (const [key, group] of Object.entries(categories)) {
      if (!group?.count) continue;
      const row = node("div", "artifact-inventory-row");
      const title = node("div", "artifact-inventory-description");
      title.append(node("strong", "", group.label || key),
        node("span", "", `${group.count} files · ${formatBytes(group.bytes)}`));
      const ages = node("div", "artifact-inventory-ages");
      for (const [age, count] of Object.entries(group.ages || {})) {
        if (count) ages.append(node("span", "", `${inventoryAgeLabels[age] || age}: ${count}`));
      }
      row.append(title, ages);
      rows.append(row);
    }
    if (!rows.children.length) rows.append(node("p", "settings-muted", "No files in the inspected directory."));
    for (const item of report.examples || []) {
      samples.append(node("div", "artifact-inventory-example",
        `${categories[item.category]?.label || item.category} · ${inventoryAgeLabels[item.age] || item.age} · ${formatBytes(item.size_bytes)}`));
    }
    note.textContent = `Inspected ${scan.scanned_directory_entries ?? 0} directory entries and ${scan.registered_records ?? 0}/${scan.registered_total ?? 0} database records. `
      + (scan.complete
        ? `Recent unregistered managed files: ${sum.recent_managed_unregistered} (${formatBytes(sum.recent_managed_unregistered_bytes)}). Nothing changed.`
        : "Scan limit reached; counts are partial and registration status may be unknown. Nothing changed.");
  } catch (error) {
    ["inventoryRecent", "inventoryEligible", "inventoryBytes"].forEach(id => { $(id).textContent = "Unavailable"; });
    note.textContent = `Read-only inventory unavailable: ${error.message}`;
  } finally { button.disabled = false; }
}

async function refreshSystem() {
  const root = $("settingsSystemRows");
  try {
    const info = await getSystemInfo();
    const entries = [
      ["Gateway", info.gateway_version], ["Codex CLI", info.codex_version || "Unavailable"],
      ["Python", info.python_version], ["Platform", info.platform],
      ["Uptime", `${Math.floor((info.uptime_seconds || 0) / 3600)}h ${Math.floor(((info.uptime_seconds || 0) % 3600) / 60)}m`],
      ["Database + WAL", formatBytes(info.database_bytes)],
      ["Running", `${info.workers?.active ?? "—"} / ${info.workers?.concurrency ?? "—"}`],
      ["Queue", `${info.workers?.queued ?? "—"} / ${info.workers?.max_queue ?? "—"}`],
    ];
    root.replaceChildren();
    entries.forEach(([label, value]) => {
      const row = document.createElement("div");
      row.append(node("span", "", label), node("strong", "", value ?? "Unavailable"));
      root.append(row);
    });
  } catch (error) { root.replaceChildren(node("div", "", `System telemetry unavailable: ${error.message}`)); }
}
function showDialog(id) {
  const dialog = $(id);
  if (typeof dialog.showModal === "function") dialog.showModal();
  else status("Your browser does not support confirmation dialogs; use an updated browser.", "error");
}
function openCleanup() {
  if (!view.preview || $("settingsCleanup").disabled || view.busy) return;
  const candidate = view.preview.candidates || {};
  $("settingsCleanupDescription").textContent = `Permanent cleanup may delete up to ${candidate.job_count || 0} terminal jobs, ${candidate.artifact_count || 0} artifacts, ${candidate.quota_snapshot_count || 0} quota snapshots and ${candidate.orphan_file_count || 0} orphan files. The plan is recomputed at execution. Back up your data first.`;
  $("settingsCleanupPhrase").value = "";
  $("settingsCleanupError").textContent = "";
  $("settingsCleanupConfirm").disabled = true;
  showDialog("settingsCleanupDialog");
  $("settingsCleanupPhrase").focus();
}
async function confirmCleanup() {
  if (view.busy || $("settingsCleanupPhrase").value !== "DELETE_EXPIRED_DATA") return;
  view.busy = true;
  $("settingsCleanupConfirm").disabled = true;
  try {
    const result = await runStorageCleanup(view.includeOrphans);
    $("settingsCleanupDialog").close();
    const done = result.result || {};
    status(`Cleanup finished: ${done.deleted_jobs ?? 0} jobs, ${done.deleted_artifacts ?? 0} artifacts, ${done.deleted_orphan_files ?? 0} orphan files removed.`, "success");
    await refreshStorage({quiet: true});
    await refreshArtifactHealth();
    await refreshPrivacy();
  } catch (error) {
    $("settingsCleanupError").textContent = `Cleanup failed: ${error.message}`;
    status(`Cleanup failed: ${error.message}`, "error");
  } finally { view.busy = false; }
}
function openPurge(scope) {
  if (view.busy) return;
  view.purgeScope = scope;
  $("settingsPurgeDescription").textContent = scope === "text"
    ? "Delete saved prompt and output text. Job metrics and images remain. This cannot be undone."
    : "Clear stored diagnostic previews. Existing jobs and metrics remain. This cannot be undone.";
  $("settingsPurgePhrase").value = "";
  $("settingsPurgeError").textContent = "";
  $("settingsPurgeConfirm").disabled = true;
  showDialog("settingsPurgeDialog");
  $("settingsPurgePhrase").focus();
}
async function confirmPurge() {
  if (view.busy || $("settingsPurgePhrase").value !== "DELETE_RETAINED_DATA") return;
  view.busy = true;
  $("settingsPurgeConfirm").disabled = true;
  try {
    const result = await purgePrivacy(view.purgeScope);
    $("settingsPurgeDialog").close();
    status(`Purge finished: ${result.retained_text_rows_deleted || 0} text rows deleted, ${result.diagnostic_previews_cleared || 0} diagnostic previews cleared.`, "success");
    await refreshPrivacy();
  } catch (error) {
    $("settingsPurgeError").textContent = `Purge failed: ${error.message}`;
    status(`Purge failed: ${error.message}`, "error");
  } finally { view.busy = false; }
}
export function initializeSettings() {
  if (view.initialized) return;
  view.initialized = true;
  $("settingsSave").addEventListener("click", saveSettings);
  $("settingsForm").addEventListener("submit", e => { e.preventDefault(); saveSettings(); });
  $("settingsDiscard").addEventListener("click", () => loadSettings({force: true}));
  $("settingsReload").addEventListener("click", () => loadSettings({force: true}));
  $("settingsPreview").addEventListener("click", () => refreshStorage());
  $("settingsHealthRefresh").addEventListener("click", refreshArtifactHealth);
  $("settingsInventoryRefresh").addEventListener("click", refreshStorageInventory);
  $("settingsIncludeOrphans").addEventListener("change", () => {
    view.includeOrphans = $("settingsIncludeOrphans").checked;
    view.preview = null;
    $("settingsCleanup").disabled = true;
    $("storageNote").textContent = "Orphan selection changed. Refresh the preview before cleanup.";
  });
  $("settingsCleanup").addEventListener("click", openCleanup);
  $("settingsCleanupCancel").addEventListener("click", () => $("settingsCleanupDialog").close());
  $("settingsCleanupPhrase").addEventListener("input", () => {
    $("settingsCleanupConfirm").disabled = $("settingsCleanupPhrase").value !== "DELETE_EXPIRED_DATA";
  });
  $("settingsCleanupConfirm").addEventListener("click", confirmCleanup);
  $("privacyPurgeText").addEventListener("click", () => openPurge("text"));
  $("privacyPurgeDiagnostics").addEventListener("click", () => openPurge("diagnostics"));
  $("settingsPurgeCancel").addEventListener("click", () => $("settingsPurgeDialog").close());
  $("settingsPurgePhrase").addEventListener("input", () => {
    $("settingsPurgeConfirm").disabled = $("settingsPurgePhrase").value !== "DELETE_RETAINED_DATA";
  });
  $("settingsPurgeConfirm").addEventListener("click", confirmPurge);
  $("settingsSystemRefresh").addEventListener("click", refreshSystem);
  document.querySelectorAll("[data-settings-section]").forEach(button => button.addEventListener("click", () => {
    const target = $(`settings-${button.dataset.settingsSection}`);
    if (!target) return;
    document.querySelectorAll("[data-settings-section]").forEach(item => item.classList.toggle("active", item === button));
    target.scrollIntoView({behavior: "auto", block: "start"});
  }));
}
export function settingsHasUnsavedChanges() { return view.dirty; }
export function discardSettingsChanges() {
  if (view.snapshot) renderSettings(view.snapshot);
}
export function refreshSettingsView() { return loadSettings({force: true}); }
