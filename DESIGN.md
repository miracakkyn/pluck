# DESIGN.md — Mimari Tasarım (Pluck)

> Bu belge, kod yazılmadan önce mimariyi, akışları ve sözleşmeleri tanımlar.
> **Kullanıcı onayı olmadan uygulama kodu yazılmaz.**

## 1. Amaç ve kapsam

IDM benzeri, genel amaçlı bir video indirme aracı. yt-dlp + ffmpeg motoru
üzerine kurulu; basit yerel web arayüzü. Akış: **link yapıştır → kalite seç →
indir**. Site-özel kod yazılmaz; yalnızca yt-dlp'nin genel yetenekleri kullanılır.

**v1 kapsamı (yalnızca bunlar):** URL kutusu · format listeleme · kalite seçimi
(en yüksek varsayılan) · indirme klasörü seçimi · canlı ilerleme · indirme
kuyruğu · cookie desteği. Bunların dışına çıkılmaz.

**Kapsam dışı (v1):** kullanıcı hesabı/kimlik doğrulama, paralel indirme,
indirme geçmişi kalıcılığı, altyazı/playlist yönetimi arayüzü, zamanlama.

## 2. Mimari genel bakış

```
┌──────────────────────────────┐       ┌──────────────────────────────────┐
│  Tarayıcı (web/ — vanilla JS) │       │  FastAPI sunucu (127.0.0.1)      │
│                              │ HTTP  │                                  │
│  index.html / app.js / css   │◄─────►│  main.py  ── route'lar           │
│                              │  SSE  │     │                            │
│  - URL kutusu                │◄──────│  ├─ ytdlp_engine.py  (yt-dlp)    │
│  - format/kalite seçimi      │       │  ├─ queue_manager.py (async worker)│
│  - kuyruk + canlı ilerleme   │       │  ├─ events.py        (SSE yayını)│
└──────────────────────────────┘       │  └─ models.py / config.py        │
                                       └─────────────┬────────────────────┘
                                                     │ alt-thread (to_thread)
                                       ┌─────────────▼────────────────────┐
                                       │ yt-dlp (gömülü) → ffmpeg (PATH)  │
                                       └──────────────────────────────────┘
```

- Sunucu **yalnızca `127.0.0.1`**'e bağlanır — ağa kapalı, kimlik doğrulama yok.
- yt-dlp **gömülü Python kütüphanesi** olarak kullanılır (`from yt_dlp import YoutubeDL`).
- Bloklayan yt-dlp çağrıları `asyncio.to_thread` ile thread'e alınır; event
  döngüsü bloklanmaz.
- Frontend framework yok; tek sayfa vanilla JS.

## 3. Dosya yapısı

```
videoaraci/
├── CLAUDE.md / DESIGN.md / README.md
├── requirements.txt / requirements-dev.txt
├── .gitignore
├── run.py                   # tek komut girişi: python run.py
├── start.bat                # Windows tek-tık (venv bootstrap + çalıştır)
├── start.sh                 # macOS/Linux tek-tık
├── start.command            # macOS Finder çift-tık → start.sh
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, route'lar, jeton middleware, statik mount
│   ├── config.py            # sabitler: host/port, indirme klasörü, api_token()
│   ├── models.py            # pydantic istek/yanıt şemaları + Job veri yapısı
│   ├── ytdlp_engine.py      # yt-dlp sarmalayıcı: list_formats(), download(), sayfa tarama
│   ├── queue_manager.py     # sıralı async worker + JobRegistry (eviction)
│   ├── events.py            # SSE ilerleme yayını
│   └── folder_picker.py     # native klasör seçici alt-process (tkinter/osascript/zenity)
├── web/
│   └── index.html / app.js / style.css   # app.js: jeton sabiti + fetch başlığı
├── extension/               # MV3 eklenti (§16): manifest, background, content,
│   │                        #   popup, overlay.css, pluck-token.js
└── tests/                   # ~238 test, %99 kapsam (her app modülü için ayrı dosya
    ├── __init__.py          #   + test_api, test_run, test_folder_picker,
    └── ...                  #   test_preset_consistency)
```

Hiçbir dosya 800 satırı geçmez; tipik 200–400 satır.

