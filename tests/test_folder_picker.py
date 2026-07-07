"""folder_picker.py — çapraz platform native klasör seçici testleri.

Gerçek pencere AÇILMAZ: tkinter sahte modüllerle, osascript/zenity ise
`subprocess.run` mock'lanarak taklit edilir. Backend seçim sırası, argüman
üretimi, iptal/hata davranışı ve `__main__` bloğu deterministik olarak test edilir.
"""
import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest

from app import folder_picker


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
class _FakeProc:
    """subprocess.CompletedProcess yerine geçen basit taklit."""

    def __init__(self, returncode: int = 0, stdout: str = ""):
        self.returncode = returncode
        self.stdout = stdout


def _install_fake_tkinter(monkeypatch, *, tk_factory, askdirectory):
    """sys.modules'e sahte `tkinter` ve `tkinter.filedialog` enjekte eder."""
    fake_tk = types.ModuleType("tkinter")
    fake_tk.Tk = tk_factory
    fake_fd = types.ModuleType("tkinter.filedialog")
    fake_fd.askdirectory = askdirectory
    fake_tk.filedialog = fake_fd
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_fd)
    return fake_tk, fake_fd


# --------------------------------------------------------------------------- #
# _try_tkinter
# --------------------------------------------------------------------------- #
def test_tkinter_success_returns_selected_path(monkeypatch):
    """tkinter kuruluysa seçilen yol döner; pencere gizlenip yok edilir."""
    fake_root = MagicMock()
    _install_fake_tkinter(
        monkeypatch,
        tk_factory=MagicMock(return_value=fake_root),
        askdirectory=MagicMock(return_value="/home/user/Downloads"),
    )

    result = folder_picker._try_tkinter()

    assert result == "/home/user/Downloads"
    # Pencere ekranda görünmemeli ve en öne alınıp sonra yok edilmeli.
    fake_root.withdraw.assert_called_once()
    fake_root.attributes.assert_called_once_with("-topmost", True)
    fake_root.destroy.assert_called_once()


def test_tkinter_cancel_returns_empty_string(monkeypatch):
    """Kullanıcı iptal ederse askdirectory boş döner → '' (None DEĞİL)."""
    fake_root = MagicMock()
    _install_fake_tkinter(
        monkeypatch,
        tk_factory=MagicMock(return_value=fake_root),
        askdirectory=MagicMock(return_value=""),
    )

    result = folder_picker._try_tkinter()

    # '' anlamlıdır: backend çalıştı ama seçim yapılmadı; pick_directory dursun.
    assert result == ""
    assert result is not None
    fake_root.destroy.assert_called_once()


def test_tkinter_missing_returns_none(monkeypatch):
    """tkinter kurulu değilse (import hatası) None döner → sonraki backend denenir."""
    # sys.modules[...] = None → `import tkinter` ImportError fırlatır.
    monkeypatch.setitem(sys.modules, "tkinter", None)

    assert folder_picker._try_tkinter() is None


def test_tkinter_tk_construction_raises_returns_none(monkeypatch):
    """Tk() başarısız olursa (ör. görüntü yok) None döner."""
    _install_fake_tkinter(
        monkeypatch,
        tk_factory=MagicMock(side_effect=RuntimeError("no display")),
        askdirectory=MagicMock(),
    )

    assert folder_picker._try_tkinter() is None


def test_tkinter_dialog_raises_still_destroys_and_returns_none(monkeypatch):
    """askdirectory patlarsa: finally ile pencere yok edilir, sonuç None olur."""
    fake_root = MagicMock()
    _install_fake_tkinter(
        monkeypatch,
        tk_factory=MagicMock(return_value=fake_root),
        askdirectory=MagicMock(side_effect=RuntimeError("boom")),
    )

    assert folder_picker._try_tkinter() is None
    # finally bloğu her hâlükârda pencereyi kapatmalı (kaynak sızıntısı olmasın).
    fake_root.destroy.assert_called_once()


# --------------------------------------------------------------------------- #
# _try_osascript  (macOS)
# --------------------------------------------------------------------------- #
def test_osascript_missing_returns_none(monkeypatch):
    """osascript PATH'te yoksa None döner (bu backend atlanır)."""
    monkeypatch.setattr(folder_picker.shutil, "which", lambda name: None)

    assert folder_picker._try_osascript() is None


