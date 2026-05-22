"""Tek komut girişi — uvicorn sunucusunu başlatır ve tarayıcıyı açar.

Windows ve macOS'ta birebir aynı çalışır:

    python run.py
"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser

import uvicorn

from app import config


def _enable_utf8_output() -> None:
    """Konsol çıktısını UTF-8'e ayarlar.

    Windows'ta varsayılan konsol kod sayfası (ör. cp1254) bazı karakterleri
    kodlayamayıp `print`'i çökertebilir; bu her iki platformda da güvenlidir.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # yönlendirilmiş/uyumsuz akış — yok say


def find_free_port(preferred: int, attempts: int = 20) -> int:
    """`preferred` portundan başlayarak boş bir TCP portu bulur.

    Hiçbiri boş değilse `preferred`'a döner (uvicorn anlamlı hata verir).
    """
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex((config.HOST, port)) != 0:
                return port  # bağlanılamıyor → port boş
    return preferred


def main() -> None:
    """Sunucuyu başlatır; hazır olunca varsayılan tarayıcıyı açar."""
    _enable_utf8_output()
    port = find_free_port(config.DEFAULT_PORT)
    url = f"http://{config.HOST}:{port}"
    print(f"\n  Pluck calisiyor  ->  {url}")
    print("  Durdurmak icin bu pencerede Ctrl+C.\n")
    # Sunucu ayağa kalkana kadar kısa gecikmeyle tarayıcıyı aç.
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=config.HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
