#!/usr/bin/env python3
"""Pillow steps for /api/erase, run with the ComfyUI venv's python (picgen itself has no Pillow).

    erase_helper.py mark   <image.png> <mask.png> <out_marked.png>          paint the masked area flat magenta; prints "w h" to use
    erase_helper.py merge  <image.png> <mask.png> <edited.png> <out.png> <feather_px>
        paste the edited picture back ONLY inside the (feathered) mask, so every pixel outside the brush stays identical.
"""
import sys
from PIL import Image, ImageFilter

MAGENTA = (255, 0, 220)


def load_mask(path, size):
    m = Image.open(path).convert("L")
    if m.size != size:
        m = m.resize(size, Image.LANCZOS)
    return m.point(lambda v: 255 if v > 127 else 0)


def fit(w, h, long_side=1216):
    s = min(1.0, long_side / max(w, h))
    return max(16, round(w * s / 16) * 16), max(16, round(h * s / 16) * 16)


def mark(image, mask, out):
    im = Image.open(image).convert("RGB")
    m = load_mask(mask, im.size).filter(ImageFilter.MaxFilter(7))   # a little past the brush so no fringe of the object survives
    flat = Image.new("RGB", im.size, MAGENTA)
    im = Image.composite(flat, im, m)
    w, h = fit(*im.size)
    im.resize((w, h), Image.LANCZOS).save(out)
    print(w, h)


def merge(image, mask, edited, out, feather):
    im = Image.open(image).convert("RGB")
    ed = Image.open(edited).convert("RGB").resize(im.size, Image.LANCZOS)
    m = load_mask(mask, im.size).filter(ImageFilter.MaxFilter(3))   # paste back only just past the brush: the model redraws slightly off-register, a wide zone shows double edges
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    Image.composite(ed, im, m).save(out)


if __name__ == "__main__":
    cmd, *a = sys.argv[1:]
    if cmd == "mark":
        mark(*a[:3])
    elif cmd == "merge":
        merge(a[0], a[1], a[2], a[3], float(a[4]))
    else:
        sys.exit("mark|merge")
