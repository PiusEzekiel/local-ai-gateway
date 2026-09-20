import {getJob, requestBlob} from "./api.js?v=8c-20260920";
import {state, update} from "./state.js?v=8c-20260920";

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

let inspectorObjectUrls = [];

async function protectedImage(path, className, alt) {
  const image = el("img", className);
  image.alt = alt;
  try {
    const blob = await requestBlob(path);
    const objectUrl = URL.createObjectURL(blob);
    inspectorObjectUrls.push(objectUrl);
    image.src = objectUrl;
  } catch {
    image.alt = `${alt} unavailable`;
  }
  return image;
}

export const compact = value => {
  if (value === null || value === undefined) return "—";
  return Intl.NumberFormat(undefined, {notation: "compact", maximumFractionDigits: 1}).format(value);
};

export function duration(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
  const minutes = Math.floor(ms / 60000);
  return `${minutes}m ${Math.round((ms % 60000) / 1000)}s`;
}

function jobRow(job, selected = false) {
  const row = el("button", `job-row${selected ? " selected" : ""}`);
  row.type = "button";
  row.dataset.jobId = job.id;
  const kind = el("span", "job-kind");
  kind.append(el("span", `status-icon ${job.status}`), document.createTextNode(job.task.toUpperCase()));
  const request = el("span", "job-request");
  request.append(el("strong", "", job.request_id), el("span", "", job.stage || job.error?.type || "—"));
  row.append(kind, request, el("span", "job-model", job.model),
    el("span", "job-tokens", compact(job.usage?.total_tokens)),
    el("span", "job-duration", duration(job.elapsed_ms)));
  row.addEventListener("click", () => selectJob(job.id));
  return row;
}

export function renderFeed(container, jobs, selectedId = null) {
  container.replaceChildren();
  if (!jobs.length) {
    const empty = el("div", "empty-state");
    empty.append(el("strong", "", "No jobs found"), el("span", "", "New executions will appear here."));
    container.append(empty);
    return;
  }
  jobs.forEach(job => container.append(jobRow(job, job.id === selectedId)));
}

function detailItem(label, value) {
  const item = el("div", "detail-item");
  item.append(el("span", "", label), el("strong", "", value ?? "—"));
  return item;
}

function section(title) {
  const node = el("section", "inspector-section");
  node.append(el("h3", "", title));
  return node;
}

function renderInspector(job) {
  const inspector = document.getElementById("jobInspector");
  inspectorObjectUrls.forEach(url => URL.revokeObjectURL(url));
  inspectorObjectUrls = [];
  inspector.replaceChildren();
  const head = el("div", "inspector-head");
  head.append(el("p", "kicker", `${job.task} · ${job.status}`), el("h2", "", job.request_id), el("p", "", job.model));
  const details = el("div", "detail-grid");
  details.append(detailItem("Duration", duration(job.elapsed_ms)), detailItem("Queue", duration(job.queue_ms)),
    detailItem("Codex", duration(job.codex_ms)), detailItem("References", String(job.reference_count || 0)),
    detailItem("Attempt", job.attempt ? String(job.attempt) : "—"), detailItem("Created", new Date(job.created_at).toLocaleString()));
  inspector.append(head, details);

  if (job.error) {
    const errorSection = section("Failure");
    errorSection.append(el("div", "error-box", `${job.error.type}: ${job.error.message}`));
    inspector.append(errorSection);
  }

  if (job.artifact || job.references?.some(reference => reference.thumbnail_url)) {
    const mediaSection = section("Images");
    if (job.artifact) {
      const imageHost = el("div");
      mediaSection.append(imageHost);
      protectedImage(job.artifact.url, "inspector-image", `Generated image for ${job.request_id}`).then(image => imageHost.append(image));
    }
    const references = job.references?.filter(reference => reference.thumbnail_url) || [];
    if (references.length) {
      const strip = el("div", "reference-strip");
      references.forEach(reference => {
        protectedImage(reference.thumbnail_url, "reference-thumb", reference.reference_id || `Reference ${reference.ordinal}`).then(image => strip.append(image));
      });
      mediaSection.append(strip);
    }
    inspector.append(mediaSection);
  }

  const usage = job.usage;
  const usageSection = section("Token usage");
  if (!usage) usageSection.append(el("div", "muted", job.status === "running" ? "Calculating…" : "Unavailable"));
  else {
    const grid = el("div", "token-grid");
    const labels = [["Input", "input_tokens"], ["Cached", "cached_input_tokens"], ["Uncached", "uncached_input_tokens"],
      ["Output", "output_tokens"], ["Reasoning", "reasoning_output_tokens"], ["Total", "total_tokens"]];
    labels.forEach(([label, key]) => {
      const line = el("div", "token-line");
      line.append(el("span", "", label), el("strong", "", compact(usage[key])));
      grid.append(line);
    });
    usageSection.append(grid);
  }
  inspector.append(usageSection);

  const timelineSection = section("Timeline");
  const timeline = el("div", "timeline");
  (job.events || []).forEach(event => {
    const eventRow = el("div", `event ${event.level || "info"}`);
    eventRow.append(el("span", "event-time", `${(event.elapsed_ms / 1000).toFixed(3)}s`), el("span", "event-dot"), el("span", "", event.stage));
    timeline.append(eventRow);
  });
  timelineSection.append(timeline);
  inspector.append(timelineSection);
}

export async function selectJob(id) {
  update({selectedJobId: id});
  renderFeed(document.getElementById("allJobs"), state.allJobs, id);
  const inspector = document.getElementById("jobInspector");
  inspector.replaceChildren(el("div", "empty-state", "Loading job…"));
  try {
    const data = await getJob(id);
    if (state.selectedJobId === id) renderInspector(data.job);
  } catch (error) {
    inspector.replaceChildren(el("div", "error-box", error.message));
  }
}
