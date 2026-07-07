"use strict";
/* Pluck — tek sayfa arayüz mantığı.
   Backend sözleşmesi: bkz. DESIGN.md §6. */

// --- kısayollar & yardımcılar -------------------------------------------

const $ = (sel) => document.querySelector(sel);

// Yerel API paylaşımlı jetonu — motorun config.api_token() varsayılanı ve
// extension/pluck-token.js ile BİREBİR eşleşmelidir. /api/* uçları bunu
// X-Pluck-Token başlığında ister (SSE hariç). Motorda PLUCK_TOKEN ortam
// değişkeni ayarlanırsa bu sabiti de güncelleyin.
const PLUCK_TOKEN = "pluck-local-v1-a3f19c7e";

const PRESET_LABELS = {
  best: "En yüksek",
  "1080p": "1080p",
  "720p": "720p",
  "480p": "480p",
  audio: "Ses (MP3)",
};
const KIND_ICONS = { video: "🎬", audio: "🎵", combined: "🎞" };

/** FastAPI hata gövdesini (string ya da 422 dizisi) okunur metne çevirir. */
function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => e.msg || "Geçersiz girdi").join("; ");
  }
  return "Bilinmeyen hata";
}

/** Ortak JSON API çağrısı; hata durumunda anlamlı Error fırlatır. */
async function api(method, path, body, timeoutMs = 120000) {
  const opts = {
    method,
    headers: { "X-Pluck-Token": PLUCK_TOKEN },
    signal: AbortSignal.timeout(timeoutMs),
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    // Zaman aşımı veya bağlantı hatası — asılı kalma yerine anlamlı hata ver.
    if (err && err.name === "TimeoutError") {
      throw new Error(`İstek zaman aşımına uğradı (${timeoutMs / 1000}sn).`);
    }
    throw new Error(err && err.message ? err.message : "Bağlantı hatası");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data ? detailText(data.detail) : `Sunucu hatası (${res.status})`);
  }
  return data;
}

function humanSize(bytes) {
  if (!bytes || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(1)} ${units[i]}`;
}

function humanDuration(seconds) {
  if (!seconds || seconds <= 0) return "";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function showError(el, message) {
  el.textContent = message;
  el.hidden = false;
}
function clearError(el) {
  el.textContent = "";
  el.hidden = true;
}

// --- durum --------------------------------------------------------------

let appConfig = null;
let currentScan = null;    // son tarama (tek video veya playlist)
let selection = "best";    // preset adı veya ham format_id

// --- başlatma -----------------------------------------------------------

async function init() {
  try {
    appConfig = await api("GET", "/api/config");
    $("#download-dir").value = appConfig.default_download_dir;
    populateBrowsers(appConfig.browsers);
    populateCommonDirs(appConfig.common_dirs);
  } catch (err) {
    showError($("#compose-error"), `Yapılandırma yüklenemedi: ${err.message}`);
  }
  connectEventStream();
  bindEvents();
}

function populateBrowsers(browsers) {
  const select = $("#browser");
  for (const name of browsers) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name.charAt(0).toUpperCase() + name.slice(1);
    select.appendChild(opt);
  }
  // Varsayılan: firefox (Firefox çerezleri Firefox açıkken bile okunabilir).
  if (browsers.includes("firefox")) {
    select.value = "firefox";
  }
}

function populateCommonDirs(dirs) {
  const list = $("#common-dirs");
  for (const dir of dirs) {
    const opt = document.createElement("option");
    opt.value = dir;
    list.appendChild(opt);
  }
}

function bindEvents() {
  $("#fetch-btn").addEventListener("click", fetchFormats);
  $("#url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fetchFormats();
  });
  $("#add-btn").addEventListener("click", addToQueue);
  $("#add-all-btn").addEventListener("click", addAllEntries);
  $("#browse-btn").addEventListener("click", pickFolder);
  $("#clear-queue-btn").addEventListener("click", clearFinishedJobs);
}

// --- klasör seçici ------------------------------------------------------

async function pickFolder() {
  const btn = $("#browse-btn");
  btn.disabled = true;
  btn.textContent = "Pencere açık…";
  try {
    await api("POST", "/api/pick-folder");
    const result = await waitForPickResult();
    if (result?.path) {
      $("#download-dir").value = result.path;
    } else if (result?.error) {
      showError($("#video-error"), result.error);
    } else if (!result) {
      // waitForPickResult zaman aşımına uğradı — sessiz kalma.
      showError($("#video-error"),
        "Klasör seçimi zaman aşımına uğradı. Yolu elle yazabilirsiniz.");
    }
  } catch (err) {
    showError($("#video-error"), `Klasör seçilemedi: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Gözat…";
  }
}

