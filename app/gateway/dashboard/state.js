const listeners = new Set();

export const state = {
  page: "overview",
  summary: null,
  recentJobs: [],
  allJobs: [],
  nextCursor: null,
  selectedJobId: null,
  galleryItems: [],
  galleryCursor: null,
  galleryCategory: "all",
  analyticsRange: "24h",
  analyticsStart: "",
  analyticsEnd: "",
  connected: false,
};

export function update(patch) {
  Object.assign(state, patch);
  for (const listener of listeners) listener(state);
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
