"use strict";
/* Pluck — MV3 service worker.
   Sayfalardaki ağ trafiğini gözleyip medya URL'lerini (m3u8/mpd/mp4) sekme
   bazında toplar. Popup açıldığında content script DOM'dan topladığını da
   ekler; ikisi birlikte yt-dlp'nin sayfa fetch'iyle yakalayamadığı kaynakları
   tamamlar (bkz. DESIGN.md §16.2).
   Ayrıca content script overlay rozetinden gelen "şu URL'yi indir" mesajını
   alıp yerel motora (FastAPI) /api/jobs isteği gönderir. */

const HELPER_PORTS = [8765, 8766, 8767, 8768, 8769, 8770];
const MEDIA_EXT_RE = /\.(m3u8|mpd|mp4|m4s)(?:$|\?)/i;

// MV3 service worker uyuyabilir; tabUrls Map kalıcılık değil sadece hızlı
// erişim için. Kalıcılık chrome.storage.session ile sağlanır (chrome 102+).
const tabUrls = new Map(); // tabId -> Set<url>

function addUrl(tabId, url) {
  if (typeof tabId !== "number" || tabId < 0) return;
  if (!url || typeof url !== "string") return;
  let set = tabUrls.get(tabId);
  if (!set) {
    set = new Set();
    tabUrls.set(tabId, set);
  }
  if (!set.has(url)) {
    set.add(url);
    persistTab(tabId, set);
  }
}

function persistTab(tabId, set) {
  // chrome.storage.session worker yeniden başlasa da hayatta kalır.
  try {
    chrome.storage.session.set({ [`tab_${tabId}`]: [...set] });
  } catch {
    /* tarayıcı oturum API'sini desteklemiyor — sorun değil, RAM yedek */
  }
}

async function restoreTab(tabId) {
  try {
    const data = await chrome.storage.session.get(`tab_${tabId}`);
    const urls = data[`tab_${tabId}`];
    if (Array.isArray(urls)) {
      tabUrls.set(tabId, new Set(urls));
    }
  } catch {
    /* yok say */
  }
}

chrome.webRequest.onResponseStarted.addListener(
  ({ tabId, url }) => {
    if (MEDIA_EXT_RE.test(url)) addUrl(tabId, url);
  },
  { urls: ["<all_urls>"], types: ["xmlhttprequest", "media", "other"] },
);

chrome.tabs.onRemoved.addListener((tabId) => {
  tabUrls.delete(tabId);
  try { chrome.storage.session.remove(`tab_${tabId}`); } catch {}
});

// Yeni navigasyon: önceki sayfanın URL'leri artık alakasız.
chrome.webNavigation.onBeforeNavigate?.addListener((details) => {
  if (details.frameId === 0) {
    tabUrls.delete(details.tabId);
    try { chrome.storage.session.remove(`tab_${details.tabId}`); } catch {}
  }
});

// --- Yerel motor iletişimi (rozet tıklamasıyla doğrudan indir) ------------

let cachedHelperBase = null;
let cachedHelperConfig = null;

async function findHelper() {
  if (cachedHelperBase) {
    // Doğrula — motor yeniden başlatılmış olabilir; başarısızsa yeniden ara.
    try {
      const r = await fetch(`${cachedHelperBase}/api/config`,
                            { signal: AbortSignal.timeout(800) });
      if (r.ok) {
        cachedHelperConfig = await r.json();
        return cachedHelperBase;
      }
    } catch { /* yeniden tara */ }
    cachedHelperBase = null;
    cachedHelperConfig = null;
  }
  for (const port of HELPER_PORTS) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/config`,
                            { signal: AbortSignal.timeout(800) });
      if (r.ok) {
        cachedHelperBase = `http://127.0.0.1:${port}`;
        cachedHelperConfig = await r.json();
        return cachedHelperBase;
      }
    } catch { /* bu port boş — sonrakini dene */ }
  }
  return null;
}

async function downloadUrl(url) {
  const base = await findHelper();
  if (!base) {
    return { ok: false, error: "Pluck motoru bulunamadı (start.bat çalıştırın)" };
  }
  try {
    const res = await fetch(`${base}/api/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        selection: "best",
        download_dir: cachedHelperConfig.default_download_dir,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      return { ok: false, error: (data && data.detail) || `HTTP ${res.status}` };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err && err.message ? err.message : String(err) };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Popup → "bu sekme için toplanmış URL'leri ver"
  if (msg && msg.type === "GET_TAB_URLS" && typeof msg.tabId === "number") {
    (async () => {
      if (!tabUrls.has(msg.tabId)) await restoreTab(msg.tabId);
      sendResponse({ urls: [...(tabUrls.get(msg.tabId) || [])] });
    })();
    return true; // async response
  }
  // Content script → "DOM'da bu URL'leri buldum, sekmeye ekle"
  if (msg && msg.type === "DOM_URLS" && Array.isArray(msg.urls)) {
    const tabId = sender.tab && sender.tab.id;
    if (typeof tabId === "number") {
      for (const u of msg.urls) addUrl(tabId, u);
    }
    return;
  }
  // Content script overlay rozeti → "şu URL'yi indir"
  if (msg && msg.type === "DOWNLOAD_URL" && typeof msg.url === "string") {
    downloadUrl(msg.url).then(sendResponse);
    return true; // async response
  }
});
