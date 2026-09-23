import {getJob, requestBlob} from "./api.js?v=ui-refresh-phase-a-20260923";
import {compact, duration} from "./jobs.js?v=ui-refresh-phase-a-20260923";

let cardObjectUrls = [];
let lightboxObjectUrl = null;
let items = [];
let currentIndex = -1;
let scale = 1;
let offsetX = 0;
let offsetY = 0;
let dragging = false;
let dragStart = null;
let imageLoadGeneration = 0;
let returnFocus = null;
let oldBodyOverflow = "";
let referenceLoadGeneration = 0;
let selectedReference = null;
let standaloneReference = null;
let galleryReferenceUrls = [];
// Controllers do actual network cancellation; generations prevent stale paint
// even if a mocked fetch or a browser cache completes after abort().
let cardRequests = new AbortController();
let cardLoadGeneration = 0;
let referenceRequests = new AbortController();
let viewerRequests = null;

function imageFailureMessage(error) {
  if (error?.type === "artifact_missing") return "Archived image file missing";
  if (error?.status === 404 || error?.status === 410) return "No retained image or record removed";
  if (error?.status === 401 || error?.status === 403) return "Image access denied — reconnect";
  return "Preview temporarily unavailable";
}

function revokeReferences() {
  referenceRequests.abort();
  referenceRequests = new AbortController();
  ++referenceLoadGeneration;
  galleryReferenceUrls.forEach(url => URL.revokeObjectURL(url));
  galleryReferenceUrls = [];
  $("lightboxReferences").replaceChildren();
  $("lightboxReferences").hidden = true;
}


const $ = id => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

async function loadProtectedImage(image, path, fallback, generation, signal) {
  try {
    const blob = await requestBlob(path, {signal}).catch(error => {
      if (signal.aborted || error?.name === "AbortError" || !fallback || fallback === path) throw error;
      return requestBlob(fallback, {signal});
    });
    if (signal.aborted || generation !== cardLoadGeneration || !image.isConnected) return;
    const objectUrl = URL.createObjectURL(blob);
    cardObjectUrls.push(objectUrl);
    image.src = objectUrl;
  } catch (error) {
    if (signal.aborted || generation !== cardLoadGeneration || !image.isConnected) return;
    image.replaceWith(el("span", "reference-unavailable", imageFailureMessage(error)));
  }
}

function revokeCards() {
  cardRequests.abort();
  cardRequests = new AbortController();
  ++cardLoadGeneration;
  cardObjectUrls.forEach(url => URL.revokeObjectURL(url));
  cardObjectUrls = [];
}

// Leaving Gallery should stop offscreen downloads; returning rerenders cards.
export function abortGalleryMedia() {
  revokeCards();
  revokeReferences();
}

export function renderGallery(container, galleryItems) {
  revokeCards();
  const cardGeneration = cardLoadGeneration;
  const signal = cardRequests.signal;
  items = galleryItems;
  container.replaceChildren();
  if (!items.length) {
    const empty = el("div", "empty-state");
    empty.append(el("strong", "", "No images found"), el("span", "", "Completed image jobs will appear here."));
    container.append(empty);
    return;
  }
  items.forEach((item, index) => {
    const card = el("button", `gallery-card ${item.status}`);
    card.type = "button";
    const thumb = el("span", "gallery-thumb");
    if (item.artifact) {
      const image = el("img");
      image.alt = "";
      thumb.append(image);
      loadProtectedImage(image, item.artifact.thumbnail_url || item.artifact.url, item.artifact.url, cardGeneration, signal);
      card.addEventListener("click", () => openLightbox(index));
    } else {
      const unavailable = item.artifact_status === "not_retained_or_removed"
        ? "Not retained or removed"
        : item.artifact_status === "record_missing" ? "Artifact record missing"
        : item.error?.type || "Image unavailable";
      thumb.append(el("span", "", unavailable));
      card.addEventListener("click", () => document.dispatchEvent(new CustomEvent("gallery:inspect-job", {detail: item.job_id})));
    }
    const info = el("span", "gallery-info");
    info.append(el("strong", "", item.request_id), el("span", "", `${item.status} · ${duration(item.elapsed_ms)} · ${item.model}`),
      el("span", "", `${item.reference_count || 0} refs · ${compact(item.total_tokens)} tok`));
    if (item.artifact?.availability?.original && item.artifact.availability.original !== "available"
        && item.artifact.availability.original !== "unknown") {
      info.append(el("span", "reference-unavailable", "Archived original missing or inaccessible"));
    }
    card.append(thumb, info);
    container.append(card);
  });
}

