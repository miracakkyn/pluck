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
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError

from app.models import FormatInfo, FormatsResponse, ScanResponse

# aria2c sistemde varsa harici indirici olarak kullanılır (her parça için 16
# paralel HTTP bağlantısı; CDN'in per-bağlantı hız limitini bypass eder).
# Yoksa yt-dlp'nin yerleşik indiricisi kullanılır (yine de paralel parçalı).
_ARIA2C_AVAILABLE: bool = shutil.which("aria2c") is not None

# ffmpeg birleştirme (bv*+ba) ve ses çıkarma (audio→mp3) için zorunlu.
# Yoksa kullanıcıya net hata gösterilir, sessizce yarım dosya bırakmak yerine.
_FFMPEG_AVAILABLE: bool = shutil.which("ffmpeg") is not None

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


_ERROR_MSG_MAX = 500


def _clean_error(exc: Exception) -> str:
    """yt-dlp hata metnini ANSI kodlarından ve 'ERROR:' önekinden arındırır.

    Tüm mesajı korur (ilk satır kesilmez); YouTube hataları çoğu zaman
    "Sign in to confirm…" gibi açıklayıcı detayı 2. satırda taşıdığı için
    önemli. En fazla 500 karakter — UI'ı bozmaz, ama bağlam kaybolmaz.
    """
    message = _ANSI_RE.sub("", str(exc)).strip()
    if message.upper().startswith("ERROR:"):
        message = message[len("ERROR:"):].strip()
    if not message:
        return "Bilinmeyen indirme hatası"
    if len(message) > _ERROR_MSG_MAX:
        message = message[:_ERROR_MSG_MAX].rstrip() + "…"
    return message


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
    """Format türünü codec'lerden belirler; codec bilgisi yoksa None."""
    has_video = vcodec not in _NONE_VALUES
    has_audio = acodec not in _NONE_VALUES
    if has_video and has_audio:
        return "combined"
    if has_video:
        return "video"
    if has_audio:
        return "audio"
    return None


def _infer_kind_without_codecs(raw: dict) -> str | None:
    """Codec bildirilmemiş formatlar için türü boyut/bitrate'ten çıkarır.

    HLS (m3u8) ana playlist'indeki varyant akışları çoğu zaman codec
    bildirmez (`vcodec`/`acodec` = None) ama çözünürlük taşır. Bu akışlar
    pratikte ses+video birleşik (muxed) olduğundan "combined" kabul edilir —
    aksi halde tüm HLS formatları elenip kullanıcıya boş liste gösterilirdi
    (BunnyCDN/MediaDelivery gibi sağlayıcılar). Boyut yoksa ama bitrate
    varsa ses akışı kabul edilir.
    """
    if raw.get("height") or raw.get("width"):
        return "combined"
    if raw.get("abr") or raw.get("asr"):
        return "audio"
    return None


