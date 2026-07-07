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

  const badges = new Map(); // hedef element -> { host, badge, popover, ... }
  const collectedUrls = new Set();
  let badgesEnabled = true; // varsayılan açık; storage'den okunup güncellenir

  // Hangi iframe host'larının video oynatıcı olduğunu bildiğimiz liste.
  // Bunlara rozet konur (kendi sayfa içlerinde <video> tag taranamasa bile).
  const VIDEO_IFRAME_HOST_RE = new RegExp(
    "(?:mediadelivery\\.net|b-cdn\\.net|bunnycdn\\.com|jwplayer\\.com|"
    + "vimeo\\.com|player\\.vimeo|youtube\\.com/embed|youtube-nocookie\\.com|"
    + "dailymotion\\.com/embed|wistia\\.net|hls\\.js|hlsplayer)",
    "i",
  );

  // Player-benzeri container heuristic: <video>/<iframe> oluşturmayan
  // custom JS player'lar (HLS.js + canvas, JWPlayer JS, vb.) için yedek.
  // Class/id'sinde "player"/"video" geçen + en az 320x180 + ~16:9 olan div'ler.
  const PLAYER_CONTAINER_SELECTORS = [
    "[class*='player' i]",
    "[id*='player' i]",
    "[class*='video' i]",
    "[id*='video' i]",
    ".jwplayer",
    ".video-js",
    ".plyr",
    ".hls-player",
  ].join(",");
  const MIN_CONTAINER_W = 320;
  const MIN_CONTAINER_H = 180;

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

  /** Yeni hedefin merkezi, zaten rozetlenmiş bir hedefin alanına düşüyor mu?
   *  Aynı oynatıcı bölgesine (ör. <video> + onu saran "player" div'i) iki
   *  rozet konmasını önler. */
  function centerInsideExistingBadge(rect) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    for (const entry of badges.values()) {
      const r = entry.target.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (cx >= r.left && cx <= r.right && cy >= r.top && cy <= r.bottom) {
        return true;
      }
    }
    return false;
  }

  /** Hedef DOM element'i (video / iframe / div container) için rozet inşa eder.
   *  Aynı element için tekrar çağrılırsa veya başka bir rozetle örtüşürse
   *  hiçbir şey yapmaz. */
  function buildBadge(target) {
    if (badges.has(target)) return;
    const rect0 = target.getBoundingClientRect();
    // Çok küçük (henüz yüklenmemiş olabilir — periyodik tarama tekrar dener)
    // veya başka bir rozetin alanına düşüyorsa atla (çift rozet önleme).
    if (rect0.width < MIN_VIDEO_W || rect0.height < MIN_VIDEO_H) return;
    if (centerInsideExistingBadge(rect0)) return;
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
    popover.className = "popover";  // .open eklenince görünür (overlay.css)
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
        triggerDownload(target, entry, preset.value);
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

    const entry = { host, badge, popover, target, popoverOpen: false };
    badges.set(target, entry);
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
    entry.popover.classList.add("open");
    entry.badge.classList.add("open");
    positionBadge(entry);
    positionPopover(entry);
  }

  /** Popover'ı viewport'a göre yerleştirir: varsayılan rozetin solunda/altında;
   *  sola taşarsa sağa, alta taşarsa yukarı çevirir. overlay.css'teki
   *  varsayılanı (right:42px; top:0) inline stille override eder. Player
   *  viewport'un sol veya alt kenarına yakınken menünün ekran dışına
   *  taşmasını engeller. */
  function positionPopover(entry) {
    const pop = entry.popover;
    const hostLeft = parseFloat(entry.host.style.left) || 0;
    const hostTop = parseFloat(entry.host.style.top) || 0;
    const rect = pop.getBoundingClientRect();
    const popW = rect.width || POPOVER_W;
    const popH = rect.height || 180;
    const GAP = 6;
    // Yatay: solda yer varsa (ya da sağ da taşıyorsa) solda kal; yoksa sağa aç.
    const roomLeft = hostLeft - GAP - popW >= 4;
    const roomRight = hostLeft + BADGE_SIZE + GAP + popW <= window.innerWidth - 4;
    if (roomLeft || !roomRight) {
      pop.style.right = (BADGE_SIZE + GAP) + "px";
      pop.style.left = "auto";
    } else {
      pop.style.left = (BADGE_SIZE + GAP) + "px";
      pop.style.right = "auto";
    }
    // Dikey: aşağı taşarsa ve yukarıda yer varsa yukarı aç.
    if (hostTop + popH > window.innerHeight - 4 && hostTop + BADGE_SIZE - popH >= 4) {
      pop.style.top = "auto";
      pop.style.bottom = "0";
    } else {
      pop.style.top = "0";
      pop.style.bottom = "auto";
    }
  }

  function closePopover(entry) {
    entry.popoverOpen = false;
    entry.popover.classList.remove("open");
    entry.badge.classList.remove("open");
  }

  function positionBadge(entry) {
    const rect = entry.target.getBoundingClientRect();
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
    const top = rect.top + BADGE_MARGIN;
    const left = rect.right - BADGE_SIZE - BADGE_MARGIN;
    entry.host.style.top = top + "px";
    entry.host.style.left = left + "px";
    // Açık popover, kaydırma/yeniden boyutlandırmada da viewport'ta kalsın.
    if (entry.popoverOpen) positionPopover(entry);
  }

  function repositionAll() {
    for (const entry of badges.values()) {
      if (!entry.target.isConnected) {
        entry.host.remove();
        badges.delete(entry.target);
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

  async function triggerDownload(target, entry, selection) {
    closePopover(entry);
    entry.badge.classList.remove("err", "added");
    entry.badge.classList.add("busy");
    entry.badge.title = "İndiriliyor…";
    try {
      // Hedef türüne göre URL stratejisi:
      // - <video>: currentSrc / src / <source> öncelik
      // - <iframe>: iframe.src (yt-dlp embed URL'ini çözer)
      // - container div: sayfa URL'si (yt-dlp ana sayfayı çözer)
      let url = null;
      if (target.tagName === "VIDEO") {
        url = collectVideoSources(target).find((u) => /^https?:/i.test(u));
      } else if (target.tagName === "IFRAME" && target.src) {
        url = target.src;
      }
      if (!url) url = window.location.href;
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

  // Capture fazında çalışır (true) — bu nedenle popover option'ının kendi
  // handler'ından ÖNCE ateşlenir. Kendi overlay host'umuza tıklandıysa
  // kapatmayı ATLA; yoksa popover, option click işlenmeden display:none olur
  // ve triggerDownload hiç çağrılmaz (rozetten kalite seçince indirme
  // başlamama bug'ının kök nedeni). Kapalı shadow root'ta composedPath()
  // iç düğümleri göstermez ama host'u içerir.
  document.addEventListener("click", (ev) => {
    const path = (typeof ev.composedPath === "function") ? ev.composedPath() : [];
    for (const entry of badges.values()) {
      if (!entry.popoverOpen) continue;
      if (path.includes(entry.host)) continue;  // kendi overlay'imize tıklandı
      closePopover(entry);
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
    // 1) <video> tag'leri — en güvenilir hedef.
    for (const v of document.querySelectorAll("video")) {
      buildBadge(v);
      reportUrls(collectVideoSources(v));
    }
    // 2) Bilinen video iframe host'ları — Bunny Stream, Vimeo, YouTube embed
    //    gibi içinde kendi <video> tag'ini render eden ama iframe sınırı
    //    nedeniyle content script'in DOM'una giremediği oynatıcılar.
    for (const f of document.querySelectorAll("iframe")) {
      if (f.src && VIDEO_IFRAME_HOST_RE.test(f.src)) {
        buildBadge(f);
        reportUrls([f.src]);
      }
    }
    // 3) Player-benzeri container heuristic — Ders-1 gibi <video>/<iframe>
    //    oluşturmayan custom JS player'lar (HLS.js + canvas, JWPlayer JS) için
    //    yedek. False positive azaltmak için: zaten <video>/<iframe> içerenler
    //    atlanır; en az 320x180 + aspect ratio 1.0-2.8 (video gibi) olmalı.
    for (const c of document.querySelectorAll(PLAYER_CONTAINER_SELECTORS)) {
      if (badges.has(c)) continue;
      if (c.querySelector("video, iframe")) continue;
      const rect = c.getBoundingClientRect();
      if (rect.width < MIN_CONTAINER_W || rect.height < MIN_CONTAINER_H) continue;
      const ar = rect.width / rect.height;
      if (ar < 1.0 || ar > 2.8) continue;
      buildBadge(c);
    }
    reportUrls(collectGlobalPlayerUrls());
  }

  loadBadgesEnabled();
  scanOnce();

  // Geç yüklenen player'ları yakala. DİKKAT: scanOnce() kendi rozet host'larını
  // document.documentElement'e ekler; observer subtree:true ile bunları da görür.
  // Debounce olmadan her ekleme yeni bir mutation → yeni scanOnce → sonsuz
  // kendi-kendini tetikleyen döngü (yüksek CPU). Bir sonraki frame'e kadar (veya
  // kısa pencere) mutation'ları tek bir taramada birleştir.
  let scanScheduled = false;
  function scheduleScan() {
    if (scanScheduled) return;
    scanScheduled = true;
    setTimeout(() => {
      scanScheduled = false;
      scanOnce();
    }, 250);
  }
  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.documentElement, {
    childList: true, subtree: true,
  });
  window.addEventListener("scroll", scheduleReposition, { passive: true });
  window.addEventListener("resize", scheduleReposition, { passive: true });
  // 2 sn periyodik: bazı player'lar src'i sonradan set ediyor (Bunny Stream),
  // MutationObserver'ı tetiklemiyor. Tam tarama + repos hep birlikte.
  setInterval(() => {
    scanOnce();
    repositionAll();
  }, 2000);
})();