def test_osascript_success_strips_and_trims_trailing_slash(monkeypatch):
    """Başarılı seçim: stdout kırpılır ve sondaki '/' temizlenir."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/osascript"
    )
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc(returncode=0, stdout="/Users/me/Movies/\n")

    monkeypatch.setattr(folder_picker.subprocess, "run", fake_run)

    result = folder_picker._try_osascript()

    # POSIX path'in sonundaki '/' atılmalı, satır sonu kırpılmalı.
    assert result == "/Users/me/Movies"
    # Doğru komut ve güvenli çağrı seçenekleri kullanılmalı.
    assert captured["cmd"][0] == "osascript"
    assert captured["cmd"][1] == "-e"
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 180


def test_osascript_cancel_nonzero_returns_empty(monkeypatch):
    """Kullanıcı iptali (-128) veya herhangi bir nonzero → '' (stdout yok sayılır)."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        folder_picker.subprocess,
        "run",
        lambda cmd, **kw: _FakeProc(returncode=-128, stdout="yok-sayılmalı"),
    )

    assert folder_picker._try_osascript() == ""


def test_osascript_empty_stdout_returns_empty(monkeypatch):
    """returncode 0 ama seçim boşsa '' döner."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        folder_picker.subprocess,
        "run",
        lambda cmd, **kw: _FakeProc(returncode=0, stdout="   \n"),
    )

    assert folder_picker._try_osascript() == ""


def test_osascript_oserror_returns_none(monkeypatch):
    """subprocess OSError fırlatırsa None döner."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/osascript"
    )

    def boom(cmd, **kw):
        raise OSError("exec failed")

    monkeypatch.setattr(folder_picker.subprocess, "run", boom)

    assert folder_picker._try_osascript() is None


def test_osascript_timeout_returns_none(monkeypatch):
    """subprocess TimeoutExpired fırlatırsa None döner."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/osascript"
    )

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=180)

    monkeypatch.setattr(folder_picker.subprocess, "run", slow)

    assert folder_picker._try_osascript() is None


# --------------------------------------------------------------------------- #
# _try_zenity  (Linux)
# --------------------------------------------------------------------------- #
def test_zenity_missing_returns_none(monkeypatch):
    """zenity PATH'te yoksa None döner."""
    monkeypatch.setattr(folder_picker.shutil, "which", lambda name: None)

    assert folder_picker._try_zenity() is None


def test_zenity_success_strips_but_keeps_trailing_slash(monkeypatch):
    """zenity yolu kırpar ama (osascript'in aksine) sondaki '/' KORUNUR."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/zenity"
    )
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(returncode=0, stdout="/home/user/Downloads/\n")

    monkeypatch.setattr(folder_picker.subprocess, "run", fake_run)

    result = folder_picker._try_zenity()

    # rstrip('/') YOK: zenity çıktısı yalnız satır sonu kırpılır.
    assert result == "/home/user/Downloads/"
    # Doğru zenity argümanları üretilmeli.
    assert captured["cmd"][0] == "zenity"
    assert "--file-selection" in captured["cmd"]
    assert "--directory" in captured["cmd"]


def test_zenity_cancel_nonzero_returns_empty(monkeypatch):
    """Nonzero returncode (iptal) → '' döner."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/zenity"
    )
    monkeypatch.setattr(
        folder_picker.subprocess,
        "run",
        lambda cmd, **kw: _FakeProc(returncode=1, stdout=""),
    )

    assert folder_picker._try_zenity() == ""


def test_zenity_empty_stdout_returns_empty(monkeypatch):
    """returncode 0 ama boş çıktı → '' döner."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/zenity"
    )
    monkeypatch.setattr(
        folder_picker.subprocess,
        "run",
        lambda cmd, **kw: _FakeProc(returncode=0, stdout="\n"),
    )

    assert folder_picker._try_zenity() == ""


def test_zenity_oserror_returns_none(monkeypatch):
    """subprocess OSError fırlatırsa None döner."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/zenity"
    )

    def boom(cmd, **kw):
        raise OSError("exec failed")

    monkeypatch.setattr(folder_picker.subprocess, "run", boom)

    assert folder_picker._try_zenity() is None