## 4. Bileşenler

### `config.py`
- `HOST = "127.0.0.1"`, `DEFAULT_PORT = 8765` (çakışırsa boş port aranır, `run.py`).
- `default_download_dir() -> Path` → `Path.home() / "Downloads"` (yoksa
  `Path.home()`'a düşer).
- `SUPPORTED_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "safari")`;
  `available_browsers()` `safari`'yi yalnız macOS'ta sunar.
- `EVENT_INTERVAL = 0.5` (SSE anlık görüntü aralığı, sn).
- `api_token()` — paylaşımlı jeton (`PLUCK_TOKEN` env ya da `_DEFAULT_API_TOKEN`).
- Tüm yollar `pathlib.Path`; sabit ayraç yok.

### `models.py` (pydantic v2) — güncel imzalar models.py'de canonical
- `FormatsRequest { url: str, browser: str | None }` — `url` `_validate_url`'den
  geçer (boş değil, http(s), loopback/link-local + kontrol-karakteri reddi).
- `FormatInfo { format_id, ext, resolution, kind, height, fps, vcodec, acodec,
  filesize, note }` — `kind ∈ {video, audio, combined}`.
- `FormatsResponse { title, formats: [FormatInfo], presets: [str], duration,
  thumbnail, uploader, url }` (`url` playlist girdileri için doldurulur).
- `ScanResponse { type: "video"|"playlist", video?, playlist_title?, entries?,
  warnings: [str] }` — `/api/formats` ve `/api/probe-urls` yanıtı (§6).
- `ProbeUrlsRequest { urls: [str] (1-20), referer, browser? }` — her URL ve
  referer `_validate_url`'den geçer.
- `JobRequest { url, selection, download_dir, browser?, title?, referer? }` —
  `selection` preset adı ya da ham `format_id`; `download_dir` var olan dizin
  olmalı; `title` verilirse dosya adında kullanılır; `referer` embed indirmelerde
  HTTP Referer'a iletilir (hepsi doğrulayıcıdan geçer).
- `Job` (dataclass) → `JobRegistry`'de tutulan iş durumu (`title_locked`,
  `cancel_requested` dahil; `snapshot()` iç bayrakları hariç serileştirir) (§9).

### `ytdlp_engine.py` — yt-dlp sarmalayıcı (saf, framework'ten bağımsız)
- `list_formats(url) -> FormatsResponse`
  - `YoutubeDL({"quiet": True, "skip_download": True}).extract_info(url, download=False)`
  - `info["formats"]` temizlenip `FormatInfo` listesine dönüştürülür; storyboard
    (`mhtml`) formatları elenir.
  - `presets` üretilir (§7).
- `build_format_selector(selection) -> str` — preset/format_id → yt-dlp format
  string'i (§7). Saf fonksiyon, kolayca test edilir.
- `download(url, selection, download_dir, browser, progress_hook, cancel_check)`
  - `YoutubeDL` seçenekleri: `format`, `merge_output_format="mp4"`,
    `paths={"home": str(download_dir)}`, `outtmpl="%(title)s.%(ext)s"`,
    `progress_hooks=[progress_hook]`, `quiet=True`, `noprogress=True`.
  - `browser` verilirse `cookiesfrombrowser=(browser,)`.
  - `audio` preset'inde `FFmpegExtractAudio` postprocessor (mp3).
  - Bloklayan; her zaman `asyncio.to_thread` içinden çağrılır.

### `queue_manager.py` — kuyruk + worker
- `JobRegistry` — `dict[str, Job]`; tek yazıcı (worker), çok okuyucu (SSE).
- `enqueue(JobRequest) -> job_id` (uuid4) — `Job(status="queued")` ekler,
  `asyncio.Queue`'ya iter.
- `worker()` — sonsuz coroutine; kuyruktan iş alır, `status="downloading"`,
  `asyncio.to_thread(engine.download, ...)` çağırır, biter → `completed`/`error`.
  Aynı anda yalnızca **bir** indirme (sıralı).
- `cancel(job_id)` — `queued` ise doğrudan `cancelled`; `downloading` ise iş
  için `cancel` bayrağı set edilir (progress hook bunu görüp indirmeyi durdurur).
