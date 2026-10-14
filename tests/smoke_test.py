"""Prueba de humo: ejecuta cada herramienta contra un GIMP real con el puente activo.

Uso:  uv run python tests/smoke_test.py [carpeta_salida]
"""

import os
import sys
import tempfile
import traceback

from gimp_mcp import server as s

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "gimp-mcp-test")
os.makedirs(out, exist_ok=True)
results = []


def step(label, fn, *a, **kw):
    try:
        r = fn(*a, **kw)
        results.append((label, True, ""))
        short = repr(r)
        print(f"OK   {label}: {short[:160]}")
        return r
    except Exception as e:  # noqa: BLE001
        results.append((label, False, str(e)))
        print(f"FAIL {label}: {e}")
        if os.environ.get("VERBOSE"):
            traceback.print_exc()
        return None


step("status", s.gimp_status)
im = step("new_image", s.gimp_new_image, 800, 600, background="#20304a")
iid = im["id"] if im else None
base = im["layers"][0]["id"] if im else None
step("set_colors", s.gimp_set_colors, foreground="#ffcc00", background="#0044ff", brush_size=12)
grad = step("new_layer", s.gimp_new_layer, name="Degradado", image_id=iid)
step("gradient", s.gimp_gradient, 0, 0, 800, 600, start_color="#ff7700", end_color="#7700ff",
     shape="linear", layer_id=grad and grad["id"])
step("set_layer", s.gimp_set_layer, grad and grad["id"], opacity=60, mode="overlay")
shape = step("new_layer shapes", s.gimp_new_layer, name="Formas", image_id=iid)
step("select rect", s.gimp_select, "rounded_rectangle", x=100, y=100, width=300, height=200, radius=30, image_id=iid)
step("fill", s.gimp_fill, "#22cc88", layer_id=shape and shape["id"])
step("stroke", s.gimp_stroke_selection, 6, "#ffffff", layer_id=shape and shape["id"])
step("select ellipse add", s.gimp_select, "ellipse", x=450, y=250, width=250, height=250, operation="replace", feather=4, image_id=iid)
step("fill ellipse", s.gimp_fill, "foreground", layer_id=shape and shape["id"])
step("modify selection", s.gimp_modify_selection, "grow", 10, image_id=iid)
step("select none", s.gimp_select, "none", image_id=iid)
step("draw", s.gimp_draw, [50, 550, 300, 450, 500, 560, 750, 480], tool="paintbrush", size=10, color="red", layer_id=shape and shape["id"])
step("draw pencil", s.gimp_draw, [60, 60, 200, 60], tool="pencil", size=4, color="white", layer_id=shape and shape["id"])
fonts = step("list fonts", s.gimp_list_resources, "fonts", "Sans", 5)
txt = step("add_text", s.gimp_add_text, "PlayTorre 🏰", x=120, y=420, font=(fonts or ["Sans-serif"])[0], size=56, color="white", image_id=iid)
step("edit_text", s.gimp_edit_text, txt and txt["id"], text="PlayTorre + GIMP", color="#ffee88")
step("filter dropshadow", s.gimp_apply_filter, "gegl:dropshadow", {"x": 6, "y": 6, "radius": 8, "color": "black", "opacity": 0.8}, layer_id=txt and txt["id"])
step("filter blur", s.gimp_apply_filter, "gegl:gaussian-blur", {"std-dev-x": 2.5, "std-dev-y": 2.5}, layer_id=grad and grad["id"])
step("filter nondestructive", s.gimp_apply_filter, "gegl:vignette", {"radius": 1.2}, layer_id=base, non_destructive=True)
step("filter_info list", s.gimp_filter_info, query="blur")
step("filter_info op", s.gimp_filter_info, "gegl:pixelize")
for op, kw in [("brightness_contrast", {"brightness": 0.1, "contrast": 0.2}),
               ("hue_saturation", {"hue": 10, "saturation": 20}),
               ("levels", {"low": 0.05, "high": 0.95, "gamma": 1.1}),
               ("auto_levels", {}), ("color_balance", {"cyan_red": 10}),
               ("curves", {"curve_points": [0, 0, 0.25, 0.2, 0.75, 0.8, 1, 1]}),
               ("exposure", {"exposure": 0.2}), ("color_temperature", {"temperature": 5500}),
               ("shadows_highlights", {"shadows": 20}), ("colorize", {"color": "#3366ff"}),
               ("posterize", {"levels": 8}), ("threshold", {"low": 0.4, "high": 1}),
               ("desaturate", {}), ("invert", {})]:
    tgt = s.gimp_duplicate_layer(base, name="adj-" + op)["id"] if base else None
    step("adjust " + op, s.gimp_adjust, op, layer_id=tgt, **kw)
    if tgt:
        s.gimp_delete_layer(tgt)