/**
 * Klasör seçimi tamamlanana kadar durumu yoklar.
 * @returns {Promise<{path: string|null, error: string|null}|null>}
 */
async function waitForPickResult(timeoutMs = 120000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 600));
    const state = await api("GET", "/api/pick-folder");
    if (!state.pending) {
      return { path: state.path, error: state.error };
    }
  }
  return null;
}

// --- format getirme -----------------------------------------------------

// fetchFormats birden çok kez tetiklenebilir (buton, Enter tekrarı). Yeniden-
// giriş koruması olmadan yavaş bir yanıt yenisini ezebilir; sıra numarasıyla
// yalnızca EN SON getirme DOM'u günceller (bkz. scanPage/#28 ile aynı desen).
let fetchSeq = 0;

async function fetchFormats() {
  const url = $("#url").value.trim();
  clearError($("#compose-error"));
  if (!url) {
    showError($("#compose-error"), "Önce bir video bağlantısı girin.");
    return;
  }
  const seq = ++fetchSeq;
  const btn = $("#fetch-btn");
  btn.disabled = true;
  btn.textContent = "Getiriliyor…";
  try {
    const browser = $("#browser").value || null;
    const scan = await api("POST", "/api/formats", { url, browser });
    if (seq !== fetchSeq) return;  // daha yeni bir getirme başladı — bayat sonucu at
    currentScan = scan;
    if (currentScan.type === "playlist") {
      renderPlaylist(currentScan);
    } else {
      renderVideo(currentScan.video);
    }
    renderWarnings(currentScan.warnings || []);
  } catch (err) {
    if (seq !== fetchSeq) return;  // bayat hata — daha yeni getirme sürüyor
    showError($("#compose-error"), err.message);
  } finally {
    if (seq === fetchSeq) {
      btn.disabled = false;
      btn.textContent = "Formatları getir";
    }
  }
}

/** Çoklu-video taramada çözümlenemeyen URL uyarılarını gösterir; boşsa gizler.
 *  Backend `warnings[]` gönderir (sessiz başarısızlığı önlemek için) — web
 *  arayüzü bunları eskiden hiç göstermiyordu. */
function renderWarnings(warnings) {
  const box = $("#video-warnings");
  if (!warnings.length) {
    box.hidden = true;
    box.replaceChildren();
    return;
  }
  box.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = `${warnings.length} video çözümlenemedi:`;
  box.appendChild(title);
  const ul = document.createElement("ul");
  for (const w of warnings) {
    const li = document.createElement("li");
    li.textContent = w;
    ul.appendChild(li);
  }
  box.appendChild(ul);
  box.hidden = false;
}

function renderVideo(video) {
  $("#video-title").textContent = video.title;
  $("#video-uploader").textContent = video.uploader || "";
  $("#video-duration").textContent = humanDuration(video.duration);

  const thumb = $("#video-thumb");
  if (video.thumbnail) {
    thumb.src = video.thumbnail;
    thumb.hidden = false;
  } else {
    thumb.hidden = true;
    thumb.removeAttribute("src");
  }

  selection = "best";
  renderPresets(video.presets);
  renderFormatList(video.formats);
  clearError($("#video-error"));
  $("#video-card").hidden = false;
  $("#single-controls").hidden = false;
  $("#playlist-controls").hidden = true;
  $("#video-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderPlaylist(scan) {
  const count = scan.entries.length;
  $("#video-title").textContent = `${count} video bulundu`;
  $("#video-uploader").textContent = scan.playlist_title || "";
  $("#video-duration").textContent = "";
  $("#video-thumb").hidden = true;
  $("#video-thumb").removeAttribute("src");

  selection = "best";
  const presets = scan.entries[0]?.presets || ["best", "1080p", "720p", "480p", "audio"];
  renderPresets(presets);

  const list = $("#playlist-entries");
  list.replaceChildren();
  scan.entries.forEach((entry, i) => {
    const li = document.createElement("li");
    li.className = "entry";
    const title = document.createElement("span");
    title.className = "entry-title";
    title.textContent = entry.title || `Video ${i + 1}`;
    const dur = document.createElement("span");
    dur.className = "entry-dur mono";
    dur.textContent = humanDuration(entry.duration);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary btn-sm";
    btn.textContent = "Kuyruğa ekle";
    btn.addEventListener("click", () => queueEntry(entry, btn));
    li.append(title, dur, btn);
    list.appendChild(li);
  });

  clearError($("#video-error"));
  $("#video-card").hidden = false;
  $("#single-controls").hidden = true;
  $("#playlist-controls").hidden = false;
  $("#video-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function queueEntry(entry, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    await api("POST", "/api/jobs", {
      url: entry.url,
      selection,
      download_dir: $("#download-dir").value.trim(),
      browser: $("#browser").value || null,
      title: entry.title || null,
    });
    if (btn) {
      btn.textContent = "Eklendi ✓";
      btn.classList.add("added");
    }
  } catch (err) {
    showError($("#video-error"), err.message);
    if (btn) { btn.disabled = false; btn.textContent = "Kuyruğa ekle"; }
  }
}

