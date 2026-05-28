"use strict";
/* Pluck — eklenti popup mantığı.
   Yerel motoru (FastAPI) bulur, aktif sekmenin videosunu yt-dlp'ye taratır
   ve indirme işini motora yollar. Backend sözleşmesi: bkz. DESIGN.md §6, §16. */

// Motorun denenebileceği portlar (run.py boş port arar; aralık taranır).
const HELPER_PORTS = [8765, 8766, 8767, 8768, 8769, 8770];
const POLL_INTERVAL_MS = 1200;

const PRESET_LABELS = {
  best: "En yüksek", "1080p": "1080p", "720p": "720p",
  "480p": "480p", audio: "Ses (MP3)",
};
const KIND_ICONS = { video: "🎬", audio: "🎵", combined: "🎞" };
const STATUS_LABELS = {
  queued: "Sırada", downloading: "İniyor", completed: "Bitti",
  error: "Hata", cancelled: "İptal",
};

let helperBase = null;
let appConfig = null;
let pageUrl = null;
let selection = "best";
let pollTimer = null;
let currentScan = null;  // son tarama yanıtı (tek video veya playlist)
let activeTabId = null;  // chrome.scripting.executeScript için

const $ = (sel) => document.querySelector(sel);
const setHidden = (sel, hidden) => { $(sel).hidden = hidden; };

// --- yardımcılar --------------------------------------------------------

function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((e) => e.msg || "Geçersiz girdi").join("; ");
  return "Bilinmeyen hata";
}

/** Metin DAİMA textContent ile yazılır (XSS yok). */
function span(text, className) {
  const el = document.createElement("span");
  el.textContent = text;
  if (className) el.className = className;
  return el;
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

function showError(message) {
  $("#error").textContent = message;
  $("#error").hidden = false;
}
function clearError() {
  $("#error").textContent = "";
  $("#error").hidden = true;
}

function renderWarnings(warnings) {
  const box = $("#warnings");
  box.replaceChildren();
  if (!warnings || !warnings.length) {
    box.hidden = true;
    return;
  }
  for (const w of warnings) {
    const li = document.createElement("li");
    li.textContent = w;  // textContent — XSS yok
    box.appendChild(li);
  }
  box.hidden = false;
}
function clearWarnings() { renderWarnings([]); }

// --- motor iletişimi ----------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(helperBase + path, opts);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data ? detailText(data.detail) : `Sunucu hatası (${res.status})`);
  }
  return data;
}

/** Yerel motoru port aralığında arar; bulursa yapılandırmasını döndürür. */
async function findHelper() {
  for (const port of HELPER_PORTS) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/config`, {
        signal: AbortSignal.timeout(800),
      });
      if (res.ok) {
        helperBase = `http://127.0.0.1:${port}`;
        return await res.json();
      }
    } catch {
      /* bu port boş — sonrakini dene */
    }
  }
  return null;
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}

/** Aktif sekmeye content script'i programatik enjekte eder (activeTab izniyle).
 *  Hatayı yutar — chrome://, file:// gibi imtiyazlı sayfalarda izin yoktur. */
async function injectContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["content.js"],
    });
    return true;
  } catch {
    return false;  // imtiyazlı sayfa veya tarayıcı izni reddetti
  }
}

/** Background service worker'ın bu sekme için topladığı medya URL'lerini al. */
async function getCollectedUrls(tabId) {
  try {
    const res = await chrome.runtime.sendMessage({
      type: "GET_TAB_URLS", tabId,
    });
    return (res && Array.isArray(res.urls)) ? res.urls : [];
  } catch {
    return [];
  }
}

// --- başlatma -----------------------------------------------------------

async function boot() {
  setHidden("#loading", false);
  setHidden("#helper-down", true);
  setHidden("#content", true);
  setHidden("#rescan-btn", true);

  appConfig = await findHelper();
  if (!appConfig) {
    setHidden("#loading", true);
    setHidden("#helper-down", false);
    return;
  }

  setHidden("#loading", true);
  setHidden("#content", false);
  setHidden("#rescan-btn", false);

  $("#dir").value = appConfig.default_download_dir;
  // Daha önce "Gözat" ile bir klasör seçildiyse onu kullan.
  try {
    const picked = await api("GET", "/api/pick-folder");
    if (picked && !picked.pending && picked.path) {
      $("#dir").value = picked.path;
    }
  } catch {
    /* önemsiz — varsayılan klasör kullanılır */
  }
  for (const name of appConfig.browsers) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name.charAt(0).toUpperCase() + name.slice(1);
    $("#browser").appendChild(opt);
  }
  // Varsayılan çerez: firefox (Firefox açıkken bile çerez okunabilir; en
  // güvenilir seçenek). Kullanıcı isterse değiştirebilir.
  if (appConfig.browsers.includes("firefox")) {
    $("#browser").value = "firefox";
  }
  for (const dir of appConfig.common_dirs) {
    const opt = document.createElement("option");
    opt.value = dir;
    $("#dirs").appendChild(opt);
  }

  const tab = await getActiveTab();
  pageUrl = tab && tab.url ? tab.url : null;
  activeTabId = tab && typeof tab.id === "number" ? tab.id : null;
  // Sayfaya content.js'i enjekte et — DOM tarama + overlay rozetler + ayrıca
  // background'a medya URL'leri rapor edecek. Çağrı tamamlanması zorunlu
  // değil; başarısızsa sadece backend'in HTML fetch'i yedeği kalır.
  if (activeTabId !== null) injectContentScript(activeTabId);
  startPolling();
  scanPage();
}