- Uygulama başlangıcında (`lifespan`) tek `worker()` görevi başlatılır.

### `events.py` — SSE
- `event_stream()` — `EVENT_INTERVAL` aralıkla `JobRegistry`'nin anlık
  görüntüsünü `data: <json>\n\n` olarak yayınlar; istemci kopunca durur.

### `main.py` — FastAPI uygulaması
- `lifespan`: worker görevini başlat/durdur.
- Route'lar §6'da. `web/` statik dosya olarak mount edilir; `GET /` → index.html.

## 5. Veri akışı

**A. Format getirme**
`app.js` → `POST /api/formats {url}` → `ytdlp_engine.list_formats` →
`FormatsResponse` → arayüz preset'leri + format tablosunu çizer.

**B. İndirme kuyruğu**
`app.js` → `POST /api/jobs {url, selection, download_dir, browser?}` →
`queue_manager.enqueue` → `job_id` → iş `asyncio.Queue`'ya girer → `worker`
sırası gelince `to_thread(engine.download)` çalıştırır.

**C. Canlı ilerleme**
Sayfa açılışında `app.js` `GET /api/events` (SSE) açar → her 0.5 sn'de tüm
işlerin durumu gelir → kuyruk listesi + ilerleme çubukları güncellenir.

## 6. API sözleşmesi

Tüm yanıtlar JSON. Hatalar `{ "detail": "<mesaj>" }` + uygun HTTP kodu.

