"""yt-dlp sarmalayıcı — format listeleme ve indirme.

Bu modül framework'ten bağımsızdır (FastAPI bilmez) ve site-özel kod
içermez; yalnızca yt-dlp'nin genel yeteneklerini kullanır. Bloklayan
fonksiyonlar (`list_formats`, `download`) çağıran taraf tarafından
`asyncio.to_thread` ile thread'e alınmalıdır.
"""
from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError

from app.models import FormatInfo, FormatsResponse, ScanResponse

# aria2c sistemde varsa harici indirici olarak kullanılır (her parça için 16
# paralel HTTP bağlantısı; CDN'in per-bağlantı hız limitini bypass eder).
# Yoksa yt-dlp'nin yerleşik indiricisi kullanılır (yine de paralel parçalı).
_ARIA2C_AVAILABLE: bool = shutil.which("aria2c") is not None

# Arayüzde sunulan hazır kalite preset'leri (sıra önemli; "best" varsayılan).
PRESETS: tuple[str, ...] = ("best", "1080p", "720p", "480p", "audio")

# Preset adı -> yt-dlp format seçici string'i.
# İlk dalda mp4 video + m4a (AAC) ses tercih edilir: bu, her oynatıcıda
# sorunsuz ÇALIŞAN (sesli) bir .mp4 üretir — opus sesin mp4 içinde sessiz
# görünmesini önler. Uygun mp4/m4a yoksa ikinci dalda en iyi video+ses'e
# düşülür (yine de ses içerir).
_PRESET_SELECTORS: dict[str, str] = {
    "best": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
    "1080p": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]",
    "720p": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]",
    "480p": "bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]+ba/b[height<=480]",
    "audio": "ba/b",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_NONE_VALUES = {None, "", "none"}


class EngineError(Exception):
    """yt-dlp kaynaklı, kullanıcıya gösterilebilir hata."""


def _clean_error(exc: Exception) -> str:
    """yt-dlp hata metnini ANSI kodlarından ve 'ERROR:' önekinden arındırır."""
    message = _ANSI_RE.sub("", str(exc)).strip()
    if message.upper().startswith("ERROR:"):
        message = message[len("ERROR:"):].strip()
    first_line = message.splitlines()[0].strip() if message else ""
    return first_line or "Bilinmeyen indirme hatası"


def build_format_selector(selection: str) -> str:
    """Preset adını veya ham format_id'yi yt-dlp format string'ine çevirir.

    Saf fonksiyon — kolayca test edilir. Ham format_id verilirse video-only
    olma ihtimaline karşı sese (`+ba`) birleştirilir, yedeği yalnız id'dir.
    """
    preset = _PRESET_SELECTORS.get(selection)
    if preset is not None:
        return preset
    return f"{selection}+ba/{selection}"


def _classify(vcodec: str | None, acodec: str | None) -> str | None:
    """Format türünü belirler; geçersiz (her ikisi de yok) ise None."""
    has_video = vcodec not in _NONE_VALUES
    has_audio = acodec not in _NONE_VALUES
    if has_video and has_audio:
        return "combined"
    if has_video:
        return "video"
    if has_audio:
        return "audio"
    return None


def _parse_format(raw: dict) -> FormatInfo | None:
    """Tek bir yt-dlp format sözlüğünü FormatInfo'ya çevirir; elenecekse None."""
    ext = raw.get("ext") or ""
    if ext == "mhtml":  # storyboard
        return None
    kind = _classify(raw.get("vcodec"), raw.get("acodec"))
    if kind is None:  # ne video ne ses
        return None
    height = raw.get("height")
    width = raw.get("width")
    resolution = raw.get("resolution")
    if not resolution:
        if width and height:
            resolution = f"{width}x{height}"
        elif height:
            resolution = f"{height}p"
        else:
            resolution = "—"
    return FormatInfo(
        format_id=str(raw.get("format_id", "")),
        ext=ext,
        resolution=resolution,
        kind=kind,
        height=height,
        fps=raw.get("fps"),
        vcodec=None if raw.get("vcodec") in _NONE_VALUES else raw.get("vcodec"),
        acodec=None if raw.get("acodec") in _NONE_VALUES else raw.get("acodec"),
        filesize=raw.get("filesize") or raw.get("filesize_approx"),
        note=raw.get("format_note") or "",
    )


def _sort_key(fmt: FormatInfo) -> tuple:
    """Video/birleşik formatlar çözünürlüğe göre azalan; ses formatları sonda."""
    is_audio = fmt.kind == "audio"
    return (is_audio, -(fmt.height or 0))