def _parse_format(raw: dict) -> FormatInfo | None:
    """Tek bir yt-dlp format sözlüğünü FormatInfo'ya çevirir; elenecekse None."""
    ext = raw.get("ext") or ""
    if ext == "mhtml":  # storyboard
        return None
    kind = _classify(raw.get("vcodec"), raw.get("acodec"))
    if kind is None:  # codec bilgisi yok — boyut/bitrate'ten çıkarmayı dene
        kind = _infer_kind_without_codecs(raw)
    if kind is None:  # ne video boyutu ne ses bitrate'i — gerçekten sınıflanamaz
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
        seen_keys: set[str] = set()
        for entry in info.get("entries") or []:
            if not entry:
                continue
            parsed = _parse_info(entry)
            entry_url = (entry.get("webpage_url") or entry.get("original_url")
                         or entry.get("url"))
            if entry_url:
                seen_keys.add(_url_dedup_key(entry_url))
            entries.append(parsed.model_copy(update={"url": entry_url}))

        # yt-dlp generic'in atladığı videoları regex taramasıyla yakala.
        # JS-tabanlı oynatıcılarda yt-dlp bazı embed'leri kaçırabiliyor;
        # _scan_page_for_video_urls iframe ve m3u8 desenlerini görür.
        # Dedupe URL'i host+path bazında karşılaştırır — yt-dlp'nin URL'lere
        # eklediği smuggle/fragment suffix'leri farkı maskelemez.
        extra_urls = _scan_page_for_video_urls(url, browser)
        missing_urls = [
            u for u in extra_urls if _url_dedup_key(u) not in seen_keys
        ]
        warnings: list[str] = []
        if missing_urls:
            missing_entries, warnings = _extract_each(
                missing_urls, browser, referer=url
            )
            entries.extend(missing_entries)

        if not entries:
            raise EngineError("Oynatma listesinde işlenebilir video bulunamadı")
        return ScanResponse(
            type="playlist",
            playlist_title=info.get("title") or "Oynatma listesi",
            entries=entries,
            warnings=warnings,
        )

    # Tek video çıktı — ama sayfa JS-tabanlı bir oynatıcı (JWPlayer, Bunny
    # Stream, custom whiteboard player vb.) kullanıyor olabilir; içinde
    # birden fazla bağımsız akış gömülü. Sayfayı (ve gerekirse gömülü video
    # iframe'lerini) tarayıp tüm medya URL'lerini topla. Birden fazlaysa
    # generic extractor'ın zaten çıkardığı info'yu ilk girdi olarak koy
    # (yeniden fetch yapılmasın) ve geri kalanları _extract_each ile çöz.
    extra_urls = _scan_page_for_video_urls(url, browser)
    if len(extra_urls) > 1:
        generic_url = (
            info.get("webpage_url") or info.get("original_url")
            or info.get("url") or url
        )
        generic_key = _url_dedup_key(generic_url)
        # Generic info'yu parsed entry olarak koru — feda etme.
        generic_entry = _parse_info(info).model_copy(
            update={"url": generic_url}
        )
        # extra_urls içinde generic ile aynı URL varsa tekrar extract etme.
        unique_extras = [
            u for u in extra_urls if _url_dedup_key(u) != generic_key
        ]
        extra_entries, warnings = _extract_each(
            unique_extras, browser, referer=url
        )
        all_entries = [generic_entry, *extra_entries]
        if len(all_entries) > 1:
            return ScanResponse(
                type="playlist",
                playlist_title=info.get("title") or "Oynatma listesi",
                entries=all_entries,
                warnings=warnings,
            )

    return ScanResponse(type="video", video=_parse_info(info))


def _url_dedup_key(url: str) -> str:
    """URL'i host+path'e indirger — query/fragment/smuggle suffix'leri at.

    yt-dlp bazı extractor'larında URL'e `#__youtubedl_smuggle=…` ekleyebilir;
    aynı kaynağa giden iki URL string olarak farklı görünse de aynı videodur.
    Dedupe için bu fonksiyonun anahtarı kullanılır.
    """
    try:
        parts = urlsplit(url)
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
    except ValueError:
        return url


# --- JS-tabanlı sayfalar için gizli video URL'i bulma ---------------------

# Regex listesi, sırayla denenir. Daha spesifik desenleri başa, generic'i sona
# koy: spesifik host'lar (BCDN, MediaDelivery) önce yakalansın; generic
# .m3u8/.mpd/.mp4 deseni yedek olarak çalışsın. `dict.fromkeys()` benzersizliği
# korur, sıra anlamlıdır (ilk eşleşen pattern URL'yi listeye katar).
_VIDEO_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # BunnyCDN broad: path/query esnek (playlist.m3u8 + master.m3u8 + ?token=…).
    re.compile(
        r"https://[a-z0-9.-]+\.b-cdn\.net/[a-f0-9-]+/"
        r"[^\"'\s<>]*?\.m3u8(?:\?[^\"'\s<>]*)?",
        re.IGNORECASE,
    ),
    # Bunny Stream MediaDelivery embed/play iframe URL'leri (her ders ayrı
    # embed; yt-dlp bunları kendi extractor'ıyla çözebiliyor).
    re.compile(
        r"https://iframe\.mediadelivery\.net/(?:embed|play)/\d+/[a-f0-9-]+",
        re.IGNORECASE,
    ),
    # Generic streaming/progressive — son çare yedek; aşağıdaki host whitelist
    # ve uzantı kontrolü false positive'leri filtreler.
    re.compile(
        r"https?://[^\s\"'<>]+?\.(?:m3u8|mpd|mp4)(?:\?[^\s\"'<>]*)?",
        re.IGNORECASE,
    ),
)