| Metot | Yol | İstek | Yanıt |
|---|---|---|---|
| GET | `/` | — | index.html (jetonsuz) |
| GET | `/api/config` | — | `{ default_download_dir, common_dirs[], browsers[], presets[] }` |
| POST | `/api/formats` | `FormatsRequest` | `ScanResponse` (tek video veya playlist + warnings) |
| POST | `/api/probe-urls` | `ProbeUrlsRequest { urls[], referer, browser? }` | `ScanResponse` (eklenti content script'inden gelen URL listesini yt-dlp ile çözer) |
| POST | `/api/jobs` | `JobRequest` | `{ job_id }` |
| GET | `/api/jobs` | — | `[Job, ...]` |
| DELETE | `/api/jobs/{job_id}` | — | `{ job_id, status }` |
| POST | `/api/jobs/clear` | — | `{ cleared: <int> }` (biten işleri temizler) |
| POST | `/api/pick-folder` | — | `{ pending: true }` (native klasör seçiciyi açar, §17) |
| GET | `/api/pick-folder` | — | `{ pending, path, error }` (seçim durumu) |
| GET | `/api/events` | — | SSE akışı (`text/event-stream`) |

**Jeton (Sprint 15):** `/api/*` uçları — **`/api/events` hariç** — `X-Pluck-Token`
başlığında sabit paylaşımlı jetonu ister; yoksa `403`. `GET /`, `/static/*` ve SSE
jetonsuz erişilebilir (EventSource özel başlık gönderemez). Amaç: rastgele diğer
yerel eklenti/yazılımın API'yi kullanmasını engellemek (bkz. §16). Jeton
`chrome-extension://` CORS ile birlikte çalışır: CORS middleware jeton kontrolünün
DIŞINDA (OPTIONS preflight jetonsuz yanıtlanır).

HTTP kodları: `200` başarı · `400` geçersiz girdi/URL · `403` eksik/yanlış jeton ·
`404` bilinmeyen `job_id` · `422` pydantic doğrulama · `500` beklenmeyen motor hatası.

## 7. Format / kalite seçimi

Arayüz iki yol sunar; **varsayılan en yüksek kalite**. İlk dalda mp4 video +
m4a (AAC) ses tercih edilir: bu, her oynatıcıda sesli çalan bir .mp4 üretir
(opus sesin mp4 içinde sessiz görünmesini önler). Tek doğruluk kaynağı
`ytdlp_engine._PRESET_SELECTORS` (adları `PRESETS`; bkz. §15 tek-kaynak testi):

| Preset | yt-dlp format string'i |
|---|---|
| `best` (varsayılan) | `bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b` |
| `1080p` | `bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b` |
| `720p` | `bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b` |
| `480p` | `bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]+ba/b[height<=480]/b` |
| `audio` | `ba/b` + `FFmpegExtractAudio` (mp3) |
| ham `format_id` | `"<id>+ba/b"` (video-only ise sese eklenir), aksi halde `"<id>"` |

`merge_output_format="mp4"`. Çözünürlük preset'lerinin son dalı **koşulsuz `/b`**:
istenen yükseklikte format yoksa (örn. yalnız-720p embed'de 480p) "format yok"
hatası yerine mevcut en iyi formata düşülür (Sprint 13).

## 8. Cookie desteği (login'li siteler)

- Arayüzde tarayıcı seçici: `SUPPORTED_BROWSERS` + "yok" (varsayılan).
- Seçiliyse yt-dlp'ye `cookiesfrombrowser=(browser,)` geçilir.
- Cookie verisi / tarayıcı içeriği **asla loglanmaz**.
- README notları: cookie ile indirmeden önce ilgili tarayıcı kapatılmalı;
  Windows'ta güncel Chrome'un App-Bound şifrelemesi tarayıcı kapalıyken çözülür;
  `safari` yalnızca macOS'ta geçerlidir.

## 9. Canlı ilerleme mekanizması (en kritik bölüm)

**Yaklaşım: anlık görüntü (snapshot) tabanlı — basit ve sağlam.**

- Her iş bir `Job` nesnesidir: `job_id, url, title, status, progress (0–100),
  speed, eta, downloaded_bytes, total_bytes, filename, error`.
- yt-dlp `progress_hooks` callback'i **indirme thread'inde** senkron çağrılır.
  Her çağrıda gelen dict (`status, downloaded_bytes, total_bytes[_estimate],
  speed, eta`) ilgili `Job`'a yazılır. Tek yazıcı thread olduğundan kilit gerekmez.
- SSE endpoint, `EVENT_INTERVAL` (0.5 sn) aralıkla `JobRegistry`'nin tamamının
  anlık görüntüsünü yayınlar. İlerleme doğası gereği "son değer" odaklıdır;
  ara değer kaçması önemsizdir → event-push yerine snapshot tercih edildi
  (daha az karmaşıklık, daha az hata yüzeyi).
- **İptal:** `Job.cancel_requested` bayrağı; progress hook bunu görürse
  `_Cancelled` fırlatıp indirmeyi durdurur. `download()` bunu `YoutubeDLError`'a
  sarmalanmış olarak da yükseltebilir; bu yüzden `queue_manager` iptal/hata
  ayrımını istisna türüne değil `cancel_requested` bayrağına göre yapar.
- **Nihai dosya adı (Sprint 14):** progress hook'un "finished" event'i
  çok-akışlı indirmelerde ARA parça dosyalarını (bv*+ba video/ses; merge sonrası
  silinir) bildirir. Bu yüzden `job.filename` ve başarı kararı progress hook'a
  DEĞİL, `download()`'ın DÖNDÜRDÜĞÜ merge-sonrası nihai yola dayanır (var olduğu
  `download()` içinde doğrulanır; yoksa `EngineError`). Aksi halde başarılı
  indirmeler "dosya bulunamadı" hatası olarak görünüyordu.
- **Bellek sınırı (Sprint 17):** `JobRegistry` `_MAX_JOBS`'u aşınca en eski
  BİTMİŞ işleri düşürür (aktifler korunur) — uzun oturumda sınırsız büyümesin.

## 10. Çapraz platform stratejisi

**Tüm uygulama kodu Windows ve macOS'ta birebir aynıdır.** Tek fark başlatma
script'leri (venv bootstrap komutları).

- Her yol `pathlib.Path`; sabit ayraç yok; varsayılan klasör `Path.home()/"Downloads"`.
- ffmpeg PATH üzerinden bulunur (yt-dlp otomatik algılar).
- `start.bat` (Windows): `py -m venv` → `.venv\Scripts\python.exe`.
- `start.sh` / `start.command` (macOS): `python3 -m venv` → `.venv/bin/python`.
- `run.py` her iki platformda aynı: uvicorn'u programatik başlatır, varsayılan
  tarayıcıyı `http://127.0.0.1:PORT`'a açar (`webbrowser` standart kütüphane).
- `safari` cookie seçeneği yalnızca macOS'ta sunulur (arayüz platforma göre).

## 11. Hata yönetimi

- **Sistem sınırında doğrulama:** URL ve `download_dir` pydantic ile doğrulanır;
  geçersizse `400/422` + kullanıcı dostu mesaj.
- yt-dlp `DownloadError`/`ExtractorError` yakalanır → ilgili `Job.status="error"`,
  `Job.error` kısa mesaj; ayrıntı sunucu loguna yazılır (cookie/gizli veri hariç).
- Worker tek bir işin hatasında çökmez; sonraki işe geçer.
- Frontend: her hata kullanıcıya görünür biçimde gösterilir; sessizce yutulmaz.

## 12. Güvenlik

- Sunucu yalnızca `127.0.0.1` (ağa kapalı). CORS yalnız `chrome-extension://`.
- **Paylaşımlı jeton (Sprint 15):** `/api/*` uçları (SSE hariç) `X-Pluck-Token`
  ister; rastgele diğer yerel eklenti/yazılım erişemez (bkz. §6, §16).
- **SSRF savunması (Sprint 14):** kullanıcının verdiği URL `_validate_url`'den
  geçer (loopback/link-local + kontrol-karakteri reddi). AYRICA sayfa taramasıyla
  KEŞFEDİLEN URL'ler (iframe src, regex m3u8/mp4) de aynı politikadan geçer
  (`_safe_discovered_url`); iframe host'u substring değil parse edilerek
  whitelist'e karşı doğrulanır (`_is_known_iframe_host`). Alan adları DNS
  rebinding'e karşı çözümlenip iç adres kontrol edilir (`_resolves_to_internal`,
  ikincil savunma). `referer` başlığı da `_validate_url`'den geçer (CRLF/header
  injection reddi).
