"""FastAPI uygulaması — route'lar ve yaşam döngüsü.

Sunucu yalnızca 127.0.0.1'e bağlanır (yerel araç). Bloklayan yt-dlp
çağrıları `asyncio.to_thread` ile thread'e alınır.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import config, ytdlp_engine
from app.events import event_stream
from app.models import (
    ConfigResponse,
    FormatsRequest,
    JobRequest,
    ProbeUrlsRequest,
    ScanResponse,
)
from app.queue_manager import QueueManager
from app.ytdlp_engine import EngineError

logger = logging.getLogger("videoaraci")

# Tek süreçli uygulama için modül seviyesinde tek kuyruk yöneticisi.
queue_manager = QueueManager()

# Klasör seçici durumu: pencere açık mı (pending), son seçilen yol (path),
# ve son denemenin hata mesajı (error — varsa UI gösterir).
_picker_state: dict = {"pending": False, "path": None, "error": None}


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Uygulama yaşam döngüsü: indirme worker'ını başlat ve düzgünce durdur."""
    queue_manager.start()
    try:
        yield
    finally:
        await queue_manager.stop()


app = FastAPI(title="Pluck", lifespan=lifespan)

# Paylaşımlı jeton koruması. /api/* uçları (SSE hariç) X-Pluck-Token başlığında
# doğru jetonu ister; eklenti (pluck-token.js) ve web arayüzü (app.js) aynı
# sabiti gönderir. Amaç: rastgele diğer yerel eklenti/yazılımın API'yi
# kullanmasını engellemek. NOT: bu middleware CORS'tan ÖNCE eklenir ki CORS DIŞTA
# kalsın — böylece OPTIONS preflight'ı jeton kontrolüne girmeden yanıtlanır.
# SSE (/api/events) hariç: EventSource özel başlık gönderemez ve yalnızca
# ilerleme okur. GET /, /static ve /api/events jetonsuz erişilebilir.
_TOKEN_EXEMPT = frozenset({"/api/events"})


@app.middleware("http")
async def _require_token(request: Request, call_next):
    path = request.url.path
    needs_token = (
        path.startswith("/api/")
        and path not in _TOKEN_EXEMPT
        and request.method != "OPTIONS"  # preflight'ı CORS yanıtlar
    )
    if needs_token and request.headers.get("x-pluck-token") != config.api_token():
        return JSONResponse(
            {"detail": "Geçersiz veya eksik jeton"}, status_code=403
        )
    return await call_next(request)


# Chrome eklentisinin (chrome-extension://) yerel motora erişebilmesi için.
# Yalnızca eklenti kaynakları yanıtı okuyabilir; rastgele web siteleri okuyamaz.
# (Son eklenen middleware EN DIŞTADIR — CORS burada, jeton kontrolünün dışında.)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Tek sayfa web arayüzünü servis eder."""
    return FileResponse(config.WEB_DIR / "index.html")


