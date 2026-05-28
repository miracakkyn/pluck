"use strict";
/* Pluck — sayfa-içi içerik script.
   1. Sayfadaki <video> elementlerini bulur, her birinin sağ-üst köşesine
      Shadow DOM içinde küçük bir "Pluck ile indir" rozeti yerleştirir.
      Rozete tıklayınca kalite menüsü (popover) açılır; seçilen kalite ile
      yerel motora indirme isteği gönderilir.
   2. DOM'dan ve bilinen global player API'lerinden (JWPlayer, Video.js) medya
      URL'lerini toplayıp background service worker'a yollar.
   3. chrome.storage.local'daki `badgesEnabled` bayrağı false ise rozetler
      gizlenir; popup'tan toggle ile değiştirilince anında uygulanır.
   manifest content_scripts ile her sayfada otomatik inject olur. */

(function pluckContentScript() {
  // Idempotent inject: hem manifest hem programatik tetiklenirse iki kez girme.
  if (window.__pluckInjected) return;
  window.__pluckInjected = true;

  const BADGE_SIZE = 36;
  const BADGE_MARGIN = 10;
  const POPOVER_W = 168;
  // Çok küçük player'lara rozet koyma — UI bozar, video play butonunu kapatır.
  const MIN_VIDEO_W = 120;
  const MIN_VIDEO_H = 80;

  // İçerik script'in destekleyeceği preset'ler — popup'taki PRESET_LABELS ile
  // simetrik tutuyoruz (sözleşme: backend bu adları yt-dlp format string'lerine
  // çevirir; buradaki sıra UI'da menü sırasıdır).
  const PRESETS = [
    { value: "best",  label: "En yüksek" },
    { value: "1080p", label: "1080p" },
    { value: "720p",  label: "720p" },
    { value: "480p",  label: "480p" },
    { value: "audio", label: "Ses (MP3)" },
  ];

  const badges = new Map(); // video element -> { host, badge, popover, ... }
  const collectedUrls = new Set();
  let badgesEnabled = true; // varsayılan açık; storage'den okunup güncellenir

  // --- yardımcılar ------------------------------------------------------

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
    } catch { /* JWPlayer yok — yok say */ }
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
    } catch { /* background uyumayabilir; periyodik tarama yine dener */ }
  }

  // --- rozet + popover ---------------------------------------------------

  function buildBadge(video) {
    if (badges.has(video)) return;
    const host = document.createElement("div");
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

    const wrap = document.createElement("div");
    wrap.className = "wrap";

    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "badge";
    badge.title = "Pluck ile indir";
    const icon = document.createElement("span");
    icon.className = "icon";
    badge.appendChild(icon);

    const popover = document.createElement("div");
    popover.className = "popover";
    popover.hidden = true;
    const popTitle = document.createElement("div");
    popTitle.className = "pop-title";
    popTitle.textContent = "Kalite seç";
    popover.appendChild(popTitle);
    for (const preset of PRESETS) {
      const opt = document.createElement("button");
      opt.type = "button";
      opt.className = "pop-opt";
      opt.dataset.preset = preset.value;
      opt.textContent = preset.label;
      opt.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        triggerDownload(video, entry, preset.value);
      });
      popover.appendChild(opt);
    }

    badge.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      togglePopover(entry);
    });

    wrap.appendChild(badge);
    wrap.appendChild(popover);
    shadow.appendChild(wrap);
    document.documentElement.appendChild(host);

    const entry = { host, badge, popover, video, popoverOpen: false };
    badges.set(video, entry);
    positionBadge(entry);
  }

  function togglePopover(entry) {
    if (entry.popoverOpen) {
      closePopover(entry);
    } else {
      // Açılmadan önce diğerlerini kapat (aynı anda tek popover).
      for (const other of badges.values()) {
        if (other !== entry && other.popoverOpen) closePopover(other);
      }
      openPopover(entry);
    }
  }

  function openPopover(entry) {
    entry.popoverOpen = true;
    entry.popover.hidden = false;
    entry.badge.classList.add("open");
    positionBadge(entry);
  }

  function closePopover(entry) {
    entry.popoverOpen = false;
    entry.popover.hidden = true;
    entry.badge.classList.remove("open");
  }

  function positionBadge(entry) {
    const rect = entry.video.getBoundingClientRect();
    const tooSmall = rect.width < MIN_VIDEO_W || rect.height < MIN_VIDEO_H;
    const offscreen = rect.bottom < 0 || rect.top > window.innerHeight
                      || rect.right < 0 || rect.left > window.innerWidth;
    if (!badgesEnabled || tooSmall || offscreen) {
      entry.host.style.visibility = "hidden";
      // Görünmezken popover de kapanmalı.
      if (entry.popoverOpen) closePopover(entry);
      return;
    }
    entry.host.style.visibility = "visible";
    // Popover rozetin solunda açılır — sağdan ekran taşma riski az.
    const top = rect.top + BADGE_MARGIN;
    const left = rect.right - BADGE_SIZE - BADGE_MARGIN;
    entry.host.style.top = top + "px";
    entry.host.style.left = left + "px";
    // Popover wrap içinde absolute; rozet sağa, popover sola hizalı.
  }

  function repositionAll() {
    for (const entry of badges.values()) {
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

  async function triggerDownload(video, entry, selection) {
    closePopover(entry);
    entry.badge.classList.remove("err", "added");
    entry.badge.classList.add("busy");
    entry.badge.title = "İndiriliyor…";
    try {
      const videoUrls = collectVideoSources(video);
      // En öncelikli: currentSrc. Yoksa sayfa URL'si (yt-dlp ana sayfayı çözer).
      const url = videoUrls.find((u) => /^https?:/i.test(u))
                  || window.location.href;
      const res = await chrome.runtime.sendMessage({
        type: "DOWNLOAD_URL",
        url,
        referer: window.location.href,
        selection,  // "best" / "1080p" / "720p" / "480p" / "audio"
      });
      if (res && res.ok) {
        entry.badge.classList.remove("busy");
        entry.badge.classList.add("added");
        entry.badge.title = "Kuyruğa eklendi";
      } else {
        entry.badge.classList.remove("busy");
        entry.badge.classList.add("err");
        entry.badge.title = "Hata: " + ((res && res.error) || "bilinmiyor");
      }
    } catch (err) {
      entry.badge.classList.remove("busy");
      entry.badge.classList.add("err");
      entry.badge.title = "Hata: " + (err && err.message ? err.message : err);
    }
  }

  // --- toggle: badge gösterimi --------------------------------------------

  async function loadBadgesEnabled() {
    try {
      const data = await chrome.storage.local.get("badgesEnabled");
      if (typeof data.badgesEnabled === "boolean") {
        badgesEnabled = data.badgesEnabled;
      }
    } catch { /* storage erişilemez — varsayılan açık */ }
    repositionAll();
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.badgesEnabled) {
      badgesEnabled = changes.badgesEnabled.newValue !== false;
      repositionAll();
    }
  });

  // --- dış tıklama popover'ı kapatır --------------------------------------

  document.addEventListener("click", (ev) => {
    // Popover içinde tıklandıysa Shadow DOM içinde stop edildi; buraya gelmez.
    for (const entry of badges.values()) {
      if (entry.popoverOpen) closePopover(entry);
    }
  }, true);

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    for (const entry of badges.values()) {
      if (entry.popoverOpen) closePopover(entry);
    }
  });

  // --- ana döngü ----------------------------------------------------------

  function scanOnce() {
    const videos = document.querySelectorAll("video");
    for (const v of videos) {
      buildBadge(v);
      reportUrls(collectVideoSources(v));
    }
    reportUrls(collectGlobalPlayerUrls());
  }

  loadBadgesEnabled();
  scanOnce();
  // Geç yüklenen player'ları yakala.
  const observer = new MutationObserver(() => scanOnce());
  observer.observe(document.documentElement, {
    childList: true, subtree: true,
  });
  window.addEventListener("scroll", scheduleReposition, { passive: true });
  window.addEventListener("resize", scheduleReposition, { passive: true });
  // 2 sn periyodik: bazı player'lar src'i sonradan set ediyor (Bunny Stream),
  // MutationObserver'ı tetiklemiyor.
  setInterval(() => {
    repositionAll();
    for (const v of document.querySelectorAll("video")) {
      reportUrls(collectVideoSources(v));
    }
    reportUrls(collectGlobalPlayerUrls());
  }, 2000);
})();
