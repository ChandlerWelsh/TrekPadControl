"""Draw the app icon (a lime speed-gauge arc on the dark app background).
Writes assets/trekpad.ico + .png (Windows) and the iOS app icon (1024 px, opaque, square:
iOS rounds the corners itself and rejects icons with transparency).
Run: python tools/make_icon.py   (needs Pillow)"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

BG, TRACK, ACCENT = (18, 20, 23, 255), (42, 46, 53, 255), (200, 242, 90, 255)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "trekpad.ico"
IOS_ICON = ROOT / "ios" / "App" / "Assets.xcassets" / "AppIcon.appiconset" / "icon-1024.png"


def draw(size: int = 1024, rounded: bool = True, scale: float = 1.0) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0) if rounded else BG)
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=BG)
    inset = size * (1 - scale) / 2
    pad, w = inset + size * scale * 0.17, int(size * scale * 0.11)
    off = size * scale * 0.03
    box = [pad, pad + off, size - pad, size - pad + off]
    # gauge: 270° track starting bottom-left, value arc to ~60%
    d.arc(box, start=135, end=405, fill=TRACK, width=w)
    d.arc(box, start=135, end=135 + 165, fill=ACCENT, width=w)
    r = (box[2] - box[0]) / 2 - w / 2
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for ang in (135, 300):  # round caps
        x, y = cx + r * math.cos(math.radians(ang)), cy + r * math.sin(math.radians(ang))
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=ACCENT)
    dot = size * scale * 0.09
    d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=ACCENT)
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    big = draw()
    big.save(OUT, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    big.resize((256, 256), Image.LANCZOS).save(OUT.with_suffix(".png"))
    print("wrote", OUT)
    IOS_ICON.parent.mkdir(parents=True, exist_ok=True)
    draw(rounded=False, scale=0.86).convert("RGB").save(IOS_ICON)
    print("wrote", IOS_ICON)
