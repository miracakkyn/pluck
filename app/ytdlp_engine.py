"""yt-dlp sarmalayıcı — format listeleme ve indirme.

Bu modül framework'ten bağımsızdır (FastAPI bilmez) ve site-özel kod
içermez; yalnızca yt-dlp'nin genel yeteneklerini kullanır. Bloklayan
fonksiyonlar (`list_formats`, `download`) çağıran taraf tarafından
`asyncio.to_thread` ile thread'e alınmalıdır.
"""
from __future__ import annotations

import gc
import ipaddress
import re
import shutil
import socket
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError

from app.models import FormatInfo, FormatsResponse, ScanResponse, _validate_url

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
    # Çözünürlük preset'lerinin son dalı sınırsız `/b`: istenen yükseklikte
    # format yoksa (örn. yalnızca 720p sunan BunnyCDN embed'inde 480p seçmek)
    # "Requested format is not available" yerine mevcut en iyi formata düşülür.
    "1080p": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
    "720p": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b",
    "480p": "bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]+ba/b[height<=480]/b",
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

# iframe host whitelist — _IFRAME_RE substring eşleştiği için host'un GERÇEKTEN
# bunlardan biri olduğu ayrıca parse edilerek doğrulanır (SSRF savunması).
_KNOWN_IFRAME_HOSTS: tuple[str, ...] = (
    "mediadelivery.net", "b-cdn.net", "bunnycdn.com", "jwplayer.com",
)


# DNS çözümleyici — testler offline determinizm için bunu patch'ler.
_resolve_host = socket.getaddrinfo


