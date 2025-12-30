const REFRESH_SECONDS = 10;

const grid = document.getElementById("camera-grid");
const cameraCount = document.getElementById("camera-count");
const incidentCount = document.getElementById("incident-count");
const errorCount = document.getElementById("error-count");
const lastUpdated = document.getElementById("last-updated");
const refreshInterval = document.getElementById("refresh-interval");
const template = document.getElementById("camera-card-template");

refreshInterval.textContent = `${REFRESH_SECONDS}s`;

const badgeClass = (state) => {
  if (!state) return "unknown";
  return state;
};

const formatConfidence = (value) => {
  if (value === null || value === undefined) return "--";
  return `${Math.round(value * 100)}%`;
};

const formatTime = (value) => {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString();
};

const buildCard = (camera) => {
  const node = template.content.firstElementChild.cloneNode(true);
  node.dataset.cameraId = camera.camera_id;
  node.querySelector(".card-name").textContent = camera.name || camera.camera_id;
  node.querySelector(".card-sub").textContent = `${camera.corridor || ""} ${camera.direction || ""}`.trim();
  const badge = node.querySelector(".badge");
  badge.textContent = "unknown";
  badge.classList.add("unknown");
  const link = node.querySelector(".snapshot-link");
  link.href = camera.source_url || "#";
  const img = node.querySelector(".snapshot-img");
  img.addEventListener("error", () => {
    img.classList.remove("loaded");
  });
  return node;
};

const updateCard = (node, summary) => {
  const badge = node.querySelector(".badge");
  const observed = node.querySelector(".observed-direction");
  const confidence = node.querySelector(".confidence");
  const updated = node.querySelector(".updated-at");
  const incidents = node.querySelector(".incidents");
  const notes = node.querySelector(".notes-text");
  const errors = node.querySelector(".errors");
  const img = node.querySelector(".snapshot-img");

  const latest = summary?.latest_log;
  const traffic = latest?.traffic_state || "unknown";
  badge.textContent = traffic.replace(/_/g, " ");
  badge.className = `badge ${badgeClass(traffic)}`;
  observed.textContent = latest?.observed_direction || "--";
  confidence.textContent = formatConfidence(latest?.overall_confidence);
  updated.textContent = formatTime(latest?.created_at);
  incidents.textContent = JSON.stringify(latest?.incidents || [], null, 2);
  notes.textContent = latest?.notes || "--";
  const errorText = latest?.error || latest?.skipped_reason || "--";
  errors.textContent = errorText;

  if (latest?.image_path) {
    img.src = `/frames/${encodeURIComponent(latest.image_path.split(/[\\/]/).pop())}`;
    img.classList.add("loaded");
  } else {
    img.classList.remove("loaded");
  }
};

const loadCameras = async () => {
  const response = await fetch("/cameras");
  return response.json();
};

const loadSummary = async () => {
  const response = await fetch("/status/summary");
  return response.json();
};

const ensureCards = (cameras) => {
  const existing = new Map();
  [...grid.children].forEach((node) => {
    existing.set(node.dataset.cameraId, node);
  });
  cameras.forEach((camera) => {
    if (existing.has(camera.camera_id)) return;
    const card = buildCard(camera);
    grid.appendChild(card);
  });
  cameraCount.textContent = cameras.length.toString();
};

const refresh = async () => {
  try {
    const [cameras, summary] = await Promise.all([loadCameras(), loadSummary()]);
    ensureCards(cameras);
    let incidentsTotal = 0;
    let errorsTotal = 0;
    summary.forEach((entry) => {
      const node = grid.querySelector(`[data-camera-id="${entry.camera_id}"]`);
      if (node) {
        updateCard(node, entry);
      }
      const latest = entry.latest_log;
      if (latest?.incidents?.length) {
        incidentsTotal += latest.incidents.length;
      }
      if (latest?.error) {
        errorsTotal += 1;
      }
    });
    incidentCount.textContent = incidentsTotal.toString();
    errorCount.textContent = errorsTotal.toString();
    lastUpdated.textContent = new Date().toLocaleTimeString();
  } catch (error) {
    errorCount.textContent = "!";
    lastUpdated.textContent = "error";
  }
};

refresh();
setInterval(refresh, REFRESH_SECONDS * 1000);
