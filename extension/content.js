"use strict";
/* Pluck — sayfa-içi içerik script.
   1. Sayfadaki <video> elementlerini bulur, her birinin sağ-üst köşesine
      Shadow DOM içinde küçük bir "Pluck ile indir" rozeti yerleştirir
      (IDM-tarzı). Rozete tıklayınca yerel motora indirme isteği gönderilir.
   2. DOM'dan ve bilinen global player API'lerinden (JWPlayer, Video.js) medya
      URL'lerini toplayıp background service worker'a yollar; popup açılınca
      bunlar /api/probe-urls'a gönderilip yt-dlp'nin kaçırdığı kaynaklar
      yakalanır.
   activeTab izniyle çalışır: popup açılınca chrome.scripting.executeScript
   ile inject edilir, content_scripts manifest entry'si yoktur. */

(function pluckContentScript() {
  // Idempotent inject: aynı sekmeye iki kez girilirse hiçbir şey yapma.
  if (window.__pluckInjected) return;
  window.__pluckInjected = true;

  const BADGE_SIZE = 36;
  const BADGE_MARGIN = 10;
  // Çok küçük player'lara rozet koyma — UI bozar, video play butonunu kapatır.
  const MIN_VIDEO_W = 120;
  const MIN_VIDEO_H = 80;

  const badges = new Map(); // video element -> { host, badge }
  const collectedUrls = new Set();

  function collectVideoSources(video) {
    const urls = [];
    if (video.currentSrc) urls.push(video.currentSrc);
    if (video.src && video.src !== video.currentSrc) urls.push(video.src);
    for (const source of video.querySelectorAll("source")) {
      if (source.src) urls.push(source.src);
    }
    return urls;
  }

  function collectGlobalPlayerUrls() {
    const urls = [];
    // JWPlayer global API
    try {
      if (typeof window.jwplayer === "function") {
        const inst = window.jwplayer();
        if (inst && typeof inst.getPlaylist === "function") {
          for (const item of inst.getPlaylist() || []) {
            if (item.file) urls.push(item.file);
            for (const s of item.sources || []) if (s.file) urls.push(s.file);
          }
        }
      }
    } catch { /* JWPlayer yok veya farklı sürüm — yok say */ }
    // Video.js global registry
    try {
      if (window.videojs && typeof window.videojs.getAllPlayers === "function") {
        for (const p of window.videojs.getAllPlayers()) {
          if (typeof p.currentSrc === "function") {
            const src = p.currentSrc();
            if (src) urls.push(src);
          }
        }
      }
    } catch { /* Video.js yok — yok say */ }
    return urls;
  }

  function reportUrls(rawUrls) {
    const fresh = [];
    for (const u of rawUrls) {
      if (typeof u !== "string") continue;
      if (!/^https?:/i.test(u)) continue;
      if (collectedUrls.has(u)) continue;
      collectedUrls.add(u);
      fresh.push(u);
    }
    if (!fresh.length) return;
    try {
      chrome.runtime.sendMessage({ type: "DOM_URLS", urls: fresh });
    } catch { /* background uyumayabilir; bir sonraki tarama yeniden dener */ }
  }

  function buildBadge(video) {
    if (badges.has(video)) return;
    const host = document.createElement("div");
    // Inline minimum stil — Shadow DOM dış host pozisyonu için.
    host.style.cssText = [
      "all:initial",
      "position:fixed",
      "z-index:2147483647",
      "pointer-events:none",
      "top:-100px",
      "left:-100px",
    ].join(";");
    const shadow = host.attachShadow({ mode: "closed" });
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = chrome.runtime.getURL("overlay.css");
    shadow.appendChild(link);

    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "badge";
    badge.title = "Pluck ile indir";
    const icon = document.createElement("span");
    icon.className = "icon";
    badge.appendChild(icon);

    badge.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      handleBadgeClick(video, badge);
    });
    shadow.appendChild(badge);
    document.documentElement.appendChild(host);

    const entry = { host, badge, video };
    badges.set(video, entry);
    positionBadge(entry);
  }

  function positionBadge(entry) {
    const rect = entry.video.getBoundingClientRect();
    const tooSmall = rect.width < MIN_VIDEO_W || rect.height < MIN_VIDEO_H;
    const offscreen = rect.bottom < 0 || rect.top > window.innerHeight
                      || rect.right < 0 || rect.left > window.innerWidth;
    if (tooSmall || offscreen) {
      entry.host.style.visibility = "hidden";
      return;
    }
    entry.host.style.visibility = "visible";
    entry.host.style.top = (rect.top + BADGE_MARGIN) + "px";
    entry.host.style.left = (rect.right - BADGE_SIZE - BADGE_MARGIN) + "px";
  }

  function repositionAll() {
    for (const entry of badges.values()) {
      // DOM'dan ayrılmış video element'lerinin rozetini de temizle.
      if (!entry.video.isConnected) {
        entry.host.remove();
        badges.delete(entry.video);
        continue;
      }
      positionBadge(entry);
    }
  }

  let rafToken = 0;
  function scheduleReposition() {
    if (rafToken) return;
    rafToken = requestAnimationFrame(() => {
      rafToken = 0;
      repositionAll();
    });
  }

  async function handleBadgeClick(video, badge) {
    badge.classList.remove("err", "added");
    badge.classList.add("busy");
    badge.title = "İndiriliyor…";
    try {
      const videoUrls = collectVideoSources(video);
      // En öncelikli: currentSrc (gerçekten oynayan akış). Yoksa sayfa URL'si
      // (yt-dlp ana sayfayı çözmeye çalışır).
      const url = videoUrls.find((u) => /^https?:/i.test(u))
                  || window.location.href;
      const res = await chrome.runtime.sendMessage({
        type: "DOWNLOAD_URL",
        url,
        referer: window.location.href,
      });
      if (res && res.ok) {
        badge.classList.remove("busy");
        badge.classList.add("added");
        badge.title = "Kuyruğa eklendi";
      } else {
        badge.classList.remove("busy");
        badge.classList.add("err");
        badge.title = "Hata: " + ((res && res.error) || "bilinmiyor");
      }
    } catch (err) {
      badge.classList.remove("busy");
      badge.classList.add("err");
      badge.title = "Hata: " + (err && err.message ? err.message : err);
    }
  }

  function scanOnce() {
    const videos = document.querySelectorAll("video");
    for (const v of videos) {
      buildBadge(v);
      reportUrls(collectVideoSources(v));
    }
    reportUrls(collectGlobalPlayerUrls());
  }

  scanOnce();
  // Geç yüklenen player'ları yakala — MutationObserver childList değişimleri.
  const observer = new MutationObserver(() => scanOnce());
  observer.observe(document.documentElement, {
    childList: true, subtree: true,
  });
  // Pozisyon güncellemesi (rAF throttle).
  window.addEventListener("scroll", scheduleReposition, { passive: true });
  window.addEventListener("resize", scheduleReposition, { passive: true });
  // 2 sn'lik periyodik tarama: bazı player'lar src'i sonradan set ediyor
  // (örn. Bunny Stream), MutationObserver'ı tetiklemiyor.
  setInterval(() => {
    repositionAll();
    for (const v of document.querySelectorAll("video")) {
      reportUrls(collectVideoSources(v));
    }
    reportUrls(collectGlobalPlayerUrls());
  }, 2000);
})();
