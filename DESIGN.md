# DESIGN.md — Mimari Tasarım

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
│   ├── main.py              # FastAPI app, route'lar, statik mount, yaşam döngüsü
│   ├── config.py            # sabitler: host/port, varsayılan indirme klasörü
│   ├── models.py            # pydantic istek/yanıt şemaları + Job veri yapısı
│   ├── ytdlp_engine.py      # yt-dlp sarmalayıcı: list_formats(), download()
│   ├── queue_manager.py     # sıralı async worker + JobRegistry
│   └── events.py            # SSE ilerleme yayını
├── web/
│   ├── index.html / app.js / style.css
└── tests/
    ├── __init__.py
    ├── test_ytdlp_engine.py / test_queue_manager.py / test_models.py
```

Hiçbir dosya 800 satırı geçmez; tipik 200–400 satır.

## 4. Bileşenler

### `config.py`
- `HOST = "127.0.0.1"`, `PORT = 8765` (sabit; çakışırsa boş port aranır).
- `default_download_dir() -> Path` → `Path.home() / "Downloads"` (yoksa
  `Path.home()`'a düşer).
- `SUPPORTED_BROWSERS = ("chrome", "edge", "firefox", "safari", "brave", "opera")`.
- `EVENT_INTERVAL = 0.5` (SSE anlık görüntü aralığı, sn).
- Tüm yollar `pathlib.Path`; sabit ayraç yok.

### `models.py` (pydantic v2)
- `FormatsRequest { url: str }` — `url` boş olamaz.
- `FormatInfo { format_id, ext, resolution, height, fps, vcodec, acodec,
  filesize, note, kind }` — `kind ∈ {video, audio, combined}`.
- `FormatsResponse { title, duration, thumbnail, uploader, formats: [FormatInfo],
  presets: [str] }`.
- `JobRequest { url, selection, download_dir, browser: str | None }` —
  `selection` ya bir preset adı ya da ham `format_id`; `download_dir` var olan
  bir dizin olmalı (doğrulayıcı); `browser` verilirse `SUPPORTED_BROWSERS`'ta olmalı.
- `Job` (dataclass) → `JobRegistry`'de tutulan iş durumu (§9).
- `ApiError { detail: str }` — tutarlı hata zarfı.

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
| GET | `/` | — | index.html |
| GET | `/api/config` | — | `{ default_download_dir, common_dirs[], browsers[] }` |
| POST | `/api/formats` | `FormatsRequest` | `FormatsResponse` |
| POST | `/api/jobs` | `JobRequest` | `{ job_id }` |
| GET | `/api/jobs` | — | `[Job, ...]` |
| DELETE | `/api/jobs/{job_id}` | — | `{ job_id, status }` |
| GET | `/api/events` | — | SSE akışı (`text/event-stream`) |

HTTP kodları: `200` başarı · `400` geçersiz girdi/URL · `404` bilinmeyen
`job_id` · `422` pydantic doğrulama · `500` beklenmeyen motor hatası.

## 7. Format / kalite seçimi

Arayüz iki yol sunar; **varsayılan en yüksek kalite**:

| Preset | yt-dlp format string'i |
|---|---|
| `best` (varsayılan) | `bv*+ba/b` |
| `1080p` | `bv*[height<=1080]+ba/b/b[height<=1080]` |
| `720p` | `bv*[height<=720]+ba/b/b[height<=720]` |
| `480p` | `bv*[height<=480]+ba/b/b[height<=480]` |
| `audio` | `ba/b` + `FFmpegExtractAudio` (mp3) |
| ham `format_id` | `"<id>+ba/b"` (video-only ise sese eklenir), aksi halde `"<id>"` |

`merge_output_format="mp4"`. Bir preset için format yoksa yt-dlp `/b`
yedeğiyle birleşik en iyi formata düşer.

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
  `yt_dlp.utils.DownloadCancelled` (yoksa özel exception) fırlatıp indirmeyi
  durdurur. Sprint 2'de yt-dlp'nin iptal API'si doğrulanacak.

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

- Sunucu yalnızca `127.0.0.1` (ağa kapalı). CORS gevşetilmez.
- `download_dir` doğrulanır (var olan dizin olmalı); yol enjeksiyonuna karşı
  `outtmpl` yalnızca `paths.home` altına yazar.
- Cookie/tarayıcı verisi loglanmaz, yanıtlarda dönmez.
- Hata mesajları yığın izi/iç ayrıntı sızdırmaz.
- Harici tek girdi URL'dir; yt-dlp'ye doğrudan dize olarak verilir (kabuk yok,
  shell enjeksiyonu yüzeyi yok — alt-process değil gömülü kütüphane).

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

### Backend değişikliği (tek)

Eklenti `chrome-extension://` kaynağından motora erişir. `CORSMiddleware`
eklenir: `allow_origin_regex = chrome-extension://.*` — yalnızca eklenti
kaynaklarının yanıtı okumasına izin verir; rastgele web siteleri okuyamaz.
`/api/formats` ve `/api/jobs` zaten URL aldığı için yeni uç gerekmez.

### İzinler (manifest)

- `activeTab` — yalnızca tıklamada aktif sekmenin URL'sine erişim (gizlilik dostu).
- `host_permissions: ["http://127.0.0.1/*"]` — yerel motora erişim (tüm portlar).

### Güvenlik notu

CORS, çapraz kaynaklı bir POST'un *gönderilmesini* engellemez; yalnızca
yanıtın okunmasını kısıtlar. Kötü niyetli bir site teorik olarak yerel motora
indirme tetikleyebilir (yanıtı okuyamaz, veri sızdıramaz). Tek kullanıcılı
yerel araç için bu düşük önemde kabul edilir; ileride paylaşımlı bir jeton
(token) ile sıkılaştırılabilir.

### Kapsam dışı (v1 eklenti)

- Sayfa ağ trafiğini dinleyerek gömülü/çoklu medya yakalama (`webRequest`) —
  v1'de aktif sekme URL'si yt-dlp'ye verilir; yt-dlp videoyu kendisi bulur.
- Eklenti mağazasına yayınlama — v1'de "paketlenmemiş yükle" ile kurulur.