# Sayfa içinde gömülü video iframe URL'leri — bilinen video host'larına gir,
# rastgele iframe'lere değil (gizlilik + performans).
_IFRAME_RE = re.compile(
    r"""<iframe[^>]+src\s*=\s*['"]([^'"]+?"""
    r"""(?:mediadelivery\.net|b-cdn\.net|bunnycdn\.com|jwplayer\.com)"""
    r"""[^'"]*)['"]""",
    re.IGNORECASE,
)


def _extract_urls_from_html(html: str) -> list[str]:
    """HTML metninden tanınan tüm medya URL desenlerini çıkarır.

    JSON içindeki escape edilmiş URL'leri normalize eder (`\\/` → `/`).
    Dedupe sırayı korur; spesifik pattern eşleşmeleri önce gelir.
    """
    normalized = html.replace("\\/", "/")
    found: list[str] = []
    for pattern in _VIDEO_URL_PATTERNS:
        found.extend(pattern.findall(normalized))
    return list(dict.fromkeys(found))


def _scan_page_for_video_urls(page_url: str, browser: str | None) -> list[str]:
    """Sayfayı (ve gömülü video iframe'lerini) çekip medya URL'lerini bulur.

    JS-tabanlı oynatıcılar (uzemykoabt.com gibi) videoları HTML iframe veya
    JSON içinde gömüyor; yt-dlp'nin generic extractor'ı bunlardan yalnızca
    birini bulabiliyor. Strateji:

    1. Ana sayfa HTML'ini fetch et, tüm pattern'lerle medya URL'lerini topla.
    2. Bilinen video iframe host'larına gir (max 1 derinlik), içlerinde de
       aynı taramayı tekrarla. Sonsuz döngü riskine karşı iç içe iframe
       takip edilmez (derinlik=1).

    Erişim hatası alınan kaynaklar sessizce atlanır — toplama best-effort.
    """
    options: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    try:
        with YoutubeDL(options) as ydl:
            main_html = ydl.urlopen(page_url).read().decode(
                "utf-8", errors="replace"
            )
        all_urls = _extract_urls_from_html(main_html)
        iframe_srcs = list(dict.fromkeys(_IFRAME_RE.findall(main_html)))
    except Exception:
        # Sayfa fetch'i veya regex işlemi başarısız: best-effort tarama,
        # boş dön; çağıran taraf generic extractor sonucunu kullanır.
        return []

    # 2. geçiş: gömülü video iframe'lerini takip et (max 1 derinlik).
    for iframe_src in iframe_srcs:
        # MediaDelivery embed URL'sinin kendisi yt-dlp ile çözülebilir; onu
        # bir indirilebilir aday olarak listeye al.
        if iframe_src not in all_urls:
            all_urls.append(iframe_src)
        try:
            with YoutubeDL(options) as ydl:
                iframe_html = ydl.urlopen(iframe_src).read().decode(
                    "utf-8", errors="replace"
                )
            iframe_urls = _extract_urls_from_html(iframe_html)
        except Exception:
            continue  # iframe erişilemez/işlenemez — diğerlerine devam
        for found_url in iframe_urls:
            if found_url not in all_urls:
                all_urls.append(found_url)

    return all_urls