function applyTransform() {
  $("lightboxImage").style.transform = `translate(calc(-50% + ${offsetX}px), calc(-50% + ${offsetY}px)) scale(${scale})`;
}

function fitImage() {
  const image = $("lightboxImage");
  const stage = $("lightboxStage");
  if (!image.naturalWidth) return;
  scale = Math.min(1, (stage.clientWidth - 50) / image.naturalWidth, (stage.clientHeight - 50) / image.naturalHeight);
  offsetX = offsetY = 0;
  applyTransform();
}

async function showProtectedViewerImage(path, title, alt) {
  viewerRequests?.abort();
  viewerRequests = new AbortController();
  const signal = viewerRequests.signal;
  const image = $("lightboxImage");
  const generation = ++imageLoadGeneration;
  const status = $("lightboxStatus");
  $("lightboxTitle").textContent = title;
  image.alt = alt;
  image.onload = null;
  image.hidden = true;
  image.removeAttribute("src");
  if (lightboxObjectUrl) URL.revokeObjectURL(lightboxObjectUrl);
  lightboxObjectUrl = null;
  status.textContent = "Loading image…";
  status.hidden = false;
  try {
    const blob = await requestBlob(path, {signal});
    if (signal.aborted || generation !== imageLoadGeneration || $("lightbox").hidden) return;
    const objectUrl = URL.createObjectURL(blob);
    lightboxObjectUrl = objectUrl;
    image.onload = () => {
      if (generation !== imageLoadGeneration) return;
      image.hidden = false;
      status.hidden = true;
      fitImage();
    };
    image.src = objectUrl;
  } catch (error) {
    if (signal.aborted || generation !== imageLoadGeneration || $("lightbox").hidden) return;
    status.textContent = imageFailureMessage(error);
    status.hidden = false;
  }
}

function showReference(index) {
  const ref = selectedReference?.references[index];
  if (!ref?.url) return;
  selectedReference.activeIndex = index;
  $("lightboxOutput").disabled = false;
  $("lightboxOutput").setAttribute("aria-pressed", "false");
  $("lightboxReferences").querySelectorAll(".lightbox-reference").forEach(
    (button, i) => button.classList.toggle("active", i === index)
  );
  showProtectedViewerImage(ref.url, `Reference ${ref.ordinal} · ${selectedReference.requestId}`,
    `Reference ${ref.ordinal} for ${selectedReference.requestId}`);
}

function showOutput() {
  if (!selectedReference) return;
  selectedReference.activeIndex = -1;
  $("lightboxOutput").disabled = true;
  $("lightboxOutput").setAttribute("aria-pressed", "true");
  $("lightboxReferences").querySelectorAll(".lightbox-reference").forEach(button => button.classList.remove("active"));
  showProtectedViewerImage(selectedReference.outputUrl, selectedReference.requestId,
    `Generated image for ${selectedReference.requestId}`);
}