def _safe_thumbnail(value: object) -> str | None:
    """Yalnızca http(s) küçük resim URL'lerini kabul eder (savunma katmanı)."""
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def _parse_info(info: dict) -> FormatsResponse:
    """yt-dlp extract_info çıktısını FormatsResponse'a dönüştürür."""
    formats: list[FormatInfo] = []
    for raw in info.get("formats") or []:
        parsed = _parse_format(raw)
        if parsed is not None:
            formats.append(parsed)
    formats.sort(key=_sort_key)
    return FormatsResponse(
        title=info.get("title") or "Adsız video",
        duration=info.get("duration"),
        thumbnail=_safe_thumbnail(info.get("thumbnail")),
        uploader=info.get("uploader"),
        formats=formats,
        presets=list(PRESETS),
    )


def list_formats(url: str, browser: str | None = None) -> ScanResponse:
    """URL'yi tarar; tek video ya da oynatma listesi (çoklu video) döndürür.

    `browser` verilirse o tarayıcının çerezleri kullanılır — login gerektiren
    siteleri yt-dlp'nin görebilmesi için tarama adımında da gereklidir.
    Çoklu video sayfalarında her girdi için ayrıca format çıkarımı yapılır
    (yt-dlp varsayılan davranışı); bu, tüm girdileri kalite seçimiyle birlikte
    göstermeyi mümkün kılar ama ücreti tarama süresinin artmasıdır.
    """
    options: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except YoutubeDLError as exc:
        raise EngineError(_clean_error(exc)) from exc

    if info is None:
        raise EngineError("Video bilgisi alınamadı")

    if info.get("_type") == "playlist":
        entries: list[FormatsResponse] = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            parsed = _parse_info(entry)
            entry_url = (entry.get("webpage_url") or entry.get("original_url")
                         or entry.get("url"))
            entries.append(parsed.model_copy(update={"url": entry_url}))
        if not entries:
            raise EngineError("Oynatma listesinde işlenebilir video bulunamadı")
        return ScanResponse(
            type="playlist",
            playlist_title=info.get("title") or "Oynatma listesi",
            entries=entries,
        )

    return ScanResponse(type="video", video=_parse_info(info))


def download(
    *,
    url: str,
    selection: str,
    download_dir: str | Path,
    browser: str | None = None,
    progress_hook: Callable[[dict], None] | None = None,
) -> None:
    """Videoyu indirir ve gerekirse ffmpeg ile birleştirir (bloklayan).

    `progress_hook` yt-dlp'nin durum sözlüğüyle düzenli çağrılır. İptal,
    hook'un içeriden bir istisna fırlatmasıyla sağlanır (queue_manager).
    """
    options: dict = {
        "format": build_format_selector(selection),
        "merge_output_format": "mp4",
        "paths": {"home": str(download_dir)},
        # Kalite/format seçimi dosya adına yazılır: aynı videoyu farklı
        # kalitede indirince çakışma olmaz (yt-dlp "zaten indirilmiş" deyip
        # atlamaz). `selection` doğrulanmış, dosya-adı-güvenli bir dizedir.
        "outtmpl": f"%(title)s [%(id)s] {selection}.%(ext)s",
        # HLS (m3u8) ve DASH parçalarını paralel indir. Yüksek RTT'li CDN'lerde
        # (örn. ~62ms BunnyCDN edge) TCP slow-start nedeniyle her bağlantı ~200-300
        # KiB/s'de takılır; paralellik bu tavanın katları kadar toplam hız verir.
        # Tek-dosya MP4 progressive indirmelerde hiçbir etkisi yoktur (no-op).
        "concurrent_fragment_downloads": 10,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [progress_hook] if progress_hook else [],
    }
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    if selection == "audio":
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
        ]
    if _ARIA2C_AVAILABLE:
        # Her HLS/HTTP parçası için aria2c 16 paralel bağlantı açar; toplam
        # akış = (concurrent_fragment_downloads) × (aria2c connections).
        options["external_downloader"] = {
            "http": "aria2c",
            "m3u8_native": "aria2c",
        }
        options["external_downloader_args"] = {
            "aria2c": [
                "-x16", "-s16",
                # min split size verilmediğinde aria2c default 20MB; küçük HLS
                # fragment'lerini de bölmesi için 256K'ya indir.
                "-k256K",
                "--summary-interval=0",
                "--console-log-level=warn",
                "--max-tries=3",
                "--retry-wait=1",
            ],
        }
    try:
        with YoutubeDL(options) as ydl:
            ydl.download([url])
    except YoutubeDLError as exc:
        raise EngineError(_clean_error(exc)) from exc