def test_zenity_timeout_returns_none(monkeypatch):
    """subprocess TimeoutExpired fırlatırsa None döner."""
    monkeypatch.setattr(
        folder_picker.shutil, "which", lambda name: "/usr/bin/zenity"
    )

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd="zenity", timeout=180)

    monkeypatch.setattr(folder_picker.subprocess, "run", slow)

    assert folder_picker._try_zenity() is None


# --------------------------------------------------------------------------- #
# pick_directory  (backend seçim sırası)
# --------------------------------------------------------------------------- #
def _tracker(monkeypatch, tk, osa, zen):
    """Üç backend'i çağrı sırasını kaydeden sahtelerle değiştirir."""
    calls = []

    def make(name, value):
        def fn():
            calls.append(name)
            return value
        return fn

    monkeypatch.setattr(folder_picker, "_try_tkinter", make("tk", tk))
    monkeypatch.setattr(folder_picker, "_try_osascript", make("osa", osa))
    monkeypatch.setattr(folder_picker, "_try_zenity", make("zen", zen))
    return calls


def test_pick_directory_tkinter_wins_short_circuits(monkeypatch):
    """tkinter bir yol döndürürse diğer backend'ler HİÇ çağrılmaz."""
    calls = _tracker(monkeypatch, tk="/tk/path", osa="/osa", zen="/zen")

    assert folder_picker.pick_directory() == "/tk/path"
    assert calls == ["tk"]


def test_pick_directory_empty_string_counts_as_result(monkeypatch):
    """tkinter None → osascript '' döner; '' None olmadığından sonuç odur.

    Kritik: zenity çağrılmamalı (boş string geçerli bir 'seçim yok' sonucudur).
    """
    calls = _tracker(monkeypatch, tk=None, osa="", zen="/should/not/reach")

    assert folder_picker.pick_directory() == ""
    assert calls == ["tk", "osa"]


def test_pick_directory_falls_through_to_zenity(monkeypatch):
    """İlk iki backend None ise zenity'nin sonucu döner."""
    calls = _tracker(monkeypatch, tk=None, osa=None, zen="/home/user/x")

    assert folder_picker.pick_directory() == "/home/user/x"
    assert calls == ["tk", "osa", "zen"]


def test_pick_directory_all_none_returns_empty(monkeypatch):
    """Tüm backend'ler None → '' döner (kullanıcı elle yazabilir)."""
    calls = _tracker(monkeypatch, tk=None, osa=None, zen=None)

    assert folder_picker.pick_directory() == ""
    assert calls == ["tk", "osa", "zen"]


# --------------------------------------------------------------------------- #
# __main__ bloğu  (runpy ile, gerçek GUI olmadan)
# --------------------------------------------------------------------------- #
def test_main_block_writes_path_to_stdout(monkeypatch, capsys):
    """`python -m app.folder_picker` seçilen yolu stdout'a yazar (satır sonu yok)."""
    import runpy

    fake_root = MagicMock()
    _install_fake_tkinter(
        monkeypatch,
        tk_factory=MagicMock(return_value=fake_root),
        askdirectory=MagicMock(return_value="/picked/dir"),
    )

    runpy.run_module("app.folder_picker", run_name="__main__")

    out = capsys.readouterr().out
    assert out == "/picked/dir"


def test_main_block_handles_exception_and_exits_nonzero(monkeypatch, capsys):
    """pick_directory beklenmedik hata verirse: stderr'e yazıp exit(1) yapar."""
    import runpy

    # tkinter import'u başarısız → _try_tkinter None; sonra osascript denenir.
    monkeypatch.setitem(sys.modules, "tkinter", None)

    # shutil.which (try bloğu DIŞINDA) patlasın → hata pick_directory'den yükselir.
    def which_boom(name):
        raise RuntimeError("which exploded")

    monkeypatch.setattr(folder_picker.shutil, "which", which_boom)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("app.folder_picker", run_name="__main__")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "folder_picker:" in err