dup = step("duplicate_layer", s.gimp_duplicate_layer, shape and shape["id"], name="Formas copia")
step("transform", s.gimp_transform_layer, dup and dup["id"], scale_percent=50, rotate_degrees=15, flip="horizontal", move_x=500, move_y=40)
step("layer_order", s.gimp_layer_order, dup and dup["id"], "bottom")
step("layer_order pos", s.gimp_layer_order, dup and dup["id"], "position", position=1)
step("mask add", s.gimp_layer_mask, dup and dup["id"], "add", "white")
step("mask apply", s.gimp_layer_mask, dup and dup["id"], "apply")
step("select from_alpha", s.gimp_select, "from_alpha", layer_id=shape and shape["id"])
step("select by color", s.gimp_select_by_color, color="#22cc88", threshold=20, layer_id=shape and shape["id"])
step("magic wand", s.gimp_select_by_color, x=200, y=200, contiguous=True, layer_id=shape and shape["id"])
step("select polygon", s.gimp_select, "polygon", points=[10, 10, 100, 10, 50, 90], image_id=iid)
step("select invert", s.gimp_select, "invert", image_id=iid)
step("select none2", s.gimp_select, "none", image_id=iid)
step("pdb_search", s.gimp_pdb_search, "plug-in-", 5)
step("pdb_info", s.gimp_pdb_info, "gimp-image-autocrop")
step("pdb_call", s.gimp_pdb_call, "gimp-image-get-name", image_id=iid)
step("run_python", s.gimp_run_python, "result = len(Gimp.get_images())")
step("image_info", s.gimp_image_info, iid)
prev = step("preview", s.gimp_preview, iid, max_size=400)
if prev:
    with open(os.path.join(out, "preview.png"), "wb") as f:
        f.write(prev.data)
step("preview layer", s.gimp_preview, iid, max_size=200, layer_id=txt and txt["id"])
step("save xcf", s.gimp_save_image, iid, os.path.join(out, "test.xcf"))
step("export png", s.gimp_export_image, os.path.join(out, "test.png"), iid)
step("export jpg", s.gimp_export_image, os.path.join(out, "test.jpg"), iid, quality=85)
step("export webp", s.gimp_export_image, os.path.join(out, "test.webp"), iid, quality=80)
step("export_load layer", s.gimp_add_layer_from_file, os.path.join(out, "test.png"), image_id=iid, width=200, name="Miniatura")
step("merge_down", s.gimp_merge_layers, "merge_down", layer_id=txt and txt["id"])
dup_im = step("duplicate_image", s.gimp_duplicate_image, iid)
did = dup_im and dup_im["id"]
step("scale_image", s.gimp_scale_image, width=400, image_id=did)
step("resize_canvas", s.gimp_resize_canvas, 500, 400, anchor="center", fill="#000000", image_id=did)
step("crop", s.gimp_crop, 10, 10, 300, 200, image_id=did)
step("rotate/flip", s.gimp_rotate_flip_image, 90, "vertical", image_id=did)
step("flatten", s.gimp_merge_layers, "flatten", image_id=did)
step("convert gray", s.gimp_convert_mode, "grayscale", image_id=did)
step("convert indexed", s.gimp_convert_mode, "indexed", colors=16, image_id=did)
def expect_refused():
    try:
        s.gimp_close_image(did)
    except Exception as e:  # noqa: BLE001
        return f"rechazado como se esperaba: {e}"
    raise AssertionError("cerró una imagen con cambios sin guardar")


step("close dirty refused", expect_refused)
step("close discard", s.gimp_close_image, did, discard_changes=True)
opened = step("open_image", s.gimp_open_image, os.path.join(out, "test.png"))
step("autocrop", s.gimp_crop, autocrop=True, image_id=opened and opened["id"])
step("close opened", s.gimp_close_image, opened and opened["id"], discard_changes=True)
step("list_images", s.gimp_list_images)

fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} OK")
for n, _, e in fails:
    print(f" - {n}: {e}")
sys.exit(1 if fails else 0)
