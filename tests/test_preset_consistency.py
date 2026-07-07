"""Preset listelerinin tek doğruluk kaynağı `ytdlp_engine.PRESETS`'tir.

Eklenti (content.js, background.js, popup.js) ve web arayüzü (app.js) preset
adlarını ayrı ayrı hardcode eder (JS ↔ Python sınırı runtime paylaşıma engel).
Bu testler drift'i yakalar: bir istemci listesi backend ile ayrışırsa (ör. yeni
bir preset eklenip birinde unutulursa) CI kırılır. Böylece "tek kaynak"
sözleşmesi araçla zorlanmış olur.
"""
import re
from pathlib import Path

from app.ytdlp_engine import PRESETS

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = set(PRESETS)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _block(text: str, start: str, end: str) -> str:
    """`start` işaretinden ilk `end`'e kadar olan metin dilimini döndürür."""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_content_js_presets_match_backend():
    block = _block(_read("extension/content.js"), "const PRESETS = [", "];")
    found = set(re.findall(r'value:\s*"([^"]+)"', block))
    assert found == CANONICAL


def test_background_valid_selections_match_backend():
    block = _block(_read("extension/background.js"),
                   "VALID_SELECTIONS = new Set([", "])")
    found = set(re.findall(r'"([^"]+)"', block))
    assert found == CANONICAL


def _preset_label_keys(rel: str) -> set[str]:
    block = _block(_read(rel), "PRESET_LABELS = {", "};")
    pairs = re.findall(r'(?:"([^"]+)"|(\w+))\s*:', block)
    return {quoted or bare for quoted, bare in pairs}


def test_web_preset_labels_match_backend():
    assert _preset_label_keys("web/app.js") == CANONICAL


def test_popup_preset_labels_match_backend():
    assert _preset_label_keys("extension/popup.js") == CANONICAL
