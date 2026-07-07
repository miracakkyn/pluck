"use strict";
importScripts("pluck-token.js");  // PLUCK_TOKEN + pluckHeaders() (classic SW)
/* Pluck — MV3 service worker.
   Content script overlay rozetinden gelen "şu URL'yi indir" mesajını alıp
   yerel motora (FastAPI) /api/jobs isteği gönderir.

   NOT (Sprint 15): Eski webRequest/DOM_URLS sniffing boru hattı kaldırıldı —
   popup onu hiç tüketmiyordu (yetim koddu). Rozet indirmesi target.src /
   window.location.href'i doğrudan kullanır; bu SW yalnızca o mesajı motora
   iletir. Böylece `webRequest` izni ve `<all_urls>` host izni de gerekmez. */

const HELPER_PORTS = [8765, 8766, 8767, 8768, 8769, 8770];

// --- Yerel motor iletişimi (rozet tıklamasıyla doğrudan indir) ------------

let cachedHelperBase = null;
let cachedHelperConfig = null;

async function findHelper() {
  if (cachedHelperBase) {
    // Doğrula — motor yeniden başlatılmış olabilir; başarısızsa yeniden ara.
    try {
      const r = await fetch(`${cachedHelperBase}/api/config`,
                            { headers: pluckHeaders(),
                              signal: AbortSignal.timeout(800) });
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
                            { headers: pluckHeaders(),
                              signal: AbortSignal.timeout(800) });
      if (r.ok) {
        cachedHelperBase = `http://127.0.0.1:${port}`;
        cachedHelperConfig = await r.json();
        return cachedHelperBase;
      }
    } catch { /* bu port boş — sonrakini dene */ }
  }
  return null;
}

const VALID_SELECTIONS = new Set(["best", "1080p", "720p", "480p", "audio"]);

/** Rozet indirmesinde kullanılacak çerez tarayıcısını belirle.
 *  Öncelik: kullanıcının popup'ta seçtiği (storage.local.cookieBrowser);
 *  yoksa motor config'inde firefox varsa firefox; yoksa ilk tarayıcı; o da
 *  yoksa null (çerezsiz). Login'li sitelerde çerez şart — popup ile aynı
 *  varsayılanı kullanmak rozet ve popup davranışını tutarlı kılar. */
async function resolveBrowser() {
  try {
    const data = await chrome.storage.local.get("cookieBrowser");
    if (typeof data.cookieBrowser === "string" && data.cookieBrowser) {
      return data.cookieBrowser;
    }
  } catch { /* storage erişilemez — config varsayılanına düş */ }
  const browsers = (cachedHelperConfig && cachedHelperConfig.browsers) || [];
  if (browsers.includes("firefox")) return "firefox";
  return browsers[0] || null;
}

async function downloadUrl(url, selection, referer) {
  const base = await findHelper();
  if (!base) {
    return { ok: false, error: "Pluck motoru bulunamadı (start.bat çalıştırın)" };
  }
  // Güvenli yedek: tanımsız/uydurma değer gelirse "best"e düş.
  const pickedSelection = VALID_SELECTIONS.has(selection) ? selection : "best";
  const browser = await resolveBrowser();
  const body = {
    url,
    selection: pickedSelection,
    download_dir: cachedHelperConfig.default_download_dir,
  };
  if (browser) body.browser = browser;
  // Referer yalnızca http(s) ise gönder (backend loopback/şema doğrular).
  if (typeof referer === "string" && /^https?:\/\//i.test(referer)) {
    body.referer = referer;
  }
  try {
    const res = await fetch(`${base}/api/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...pluckHeaders() },
      body: JSON.stringify(body),
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
  // Content script overlay rozeti → "şu URL'yi indir"
  if (msg && msg.type === "DOWNLOAD_URL" && typeof msg.url === "string") {
    downloadUrl(msg.url, msg.selection, msg.referer).then(sendResponse);
    return true; // async response
  }
});
