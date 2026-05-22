"""Yerel klasör seçme penceresi (tkinter).

Ayrı bir alt-process olarak çalıştırılır — böylece tkinter kendi ana
thread'inde çalışır (Windows ve macOS'ta uyumlu). Seçilen yol stdout'a
yazılır; kullanıcı iptal ederse boş çıktı verilir.

    python -m app.folder_picker      (veya doğrudan bu dosya)
"""
from __future__ import annotations

import sys


def pick_directory() -> str:
    """Native klasör seçme penceresini açar; seçilen yolu döndürür."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # pencere öne gelsin
    try:
        path = filedialog.askdirectory(title="İndirme klasörünü seç")
    finally:
        root.destroy()
    return path or ""


if __name__ == "__main__":
    try:
        sys.stdout.write(pick_directory())
    except Exception:  # tkinter yoksa / pencere açılamazsa
        sys.exit(1)
