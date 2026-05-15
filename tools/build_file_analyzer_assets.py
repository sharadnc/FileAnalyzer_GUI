"""Rebuild ``file_analyzer.ico`` and branding JPG from ``file_analyzer.png``.

Purpose
-------
Keep ``assets/icons/file_analyzer.ico`` (multi-resolution, Windows-friendly) and
``assets/branding/file_analyzer.jpg`` in sync after updating the master
``assets/icons/file_analyzer.png`` artwork.

Internal Logic
---------------
1. Load ``assets/icons/file_analyzer.png`` as RGBA and normalize to 512x512.
2. Save ``file_analyzer.ico`` via Pillow using the ``sizes=`` list so Windows
   picks the best embedded resolution for title bar and taskbar.
3. Compose a light pastel banner and write ``file_analyzer.jpg``.

Example invocation
--------------------
From the repository root (after updating the PNG)::

    py -3 tools/build_file_analyzer_assets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    """Regenerate ICO and JPG; return 0 on success."""

    root = _repo_root()
    icons = root / "assets" / "icons"
    branding = root / "assets" / "branding"
    png_path = icons / "file_analyzer.png"
    if not png_path.is_file():
        print(f"Missing master PNG: {png_path}", file=sys.stderr)
        return 1

    branding.mkdir(parents=True, exist_ok=True)

    img = Image.open(png_path).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((512, 512), Image.Resampling.LANCZOS)
    img.save(png_path, "PNG")

    im256 = img.resize((256, 256), Image.Resampling.LANCZOS)
    ico_path = icons / "file_analyzer.ico"
    im256.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    bw, bh = 960, 400
    banner = Image.new("RGB", (bw, bh), (238, 246, 255))
    thumb = img.resize((300, 300), Image.Resampling.LANCZOS)
    layer = Image.new("RGB", thumb.size, (238, 246, 255))
    layer.paste(thumb, mask=thumb.split()[3])
    banner.paste(layer, ((bw - 300) // 2, (bh - 300) // 2))
    banner.save(branding / "file_analyzer.jpg", "JPEG", quality=92)

    print(f"Wrote {ico_path.name} ({ico_path.stat().st_size} bytes)")
    print(f"Wrote {branding / 'file_analyzer.jpg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
