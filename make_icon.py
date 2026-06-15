"""
Generate icon.ico (multi-size) and icon.png from a simple monochrome 'O' mark.

Run once before the first build:
    python make_icon.py

build.ps1 calls this automatically if icon.ico is missing.
Requires Pillow (pip install pillow).
"""
from pathlib import Path


def make(out_ico: Path = Path("icon.ico"), out_png: Path = Path("icon.png")):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow not installed; run `pip install pillow` then re-run.")
        return False

    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        img = Image.new("RGBA", (s, s), (10, 10, 10, 255))
        d = ImageDraw.Draw(img)
        # subtle inner ring
        pad = max(2, s // 12)
        ring_w = max(2, s // 12)
        d.ellipse(
            [pad, pad, s - pad - 1, s - pad - 1],
            outline=(245, 245, 245, 255),
            width=ring_w,
        )
        # tiny dot in the middle so it reads as a brand mark, not a zero
        dot_r = max(1, s // 18)
        cx, cy = s // 2, s // 2
        d.ellipse(
            [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
            fill=(245, 245, 245, 255),
        )
        images.append(img)

    # Save .ico with all sizes embedded
    images[0].save(
        out_ico, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    # Save a 256 .png for runtime iconphoto on non-Windows
    images[-1].save(out_png, format="PNG")

    print(f"Wrote {out_ico} (sizes: {sizes})  and  {out_png}")
    return True


if __name__ == "__main__":
    make()
