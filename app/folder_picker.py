"""Yerel klasör seçme penceresi — çapraz platform.

Ayrı bir alt-process olarak çalıştırılır. Seçilen yol stdout'a yazılır;
kullanıcı iptal ederse veya hiçbir backend bulunamazsa boş çıktı verilir.

Sıralı denemeler:
1. tkinter (Windows + tkinter kurulu macOS/Linux)
2. osascript (macOS — Homebrew Python tkinter olmadan gelir)
3. zenity (Linux GTK)

    python -m app.folder_picker      (veya doğrudan bu dosya)
"""
from __future__ import annotations

import shutil
import subprocess
import sys


def _try_tkinter() -> str | None:
    """tkinter native picker; tkinter yoksa None."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askdirectory(title="İndirme klasörünü seç")
        finally:
            root.destroy()
        return path or ""
    except Exception:
        return None


def _try_osascript() -> str | None:
    """macOS Finder klasör seçici (AppleScript). osascript yoksa None."""
    if not shutil.which("osascript"):
        return None
    script = 'POSIX path of (choose folder with prompt "İndirme klasörünü seç")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        # Kullanıcı iptal etti (-128) ya da hata; her iki durumda da boş döndür.
        return ""
    return result.stdout.strip().rstrip("/") or ""


def _try_zenity() -> str | None:
    """Linux GTK klasör seçici. zenity yoksa None."""
    if not shutil.which("zenity"):
        return None
    try:
        result = subprocess.run(
            ["zenity", "--file-selection", "--directory",
             "--title=İndirme klasörünü seç"],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return ""
    return result.stdout.strip() or ""


def pick_directory() -> str:
    """Native klasör seçme penceresini açar; seçilen yolu döndürür.

    Tüm backend'ler başarısızsa boş döner (kullanıcı manuel yazabilir).
    """
    for backend in (_try_tkinter, _try_osascript, _try_zenity):
        result = backend()
        if result is not None:
            return result
    return ""


if __name__ == "__main__":
    try:
        sys.stdout.write(pick_directory())
    except Exception as exc:  # son emniyet
        sys.stderr.write(f"folder_picker: {exc}\n")
        sys.exit(1)