@app.get("/api/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Arayüz için varsayılan klasör, sık klasörler, tarayıcılar ve preset'ler."""
    return ConfigResponse(
        default_download_dir=str(config.default_download_dir()),
        common_dirs=config.common_dirs(),
        browsers=config.available_browsers(),
        presets=list(ytdlp_engine.PRESETS),
    )


@app.post("/api/formats", response_model=ScanResponse)
async def post_formats(req: FormatsRequest) -> ScanResponse:
    """URL'yi tarar; tek video veya oynatma listesi yanıtı döndürür."""
    try:
        return await asyncio.to_thread(
            ytdlp_engine.list_formats, req.url, req.browser
        )
    except EngineError as exc:
        # Kullanıcı hatası; izleme için kaydedilir (URL zaten kullanıcı girdisi).
        logger.warning("Format listeleme başarısız: %s | url=%s", exc, req.url)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # beklenmeyen — ayrıntı sızdırma
        logger.exception("Format listeleme sırasında beklenmeyen hata")
        raise HTTPException(
            status_code=500, detail="Beklenmeyen bir hata oluştu"
        ) from exc


@app.post("/api/probe-urls", response_model=ScanResponse)
async def post_probe_urls(req: ProbeUrlsRequest) -> ScanResponse:
    """Eklenti content script'inden gelen URL listesini doğrudan motora yollar.

    Backend HTML regex'i göremediği JS-oluşturulan medya URL'lerini tarayıcı
    DOM tarama + webRequest sniffing ile yakalar; bu uç o URL'leri yt-dlp ile
    extract eder. Tek başarılı URL → `type="video"`, birden fazlası → playlist.
    `referer` BunnyCDN gibi referer-koruyan CDN'ler için zorunlu.
    """
    try:
        entries, warnings = await asyncio.to_thread(
            ytdlp_engine._extract_each, req.urls, req.browser, req.referer,
        )
    except Exception as exc:  # _extract_each kendi hatalarını yutar, bu defansif
        logger.exception("URL probe sırasında beklenmeyen hata")
        raise HTTPException(
            status_code=500, detail="Beklenmeyen bir hata oluştu"
        ) from exc
    if not entries:
        detail = warnings[0] if warnings else "Verilen URL'lerden hiçbiri çözümlenemedi"
        raise HTTPException(status_code=400, detail=detail)
    if len(entries) == 1:
        return ScanResponse(type="video", video=entries[0], warnings=warnings)
    return ScanResponse(
        type="playlist",
        playlist_title="Sayfadaki videolar",
        entries=entries,
        warnings=warnings,
    )


@app.post("/api/jobs")
async def post_job(req: JobRequest) -> dict:
    """Bir indirme işini kuyruğa ekler."""
    return {"job_id": queue_manager.enqueue(req)}


@app.get("/api/jobs")
async def get_jobs() -> list[dict]:
    """Tüm işleri ve güncel durumlarını döndürür."""
    return queue_manager.jobs()


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict:
    """İşi iptal eder (kuyruktaysa kaldırır, indiriliyorsa durdurur)."""
    try:
        job = queue_manager.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="İş bulunamadı") from exc
    return job.snapshot()


@app.post("/api/jobs/clear")
async def clear_finished_jobs() -> dict:
    """Tamamlanmış / hatalı / iptal edilmiş işleri kuyruktan temizler."""
    return {"cleared": queue_manager.clear_finished()}


@app.get("/api/events")
async def get_events() -> StreamingResponse:
    """Canlı indirme ilerlemesi akışı (Server-Sent Events)."""
    return StreamingResponse(
        event_stream(queue_manager.jobs),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def _run_folder_picker() -> None:
    """Klasör seçme penceresini alt-process olarak açar; sonucu state'e yazar.

    Picker birden fazla backend dener (tkinter / osascript / zenity);
    hepsi başarısızsa kullanıcıya UI'da gösterilebilir bir hata kaydeder.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(config.FOLDER_PICKER),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.warning("Klasör seçici başlatılamadı: %s", exc)
        _picker_state["error"] = "Klasör seçici başlatılamadı"
        _picker_state["pending"] = False
        return
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        chosen = stdout.decode("utf-8", errors="replace").strip()
        if chosen:
            _picker_state["path"] = chosen
        elif proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            logger.warning("Klasör seçici başarısız (kod=%s): %s",
                           proc.returncode, err_text)
            _picker_state["error"] = (
                "Klasör seçici açılamadı. macOS'ta tkinter eksikse "
                "AppleScript denenir; izin reddedildiyse Sistem Ayarları → "
                "Gizlilik ve Güvenlik → Otomasyon'u kontrol edin."
            )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        _picker_state["error"] = "Klasör seçici zaman aşımına uğradı"
    finally:
        _picker_state["pending"] = False


@app.post("/api/pick-folder")
async def post_pick_folder() -> dict:
    """Native klasör seçme penceresini açar (arka planda çalışır)."""
    if not _picker_state["pending"]:
        _picker_state["pending"] = True
        _picker_state["path"] = None
        _picker_state["error"] = None
        asyncio.create_task(_run_folder_picker())
    return {"pending": True}


@app.get("/api/pick-folder")
async def get_pick_folder() -> dict:
    """Klasör seçici durumunu döndürür: {pending, path}."""
    return dict(_picker_state)


# web/ varlıkları (app.js, style.css) /static altında servis edilir.
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")