- `download_dir` doğrulanır (var olan dizin olmalı); yol enjeksiyonuna karşı
  `outtmpl` yalnızca `paths.home` altına yazar.
- Cookie/tarayıcı verisi loglanmaz, yanıtlarda dönmez.
- Hata mesajları yığın izi/iç ayrıntı sızdırmaz.
- yt-dlp gömülü kütüphane olarak çağrılır (kabuk yok). Tek alt-process klasör
  seçicidir: sabit komutla (`python folder_picker.py`) çalıştırılır, komut
  satırında kullanıcı girdisi yoktur — enjeksiyon yüzeyi yoktur.

## 13. Test stratejisi (hedef ≥ %80 kapsam)

- **Birim:** `build_format_selector` (tüm preset'ler + format_id), `list_formats`
  ayrıştırması (sabit `extract_info` çıktısı mock'lanır), pydantic doğrulayıcılar,
  kuyruk durum geçişleri (`queued→downloading→completed/error/cancelled`).
- **Entegrasyon:** FastAPI `TestClient` ile uçlar (yt-dlp motoru mock'lanır).
- **Manuel/uçtan uca:** Sprint 4 sonunda gerçek indirme; Sprint 5'te cookie'li
  login URL testi.
- Çevrimiçi gerçek indirme yapan testler `@pytest.mark.network` ile işaretlenir
  ve varsayılan koşuda atlanır (deterministiklik).

## 14. Başlatma

- **Tek komut:** `python run.py` (venv etkinken).
- **Tek-tık:** `start.bat` (Windows) / `start.command` (macOS) — venv yoksa
  oluşturur, bağımlılıkları kurar, `run.py`'yi çalıştırır, tarayıcıyı açar.
- README'de platforma özel kurulum: Windows `winget` (ffmpeg, isteğe bağlı deno),
  macOS `brew` (ffmpeg, isteğe bağlı deno). yt-dlp her iki platformda da venv
  içine pip ile kurulur (start script otomatik yapar).

## 15. Bilinen kısıtlar ve notlar

- **YouTube + JS runtime:** yt-dlp 2026.03.17, YouTube için `deno` runtime'ı
  öneriyor; yoksa bazı formatlar eksik olabilir. README'de **isteğe bağlı**
  kurulum belgelenir (`winget install DenoLand.Deno` / `brew install deno`).
  YouTube dışı sitelerde gerekmez. Site-özel kod değildir.
- v1'de indirme sıralıdır (aynı anda tek iş) — basitlik ve bant genişliği
  öngörülebilirliği için bilinçli tercih.
- İş listesi bellekte tutulur; sunucu yeniden başlarsa kuyruk sıfırlanır
  (v1 kapsamı — kalıcılık yok).
- **Çoklu video tespiti (Sprint 9):** yt-dlp'nin generic extractor'ı bazı
  JS-tabanlı oynatıcılarda gömülü kaynakların yalnızca bir kısmını bulur.
  `ytdlp_engine` `_VIDEO_URL_PATTERNS` regex listesiyle sayfayı tarar
  (BunnyCDN broad + mediadelivery embed + generic .m3u8/.mpd/.mp4) ve
  bilinen video iframe host'larına 1 derinlik girip içlerinde de regex
  uygular. yt-dlp playlist branch'inde de bu tarama yapılır; eksikler
  `_extract_each()` ile tek tek çözülüp `ScanResponse.warnings` alanına
  başarısızlar bildirim olarak iliştirilir. URL dedupe host+path bazında
  (`_url_dedup_key`) — yt-dlp'nin smuggle suffix'leri farkı maskelemez.
- **HLS codec bildirimi (Sprint 12):** HLS (m3u8) ana playlist varyantları
  çoğu sağlayıcıda (BunnyCDN/MediaDelivery) `vcodec`/`acodec` bildirmez.
  `_parse_format` codec yoksa `_infer_kind_without_codecs` ile boyut/bitrate'ten
  tür çıkarır (çözünürlük varsa "combined"); aksi halde tüm HLS formatları
  elenip kullanıcıya boş liste gösterilirdi. yt-dlp'nin kendi seçicisi
  bağımsızdır; bu yalnızca UI'da çözünürlük listesini doldurur.
- **Referer ile embed indirme (Sprint 12):** Eklenti rozeti, gömülü oynatıcının
  *temiz* embed URL'sini (smuggle eki olmadan) gönderir. `JobRequest.referer`
  (→ `download(referer=…)`) verilirse HTTP Referer olarak iletilir; referer-koruyan
  CDN'ler (BunnyCDN/MediaDelivery) 403 vermez. Referer de `_validate_url`
  doğrulamasından geçer (loopback/şema reddi). Rozet ayrıca çerez tarayıcısını
  (popup ile aynı; `storage.local.cookieBrowser`) gönderir — login'li embed'ler
  için gerekli.

## 16. Tarayıcı eklentisi (Sprint 6 — hibrit mimari)

### Neden hibrit

Bir Chrome eklentisi tek başına yt-dlp (Python) veya ffmpeg çalıştıramaz ve
YouTube gibi sitelerin ayrı video+ses akışlarını birleştiremez — bu tarayıcı
korumalı alanının kısıtıdır. Bu yüzden eklenti yalnızca **ön yüz**tür; ağır
işi (yt-dlp + ffmpeg) zaten var olan yerel motor (FastAPI sunucusu) yapar.

```
┌────────────────────────┐   fetch http://127.0.0.1:<port>   ┌──────────────┐
│ Chrome eklentisi (MV3) │ ────────────────────────────────► │ Yerel motor  │
│  popup.html/js/css     │   /api/config /api/formats        │ (FastAPI +   │
│  - aktif sekme URL'si  │   /api/jobs   /api/jobs (poll)     │  yt-dlp +    │
│  - format + kuyruk UI  │ ◄──────────────────────────────── │  ffmpeg)     │
└────────────────────────┘            JSON                   └──────────────┘
```

Yerel web arayüzü (web/) yedek olarak korunur; eklenti onun yerini almaz.

### Eklenti dosya yapısı

```
extension/
├── manifest.json          # Manifest V3
├── popup.html / popup.css / popup.js
└── icons/  icon16.png · icon48.png · icon128.png  (+ generate_icons.py)
```

### Akış

1. Kullanıcı eklenti ikonuna tıklar → popup açılır.
2. Popup motoru bulur: `127.0.0.1:8765..8770` portlarında `/api/config` denenir.
   Bulunamazsa "motoru başlat (start.bat)" uyarısı gösterilir.
3. Motor varsa: aktif sekmenin URL'si alınır (`activeTab` izni) ve otomatik
   `POST /api/formats`'a gönderilir — yt-dlp o sayfadaki videoyu/formatları bulur.
4. Kullanıcı kalite seçip "İndir"e basar → `POST /api/jobs`.
5. Popup açık kaldığı sürece `GET /api/jobs` ~1.2 sn'de bir yoklanıp ilerleme
   gösterilir (popup kapanınca indirme motorda devam eder).

### Backend değişikliği (CORS + jeton)

Eklenti `chrome-extension://` kaynağından motora erişir. `CORSMiddleware`
eklenir: `allow_origin_regex = chrome-extension://.*` — yalnızca eklenti
kaynaklarının yanıtı **okumasına** izin verir; rastgele web siteleri okuyamaz.
`/api/formats` ve `/api/jobs` zaten URL aldığı için yeni uç gerekmez.

**Jeton (Sprint 15):** CORS, çapraz-köken POST'un *gönderilmesini* engellemez;
ayrıca kurulu HERHANGİ bir eklenti (yalnız Pluck'ınki değil) `chrome-extension://`
kaynağından motora erişebilir. Bu yüzden `/api/*` uçları (SSE hariç) sabit bir
paylaşımlı jeton (`X-Pluck-Token`) ister — Pluck'ın istemcileri (eklenti +
web arayüzü) gönderir, rastgele diğer yerel yazılım göndermez → `403`. Jeton
middleware'i CORS'tan ÖNCE eklenir (CORS dışta kalıp OPTIONS preflight'ı
jetonsuz yanıtlar). Sabit paylaşımlı sırdır; hedefli saldırıya karşı değildir
(bunun için `PLUCK_TOKEN` ortam değişkeni + istemci sabitleri eşleştirilir).

### İzinler (manifest)

- `activeTab` — yalnızca tıklamada aktif sekmenin URL'sine erişim (gizlilik dostu).
- `host_permissions: ["http://127.0.0.1/*"]` — yerel motora erişim (tüm portlar).

### Güvenlik notu

CORS, çapraz kaynaklı bir POST'un *gönderilmesini* engellemez; yalnızca
yanıtın okunmasını kısıtlar. Kötü niyetli bir web sitesi teorik olarak yerel
motora indirme tetikleyebilir (yanıtı okuyamaz, veri sızdıramaz) — pratikte
`/api/*` uçları JSON içerik-tipi preflight'ı ile web sayfalarına kapalıdır. Kurulu
DİĞER eklentilere karşı ise **paylaşımlı jeton uygulandı** (Sprint 15, bkz.
"Backend değişikliği"): jetonu bilmeyen bir eklenti `403` alır.

### Kapsam dışı (v1 eklenti)

- Eklenti mağazasına yayınlama — v1'de "paketlenmemiş yükle" ile kurulur.

## 16.2 Content script + sayfa-içi rozet (Sprint 10 → 11 → 15)

Sprint 6'daki popup-only mimari, sayfada gezerken IDM tarzı bir "sayfa-içi indir"
deneyimi sunmuyordu. Sprint 10-11'de content script + Shadow DOM rozeti eklendi;
Sprint 10 ayrıca bir `webRequest` sniffer + DOM URL toplama boru hattı içeriyordu.

**Sprint 15 sadeleştirmesi:** o boru hattı (`webRequest.onResponseStarted`,
sekme-bazlı `tabUrls`, `DOM_URLS`/`GET_TAB_URLS` mesajları, `chrome.storage.session`
kalıcılığı, `webNavigation` temizliği) **söküldü** — çünkü `popup.js` topladığı
URL'leri hiçbir zaman tüketmiyordu (`GET_TAB_URLS`/`/api/probe-urls` çağrılmıyordu);
tamamen yetim koddu. Rozet zaten indirilecek URL'yi doğrudan hedeften alıyor. Bu
sadeleştirme `webRequest` iznini ve `<all_urls>` host iznini kaldırdı.

```
┌─────────────────────────────────────────────────────────────┐
│ Sayfa (herhangi)                                             │
│  <video> / video-iframe / player-div  ← her birine Shadow    │
│  [▼ Pluck]                              DOM rozet (IDM tarzı) │
│       │ tıkla → kalite menüsü                                 │
│       ▼ target.currentSrc/src/iframe.src/sayfa               │
│  content.js ── sendMessage{DOWNLOAD_URL} ──┐                  │
│                                            ▼                  │
│                            background.js (MV3 SW)            │
│         motoru bul (8765-8770) + X-Pluck-Token               │
│         POST /api/jobs {url, selection, referer, browser}    │
│                                            │                  │
│                                            ▼                  │
│                                    FastAPI motor             │
└─────────────────────────────────────────────────────────────┘
```

- **`content.js`**: manifest `content_scripts` ile HER sayfaya otomatik enjekte
  olur (`matches: ["<all_urls>"]`, `all_frames: false`). `<video>` elementlerine,
  bilinen video-iframe host'larına ve player-benzeri container'lara Shadow DOM
  içinde yarı şeffaf rozet (`overlay.css`) yerleştirir. Rozete tıklayınca kalite
  menüsü (popover) açılır; seçim → `sendMessage({type:"DOWNLOAD_URL", url, referer,
  selection})`. İndirilecek URL doğrudan hedeften alınır (`<video>.currentSrc`/
  `src`/`<source>`, `<iframe>.src`, ya da yedek olarak sayfa adresi). Debounce'lu
  `MutationObserver` + 2 sn periyodik tarama geç yüklenen player'ları yakalar;
  popover viewport kenarında ekran dışına taşmaz. `all_frames:false` çift rozeti
  önler (üst çerçeve iframe elementini zaten rozetler).

- **`background.js`** (MV3 service worker): tek iş — `DOWNLOAD_URL` mesajını alıp
  8765-8770 aralığında motoru bulur ve `X-Pluck-Token` başlığıyla `POST /api/jobs`
  eder (kullanıcının seçtiği kalite + config'ten default klasör + popup ile aynı
  çerez tarayıcısı). `importScripts("pluck-token.js")` ile jetonu yükler.

- **`popup.js`**: `scanPage()` yalnızca `/api/formats` çağırır (backend Sprint 9
  sayfa taraması + iframe + `warnings[]` ile JS-gömülü videoları zaten yakalar).
  `/api/probe-urls` ucu motor tarafında durur (eklenti onu artık çağırmaz; ileride
  ya da manuel kullanım için mevcut).

**İzinler** (manifest.json):
- `activeTab` — kullanıcı eklentiye tıkladığında aktif sekme (programatik inject yedeği).
- `scripting` — `chrome.scripting.executeScript` (boot'ta yedek inject).
- `storage` — `chrome.storage.local` (rozet toggle `badgesEnabled`, `cookieBrowser`).
- `host_permissions`: `["http://127.0.0.1/*"]` — yalnız yerel motor.
- `web_accessible_resources`: `overlay.css` (Shadow DOM'a yüklemek için).

**Gizlilik / bilinçli tercih:** content_scripts `<all_urls>` ile HER sayfaya
otomatik enjekte olur — bu, "gez ve gördüğün videoyu indir" IDM deneyiminin
gereğidir (rozet her zaman açık). Script yalnızca rozet yerleştirmek için DOM
okur; hiçbir çapraz-köken fetch yapmaz ve veri sızdırmaz — rozet tıklamasında
seçilen URL SADECE yerel motora (`127.0.0.1`) gider. Rozetler `badgesEnabled`
toggle ile kapatılabilir.

## 17. Klasör seçici (native pencere)

`POST /api/pick-folder` yerel motorda bir alt-process (`app/folder_picker.py`,
tkinter) başlatır; bu, kendi ana thread'inde native bir klasör seçme penceresi
açar (Windows + macOS uyumlu). Sonuç modül durumuna (`_picker_state`) yazılır.

- **Web arayüzü:** `POST` sonrası `GET /api/pick-folder` yoklanır; seçilen yol
  klasör kutusuna yazılır.
- **Eklenti:** native pencere odağı alınca Chrome popup'ı kapanır; bu yüzden
  eklenti `POST` eder, kullanıcı klasörü seçer, eklenti yeniden açıldığında
  `GET /api/pick-folder` ile seçilen yol doldurulur.
- tkinter yoksa pencere açılmaz; klasör her zaman elle de yazılabilir.
