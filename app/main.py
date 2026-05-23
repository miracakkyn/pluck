"""FastAPI uygulaması — route'lar ve yaşam döngüsü.

Sunucu yalnızca 127.0.0.1'e bağlanır (yerel araç). Bloklayan yt-dlp
çağrıları `asyncio.to_thread` ile thread'e alınır.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import config, ytdlp_engine
from app.events import event_stream
from app.models import ConfigResponse, FormatsRequest, JobRequest, ScanResponse
from app.queue_manager import QueueManager
from app.ytdlp_engine import EngineError

logger = logging.getLogger("videoaraci")

# Tek süreçli uygulama için modül seviyesinde tek kuyruk yöneticisi.
queue_manager = QueueManager()

# Klasör seçici durumu: pencere açık mı (pending) ve son seçilen yol (path).
_picker_state: dict = {"pending": False, "path": None}


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Uygulama yaşam döngüsü: indirme worker'ını başlat ve düzgünce durdur."""
    queue_manager.start()
    try:
        yield
    finally:
        await queue_manager.stop()


app = FastAPI(title="Pluck", lifespan=lifespan)

# Chrome eklentisinin (chrome-extension://) yerel motora erişebilmesi için.
# Yalnızca eklenti kaynakları yanıtı okuyabilir; rastgele web siteleri okuyamaz.
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

    tkinter ayrı bir süreçte kendi ana thread'inde çalışır (Windows + macOS
    uyumlu). Kullanıcı iptal ederse veya tkinter yoksa `path` None kalır.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(config.FOLDER_PICKER),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        logger.warning("Klasör seçici başlatılamadı")
        _picker_state["pending"] = False
        return
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        chosen = stdout.decode("utf-8", errors="replace").strip()
        if chosen:
            _picker_state["path"] = chosen
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
    finally:
        _picker_state["pending"] = False


@app.post("/api/pick-folder")
async def post_pick_folder() -> dict:
    """Native klasör seçme penceresini açar (arka planda çalışır)."""
    if not _picker_state["pending"]:
        _picker_state["pending"] = True
        _picker_state["path"] = None
        asyncio.create_task(_run_folder_picker())
    return {"pending": True}


@app.get("/api/pick-folder")
async def get_pick_folder() -> dict:
    """Klasör seçici durumunu döndürür: {pending, path}."""
    return dict(_picker_state)


# web/ varlıkları (app.js, style.css) /static altında servis edilir.
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")