async function addAllEntries() {
  if (!currentScan || currentScan.type !== "playlist") return;
  const btn = $("#add-all-btn");
  btn.disabled = true;
  btn.textContent = "Ekleniyor…";
  try {
    for (const entry of currentScan.entries) {
      await api("POST", "/api/jobs", {
        url: entry.url,
        selection,
        download_dir: $("#download-dir").value.trim(),
        browser: $("#browser").value || null,
        title: entry.title || null,
      });
    }
    for (const b of document.querySelectorAll("#playlist-entries .btn")) {
      b.textContent = "Eklendi ✓";
      b.classList.add("added");
      b.disabled = true;
    }
    const flash = $("#add-all-flash");
    flash.hidden = false;
    setTimeout(() => { flash.hidden = true; }, 2500);
  } catch (err) {
    showError($("#video-error"), err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Tümünü kuyruğa ekle";
  }
}

function renderPresets(presets) {
  const row = $("#preset-row");
  row.replaceChildren();
  for (const name of presets) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preset";
    btn.dataset.preset = name;
    btn.textContent = PRESET_LABELS[name] || name;
    btn.setAttribute("aria-pressed", String(name === selection));
    btn.addEventListener("click", () => setSelection(name));
    row.appendChild(btn);
  }
}

/** Tek bir <span> oluşturur; metin DAİMA textContent ile yazılır (XSS yok). */
function span(text, className) {
  const el = document.createElement("span");
  el.textContent = text;
  if (className) el.className = className;
  return el;
}

function renderFormatList(formats) {
  const list = $("#formats-list");
  list.replaceChildren();
  for (const fmt of formats) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "fmt-row";
    row.dataset.formatId = fmt.format_id;
    row.setAttribute("aria-pressed", "false");
    // yt-dlp meta verisi (resolution/note/ext) güvenilmez kabul edilir.
    row.append(
      span(KIND_ICONS[fmt.kind] || "•", "fmt-kind"),
      span(fmt.note ? `${fmt.resolution} · ${fmt.note}` : fmt.resolution),
      span(fmt.ext, "fmt-ext"),
      span(humanSize(fmt.filesize), "fmt-size"),
    );
    row.addEventListener("click", () => setSelection(fmt.format_id));
    list.appendChild(row);
  }
}

/** Seçimi (preset veya format_id) günceller ve görsel vurguyu eşitler. */
function setSelection(value) {
  selection = value;
  for (const btn of document.querySelectorAll(".preset")) {
    btn.setAttribute("aria-pressed", String(btn.dataset.preset === value));
  }
  for (const row of document.querySelectorAll(".fmt-row")) {
    row.setAttribute("aria-pressed", String(row.dataset.formatId === value));
  }
}

// --- kuyruğa ekleme -----------------------------------------------------

async function addToQueue() {
  const url = $("#url").value.trim();
  const downloadDir = $("#download-dir").value.trim();
  const browser = $("#browser").value || null;
  clearError($("#video-error"));
  if (!url) {
    showError($("#video-error"), "Video bağlantısı boş olamaz.");
    return;
  }
  const btn = $("#add-btn");
  btn.disabled = true;
  try {
    await api("POST", "/api/jobs", {
      url,
      selection,
      download_dir: downloadDir,
      browser,
    });
    flashAdded();
  } catch (err) {
    showError($("#video-error"), err.message);
  } finally {
    btn.disabled = false;
  }
}