def _extract_each(
    video_urls: list[str],
    browser: str | None,
    referer: str,
) -> tuple[list[FormatsResponse], list[str]]:
    """Her video URL'sini ayrı ayrı yt-dlp'ye verir; başarısızları topla.

    `referer` BunnyCDN'in m3u8 erişim kontrolü için gerekli (yoksa 403).
    Dönüş `(entries, warnings)` — başarısız URL'ler sessizce yutulmaz;
    `warnings` listesi UI'da kullanıcıya bildirim olarak gösterilir.
    """
    options: dict = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "http_headers": {"Referer": referer},
    }
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    entries: list[FormatsResponse] = []
    warnings: list[str] = []
    for idx, video_url in enumerate(video_urls):
        try:
            with YoutubeDL(options) as ydl:
                entry_info = ydl.extract_info(video_url, download=False)
            if entry_info:
                # m3u8 URL'inden gelen jenerik "playlist" title'i kullanışsız;
                # sıralı numara ver ki UI'da ayırt edilebilsin.
                title = (entry_info.get("title") or "").strip().lower()
                if title in ("", "playlist", "index"):
                    entry_info["title"] = f"Video {idx + 1}"
                parsed = _parse_info(entry_info)
                entries.append(parsed.model_copy(update={"url": video_url}))
        except YoutubeDLError as exc:
            warnings.append(
                f"Video {idx + 1} alınamadı: {_clean_error(exc)}"
            )
            continue
    return entries, warnings


_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_for_filename(text: str, max_len: int = 80) -> str:
    """Dosya adı için güvenli karakterlere indir."""
    cleaned = _FILENAME_UNSAFE.sub("_", text).strip().rstrip(".")
    return cleaned[:max_len] or "video"


# İndirme yarıda kalırsa geride kalan parça dosyaları (yt-dlp'nin
# .fNNN.mp4.part / .fNNN.m4a.part / .ytdl / aria2c'nin .aria2 kalıntıları).
_LEFTOVER_SUFFIXES: tuple[str, ...] = (
    ".part", ".ytdl", ".aria2", ".temp",
)


def _selection_needs_ffmpeg(selection: str) -> bool:
    """Bu seçim ffmpeg gerektiriyor mu (merge ya da ses çıkarma)?

    Tüm preset'ler `bv*+ba` türevi olduğundan video+ses merge ister;
    `audio` ise mp3 postprocessor ister. Ham format_id verilirse `+ba`
    yedeği eklendiği için (build_format_selector) yine merge olasıdır.
    """
    return True


