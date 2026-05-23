"""Pydantic istek/yanıt şemaları ve dahili Job veri yapısı.

Girdiler sistem sınırında (API katmanında) doğrulanır; geçersiz veri
buraya kadar gelmez.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator

from app import config

FormatKind = Literal["video", "audio", "combined"]
JobStatus = Literal["queued", "downloading", "completed", "error", "cancelled"]

# Geçerli bir preset adı veya yt-dlp format_id için izin verilen karakterler.
_SELECTION_RE = re.compile(r"^[A-Za-z0-9_.+\-]+$")

# İndirilmesi engellenen iç ana makine adları (loopback / geçersiz).
_BLOCKED_HOSTS = {"localhost", "0.0.0.0", "::1", "::"}


def _validate_url(value: str) -> str:
    """URL boş olamaz, http(s) olmalı ve iç/loopback adres göstermemeli.

    LAN medya sunucuları (RFC 1918) bilinçli olarak engellenmez; yalnızca
    loopback ve link-local adresler reddedilir (SSRF yüzeyini daraltır).
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("URL boş olamaz")
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise ValueError("URL http:// veya https:// ile başlamalı")

    host = (urlparse(cleaned).hostname or "").lower()
    if not host:
        raise ValueError("URL geçerli bir adres içermeli")
    if host in _BLOCKED_HOSTS:
        raise ValueError("Yerel/iç adresler indirilemez")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_loopback or ip.is_link_local):
        raise ValueError("Yerel/iç adresler indirilemez")
    return cleaned


def _validate_browser(value: str | None) -> str | None:
    """Boş ise None; aksi halde bu platformda desteklenen bir tarayıcı olmalı."""
    if value is None or not value.strip():
        return None
    cleaned = value.strip().lower()
    if cleaned not in config.available_browsers():
        raise ValueError(f"Desteklenmeyen tarayıcı: {value}")
    return cleaned


class FormatsRequest(BaseModel):
    """POST /api/formats gövdesi.

    `browser` verilirse format taraması o tarayıcının çerezleriyle yapılır —
    login gerektiren siteler için gereklidir.
    """

    url: str
    browser: str | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        return _validate_url(value)

    @field_validator("browser")
    @classmethod
    def _check_browser(cls, value: str | None) -> str | None:
        return _validate_browser(value)


class FormatInfo(BaseModel):
    """Tek bir indirilebilir format/çözünürlük."""

    format_id: str
    ext: str
    resolution: str
    kind: FormatKind
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    note: str = ""


class FormatsResponse(BaseModel):
    """Tek bir videonun tarama sonucu.

    `url` yalnızca oynatma listesi girdileri için doldurulur (her girdinin
    kendi sayfa adresi); tek video durumunda isteğin URL'si zaten bilindiği
    için boş bırakılır.
    """

    title: str
    formats: list[FormatInfo]
    presets: list[str]
    duration: float | None = None
    thumbnail: str | None = None
    uploader: str | None = None
    url: str | None = None


class ScanResponse(BaseModel):
    """POST /api/formats yanıtı: tek video veya oynatma listesi.

    - `type="video"`  → `video` doludur, `entries` boş.
    - `type="playlist"` → `entries` doludur (her girdi tam format bilgisiyle).
    """

    type: Literal["video", "playlist"]
    video: FormatsResponse | None = None
    playlist_title: str | None = None
    entries: list[FormatsResponse] | None = None


class ConfigResponse(BaseModel):
    """GET /api/config yanıtı."""

    default_download_dir: str
    common_dirs: list[str]
    browsers: list[str]
    presets: list[str]


class JobRequest(BaseModel):
    """POST /api/jobs gövdesi — kuyruğa bir indirme işi ekler.

    `title` verilirse dosya adında o kullanılır (m3u8 URL'lerinden gelen
    jenerik "playlist" yerine "Video 1" vb.). Yoksa yt-dlp'nin algıladığı
    title kullanılır.
    """

    url: str
    selection: str          # preset adı ("best", "720p", ...) veya ham format_id
    download_dir: str
    browser: str | None = None
    title: str | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        return _validate_url(value)

    @field_validator("selection")
    @classmethod
    def _check_selection(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Bir kalite/format seçilmeli")
        if not _SELECTION_RE.match(cleaned):
            raise ValueError("Geçersiz format seçimi")
        return cleaned

    @field_validator("download_dir")
    @classmethod
    def _check_dir(cls, value: str) -> str:
        path = Path(value).expanduser()
        if not path.is_dir():
            # Ham yol yansıtılmaz (bilgi sızması).
            raise ValueError("Belirtilen indirme klasörü bulunamadı veya erişilemez")
        return str(path)

    @field_validator("browser")
    @classmethod
    def _check_browser(cls, value: str | None) -> str | None:
        return _validate_browser(value)


@dataclass
class Job:
    """Kuyruktaki bir indirme işinin bellekteki durumu.

    Tek yazıcı (worker thread) tarafından güncellenir; SSE yalnızca okur.
    """

    job_id: str
    url: str
    selection: str
    download_dir: str
    browser: str | None = None
    title: str = ""
    status: JobStatus = "queued"
    progress: float = 0.0          # 0–100
    speed: str | None = None       # insan-okur, ör. "1.2MiB/s"
    eta: str | None = None         # insan-okur, ör. "00:42"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    filename: str | None = None
    error: str | None = None
    cancel_requested: bool = False  # dahili; snapshot'a dahil edilmez
    title_locked: bool = False  # True ise progress hook title'i ezmez

    def snapshot(self) -> dict:
        """SSE/JSON için serileştirilebilir görüntü (dahili bayraklar hariç)."""
        return {
            "job_id": self.job_id,
            "url": self.url,
            "selection": self.selection,
            "title": self.title,
            "status": self.status,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "filename": self.filename,
            "error": self.error,
        }