// --- sayfa tarama -------------------------------------------------------

function hideAllModes() {
  setHidden("#shared-controls", true);
  setHidden("#single-controls", true);
  setHidden("#playlist-controls", true);
}

async function scanPage() {
  clearError();
  clearWarnings();
  hideAllModes();
  if (!pageUrl || !/^https?:/i.test(pageUrl)) {
    $("#video-title").textContent = "Bu sekmede video yok";
    $("#video-sub").textContent = "";
    showError("Bir web sayfasında açıkken eklentiyi kullanın.");
    return;
  }
  $("#video-title").textContent = "Sayfa taranıyor…";
  $("#video-sub").textContent = pageUrl;
  try {
    const browser = $("#browser").value || null;
    // Backend'in /api/formats çağrısı zaten sayfa HTML'sini regex'le tarıyor;
    // ek olarak content script DOM + global player API + webRequest sniffer
    // ile yakalanmış URL'leri /api/probe-urls'a yolla. İki yanıttan daha çok
    // entry üretene güven (genelde probe-urls).
    const [scan, probeScan] = await Promise.allSettled([
      api("POST", "/api/formats", { url: pageUrl, browser }),
      probeCollectedUrls(browser),
    ]);
    const formatsResult = scan.status === "fulfilled" ? scan.value : null;
    const probeResult = probeScan.status === "fulfilled" ? probeScan.value : null;
    const chosen = pickBetterScan(formatsResult, probeResult);
    if (!chosen) {
      const reason = scan.status === "rejected" ? scan.reason
                    : probeScan.status === "rejected" ? probeScan.reason
                    : new Error("Sayfada video bulunamadı");
      throw reason;
    }
    currentScan = chosen;
    if (chosen.type === "playlist") {
      renderPlaylist(chosen);
    } else {
      renderVideo(chosen.video);
    }
    renderWarnings(chosen.warnings || []);
  } catch (err) {
    $("#video-title").textContent = "Video bulunamadı";
    $("#video-sub").textContent = "";
    showError(err.message || "Bilinmeyen hata");
  }
}

/** Content script + webRequest'ten toplanan URL'leri motora yollar.
 *  Hiç URL yoksa null döner (caller eski /api/formats sonucuna düşer). */
async function probeCollectedUrls(browser) {
  if (activeTabId === null) return null;
  const urls = await getCollectedUrls(activeTabId);
  if (!urls.length) return null;
  return api("POST", "/api/probe-urls", { urls, referer: pageUrl, browser });
}

/** İki tarama sonucundan "daha iyiyi" seç: playlist'i tek videoya tercih et,
 *  iki playlist arasında daha çok entry'si olanı al. */
function pickBetterScan(a, b) {
  if (!a) return b;
  if (!b) return a;
  const sizeA = a.type === "playlist" ? (a.entries || []).length : 1;
  const sizeB = b.type === "playlist" ? (b.entries || []).length : 1;
  return sizeB > sizeA ? b : a;
}

function renderVideo(video) {
  $("#video-title").textContent = video.title;
  $("#video-sub").textContent = [video.uploader, humanDuration(video.duration)]
    .filter(Boolean).join(" · ");
  selection = "best";
  renderPresets(video.presets);
  renderFormats(video.formats);
  setHidden("#shared-controls", false);
  setHidden("#single-controls", false);
  setHidden("#playlist-controls", true);
}

function renderPlaylist(scan) {
  const count = scan.entries.length;
  $("#video-title").textContent = `${count} video bulundu`;
  $("#video-sub").textContent = scan.playlist_title || "";
  selection = "best";
  // Tüm girdiler aynı preset listesini kullanır; ilk girdiden alıyoruz.
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
    btn.textContent = "İndir";
    btn.addEventListener("click", () => queueEntry(entry, btn));
    li.append(title, dur, btn);
    list.appendChild(li);
  });

  setHidden("#shared-controls", false);
  setHidden("#single-controls", true);
  setHidden("#playlist-controls", false);
}