// Jobs provides reference metadata with its selected job; Gallery fetches the
// same metadata only when its viewer opens. Share the renderer to keep image
// behavior and thumbnail fallbacks identical in both places.
function paintReferenceRail(references, requestId, outputUrl, generation) {
  if (generation !== referenceLoadGeneration || $("lightbox").hidden) return;
  const section = $("lightboxReferences");
  section.hidden = false;
  selectedReference = {requestId, outputUrl, references, activeIndex: -1};
  section.replaceChildren();
  section.append(el("span", "lightbox-reference-label", `References · ${references.length}`));
  for (const [index, ref] of references.entries()) {
    const button = el("button", "lightbox-reference");
    button.type = "button";
    button.title = `View reference ${ref.ordinal}`;
    button.setAttribute("aria-label", `View reference ${ref.ordinal} for ${requestId}`);
    button.append(el("span", "lightbox-reference-frame"), el("span", "", `Ref ${ref.ordinal}`));
    const frame = button.firstElementChild;
    if (!ref.url) {
      button.disabled = true;
      button.title = `Reference ${ref.ordinal} no longer retained`;
      frame.append(el("span", "reference-unavailable", "Unavailable"));
    } else {
      const image = el("img");
      image.alt = `Reference ${ref.ordinal}`;
      frame.append(image);
      button.addEventListener("click", () => showReference(index));
      // Preserve list order; only paint if viewer still shows this job.
      const signal = referenceRequests.signal;
      requestBlob(ref.thumbnail_url || ref.url, {signal}).catch(error => {
        if (signal.aborted || error?.name === "AbortError" || !ref.thumbnail_url || ref.thumbnail_url === ref.url) throw error;
        return requestBlob(ref.url, {signal});
      }).then(blob => {
        if (signal.aborted || generation !== referenceLoadGeneration || !image.isConnected) return;
        const url = URL.createObjectURL(blob);
        galleryReferenceUrls.push(url);
        image.src = url;
      }).catch(error => {
        if (!signal.aborted && generation === referenceLoadGeneration && frame.isConnected) {
          frame.replaceChildren(el("span", "reference-unavailable", imageFailureMessage(error)));
        }
      });
    }
    section.append(button);
  }
}

async function showReferencesForJob(item) {
  revokeReferences();
  const generation = referenceLoadGeneration;
  const section = $("lightboxReferences");
  if (!item.reference_count) return;
  section.hidden = false;
  section.append(el("span", "lightbox-reference-note", "Loading references…"));
  try {
    const detail = await getJob(item.job_id, {signal: referenceRequests.signal});
    if (referenceRequests.signal.aborted || generation !== referenceLoadGeneration || $("lightbox").hidden || items[currentIndex]?.job_id !== item.job_id) return;
    paintReferenceRail(detail.job?.references || [], item.request_id, item.artifact.url, generation);
  } catch (error) {
    if (!referenceRequests.signal.aborted && generation === referenceLoadGeneration && section.isConnected) {
      section.replaceChildren(el("span", "lightbox-reference-note", "References unavailable. The output image is unaffected."));
    }
  }
}

async function showCurrent() {
  const item = items[currentIndex];
  if (!item?.artifact) return;
  standaloneReference = null;
  selectedReference = {requestId: item.request_id, outputUrl: item.artifact.url, references: [], activeIndex: -1};
  $("lightboxOutput").hidden = false;
  $("lightboxOutput").disabled = true;
  $("lightboxOutput").setAttribute("aria-pressed", "true");
  $("previousImage").hidden = false;
  $("nextImage").hidden = false;
  showProtectedViewerImage(item.artifact.url, item.request_id, `Generated image for ${item.request_id}`);
  showReferencesForJob(item);
}

export function openLightbox(index) {
  if (!items[index]?.artifact) return;
  currentIndex = index;
  if ($("lightbox").hidden) {
    returnFocus = document.activeElement;
    oldBodyOverflow = document.body.style.overflow;
  }
  $("lightbox").hidden = false;
  document.body.style.overflow = "hidden";
  $("closeLightbox").focus();
  showCurrent();
}

export function closeLightbox() {
  if ($("lightbox").hidden) return;
  $("lightbox").hidden = true;
  ++imageLoadGeneration;
  viewerRequests?.abort();
  viewerRequests = null;
  revokeReferences();
  selectedReference = null;
  standaloneReference = null;
  document.body.style.overflow = oldBodyOverflow;
  const image = $("lightboxImage");
  image.onload = null;
  image.hidden = true;
  image.removeAttribute("src");
  $("lightboxStatus").hidden = true;
  if (lightboxObjectUrl) URL.revokeObjectURL(lightboxObjectUrl);
  lightboxObjectUrl = null;
  if (returnFocus?.isConnected) returnFocus.focus();
  returnFocus = null;
}

function navigate(direction) {
  const available = items.map((item, index) => item.artifact ? index : -1).filter(index => index >= 0);
  const position = available.indexOf(currentIndex);
  if (position < 0 || !available.length) return;
  currentIndex = available[(position + direction + available.length) % available.length];
  showCurrent();
}

