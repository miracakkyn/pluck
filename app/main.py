"""FastAPI uygulaması — route'lar ve yaşam döngüsü.

Sunucu yalnızca 127.0.0.1'e bağlanır (yerel araç). Bloklayan yt-dlp
çağrıları `asyncio.to_thread` ile thread'e alınır.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app import config, ytdlp_engine
from app.events import event_stream
from app.models import ConfigResponse, FormatsRequest, FormatsResponse, JobRequest
from app.queue_manager import QueueManager
from app.ytdlp_engine import EngineError

logger = logging.getLogger("videoaraci")

# Tek süreçli uygulama için modül seviyesinde tek kuyruk yöneticisi.
queue_manager = QueueManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Uygulama yaşam döngüsü: indirme worker'ını başlat ve düzgünce durdur."""
    queue_manager.start()
    try:
        yield
    finally:
        await queue_manager.stop()


app = FastAPI(title="Genel Amaçlı Video İndirme Aracı", lifespan=lifespan)


@app.get("/api/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Arayüz için varsayılan klasör, sık klasörler, tarayıcılar ve preset'ler."""
    return ConfigResponse(
        default_download_dir=str(config.default_download_dir()),
        common_dirs=config.common_dirs(),
        browsers=config.available_browsers(),
        presets=list(ytdlp_engine.PRESETS),
    )


@app.post("/api/formats", response_model=FormatsResponse)
async def post_formats(req: FormatsRequest) -> FormatsResponse:
    """Verilen URL için mevcut format/çözünürlükleri döndürür."""
    try:
        return await asyncio.to_thread(ytdlp_engine.list_formats, req.url)
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


@app.get("/api/events")
async def get_events() -> StreamingResponse:
    """Canlı indirme ilerlemesi akışı (Server-Sent Events)."""
    return StreamingResponse(
        event_stream(queue_manager.jobs),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
