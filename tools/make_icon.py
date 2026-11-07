"""Draw the app icon (a lime speed-gauge arc on the dark app background) -> assets/trekpad.ico.
Run: python tools/make_icon.py   (needs Pillow)"""
from pathlib import Path

from PIL import Image, ImageDraw

BG, TRACK, ACCENT = (18, 20, 23, 255), (42, 46, 53, 255), (200, 242, 90, 255)
OUT = Path(__file__).resolve().parent.parent / "assets" / "trekpad.ico"


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=BG)
    pad, w = int(size * 0.17), int(size * 0.11)
    box = [pad, pad + int(size * 0.03), size - pad, size - pad + int(size * 0.03)]
    # gauge: 270° track starting bottom-left, value arc to ~60%
    d.arc(box, start=135, end=405, fill=TRACK, width=w)
    d.arc(box, start=135, end=135 + 165, fill=ACCENT, width=w)
    for ang in (135, 300):  # round caps
        import math
        r = (box[2] - box[0]) / 2 - w / 2
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        x, y = cx + r * math.cos(math.radians(ang)), cy + r * math.sin(math.radians(ang))
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=ACCENT)
    c = size / 2
    dot = size * 0.09
    d.ellipse([c - dot, c + size * 0.03 - dot, c + dot, c + size * 0.03 + dot], fill=ACCENT)
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    big = draw()
    big.save(OUT, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    big.resize((256, 256), Image.LANCZOS).save(OUT.with_suffix(".png"))
    print("wrote", OUT)