export function initializeLightbox() {
  $("lightboxOutput").addEventListener("click", showOutput);
  document.addEventListener("job:preview-generated", event => {
    const {url, requestId, references} = event.detail || {};
    if (typeof url !== "string" || !url.startsWith("/dashboard/api/artifacts/")) return;
    if ($("lightbox").hidden) {
      returnFocus = document.activeElement;
      oldBodyOverflow = document.body.style.overflow;
    }
    $("lightbox").hidden = false;
    document.body.style.overflow = "hidden";
    revokeReferences();
    // A Jobs preview is outside the Gallery list. Disable Gallery navigation
    // while retaining the reference rail and Show generated control.
    standaloneReference = {url, requestId};
    selectedReference = {requestId, outputUrl: url, references: [], activeIndex: -1};
    $("lightboxOutput").hidden = false;
    $("lightboxOutput").disabled = true;
    $("lightboxOutput").setAttribute("aria-pressed", "true");
    $("previousImage").hidden = true;
    $("nextImage").hidden = true;
    $("closeLightbox").focus();
    showProtectedViewerImage(url, requestId || "Generated image", `Generated image for ${requestId || "job"}`);
    if (Array.isArray(references) && references.length) {
      paintReferenceRail(references, requestId || "job", url, referenceLoadGeneration);
    }
  });
  document.addEventListener("job:preview-reference", event => {
    const {url, title, requestId} = event.detail || {};
    if (typeof url !== "string" || !url.startsWith("/dashboard/api/artifacts/")) return;
    if ($("lightbox").hidden) {
      returnFocus = document.activeElement;
      oldBodyOverflow = document.body.style.overflow;
    }
    $("lightbox").hidden = false;
    document.body.style.overflow = "hidden";
    standaloneReference = {url, title, requestId};
    selectedReference = null;
    revokeReferences();
    $("lightboxOutput").hidden = true;
    $("previousImage").hidden = true;
    $("nextImage").hidden = true;
    $("closeLightbox").focus();
    showProtectedViewerImage(url, title || "Reference image", title || "Reference image");
  });
  $("closeLightbox").addEventListener("click", closeLightbox);
  $("zoomIn").addEventListener("click", () => { scale = Math.min(8, scale * 1.25); applyTransform(); });
  $("zoomOut").addEventListener("click", () => { scale = Math.max(.1, scale / 1.25); applyTransform(); });
  $("zoomFit").addEventListener("click", fitImage);
  $("zoomActual").addEventListener("click", () => { scale = 1; offsetX = offsetY = 0; applyTransform(); });
  $("previousImage").addEventListener("click", () => navigate(-1));
  $("nextImage").addEventListener("click", () => navigate(1));
  $("lightboxImage").addEventListener("dblclick", fitImage);
  $("lightboxStage").addEventListener("wheel", event => {
    event.preventDefault(); scale = Math.min(8, Math.max(.1, scale * (event.deltaY < 0 ? 1.12 : .89))); applyTransform();
  }, {passive: false});
  $("lightboxStage").addEventListener("pointerdown", event => {
    dragging = true; dragStart = {x: event.clientX - offsetX, y: event.clientY - offsetY};
    $("lightboxStage").classList.add("dragging"); $("lightboxStage").setPointerCapture(event.pointerId);
  });
  $("lightboxStage").addEventListener("pointermove", event => {
    if (!dragging) return; offsetX = event.clientX - dragStart.x; offsetY = event.clientY - dragStart.y; applyTransform();
  });
  const endDrag = () => { dragging = false; $("lightboxStage").classList.remove("dragging"); };
  $("lightboxStage").addEventListener("pointerup", endDrag);
  $("lightboxStage").addEventListener("pointercancel", endDrag);
  document.addEventListener("keydown", event => {
    if ($("lightbox").hidden) return;
    if (event.key === "Escape") closeLightbox();
    else if (!standaloneReference && event.key === "ArrowLeft") navigate(-1);
    else if (!standaloneReference && event.key === "ArrowRight") navigate(1);
  });
}

window.addEventListener("beforeunload", () => {
  viewerRequests?.abort();
  revokeCards(); revokeReferences();
  if (lightboxObjectUrl) URL.revokeObjectURL(lightboxObjectUrl);
});
