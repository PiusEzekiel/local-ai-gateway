let token = sessionStorage.getItem("gatewayToken") || "";
let unauthorizedHandler = null;

// A single session-bound handler catches 401s from *all* dashboard API calls,
// not only Overview and SSE. Never include the bearer value in diagnostics.
export function setUnauthorizedHandler(callback) {
  unauthorizedHandler = typeof callback === "function" ? callback : null;
}

export function setToken(value) {
  token = value.trim();
  if (token) sessionStorage.setItem("gatewayToken", token);
  else sessionStorage.removeItem("gatewayToken");
}

export function hasToken() { return Boolean(token); }

export async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, {...options, headers, cache: "no-store"});
  if (!response.ok) {
    if (response.status === 401) unauthorizedHandler?.();
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).error?.message || message; } catch { /* non-JSON response */ }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export const getSummary = () => request("/dashboard/api/summary");
export const getJob = (id, options = {}) => request(`/dashboard/api/jobs/${encodeURIComponent(id)}`, options);
export const getQuota = (historyLimit = 120) => request(`/dashboard/api/quota?history_limit=${historyLimit}`);
export const refreshQuota = () => request("/dashboard/api/quota/refresh", {method: "POST"});

function analyticsPath(path, {range = "24h", start = "", end = ""} = {}) {
  const params = new URLSearchParams({range});
  if (range === "custom" && start && end) { params.set("start", start); params.set("end", end); }
  return request(`${path}?${params}`);
}

export const getUsage = query => analyticsPath("/dashboard/api/usage", query);
export const getPerformance = query => analyticsPath("/dashboard/api/performance", query);

export async function requestBlob(path, {signal} = {}) {
  const headers = new Headers({Authorization: `Bearer ${token}`});
  const response = await fetch(path, {headers, signal, cache: "no-store"});
  if (!response.ok) {
    if (response.status === 401) unauthorizedHandler?.();
    // Preserve only the stable server error type. Never leak host paths or
    // untrusted error messages into Gallery/Jobs media status labels.
    let type = "";
    try {
      const data = await response.json();
      if (typeof data?.error?.type === "string") type = data.error.type;
    } catch { /* non-JSON image failure */ }
    const error = new Error(`Image request failed (${response.status})`);
    error.status = response.status;
    error.type = type;
    throw error;
  }
  return response.blob();
}

export function getJobs({limit = 50, cursor = "", status = "", task = "", search = ""} = {}) {
  const params = new URLSearchParams({limit: String(limit)});
  if (cursor) params.set("cursor", cursor);
  if (status) params.set("status", status);
  if (task) params.set("task", task);
  if (search) params.set("search", search);
  return request(`/dashboard/api/jobs?${params}`);
}

export function getGallery({limit = 48, cursor = "", category = "all", search = ""} = {}, options = {}) {
  const params = new URLSearchParams({limit: String(limit), category});
  if (cursor) params.set("cursor", cursor);
  if (search) params.set("search", search);
  return request(`/dashboard/api/gallery?${params}`, options);
}

// Module 6D: authenticated, read-only failure investigation API.
// Keep diagnostic filters separate from the existing Usage/Performance ranges.
function diagnosticsPath(path, {range = "7d", start = "", end = "", limit = "", cursor = "",
  task = "", operation = "", model = "", error_type = "", search = ""} = {}) {
  const params = new URLSearchParams({range});
  if (range === "custom" && start && end) { params.set("start", start); params.set("end", end); }
  for (const [key, value] of Object.entries({limit, cursor, task, operation, model, error_type, search})) {
    if (value !== "" && value !== null && value !== undefined) params.set(key, String(value));
  }
  return request(`${path}?${params}`);
}

export const getDiagnosticsSummary = query => diagnosticsPath("/dashboard/api/diagnostics/summary", query);
export const getDiagnostics = query => diagnosticsPath("/dashboard/api/diagnostics", query);
export const getDiagnostic = id => request(`/dashboard/api/diagnostics/${encodeURIComponent(id)}`);
export const getDiagnosticBundle = id => request(`/dashboard/api/diagnostics/${encodeURIComponent(id)}/bundle`);

// Modules 7A–7D: authenticated settings and safe storage management.
export const getSettings = () => request("/dashboard/api/settings");
export const patchSettings = patch => request("/dashboard/api/settings", {
  method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(patch),
});
export const getPrivacyStorage = () => request("/dashboard/api/privacy/storage");
export const purgePrivacy = scope => request("/dashboard/api/privacy/purge", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({confirmation: "DELETE_RETAINED_DATA", scope}),
});
export const getStorageHealth = () => request("/dashboard/api/storage/health");
export const getStorageInventory = () => request("/dashboard/api/storage/inventory");
export const getStoragePreview = (includeOrphans = false) => request(
  `/dashboard/api/storage/preview?include_orphans=${includeOrphans ? "true" : "false"}`
);
export const runStorageCleanup = (includeOrphans = false) => request("/dashboard/api/storage/cleanup", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({confirmation: "DELETE_EXPIRED_DATA", include_orphans: includeOrphans}),
});
export const getSystemInfo = () => request("/dashboard/api/system");
export const getReferenceCache = () => request("/dashboard/api/reference-cache");
export const clearReferenceCache = scope => request("/dashboard/api/reference-cache/clear", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({confirmation: "DELETE_REFERENCE_CACHE", scope}),
});

// Module 8B: native fetch stream, so the Bearer secret stays in an HTTP header.
// Never add the token to a URL, EventSource, or an SSE event log.
export function openEventStream(signal) {
  const headers = new Headers({Authorization: `Bearer ${token}`, Accept: "text/event-stream"});
  return fetch("/dashboard/api/events", {headers, signal, cache: "no-store"});
}