def _resolves_to_internal(host: str) -> bool:
    """Alan adı loopback/link-local bir IP'ye çözülüyor mu? (DNS rebinding).

    Çözümleme başarısızsa False (fail-open): geçerli bir URL'yi geçici DNS
    hatası yüzünden reddetme. TOCTOU: fetch anında IP değişebilir — bu, IP
    literal kontrolünü tamamlayan ikincil bir savunmadır (yerel araç modeli).
    """
    try:
        infos = _resolve_host(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            continue
        if ip.is_loopback or ip.is_link_local:
            return True
    return False


def _safe_discovered_url(url: str) -> str | None:
    """Sayfa taramasıyla KEŞFEDİLEN URL'yi doğrular; geçersizse None döner.

    Kullanıcının yapıştırdığı URL API katmanında `_validate_url`'den geçer; ama
    sayfadan çıkarılan (iframe src, regex m3u8/mp4) URL'ler o kontrolü hiç
    görmüyordu. Kötü niyetli/ele geçirilmiş bir sayfa gömülü bir
    `http://127.0.0.1/...` veya link-local URL'yle motoru iç kaynaklara
    yönlendirebilir (çerez seçiliyse kimlik-doğrulamalı SSRF). Bu yüzden
    keşfedilen her URL, yapıştırılan URL ile AYNI politikadan geçirilir; ayrıca
    alan adları DNS rebinding'e karşı çözümlenip iç adres kontrol edilir.
    """
    try:
        cleaned = _validate_url(url)
    except ValueError:
        return None
    # _validate_url IP literal loopback/link-local'i eler; alan adı bir rebinding
    # host'u olabilir (evil.com → 127.0.0.1). Host alan adıysa çözümle ve iç
    # adrese işaret ediyorsa reddet.
    host = (urlsplit(cleaned).hostname or "").lower()
    try:
        ipaddress.ip_address(host)  # IP literal → DNS'e gerek yok
    except ValueError:
        if host and _resolves_to_internal(host):
            return None
    return cleaned


def _is_known_iframe_host(url: str) -> bool:
    """iframe src'nin parse edilmiş host'u bilinen bir video host'u mu?

    `_IFRAME_RE` yalnızca substring eşleştiği için `http://127.0.0.1/x?
    d=mediadelivery.net` gibi bir URL deseni yakalayabilir; host'u ayrıca
    parse edip whitelist ile karşılaştırmak bu atlatmayı kapatır.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _KNOWN_IFRAME_HOSTS)


def _extract_urls_from_html(html: str) -> list[str]:
    """HTML metninden tanınan tüm medya URL desenlerini çıkarır.

    JSON içindeki escape edilmiş URL'leri normalize eder (`\\/` → `/`).
    Dedupe sırayı korur; spesifik pattern eşleşmeleri önce gelir. Keşfedilen
    URL'ler `_safe_discovered_url` ile doğrulanır (loopback/link-local elenir).
    """
    normalized = html.replace("\\/", "/")
    found: list[str] = []
    for pattern in _VIDEO_URL_PATTERNS:
        found.extend(pattern.findall(normalized))
    deduped = list(dict.fromkeys(found))
    return [u for u in (_safe_discovered_url(x) for x in deduped) if u]


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
        # SSRF savunması: iframe src fetch edilmeden ÖNCE, yapıştırılan URL ile
        # aynı doğrulamadan geçmeli VE gerçek host'u bilinen bir video host'u
        # olmalı (substring eşleşmesi loopback/iç hedefleri geçirmesin).
        safe_src = _safe_discovered_url(iframe_src)
        if not safe_src or not _is_known_iframe_host(safe_src):
            continue
        # MediaDelivery embed URL'sinin kendisi yt-dlp ile çözülebilir; onu
        # bir indirilebilir aday olarak listeye al.
        if safe_src not in all_urls:
            all_urls.append(safe_src)
        try:
            with YoutubeDL(options) as ydl:
                iframe_html = ydl.urlopen(safe_src).read().decode(
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


def _is_leftover_name(name: str) -> bool:
    """Dosya adı yarım-indirme artığı mı?

    `endswith` yetmez: aria2c HLS parçaları `....mp4.part-Frag44`,
    `....mp4.part-Frag44.aria2` gibi adlar üretir — leftover işareti adın
    ORTASINDA kalır. Bu yüzden işaretleri substring olarak ararız. `[id]`
    kapısıyla zaten o indirmeye özgü dosyalara daraltıldığı için güvenli.
    """
    return any(marker in name for marker in _LEFTOVER_SUFFIXES)


def _matching_leftovers(
    download_dir: Path, needle: str | None, prefix: str | None,
) -> list[Path]:
    """download_dir içindeki, bu indirmeye ait yarım-parça dosyalarını döndürür."""
    found: list[Path] = []
    try:
        for entry in download_dir.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if not _is_leftover_name(name):
                continue
            matches_id = needle is not None and needle in name
            matches_prefix = prefix is not None and name.startswith(prefix)
            if matches_id or matches_prefix:
                found.append(entry)
    except OSError:
        pass
    return found


def _cleanup_artifacts(
    download_dir: Path,
    info: dict | None,
    name_prefix: str | None = None,
    max_wait: float = 3.0,
) -> None:
    """Yarım kalan parça/temp dosyaları temizler.

    İki eşleştirme ölçütünden biri tutmalı (ve dosya leftover işareti taşımalı):
    - `[{id}]` — yt-dlp'nin verdiği video id'si (progress hook'tan yakalanmışsa).
    - `name_prefix` ile başlama — `outtmpl`'in sabit öneki (title-locked işlerde
      `{safe_title} [`). İPTAL için kritik: aria2c harici indirici kullanılırken
      progress hook id'yi yakalayamadan iptal gelebilir; id boş kalsa bile önek
      eşleşmesiyle çöp parçalar temizlenir.

    İptalde aria2c hemen ölmez (~1-3sn) ve ölürken 0-baytlık `.part`'ı YENİDEN
    yazabilir; bu yüzden tek silme yetmez. Bir tarama hiç eşleşme bulmayana ya da
    `max_wait` dolana kadar döngüde silinir. Başarı yolunda (aria2c yok) ilk
    silmeden sonra ikinci tarama boş döner → anında çıkar. Hata yutar.
    """
    video_id = str((info or {}).get("id") or "").strip()
    needle = f"[{video_id}]" if video_id else None
    prefix = name_prefix or None
    if needle is None and prefix is None:
        return  # ne id ne önek — neyi sileceğimizi bilmiyoruz, dokunma

    # yt-dlp'nin indirici nesnesi (with-bloğu çıktıktan sonra referanssız) ana
    # `.mp4.part` dosyasının tutacını açık bırakabiliyor; Windows'ta bu, dosyanın
    # silinmesini engeller. gc.collect() nesneyi sonlandırıp tutacı kapatır →
    # parça dosyaları sorunsuz silinir.
    gc.collect()

    deadline = time.monotonic() + max_wait
    while True:
        remaining = _matching_leftovers(download_dir, needle, prefix)
        if not remaining:
            return
        for entry in remaining:
            try:
                entry.unlink(missing_ok=True)
            except OSError:
                pass  # kilitli (aria2c hâlâ ölüyor) — sonraki turda yeniden dene
        # Silme kalıcı oldu mu? Kalmadıysa bitti (başarı yolunda hızlı çıkış).
        if not _matching_leftovers(download_dir, needle, prefix):
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.4)


def cleanup_partial_files(
    download_dir: str | Path,
    *,
    title: str | None,
    selection: str,
) -> None:
    """İptal/hata sonrası yarım kalan parça dosyalarını temizler (ikinci geçiş).

    `download()` İÇİNDEKİ cleanup, fonksiyon henüz dönmediği ve yt-dlp indirici
    nesnesi `except` bloğunda hâlâ referanslı olduğu için ana `.mp4.part`
    dosyasının tutacını kapatamaz (Windows'ta silinemez). Bu fonksiyon
    `download()` TAMAMEN döndükten SONRA (queue_manager tarafından) çağrılır;
    frame yok olduğundan gc.collect() indiriciyi sonlandırıp tutacı kapatır ve
    ana parça da silinir. Yalnızca title-locked işlerde önek bilinir.
    """
    if not title:
        return  # önek yok — in-download id-tabanlı temizliğe bırak
    prefix = f"{_sanitize_for_filename(title)} ["
    # download() döndükten sonra ikinci geçiş: aria2c ölürken yeniden yazılan
    # parçaları yakalar. Ana `.mp4.part`, fragment indirme thread havuzunca
    # süreç ömrü boyunca açık tutulabildiğinden bu oturumda silinemeyebilir
    # (yt-dlp/Windows kısıtı) — kısa pencere yeterli, worker'ı boşuna bekletme.
    _cleanup_artifacts(Path(download_dir), {}, prefix, max_wait=3.0)


def download(
    *,
    url: str,
    selection: str,
    download_dir: str | Path,
    browser: str | None = None,
    title: str | None = None,
    referer: str | None = None,
    progress_hook: Callable[[dict], None] | None = None,
) -> str | None:
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

    Dönüş: merge/dönüştürme SONRASI nihai dosya yolu (var olduğu doğrulanmış),
    ya da hiçbir yol yakalanamadıysa None. Çağıran bunu `job.filename` olarak
    kullanmalı — `progress_hook`'un "finished" event'i çok-akışlı indirmelerde
    ARA parça dosyalarını (merge sonrası silinen .fNNN.mp4/.m4a) bildirir; bu
    yüzden güvenilir yol yalnızca buradan (return değeri) alınabilir.
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
        # outtmpl'in sabit öneki — iptal/hata sonrası çöp parça temizliğinde
        # id yakalanmasa bile bu önekle eşleşen .part dosyaları silinir.
        cleanup_prefix = f"{safe_title} ["
    else:
        outtmpl = f"%(title)s [%(id)s] {selection}.%(ext)s"
        cleanup_prefix = None  # title yok → id-tabanlı temizliğe güven

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
        # id'yi MÜMKÜN OLAN EN ERKEN yakala: iptal/hata indirme ortasında
        # gelirse "finished" event'i hiç ateşlenmez; o yüzden "downloading"
        # event'lerinde de id'yi sakla — yoksa cleanup id bulamaz, .part
        # parçaları silinmez (iptal sonrası çöp dosya birikir).
        info = data.get("info_dict") or {}
        if info.get("id") and not captured_info.get("id"):
            captured_info.update(info)
        if data.get("status") == "finished" and data.get("filename"):
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

    def _execute() -> None:
        # YoutubeDL'i ayrı bir frame'de çalıştır: istisna fırlarsa bu frame
        # çözülür ve `ydl` referansı düşer. cleanup'taki gc.collect() ancak o
        # zaman indirici nesnesini sonlandırıp ana `.mp4.part` dosyasının
        # tutacını kapatabilir (referans `except` içinde canlı kalırsa kapanmaz).
        with YoutubeDL(options) as ydl:
            ydl.download([url])

    try:
        _execute()
    except YoutubeDLError as exc:
        _cleanup_artifacts(download_path, captured_info, cleanup_prefix)
        raise EngineError(_clean_error(exc)) from exc
    except BaseException:
        # İptal (queue_manager progress hook'tan _Cancelled fırlatır) veya
        # başka herhangi bir kesinti: yt-dlp bunu YoutubeDLError'a sarmalamaz,
        # bu yüzden ayrıca yakala. Artıkları temizle ve istisnayı aynen yükselt
        # (queue_manager iptal/hata ayrımını bayrağa göre yapar).
        _cleanup_artifacts(download_path, captured_info, cleanup_prefix)
        raise

    # yt-dlp exception fırlatmasa bile merge çökmüş olabilir — diskte nihai
    # dosyanın gerçekten var olduğunu doğrula. Yoksa .part'lar geride kalır.
    expected = final_filename.get("path")
    if expected and not Path(expected).exists():
        _cleanup_artifacts(download_path, captured_info, cleanup_prefix)
        raise EngineError(
            "İndirme tamamlanamadı — ffmpeg birleştirmesi başarısız olmuş "
            "olabilir. ffmpeg sürümünü kontrol edin."
        )
    if not expected:
        # Hiçbir nihai yol yakalanmadı — artığı temizle. Çağıran (queue_manager)
        # None dönüşünü görüp işi "dosya bulunamadı" ile hata işaretler.
        _cleanup_artifacts(download_path, captured_info, cleanup_prefix)
    # Nihai (merge/convert sonrası) dosya yolunu döndür — queue_manager bunu
    # job.filename olarak kullanır (progress hook'un bildirdiği ara dosyayı değil).
    return expected
