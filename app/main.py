"""FastAPI uygulaması — route'lar ve yaşam döngüsü.

Sunucu yalnızca 127.0.0.1'e bağlanır (yerel araç). Bloklayan yt-dlp
çağrıları `asyncio.to_thread` ile thread'e alınır.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException

from app import config, ytdlp_engine
from app.models import ConfigResponse, FormatsRequest, FormatsResponse
from app.ytdlp_engine import EngineError

logger = logging.getLogger("videoaraci")

app = FastAPI(title="Genel Amaçlı Video İndirme Aracı")


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
