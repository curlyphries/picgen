#!/usr/bin/env python3
"""Faraway Magic Erase: a GIMP 3 plug-in. Select the thing you want gone (free select, lasso, or paint a quick mask),
then Filters > Faraway > Magic Erase. The selection is sent to picgen's /api/erase (Qwen-Image-Edit on the picgen host);
the result comes back as a NEW layer above the current one, with only the selected pixels changed.

Install: this file lives in ~/.config/GIMP/3.2/plug-ins/faraway-erase/faraway-erase.py and must be executable.
"""
import io, json, sys, urllib.request, uuid

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, GLib, GObject  # noqa: E402

PICGEN = "http://127.0.0.1:8070/api/erase"


def _png_of_drawable(drawable, x, y, w, h, fmt="R'G'B'A u8"):
    from PIL import Image
    buf = drawable.get_buffer()
    rect = Gegl.Rectangle.new(x, y, w, h)
    data = buf.get(rect, 1.0, fmt, Gegl.AbyssPolicy.CLAMP)
    mode = "RGBA" if fmt.startswith("R'G'B'A") else "L"
    im = Image.frombytes(mode, (w, h), bytes(data))
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue(), im


def _multipart(fields, files):
    bnd = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (fn, data) in files.items():
        body += f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: image/png\r\n\r\n".encode() + data + b"\r\n"
    body += f"--{bnd}--\r\n".encode()
    return body, f"multipart/form-data; boundary={bnd}"


def run(procedure, run_mode, image, drawables, config, data):
    from PIL import Image
    if not drawables:
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error("Pick a layer first."))
    layer = drawables[0]
    if run_mode == Gimp.RunMode.INTERACTIVE:                      # the hint box + OK/Cancel; GIMP only shows a dialog if the plug-in asks
        gi.require_version("GimpUi", "3.0")
        from gi.repository import GimpUi
        GimpUi.init("faraway-magic-erase")
        dialog = GimpUi.ProcedureDialog(procedure=procedure, config=config)
        dialog.fill(None)
        ok = dialog.run()
        dialog.destroy()
        if not ok:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    sel = image.get_selection()
    non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)[:5] if hasattr(Gimp.Selection, "bounds") else (False, 0, 0, 0, 0)
    if not non_empty:
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, GLib.Error("Select the thing to erase first (lasso or free select)."))
    W, H = image.get_width(), image.get_height()
    # send what the user SEES: every visible layer merged (a duplicate, so the real image is untouched)
    dup = image.duplicate()
    try:
        merged = dup.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
        merged.resize_to_image_size()
        img_png, img_im = _png_of_drawable(merged, 0, 0, W, H)
    finally:
        dup.delete()
    if img_im.mode == "RGBA":                                      # transparent areas become white, not black
        from PIL import Image as _I
        flat = _I.new("RGB", img_im.size, (255, 255, 255)); flat.paste(img_im, mask=img_im.split()[3])
        b = io.BytesIO(); flat.save(b, "PNG"); img_png = b.getvalue()
    mask_png, _ = _png_of_drawable(sel, 0, 0, W, H, "Y' u8")
    prompt = config.get_property("prompt") if config else ""
    Gimp.progress_init("Faraway: erasing…")
    body, ctype = _multipart({"prompt": prompt or "", "feather": "3"}, {"image": ("image.png", img_png), "mask": ("mask.png", mask_png)})
    req = urllib.request.Request(PICGEN, data=body, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:300]
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(f"picgen said: {msg}"))
    except Exception as e:
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(f"picgen unreachable: {e}"))
    res = Image.open(io.BytesIO(out)).convert("RGBA")
    if res.size != (W, H):
        res = res.resize((W, H), Image.LANCZOS)
    # keep only the selected pixels on the new layer so it reads as a patch over the original
    _, selim = _png_of_drawable(sel, 0, 0, W, H, "Y' u8")
    alpha = selim.point(lambda v: 255 if v > 0 else 0)
    res.putalpha(alpha)
    image.undo_group_start()
    new = Gimp.Layer.new(image, "Magic Erase", W, H, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
    image.insert_layer(new, None, 0)                              # on top of everything, so it covers all layers below
    buf = new.get_buffer()
    buf.set(Gegl.Rectangle.new(0, 0, W, H), "R'G'B'A u8", res.tobytes())
    new.merge_shadow(True); new.update(0, 0, W, H)
    image.undo_group_end()
    Gimp.displays_flush()
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


class FarawayErase(Gimp.PlugIn):
    def do_set_i18n(self, procname):
        return False

    def do_query_procedures(self):
        return ["faraway-magic-erase"]

    def do_create_procedure(self, name):
        Gegl.init(None)
        p = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, run, None)
        p.set_image_types("RGB*")
        p.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE | Gimp.ProcedureSensitivityMask.DRAWABLES)
        p.set_menu_label("Magic Erase (Faraway)")
        p.add_menu_path("<Image>/Filters/Faraway")
        p.set_documentation("Erase the selection and fill in the background with picgen (Qwen-Image-Edit).", "Result is a new layer; only selected pixels change.", name)
        p.set_attribution("City of Faraway", "picgen", "2026")
        p.add_string_argument("prompt", "Hint (optional)", "What is behind it, e.g. 'wooden floor and the chalkboard'", "", GObject.ParamFlags.READWRITE)
        return p


Gimp.main(FarawayErase.__gtype__, sys.argv)