def _cleanup_artifacts(download_dir: Path, info: dict | None) -> None:
    """Yarım kalan parça/temp dosyaları temizler.

    Glob shell metakarakterlerini etkisizleştirmek için yt-dlp'nin verdiği
    `id` ve sanitize edilmiş başlık üzerinden filtreler. Hata yutar —
    cleanup'ın kendisi indirme hatasını gizlememeli.
    """
    if not info:
        return
    video_id = str(info.get("id") or "").strip()
    if not video_id:
        return
    try:
        for entry in download_dir.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            # Hem [id] içeren hem de bilinen leftover uzantısıyla biten dosyalar.
            if f"[{video_id}]" not in name:
                continue
            if not any(name.endswith(suffix) for suffix in _LEFTOVER_SUFFIXES):
                continue
            try:
                entry.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def download(
    *,
    url: str,
    selection: str,
    download_dir: str | Path,
    browser: str | None = None,
    title: str | None = None,
    referer: str | None = None,
    progress_hook: Callable[[dict], None] | None = None,
) -> None:
    """Videoyu indirir ve gerekirse ffmpeg ile birleştirir (bloklayan).

    `progress_hook` yt-dlp'nin durum sözlüğüyle düzenli çağrılır. İptal,
    hook'un içeriden bir istisna fırlatmasıyla sağlanır (queue_manager).
    `title` verilirse dosya adının başında o kullanılır (jenerik m3u8
    "playlist" yerine "Video 1" gibi).
    `referer` verilirse HTTP Referer başlığı olarak kullanılır — eklenti
    rozeti, gömülü oynatıcının temiz embed URL'sini (smuggle eki olmadan)
    gönderdiğinde, referer-koruyan CDN'lerin (BunnyCDN/MediaDelivery) 403
    vermemesi için sayfa adresini referer olarak iletir.

    İndirme bittikten sonra dosyanın diskte gerçekten var olduğu doğrulanır;
    yoksa (ffmpeg merge sessizce başarısız olduysa) EngineError fırlatır.
    Hatada yarım kalan .part / .ytdl / .aria2 dosyaları temizlenir.
    """
    # ffmpeg her preset için gerekli (video+ses merge ya da audio→mp3). Yoksa
    # erkenden net hata ver; sessiz başarısızlıkla .part dosyalarını biriktirme.
    if _selection_needs_ffmpeg(selection) and not _FFMPEG_AVAILABLE:
        raise EngineError(
            "ffmpeg bulunamadı. macOS: 'brew install ffmpeg', "
            "Windows: 'winget install ffmpeg' ile kurun."
        )
    download_path = Path(download_dir)
    if title:
        safe_title = _sanitize_for_filename(title)
        outtmpl = f"{safe_title} [%(id)s] {selection}.%(ext)s"
    else:
        outtmpl = f"%(title)s [%(id)s] {selection}.%(ext)s"

    # postprocessor_hooks ile merge sonrası nihai dosya yolunu yakala; bu yol
    # progress_hook'taki "finished" event'inden daha güvenilirdir (merge
    # tamamlandığında ateşlenir, video-only/audio-only ara dosyalardan sonra).
    final_filename: dict[str, str] = {}

    def _pp_hook(data: dict) -> None:
        if data.get("status") == "finished":
            info_dict = data.get("info_dict") or {}
            filepath = info_dict.get("filepath") or info_dict.get("_filename")
            if filepath:
                final_filename["path"] = filepath

    captured_info: dict = {}

    def _wrapped_progress(data: dict) -> None:
        # Son finished event'inde info_dict'i sakla; cleanup için id lazım.
        if data.get("status") == "finished":
            info = data.get("info_dict") or {}
            if info.get("id"):
                captured_info.update(info)
            if data.get("filename"):
                final_filename.setdefault("path", data["filename"])
        if progress_hook is not None:
            progress_hook(data)

    options: dict = {
        "format": build_format_selector(selection),
        "merge_output_format": "mp4",
        "paths": {"home": str(download_path)},
        # Kalite/format seçimi dosya adına yazılır: aynı videoyu farklı
        # kalitede indirince çakışma olmaz (yt-dlp "zaten indirilmiş" deyip
        # atlamaz). `selection` doğrulanmış, dosya-adı-güvenli bir dizedir.
        "outtmpl": outtmpl,
        # HLS (m3u8) ve DASH parçalarını paralel indir. Yüksek RTT'li CDN'lerde
        # (örn. ~62ms BunnyCDN edge) TCP slow-start nedeniyle her bağlantı ~200-300
        # KiB/s'de takılır; paralellik bu tavanın katları kadar toplam hız verir.
        # Tek-dosya MP4 progressive indirmelerde hiçbir etkisi yoktur (no-op).
        "concurrent_fragment_downloads": 10,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [_wrapped_progress],
        "postprocessor_hooks": [_pp_hook],
    }
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    # Referer önceliği:
    # 1) Çağıranın verdiği `referer` (eklenti rozeti → sayfa adresi). Embed
    #    oynatıcılar (MediaDelivery vb.) genelde gömüldükleri sayfayı referer
    #    olarak ister; temiz embed URL'sinde smuggle eki olmadığında bu gerekir.
    # 2) Aksi halde, doğrudan BunnyCDN m3u8 URL'leri için generic mediadelivery
    #    refereri (yoksa 403).
    if referer:
        options["http_headers"] = {"Referer": referer}
    elif ".b-cdn.net/" in url and url.endswith(".m3u8"):
        options["http_headers"] = {"Referer": "https://iframe.mediadelivery.net/"}
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
        _cleanup_artifacts(download_path, captured_info)
        raise EngineError(_clean_error(exc)) from exc

    # yt-dlp exception fırlatmasa bile merge çökmüş olabilir — diskte nihai
    # dosyanın gerçekten var olduğunu doğrula. Yoksa .part'lar geride kalır.
    expected = final_filename.get("path")
    if expected and not Path(expected).exists():
        _cleanup_artifacts(download_path, captured_info)
        raise EngineError(
            "İndirme tamamlanamadı — ffmpeg birleştirmesi başarısız olmuş "
            "olabilir. ffmpeg sürümünü kontrol edin."
        )
    if not expected:
        # Hiçbir nihai yol yakalanmadıysa kuyruk tarafı yine de hata işaretler
        # (queue_manager kontrolü); burada en azından artığı temizle.
        _cleanup_artifacts(download_path, captured_info)
