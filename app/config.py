"""Uygulama sabitleri ve platform-bağımsız yardımcılar.

Tüm yollar `pathlib.Path` ile üretilir; sabit yol ayracı kullanılmaz —
böylece kod Windows ve macOS'ta birebir aynı çalışır.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Yerel API paylaşımlı jetonu — Pluck'ın kendi istemcilerini (tarayıcı eklentisi
# + web arayüzü) rastgele diğer yerel eklenti/yazılımdan ayırır. Sabit, repo'da
# gömülü bir paylaşımlı sırdır: fırsatçı localhost-tarayan bir eklenti bu başlığı
# göndermez → 403. Hedefli (repo'yu okuyan) saldırgana karşı değil; onun için
# PLUCK_TOKEN ortam değişkeniyle değiştirin (o durumda extension/pluck-token.js
# ve web/app.js içindeki sabiti de EŞLEŞTİRİN). Bkz. DESIGN.md §16.
_DEFAULT_API_TOKEN = "pluck-local-v1-a3f19c7e"


def api_token() -> str:
    """Beklenen X-Pluck-Token değeri (ortam değişkeniyle geçersiz kılınabilir)."""
    return os.environ.get("PLUCK_TOKEN") or _DEFAULT_API_TOKEN

# SSE anlık görüntü aralığı (saniye).
EVENT_INTERVAL = 0.5

# Cookie için yt-dlp'ye geçilebilecek tüm tarayıcı değerleri (doğrulama kümesi).
SUPPORTED_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "safari")

# web/ statik dosya dizini (app/ ile aynı seviyede).
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Klasör seçme penceresini açan yardımcı script (alt-process olarak çalışır).
FOLDER_PICKER = Path(__file__).resolve().parent / "folder_picker.py"


def default_download_dir() -> Path:
    """Varsayılan indirme klasörü: ~/Downloads (yoksa ev dizini)."""
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


def common_dirs() -> list[str]:
    """Arayüzde hızlı seçim için sık kullanılan, var olan klasörler."""
    home = Path.home()
    candidates = [
        home / "Downloads",
        home / "Desktop",
        home / "Videos",   # Windows
        home / "Movies",   # macOS
        home,
    ]
    return list(dict.fromkeys(str(p) for p in candidates if p.is_dir()))


def available_browsers() -> list[str]:
    """Bu platformda cookie için sunulabilecek tarayıcılar.

    Tek doğruluk kaynağı `SUPPORTED_BROWSERS`'tır; `safari` yalnızca macOS'ta
    sunulur.
    """
    return [
        browser for browser in SUPPORTED_BROWSERS
        if browser != "safari" or sys.platform == "darwin"
    ]
