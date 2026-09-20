import {requestBlob} from "./api.js?v=8c-20260920";
import {compact, duration} from "./jobs.js?v=8c-20260920";

let cardObjectUrls = [];
let lightboxObjectUrl = null;
let items = [];
let currentIndex = -1;
let scale = 1;
let offsetX = 0;
let offsetY = 0;
let dragging = false;
let dragStart = null;

const $ = id => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

async function loadProtectedImage(image, path) {
  try {
    const blob = await requestBlob(path);
    if (!image.isConnected) return;
    const objectUrl = URL.createObjectURL(blob);
    cardObjectUrls.push(objectUrl);
    image.src = objectUrl;
  } catch {
    image.replaceWith(el("span", "", "Preview unavailable"));
  }
}

function revokeCards() {
  cardObjectUrls.forEach(url => URL.revokeObjectURL(url));
  cardObjectUrls = [];
}

export function renderGallery(container, galleryItems) {
  revokeCards();
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
      loadProtectedImage(image, item.artifact.thumbnail_url);
      card.addEventListener("click", () => openLightbox(index));
    } else {
      thumb.append(el("span", "", item.error?.type || "Image unavailable"));
      card.addEventListener("click", () => document.dispatchEvent(new CustomEvent("gallery:inspect-job", {detail: item.job_id})));
    }
    const info = el("span", "gallery-info");
    info.append(el("strong", "", item.request_id), el("span", "", `${item.status} · ${duration(item.elapsed_ms)} · ${item.model}`),
      el("span", "", `${item.reference_count || 0} refs · ${compact(item.total_tokens)} tok`));
    card.append(thumb, info);
    container.append(card);
  });
}

function applyTransform() {
  $("lightboxImage").style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`;
}

function fitImage() {
  const image = $("lightboxImage");
  const stage = $("lightboxStage");
  if (!image.naturalWidth) return;
  scale = Math.min(1, (stage.clientWidth - 50) / image.naturalWidth, (stage.clientHeight - 50) / image.naturalHeight);
  offsetX = offsetY = 0;
  applyTransform();
}

async function showCurrent() {
  const available = items.map((item, index) => item.artifact ? index : -1).filter(index => index >= 0);
  if (!available.includes(currentIndex)) return;
  const item = items[currentIndex];
  $("lightboxTitle").textContent = item.request_id;
  const image = $("lightboxImage");
  image.alt = `Generated image for ${item.request_id}`;
  image.removeAttribute("src");
  if (lightboxObjectUrl) URL.revokeObjectURL(lightboxObjectUrl);
  lightboxObjectUrl = null;
  try {
    const blob = await requestBlob(item.artifact.url);
    if ($("lightbox").hidden) return;
    lightboxObjectUrl = URL.createObjectURL(blob);
    image.onload = fitImage;
    image.src = lightboxObjectUrl;
  } catch {
    image.alt = "Full image unavailable";
  }
}

export function openLightbox(index) {
  if (!items[index]?.artifact) return;
  currentIndex = index;
  $("lightbox").hidden = false;
  document.body.style.overflow = "hidden";
  $("closeLightbox").focus();
  showCurrent();
}

export function closeLightbox() {
  $("lightbox").hidden = true;
  document.body.style.overflow = "";
  if (lightboxObjectUrl) URL.revokeObjectURL(lightboxObjectUrl);
  lightboxObjectUrl = null;
  $("lightboxImage").removeAttribute("src");
}

function navigate(direction) {
  const available = items.map((item, index) => item.artifact ? index : -1).filter(index => index >= 0);
  const position = available.indexOf(currentIndex);
  if (position < 0 || !available.length) return;
  currentIndex = available[(position + direction + available.length) % available.length];
  showCurrent();
}

export function initializeLightbox() {
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
  $("lightboxStage").addEventListener("pointerup", () => { dragging = false; $("lightboxStage").classList.remove("dragging"); });
  document.addEventListener("keydown", event => {
    if ($("lightbox").hidden) return;
    if (event.key === "Escape") closeLightbox();
    else if (event.key === "ArrowLeft") navigate(-1);
    else if (event.key === "ArrowRight") navigate(1);
  });
}

window.addEventListener("beforeunload", () => { revokeCards(); if (lightboxObjectUrl) URL.revokeObjectURL(lightboxObjectUrl); });