async function queueEntry(entry, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    await api("POST", "/api/jobs", {
      url: entry.url,
      selection,
      download_dir: $("#dir").value.trim(),
      browser: $("#browser").value || null,
      title: entry.title || null,
    });
    if (btn) {
      btn.textContent = "Eklendi ✓";
      btn.classList.add("added");
    }
  } catch (err) {
    showError(err.message);
    if (btn) { btn.disabled = false; btn.textContent = "İndir"; }
  }
}

async function downloadAllEntries() {
  if (!currentScan || currentScan.type !== "playlist") return;
  const btn = $("#download-all-btn");
  btn.disabled = true;
  btn.textContent = "Ekleniyor…";
  try {
    for (const entry of currentScan.entries) {
      await api("POST", "/api/jobs", {
        url: entry.url,
        selection,
        download_dir: $("#dir").value.trim(),
        browser: $("#browser").value || null,
        title: entry.title || null,
      });
    }
    for (const b of document.querySelectorAll("#playlist-entries .btn")) {
      b.textContent = "Eklendi ✓";
      b.classList.add("added");
      b.disabled = true;
    }
    const flash = $("#playlist-flash");
    flash.hidden = false;
    setTimeout(() => { flash.hidden = true; }, 2500);
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Tümünü indir";
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

function renderFormats(formats) {
  const list = $("#fmt-list");
  list.replaceChildren();
  for (const fmt of formats) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "fmt-row";
    row.dataset.formatId = fmt.format_id;
    row.setAttribute("aria-pressed", "false");
    // yt-dlp meta verisi güvenilmez kabul edilir → textContent.
    row.append(
      span(KIND_ICONS[fmt.kind] || "•"),
      span(fmt.note ? `${fmt.resolution} · ${fmt.note}` : fmt.resolution),
      span(fmt.ext, "fmt-ext"),
      span(humanSize(fmt.filesize), "fmt-size"),
    );
    row.addEventListener("click", () => setSelection(fmt.format_id));
    list.appendChild(row);
  }
}

function setSelection(value) {
  selection = value;
  for (const btn of document.querySelectorAll(".preset")) {
    btn.setAttribute("aria-pressed", String(btn.dataset.preset === value));
  }
  for (const row of document.querySelectorAll(".fmt-row")) {
    row.setAttribute("aria-pressed", String(row.dataset.formatId === value));
  }
}

// --- indirme ------------------------------------------------------------

async function startDownload() {
  clearError();
  const downloadDir = $("#dir").value.trim();
  const browser = $("#browser").value || null;
  const btn = $("#download-btn");
  btn.disabled = true;
  try {
    await api("POST", "/api/jobs", {
      url: pageUrl,
      selection,
      download_dir: downloadDir,
      browser,
    });
    flashAdded();
    pollJobs();
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
  }
}

function flashAdded() {
  const flash = $("#flash");
  flash.hidden = false;
  setTimeout(() => { flash.hidden = true; }, 2500);
}

// --- kuyruk (yoklama) ---------------------------------------------------

function startPolling() {
  pollJobs();
  pollTimer = setInterval(pollJobs, POLL_INTERVAL_MS);
}

async function pollJobs() {
  try {
    renderQueue(await api("GET", "/api/jobs"));
  } catch {
    /* motor geçici erişilemez — sonraki yoklamada düzelir */
  }
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
      <button type="button" class="job-cancel">İptal</button>
    </div>
    <div class="progress-track"><div class="progress-fill"></div></div>
    <div class="job-meta"></div>`;
  return li;
}

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
  } else if (job.status === "completed") {
    const size = job.total_bytes ? ` · ${humanSize(job.total_bytes)}` : "";
    metaEl.append(span(`Bitti${size}`));
  }
}

async function cancelJob(jobId) {
  try {
    await api("DELETE", `/api/jobs/${jobId}`);
    pollJobs();
  } catch {
    /* iş zaten bitmiş olabilir; sonraki yoklama düzeltir */
  }
}

async function clearFinishedJobs() {
  try {
    await api("POST", "/api/jobs/clear");
    pollJobs();
  } catch {
    /* önemsiz */
  }
}

// --- klasör seçici ------------------------------------------------------

async function pickFolder() {
  // Native pencere açılınca Chrome popup'ı odağı kaybedip kapanır; kullanıcı
  // klasörü seçtikten sonra eklentiyi yeniden açtığında boot() onu doldurur.
  try {
    await api("POST", "/api/pick-folder");
  } catch (err) {
    showError(err.message);
  }
}

// --- olay bağlama -------------------------------------------------------

$("#retry-btn").addEventListener("click", boot);
$("#rescan-btn").addEventListener("click", scanPage);
$("#download-btn").addEventListener("click", startDownload);
$("#download-all-btn").addEventListener("click", downloadAllEntries);
$("#browse-btn").addEventListener("click", pickFolder);
$("#clear-queue-btn").addEventListener("click", clearFinishedJobs);

boot();
