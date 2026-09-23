import {compact, duration} from "./jobs.js?v=ui-refresh-phase-a-20260923";

const $ = id => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const COLORS = ["#77a7ff", "#54d69b", "#e9b95f", "#f27b83", "#b69cff", "#7ed7dd"];

function text(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function percent(value) {
  return value === null || value === undefined ? "Unavailable" : `${value}%`;
}

function svgElement(name, attributes = {}) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function emptyChart(container, message = "No telemetry in this range") {
  container.replaceChildren(Object.assign(document.createElement("div"), {
    className: "chart-empty", textContent: message,
  }));
}

function renderLines(container, series, {maxValue = null, suffix = "", label = "Telemetry chart"} = {}) {
  const populated = series.filter(item => item.points.length);
  if (!populated.length) return emptyChart(container);
  const width = 760; const height = 250;
  const plot = {left: 46, right: 18, top: 18, bottom: 34};
  const allPoints = populated.flatMap(item => item.points);
  const minX = Math.min(...allPoints.map(point => point.x));
  const maxX = Math.max(...allPoints.map(point => point.x));
  const observedMax = Math.max(0, ...allPoints.map(point => point.y));
  const topValue = maxValue ?? (observedMax ? observedMax * 1.12 : 1);
  const xScale = value => plot.left + ((value - minX) / Math.max(1, maxX - minX)) * (width - plot.left - plot.right);
  const yScale = value => height - plot.bottom - (value / Math.max(1, topValue)) * (height - plot.top - plot.bottom);
  const svg = svgElement("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label});
  for (let index = 0; index <= 4; index += 1) {
    const value = topValue * index / 4;
    const y = yScale(value);
    svg.append(svgElement("line", {x1: plot.left, y1: y, x2: width - plot.right, y2: y, class: "chart-grid-line"}));
    const tick = svgElement("text", {x: plot.left - 8, y: y + 3, class: "chart-tick", "text-anchor": "end"});
    tick.textContent = `${compact(Math.round(value))}${suffix}`;
    svg.append(tick);
  }
  populated.forEach((item, seriesIndex) => {
    const color = item.color || COLORS[seriesIndex % COLORS.length];
    const sorted = [...item.points].sort((a, b) => a.x - b.x);
    const path = sorted.map((point, index) => `${index ? "L" : "M"}${xScale(point.x).toFixed(1)},${yScale(point.y).toFixed(1)}`).join(" ");
    svg.append(svgElement("path", {d: path, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round"}));
    sorted.forEach(point => {
      const circle = svgElement("circle", {cx: xScale(point.x), cy: yScale(point.y), r: 2.5, fill: color});
      const title = svgElement("title"); title.textContent = `${item.label}: ${point.y}${suffix}`; circle.append(title); svg.append(circle);
    });
  });
  const startLabel = svgElement("text", {x: plot.left, y: height - 8, class: "chart-tick"});
  startLabel.textContent = new Date(minX * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit"});
  const endLabel = svgElement("text", {x: width - plot.right, y: height - 8, class: "chart-tick", "text-anchor": "end"});
  endLabel.textContent = new Date(maxX * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit"});
  svg.append(startLabel, endLabel);
  const legend = document.createElement("div"); legend.className = "chart-legend";
  populated.forEach((item, index) => {
    const entry = document.createElement("span");
    const swatch = document.createElement("i"); swatch.style.background = item.color || COLORS[index % COLORS.length];
    entry.append(swatch, document.createTextNode(item.label)); legend.append(entry);
  });
  container.replaceChildren(svg, legend);
}

function renderDistribution(container, rows, labelKey, valueKey = "jobs") {
  if (!rows?.length) return emptyChart(container);
  const maximum = Math.max(...rows.map(row => row[valueKey] || 0), 1);
  const fragment = document.createDocumentFragment();
  rows.forEach(row => {
    const item = document.createElement("div"); item.className = "distribution-row";
    const heading = document.createElement("div");
    const label = document.createElement("span"); label.textContent = row[labelKey] || "Unknown";
    const value = document.createElement("strong"); value.textContent = compact(row[valueKey] || 0);
    heading.append(label, value);
    const track = document.createElement("div"); track.className = "distribution-track";
    const bar = document.createElement("span"); bar.style.width = `${(row[valueKey] || 0) * 100 / maximum}%`; track.append(bar);
    item.append(heading, track); fragment.append(item);
  });
  container.replaceChildren(fragment);
}

function quotaSeries(rows) {
  const grouped = new Map();
  (rows || []).forEach(row => {
    const key = `${row.limit_id}:${row.window_type}`;
    if (!grouped.has(key)) grouped.set(key, {
      label: `${row.limit_name || row.limit_id} · ${row.window_type}`,
      points: [],
    });
    grouped.get(key).points.push({x: row.timestamp, y: row.used_percent});
  });
  return [...grouped.values()];
}

function renderPacing(rows) {
  const container = $("quotaPacing"); container.replaceChildren();
  if (!rows?.length) return container.append(Object.assign(document.createElement("div"), {className: "chart-empty", textContent: "Observed pace unavailable"}));
  rows.forEach(row => {
    const item = document.createElement("div"); item.className = "pace-row";
    const heading = document.createElement("strong"); heading.textContent = `${row.limit_name || row.limit_id} · ${row.window_type}`;
    const values = document.createElement("span");
    const delta = row.deltas?.last_6_hours;
    const pace = row.recent_points_per_hour;
    values.textContent = `${delta === null ? "6h change unavailable" : `6h ${delta >= 0 ? "+" : ""}${delta} points`} · ${pace === null ? "pace unavailable" : `${pace} points/hour observed`}`;
    item.append(heading, values); container.append(item);
  });
}

export function renderUsage(data) {
  const summary = data.summary || {};
  text("usageRangeLabel", `${new Date(data.range.start).toLocaleString()} — ${new Date(data.range.end).toLocaleString()}`);
  text("usageTasks", compact(summary.total_tasks || 0));
  text("usageSuccessful", compact(summary.successful || 0));
  text("usageFailed", compact(summary.failed || 0));
  text("usageRetried", compact(summary.retried || 0));
  text("usageInput", compact(summary.input_tokens || 0));
  text("usageCached", compact(summary.cached_input_tokens || 0));
  text("usageOutput", compact(summary.output_tokens || 0));
  text("usageReasoning", compact(summary.reasoning_output_tokens || 0));
  text("usageTotalTokens", compact(summary.total_tokens || 0));
  text("usageImages", compact(summary.images_generated || 0));
  text("usageResearch", compact(summary.research_jobs || 0));
  text("usageChat", compact(summary.chat_jobs || 0));
  text("usageStructured", compact(summary.structured_jobs || 0));
  text("usageGenerate", compact(summary.generation_jobs || 0));
  text("timeCumulative", duration(summary.cumulative_task_ms));
  text("timeBusy", duration(summary.gateway_wall_clock_busy_ms));
  text("timeCodex", duration(summary.codex_compute_ms));
  text("timeQueue", duration(summary.queue_wait_ms));
  text("timeReferences", duration(summary.reference_download_ms));
  text("timeArtifacts", duration(summary.artifact_processing_ms));
  text("timeOverhead", duration(summary.gateway_overhead_ms));
  const timeline = data.timeline || [];
  renderLines($("jobsChart"), [
    {label: "All jobs", points: timeline.map(row => ({x: row.timestamp, y: row.jobs}))},
    {label: "Successful", points: timeline.map(row => ({x: row.timestamp, y: row.successful}))},
    {label: "Failed", points: timeline.map(row => ({x: row.timestamp, y: row.failed}))},
  ], {label: "Jobs over time"});
  renderLines($("tokensChart"), [
    {label: "Total", points: timeline.map(row => ({x: row.timestamp, y: row.total_tokens}))},
    {label: "Input", points: timeline.map(row => ({x: row.timestamp, y: row.input_tokens}))},
    {label: "Cached", points: timeline.map(row => ({x: row.timestamp, y: row.cached_input_tokens}))},
    {label: "Output", points: timeline.map(row => ({x: row.timestamp, y: row.output_tokens}))},
  ], {label: "Tokens over time"});
  renderLines($("quotaHistoryChart"), quotaSeries(data.quota?.series), {maxValue: 100, suffix: "%", label: "Quota usage over time"});
  renderDistribution($("taskDistribution"), data.operations, "operation");
  renderDistribution($("modelDistribution"), data.models, "model");
  renderPacing(data.quota?.pacing);
}

export function renderPerformance(data) {
  const summary = data.summary || {};
  const image = data.latency?.image || {};
  const textLatency = data.latency?.text || {};
  text("performanceRangeLabel", `${new Date(data.range.start).toLocaleString()} — ${new Date(data.range.end).toLocaleString()}`);
  text("perfSuccess", percent(summary.success_rate));
  text("perfImageSuccess", percent(summary.image_success_rate));
  text("perfTextSuccess", percent(summary.text_success_rate));
  text("perfRetries", compact(summary.retries || 0));
  text("perfRateLimit", compact(summary.rate_limit_failures || 0));
  text("perfReference", compact(summary.reference_failures || 0));
  text("perfSafety", summary.safety_rewrites === null ? "Unavailable" : compact(summary.safety_rewrites));
  text("imageAverage", duration(image.average_ms)); text("imageP50", duration(image.p50_ms)); text("imageP95", duration(image.p95_ms));
  text("textAverage", duration(textLatency.average_ms)); text("textP50", duration(textLatency.p50_ms)); text("textP95", duration(textLatency.p95_ms));
  const body = $("modelPerformanceBody"); body.replaceChildren();
  if (!data.models?.length) {
    const row = document.createElement("tr"); const cell = document.createElement("td"); cell.colSpan = 5; cell.textContent = "No completed telemetry in this range"; row.append(cell); body.append(row);
  } else data.models.forEach(model => {
    const row = document.createElement("tr");
    [model.model, compact(model.jobs), duration(model.average_ms), duration(model.p95_ms), percent(model.failure_rate)].forEach(value => {
      const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
    });
    body.append(row);
  });
  renderDistribution($("errorDistribution"), (data.errors || []).map(row => ({...row, jobs: row.failures})), "error_type");
}

export function renderAnalyticsError(page, message) {
  const target = page === "usage" ? $("usageError") : $("performanceError");
  target.textContent = message; target.hidden = false;
}

export function clearAnalyticsError(page) {
  const target = page === "usage" ? $("usageError") : $("performanceError");
  target.textContent = ""; target.hidden = true;
}