function flashAdded() {
  const flash = $("#add-flash");
  flash.hidden = false;
  setTimeout(() => {
    flash.hidden = true;
  }, 2500);
}

// --- canlı ilerleme (SSE) ----------------------------------------------

function connectEventStream() {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => {
    setStreamStatus(true);
    try {
      renderQueue(JSON.parse(event.data));
    } catch {
      /* bozuk çerçeve yok sayılır */
    }
  };
  source.onopen = () => setStreamStatus(true);
  // Bağlantı koparsa tarayıcı otomatik yeniden bağlanır; kullanıcıya durumu
  // bildir (aksi halde kuyruk sessizce donar ve donduğu anlaşılmaz).
  source.onerror = () => setStreamStatus(false);
}

/** Canlı SSE bağlantı durumunu kullanıcıya gösterir (kesildiyse uyarı satırı). */
function setStreamStatus(connected) {
  const el = $("#stream-status");
  if (el) el.hidden = connected;
}

const jobElements = new Map();

function renderQueue(jobs) {
  const container = $("#queue");
  const seen = new Set();
  for (const job of jobs) {
    seen.add(job.job_id);
    let el = jobElements.get(job.job_id);
    if (!el) {
      el = createJobElement();
      jobElements.set(job.job_id, el);
      container.prepend(el); // en yeni iş üstte
    }
    updateJobElement(el, job);
  }
  for (const [id, el] of jobElements) {
    if (!seen.has(id)) {
      el.remove();
      jobElements.delete(id);
    }
  }
  $("#queue-empty").hidden = jobs.length > 0;
}

function createJobElement() {
  const li = document.createElement("li");
  li.className = "job";
  li.innerHTML = `
    <div class="job-top">
      <span class="job-title"></span>
      <span class="badge"></span>
      <button type="button" class="btn btn-ghost job-cancel">İptal</button>
    </div>
    <div class="progress-track"><div class="progress-fill"></div></div>
    <div class="job-meta"></div>`;
  return li;
}

const STATUS_LABELS = {
  queued: "Sırada",
  downloading: "İndiriliyor",
  completed: "Tamamlandı",
  error: "Hata",
  cancelled: "İptal",
};

function updateJobElement(el, job) {
  el.querySelector(".job-title").textContent = job.title || job.url;

  const badge = el.querySelector(".badge");
  badge.className = `badge ${job.status}`;
  badge.textContent = STATUS_LABELS[job.status] || job.status;

  const fill = el.querySelector(".progress-fill");
  fill.className = `progress-fill ${job.status}`;
  fill.style.width = `${job.progress || 0}%`;

  const cancelBtn = el.querySelector(".job-cancel");
  const active = job.status === "queued" || job.status === "downloading";
  cancelBtn.hidden = !active;
  if (active && !cancelBtn.dataset.bound) {
    cancelBtn.dataset.bound = "1";
    cancelBtn.addEventListener("click", () => cancelJob(job.job_id));
  }

  renderJobMeta(el.querySelector(".job-meta"), job);
}

function renderJobMeta(metaEl, job) {
  metaEl.replaceChildren();
  if (job.status === "error") {
    metaEl.append(span(job.error || "Bilinmeyen hata", "err-text"));
  } else if (job.status === "downloading") {
    metaEl.append(span(`${(job.progress || 0).toFixed(1)}%`));
    if (job.speed) metaEl.append(span(job.speed));
    if (job.eta) metaEl.append(span(`kalan ${job.eta}`));
    if (job.total_bytes) metaEl.append(span(humanSize(job.total_bytes)));
  } else if (job.status === "completed") {
    const size = job.total_bytes ? ` · ${humanSize(job.total_bytes)}` : "";
    metaEl.append(span(`Bitti${size}`));
  }
}

async function cancelJob(jobId) {
  try {
    await api("DELETE", `/api/jobs/${jobId}`);
  } catch {
    /* iş zaten bitmiş olabilir; SSE bir sonraki çerçevede düzeltir */
  }
}

async function clearFinishedJobs() {
  // Kullanıcının açık eylemi: başarısız olursa (ağ/sunucu hatası) sessiz kalma,
  // aksi halde "temizle"ye basıp hiçbir şey olmaması kafa karıştırır.
  try {
    await api("POST", "/api/jobs/clear");
  } catch (err) {
    showError($("#video-error"), `Kuyruk temizlenemedi: ${err.message}`);
  }
}

// --- çalıştır -----------------------------------------------------------

init();
