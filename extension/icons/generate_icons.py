"""Eklenti ikonlarını üretir — yeşil yuvarlatılmış kare + indirme oku.

Yalnızca ikonları yeniden üretmek için gereklidir (PNG'ler depoda mevcut).
Çalıştır:

    pip install pillow
    python extension/icons/generate_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (74, 222, 128, 255)   # #4ade80
INK = (4, 33, 15, 255)         # koyu yeşil
OUT_DIR = Path(__file__).resolve().parent


def make_icon(size: int) -> Image.Image:
    """`size` x `size` boyutunda tek bir ikon üretir."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, round(size * 0.22))
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=ACCENT)
    cx = size / 2
    stem_w = size * 0.16
    draw.rectangle(
        [cx - stem_w / 2, size * 0.24, cx + stem_w / 2, size * 0.56], fill=INK
    )
    draw.polygon(
        [
            (cx - size * 0.24, size * 0.48),
            (cx + size * 0.24, size * 0.48),
            (cx, size * 0.80),
        ],
        fill=INK,
    )
    return img


def main() -> None:
    for size in (16, 48, 128):
        path = OUT_DIR / f"icon{size}.png"
        make_icon(size).save(path)
        print(f"yazildi: {path}")


if __name__ == "__main__":
    main()
