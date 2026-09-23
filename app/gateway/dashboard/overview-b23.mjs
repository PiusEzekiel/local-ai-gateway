/** Overview Phase B.3: small, no-network presentation enhancements.
 *  Reads existing authenticated dashboard DOM, never quota secrets or API keys.
 *  No changes to the gateway's API or existing JavaScript import graph.
 */
export const QUOTA_DETAILS_PREF = "gatewayQuotaDetailsExpanded";

export function remainingQuotaLabel(text) {
  const match = String(text ?? "").match(/^\s*(\d+(?:\.\d+)?)% left\b/i);
  if (!match) return "—";
  const number = Number(match[1]);
  return Number.isFinite(number) && number >= 0 && number <= 100 ? `${match[1]}% left` : "—";
}

export function kindPresentation(kind) {
  switch (String(kind ?? "").trim().toLowerCase()) {
    case "image":
      return {label: "Image", className: "kind-image"};

    case "generate":
    case "text":
      return {label: "Text", className: "kind-text"};

    case "research":
      return {label: "Research", className: "kind-research"};

    default:
      return null;
  }
}

export function readQuotaExpanded(storage) {
  try { return storage?.getItem(QUOTA_DETAILS_PREF) === "1"; }
  catch { return false; } // Storage can be blocked in private/managed browsers.
}

export function writeQuotaExpanded(storage, expanded) {
  try { storage?.setItem(QUOTA_DETAILS_PREF, expanded ? "1" : "0"); }
  catch { /* UI still works when persistence is unavailable. */ }
}

export function initializeOverviewPolish(doc = document, storage = localStorage) {
  const toggle = doc.getElementById("quotaToggle");
  const buckets = doc.getElementById("quotaBuckets");
  const source = doc.getElementById("topQuota");
  const mini = doc.getElementById("quotaMiniValue");
  const brief = doc.getElementById("quotaBrief");
  if (!toggle || !buckets || !source || !mini || !brief) return;

  function setExpanded(expanded) {
    buckets.hidden = !expanded;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", `${expanded ? "Hide" : "Show"} Codex quota details`);
    toggle.firstChild.textContent = expanded ? "Hide details " : "Show details ";
    writeQuotaExpanded(storage, expanded);
  }
  setExpanded(readQuotaExpanded(storage));
  toggle.addEventListener("click", () => setExpanded(buckets.hidden));

  function syncQuota() {
    const label = remainingQuotaLabel(source.dataset.quotaLeft || source.textContent);
    mini.textContent = label;
    const hasValue = label !== "—";
    brief.textContent = hasValue ? `Lowest reported window · ${label}` : "No quota balance reported";
    const detail = source.textContent || "Quota unavailable";
    mini.title = hasValue ? `${detail} · percentage, not remaining token count` : detail;
  }
  syncQuota();
  // The existing authenticated summary updater owns #topQuota. Observe only
  // its replacement text; no polling, duplicate fetches or API-contract changes.
  const quotaObserver = new MutationObserver(syncQuota);
  quotaObserver.observe(source, {childList:true, characterData:true, subtree:true});

  function decorateFeed(feed) {
    for (const row of feed.querySelectorAll(".job-row:not([data-kind-decorated])")) {
      const kind = row.querySelector(".job-kind");
      if (!kind) continue;
      const type = kindPresentation(kind.textContent);
      if (!type) continue;
      row.dataset.kindDecorated = "1";
      row.classList.add(type.className);
      // Replace the technical task label with a user-friendly display label.
      // This changes the visible DOM only, never the underlying gateway task.
      const labelNode = [...kind.childNodes].find(
        child => child.nodeType === 3 && child.textContent.trim()
      );

      if (labelNode) {
        labelNode.textContent = type.label.toUpperCase();
      }

      // SVG artwork is supplied by CSS; no emoji or external icon library.
      const icon = doc.createElement("span");
      icon.className = "job-type-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "";

      const dot = kind.querySelector(".status-icon");

      kind.insertBefore(
        icon,
        dot ? dot.nextSibling : kind.firstChild
      );
    }
  }
  for (const id of ["recentJobs", "allJobs"]) {
    const feed = doc.getElementById(id);
    if (!feed) continue;
    decorateFeed(feed);
    // renderFeed replaces children on every job-list refresh. Observe only
    // direct child updates to avoid cycles from badge insertion.
    const observer = new MutationObserver(() => decorateFeed(feed));
    observer.observe(feed, {childList:true});
  }
}

// This script is an independent progressive enhancement. Importing it in
// Node tests does not require a DOM or touch browser storage.
if (typeof document !== "undefined") {
  let preferences = null;
  try { preferences = localStorage; } catch { /* Browser storage may be unavailable. */ }
  initializeOverviewPolish(document, preferences);
}
