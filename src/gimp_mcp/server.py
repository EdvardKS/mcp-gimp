"""Servidor MCP para controlar GIMP 3 con detalle."""

from __future__ import annotations

import os
import tempfile
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.types import ToolAnnotations

from .bridge import bridge

mcp = MCPServer(
    "gimp",
    instructions=(
        "Control completo de GIMP 3. Flujo típico: gimp_open_image o gimp_new_image -> "
        "editar (capas, selecciones, pintura, texto, ajustes, filtros GEGL) -> gimp_preview "
        "para ver el resultado -> gimp_export_image. Los ids de imagen y capa salen de "
        "gimp_list_images / gimp_image_info. Si no se indica image_id se usa la imagen más "
        "reciente; si no se indica layer_id, la capa seleccionada. Los colores aceptan "
        "'#rrggbb', nombres CSS ('red') o [r,g,b(,a)] 0-255. Para lo que no cubran las "
        "herramientas: gimp_apply_filter (cualquier operación GEGL), gimp_pdb_call "
        "(cualquier procedimiento de GIMP) o gimp_run_python."
    ),
)


# Pistas para el cliente MCP: lectura, edición normal (deshacible en GIMP) o potencialmente destructiva
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DANGER = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


def _run(code: str, **params: Any) -> Any:
    resp = bridge.run(code, params)
    out = resp.get("result")
    if resp.get("stdout"):
        if out is None:
            return resp["stdout"].strip()
        return {"result": out, "stdout": resp["stdout"].strip()}
    return out


def _tmp_png() -> str:
    d = os.path.join(tempfile.gettempdir(), "gimp-mcp")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"preview-{os.getpid()}-{os.urandom(4).hex()}.png")


# ---------------------------------------------------------------- estado / imágenes

@mcp.tool(annotations=READ)
def gimp_status() -> dict:
    """Comprueba la conexión con GIMP (lo arranca si hace falta) y lista las imágenes abiertas."""
    ping = bridge.ping()
    images = _run("result = [_image_info(i, layers=False) for i in Gimp.get_images()]")
    return {"gimp_version": ping["result"]["gimp"], "images": images}


@mcp.tool(annotations=READ)
def gimp_list_images() -> list:
    """Lista las imágenes abiertas en GIMP (id, nombre, archivo, tamaño)."""
    return _run("result = [_image_info(i, layers=False) for i in Gimp.get_images()]")


@mcp.tool(annotations=READ)
def gimp_image_info(image_id: int | None = None) -> dict:
    """Detalle de una imagen: tamaño, modo, resolución, selección y árbol de capas con ids."""
    return _run("result = _image_info(_img(P['image_id']))", image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_new_image(width: int, height: int, background: str = "white",
                   mode: Literal["rgb", "grayscale"] = "rgb", name: str = "Capa base") -> dict:
    """Crea una imagen nueva. background: color, 'transparent', 'foreground' o 'background'."""
    return _run("""
base = Gimp.ImageBaseType.RGB if P['mode'] == 'rgb' else Gimp.ImageBaseType.GRAY
im = Gimp.Image.new(P['width'], P['height'], base)
transparent = P['background'] == 'transparent'
if P['mode'] == 'rgb':
    t = Gimp.ImageType.RGBA_IMAGE if transparent else Gimp.ImageType.RGB_IMAGE
else:
    t = Gimp.ImageType.GRAYA_IMAGE if transparent else Gimp.ImageType.GRAY_IMAGE
l = Gimp.Layer.new(im, P['name'], P['width'], P['height'], t, 100.0, Gimp.LayerMode.NORMAL)
im.insert_layer(l, None, 0)
bg = P['background']
if bg == 'transparent':
    l.edit_fill(Gimp.FillType.TRANSPARENT)
elif bg == 'foreground':
    l.edit_fill(Gimp.FillType.FOREGROUND)
elif bg == 'background':
    l.edit_fill(Gimp.FillType.BACKGROUND)
else:
    Gimp.context_push()
    Gimp.context_set_foreground(_color(bg))
    l.edit_fill(Gimp.FillType.FOREGROUND)
    Gimp.context_pop()
im.clean_all()
_show(im)
result = _image_info(im)
""", width=width, height=height, background=background, mode=mode, name=name)


@mcp.tool(annotations=EDIT)
def gimp_open_image(path: str) -> dict:
    """Abre un archivo de imagen (png, jpg, xcf, psd, webp, tif, pdf...) y devuelve su info."""
    return _run("""
im = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, _file(P['path']))
_show(im)
result = _image_info(im)
""", path=path)


@mcp.tool(annotations=DANGER)
def gimp_save_image(image_id: int | None = None, path: str | None = None) -> str:
    """Guarda en formato XCF de GIMP (con capas). Sin path, guarda sobre su archivo .xcf actual."""
    return _run("""
im = _img(P['image_id'])
path = P['path']
if not path:
    f = im.get_xcf_file() if hasattr(im, 'get_xcf_file') else im.get_file()
    if f is None or not f.get_path().lower().endswith('.xcf'):
        raise ValueError('La imagen no tiene archivo .xcf; indica path')
    path = f.get_path()
if not path.lower().endswith('.xcf'):
    path += '.xcf'
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, im, _file(path), None)
im.set_file(_file(path))
im.clean_all()
result = os.path.abspath(path)
""", image_id=image_id, path=path)


@mcp.tool(annotations=DANGER)
def gimp_export_image(path: str, image_id: int | None = None, quality: int | None = None,
                      flatten: bool = False) -> str:
    """Exporta la imagen al formato que indique la extensión (png, jpg, webp, gif, tif, bmp, pdf...).

    quality (1-100) aplica a jpg/webp. flatten=True une las capas antes de exportar
    (sobre una copia, la imagen original no cambia)."""
    return _run("""
im = _img(P['image_id'])
path = os.path.abspath(os.path.expanduser(P['path']))
os.makedirs(os.path.dirname(path), exist_ok=True)
ext = os.path.splitext(path)[1].lower()
work = im.duplicate()
try:
    if P['flatten'] or ext in ('.jpg', '.jpeg', '.bmp'):
        work.flatten()
    else:
        work.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
    q = P['quality']
    if q is not None and ext in ('.jpg', '.jpeg'):
        _pdb_run('file-jpeg-export', run_mode=Gimp.RunMode.NONINTERACTIVE, image=work,
                 file=_file(path), quality=max(0.0, min(1.0, q / 100.0)))
    elif q is not None and ext == '.webp':
        _pdb_run('file-webp-export', run_mode=Gimp.RunMode.NONINTERACTIVE, image=work,
                 file=_file(path), quality=float(q))
    else:
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, work, _file(path), None)
finally:
    work.delete()
result = path
""", image_id=image_id, path=path, quality=quality, flatten=flatten)


@mcp.tool(annotations=DANGER)
def gimp_close_image(image_id: int | None = None, discard_changes: bool = False) -> str:
    """Cierra una imagen. Por seguridad falla si tiene cambios sin guardar, salvo discard_changes=True."""
    return _run("""
im = _img(P['image_id'])
if im.is_dirty() and not P['discard_changes']:
    raise ValueError('La imagen tiene cambios sin guardar (usa discard_changes=True para descartarlos)')
name = im.get_name()
for d in Gimp.Display.list() if hasattr(Gimp.Display, 'list') else []:
    if d.get_image() == im:
        d.delete()
if im.is_valid():
    im.delete()
result = 'cerrada: ' + name
""", image_id=image_id, discard_changes=discard_changes)


@mcp.tool(annotations=EDIT)
def gimp_duplicate_image(image_id: int | None = None) -> dict:
    """Duplica una imagen completa (con capas) en una imagen nueva."""
    return _run("""
dup = _img(P['image_id']).duplicate()
_show(dup)
result = _image_info(dup)
""", image_id=image_id)


@mcp.tool(annotations=READ)
def gimp_preview(image_id: int | None = None, max_size: int = 1024,
                 layer_id: int | None = None) -> Image:
    """Devuelve una vista previa PNG de la imagen (o de una sola capa) para VER el resultado.

    Úsala tras cada cambio importante para comprobar que ha quedado bien."""
    path = _tmp_png()
    _run("""
im = _img(P['image_id'])
work = im.duplicate()
try:
    if P['layer_id'] is not None:
        src = _item(P['layer_id'])
        keep_name = src.get_name()
        for l in work.get_layers():
            l.set_visible(False)
        target = [l for l in work.get_layers() if l.get_name() == keep_name]
        if target:
            target[0].set_visible(True)
    work.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
    w, h = work.get_width(), work.get_height()
    m = P['max_size']
    if max(w, h) > m:
        s = m / float(max(w, h))
        work.scale(max(1, int(w * s)), max(1, int(h * s)))
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, work, _file(P['out']), None)
finally:
    work.delete()
""", image_id=image_id, max_size=max_size, layer_id=layer_id, out=path)
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    return Image(data=data, format="png")


# ---------------------------------------------------------------- imagen completa

@mcp.tool(annotations=EDIT)
def gimp_scale_image(width: int | None = None, height: int | None = None,
                     percent: float | None = None, image_id: int | None = None,
                     interpolation: Literal["none", "linear", "cubic", "nohalo", "lohalo"] = "cubic") -> dict:
    """Escala la imagen entera. Indica width y/o height (mantiene proporción si falta uno) o percent."""
    return _run("""
im = _img(P['image_id'])
w0, h0 = im.get_width(), im.get_height()
if P['percent']:
    w, h = round(w0 * P['percent'] / 100), round(h0 * P['percent'] / 100)
else:
    w, h = P['width'], P['height']
    if w and not h: h = round(h0 * w / w0)
    if h and not w: w = round(w0 * h / h0)
if not w or not h:
    raise ValueError('Indica width, height o percent')
Gimp.context_push()
Gimp.context_set_interpolation(_enum(Gimp.InterpolationType, P['interpolation']))
im.scale(int(w), int(h))
Gimp.context_pop()
result = _image_info(im, layers=False)
""", image_id=image_id, width=width, height=height, percent=percent, interpolation=interpolation)


@mcp.tool(annotations=EDIT)
def gimp_resize_canvas(width: int, height: int, anchor: Literal[
        "center", "top-left", "top", "top-right", "left", "right",
        "bottom-left", "bottom", "bottom-right"] = "center",
        fill: str | None = None, image_id: int | None = None) -> dict:
    """Cambia el tamaño del lienzo sin escalar el contenido. fill: color para rellenar el espacio nuevo
    (si no, queda transparente)."""
    return _run("""
im = _img(P['image_id'])
w0, h0 = im.get_width(), im.get_height()
a = P['anchor']
ox = 0 if 'left' in a else (P['width'] - w0 if 'right' in a else (P['width'] - w0) // 2)
oy = 0 if a.startswith('top') else (P['height'] - h0 if a.startswith('bottom') else (P['height'] - h0) // 2)
with _undo(im):
    im.resize(P['width'], P['height'], ox, oy)
    for l in im.get_layers():
        if not l.has_alpha():
            l.add_alpha()
        l.resize_to_image_size()
    if P['fill']:
        l = Gimp.Layer.new(im, 'Fondo', P['width'], P['height'], Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
        im.insert_layer(l, None, len(im.get_layers()))
        Gimp.context_push(); Gimp.context_set_foreground(_color(P['fill']))
        l.edit_fill(Gimp.FillType.FOREGROUND); Gimp.context_pop()
result = _image_info(im, layers=False)
""", image_id=image_id, width=width, height=height, anchor=anchor, fill=fill)


@mcp.tool(annotations=EDIT)
def gimp_crop(x: int | None = None, y: int | None = None, width: int | None = None,
              height: int | None = None, to_selection: bool = False, autocrop: bool = False,
              image_id: int | None = None) -> dict:
    """Recorta la imagen: a un rectángulo (x,y,width,height), a la selección actual
    (to_selection) o quitando bordes uniformes (autocrop)."""
    return _run("""
im = _img(P['image_id'])
with _undo(im):
    if P['autocrop']:
        _pdb_run('gimp-image-autocrop', image=im, drawable=_drawable(im))
    elif P['to_selection']:
        r = Gimp.Selection.bounds(im)
        ne, x1, y1, x2, y2 = r[-5:]
        if not ne:
            raise ValueError('No hay selección')
        im.crop(x2 - x1, y2 - y1, x1, y1)
    else:
        im.crop(P['width'], P['height'], P['x'] or 0, P['y'] or 0)
result = _image_info(im, layers=False)
""", image_id=image_id, x=x, y=y, width=width, height=height,
                to_selection=to_selection, autocrop=autocrop)


@mcp.tool(annotations=EDIT)
def gimp_rotate_flip_image(rotate: Literal[0, 90, 180, 270] = 0,
                           flip: Literal["none", "horizontal", "vertical"] = "none",
                           image_id: int | None = None) -> dict:
    """Gira la imagen entera 90/180/270 grados (sentido horario) y/o la voltea."""
    return _run("""
im = _img(P['image_id'])
with _undo(im):
    rot = {90: Gimp.RotationType.DEGREES90, 180: Gimp.RotationType.DEGREES180,
           270: Gimp.RotationType.DEGREES270}.get(P['rotate'])
    if rot is not None:
        im.rotate(rot)
    if P['flip'] != 'none':
        im.flip(Gimp.OrientationType.HORIZONTAL if P['flip'] == 'horizontal' else Gimp.OrientationType.VERTICAL)
result = _image_info(im, layers=False)
""", image_id=image_id, rotate=rotate, flip=flip)


@mcp.tool(annotations=EDIT)
def gimp_convert_mode(mode: Literal["rgb", "grayscale", "indexed"], colors: int = 256,
                      image_id: int | None = None) -> dict:
    """Convierte el modo de color de la imagen. Para indexed, colors = nº de colores de la paleta."""
    return _run("""
im = _img(P['image_id'])
m = P['mode']
if m == 'rgb':
    im.convert_rgb()
elif m == 'grayscale':
    im.convert_grayscale()
else:
    im.convert_indexed(Gimp.ConvertDitherType.NONE, Gimp.ConvertPaletteType.GENERATE,
                       P['colors'], False, False, '')
result = _image_info(im, layers=False)
""", image_id=image_id, mode=mode, colors=colors)


# ---------------------------------------------------------------- capas

@mcp.tool(annotations=EDIT)
def gimp_new_layer(name: str = "Capa", fill: str = "transparent", image_id: int | None = None,
                   width: int | None = None, height: int | None = None, x: int = 0, y: int = 0,
                   opacity: float = 100.0, mode: str = "normal", position: int = 0,
                   group: bool = False, parent_id: int | None = None) -> dict:
    """Crea una capa (o un grupo de capas si group=True). fill: 'transparent' o un color.
    position 0 = arriba del todo. parent_id: grupo donde meterla."""
    return _run("""
im = _img(P['image_id'])
parent = _item(P['parent_id']) if P['parent_id'] is not None else None
with _undo(im):
    if P['group']:
        l = Gimp.GroupLayer.new(im, P['name'])
    else:
        l = Gimp.Layer.new(im, P['name'], P['width'] or im.get_width(), P['height'] or im.get_height(),
                           Gimp.ImageType.RGBA_IMAGE if im.get_base_type() == Gimp.ImageBaseType.RGB else Gimp.ImageType.GRAYA_IMAGE,
                           P['opacity'], _enum(Gimp.LayerMode, P['mode']))
    im.insert_layer(l, parent, P['position'])
    if not P['group']:
        l.set_offsets(P['x'], P['y'])
        if P['fill'] == 'transparent':
            l.edit_fill(Gimp.FillType.TRANSPARENT)
        else:
            Gimp.context_push(); Gimp.context_set_foreground(_color(P['fill']))
            l.edit_fill(Gimp.FillType.FOREGROUND); Gimp.context_pop()
    im.set_selected_layers([l])
result = _layer_info(l)
""", image_id=image_id, name=name, fill=fill, width=width, height=height, x=x, y=y,
                opacity=opacity, mode=mode, position=position, group=group, parent_id=parent_id)


@mcp.tool(annotations=EDIT)
def gimp_add_layer_from_file(path: str, image_id: int | None = None, x: int | None = None,
                             y: int | None = None, name: str | None = None,
                             width: int | None = None, height: int | None = None,
                             position: int = 0) -> dict:
    """Inserta otra imagen (logo, foto...) como capa nueva. Sin x/y queda centrada.
    width/height opcionales para escalarla (si das uno, mantiene proporción)."""
    return _run("""
im = _img(P['image_id'])
with _undo(im):
    l = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, im, _file(P['path']))
    im.insert_layer(l, None, P['position'])
    if P['name']:
        l.set_name(P['name'])
    w0, h0 = l.get_width(), l.get_height()
    w, h = P['width'], P['height']
    if w and not h: h = round(h0 * w / w0)
    if h and not w: w = round(w0 * h / h0)
    if w and h:
        l.scale(int(w), int(h), False)
    x = P['x'] if P['x'] is not None else (im.get_width() - l.get_width()) // 2
    y = P['y'] if P['y'] is not None else (im.get_height() - l.get_height()) // 2
    l.set_offsets(x, y)
    im.set_selected_layers([l])
result = _layer_info(l)
""", image_id=image_id, path=path, x=x, y=y, name=name, width=width, height=height, position=position)


@mcp.tool(annotations=EDIT)
def gimp_set_layer(layer_id: int, name: str | None = None, visible: bool | None = None,
                   opacity: float | None = None, mode: str | None = None,
                   x: int | None = None, y: int | None = None, lock_alpha: bool | None = None,
                   select: bool | None = None) -> dict:
    """Cambia propiedades de una capa: nombre, visibilidad, opacidad (0-100), modo de fusión
    (normal, multiply, screen, overlay, soft-light, hard-light, difference, addition, subtract,
    darken-only, lighten-only, hue, saturation, color, value...), posición x/y, bloqueo de alfa,
    o la marca como capa activa (select=True)."""
    return _run("""
l = _item(P['layer_id'])
im = l.get_image()
with _undo(im):
    if P['name'] is not None: l.set_name(P['name'])
    if P['visible'] is not None: l.set_visible(P['visible'])
    if P['opacity'] is not None: l.set_opacity(float(P['opacity']))
    if P['mode'] is not None: l.set_mode(_enum(Gimp.LayerMode, P['mode']))
    if P['x'] is not None or P['y'] is not None:
        x0, y0 = _offsets(l)
        l.set_offsets(P['x'] if P['x'] is not None else x0, P['y'] if P['y'] is not None else y0)
    if P['lock_alpha'] is not None: l.set_lock_alpha(P['lock_alpha'])
    if P['select']: im.set_selected_layers([l])
result = _layer_info(l)
""", layer_id=layer_id, name=name, visible=visible, opacity=opacity, mode=mode, x=x, y=y,
                lock_alpha=lock_alpha, select=select)


@mcp.tool(annotations=EDIT)
def gimp_layer_order(layer_id: int, action: Literal["raise", "lower", "top", "bottom", "position"],
                     position: int = 0) -> dict:
    """Mueve una capa en la pila: raise/lower (un paso), top/bottom, o position (0 = arriba)."""
    return _run("""
l = _item(P['layer_id'])
im = l.get_image()
a = P['action']
if a == 'raise': im.raise_item(l)
elif a == 'lower': im.lower_item(l)
elif a == 'top': im.raise_item_to_top(l)
elif a == 'bottom': im.lower_item_to_bottom(l)
else: im.reorder_item(l, l.get_parent(), P['position'])
result = [x['name'] for x in _image_info(im)['layers']]
""", layer_id=layer_id, action=action, position=position)


@mcp.tool(annotations=EDIT)
def gimp_duplicate_layer(layer_id: int, name: str | None = None) -> dict:
    """Duplica una capa (queda justo encima de la original)."""
    return _run("""
l = _item(P['layer_id'])
im = l.get_image()
c = l.copy()
im.insert_layer(c, l.get_parent(), im.get_item_position(l))
if P['name']: c.set_name(P['name'])
im.set_selected_layers([c])
result = _layer_info(c)
""", layer_id=layer_id, name=name)


@mcp.tool(annotations=DANGER)
def gimp_delete_layer(layer_id: int) -> str:
    """Elimina una capa."""
    return _run("""
l = _item(P['layer_id'])
n = l.get_name()
l.get_image().remove_layer(l)
result = 'eliminada: ' + n
""", layer_id=layer_id)


@mcp.tool(annotations=DANGER)
def gimp_merge_layers(action: Literal["merge_down", "merge_visible", "flatten"],
                      layer_id: int | None = None, image_id: int | None = None) -> dict:
    """Combina capas: merge_down (la capa con la de debajo), merge_visible (todas las visibles)
    o flatten (todo en una capa sin transparencia)."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
with _undo(im):
    if P['action'] == 'merge_down':
        l = im.merge_down(_drawable(im, P['layer_id']), Gimp.MergeType.EXPAND_AS_NECESSARY)
    elif P['action'] == 'merge_visible':
        l = im.merge_visible_layers(Gimp.MergeType.EXPAND_AS_NECESSARY)
    else:
        l = im.flatten()
result = _layer_info(l)
""", action=action, layer_id=layer_id, image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_transform_layer(layer_id: int | None = None, image_id: int | None = None,
                         scale_width: int | None = None, scale_height: int | None = None,
                         scale_percent: float | None = None, rotate_degrees: float | None = None,
                         flip: Literal["none", "horizontal", "vertical"] = "none",
                         move_x: int | None = None, move_y: int | None = None,
                         layer_to_image_size: bool = False) -> dict:
    """Transforma una capa: escalar (px o %), rotar (grados, sentido horario, sobre su centro),
    voltear, mover a x/y absolutos, o ajustar el tamaño del borde de la capa al de la imagen."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
l = _drawable(im, P['layer_id'])
with _undo(im):
    w0, h0 = l.get_width(), l.get_height()
    w, h = P['scale_width'], P['scale_height']
    if P['scale_percent']:
        w, h = round(w0 * P['scale_percent'] / 100), round(h0 * P['scale_percent'] / 100)
    if w and not h: h = round(h0 * w / w0)
    if h and not w: w = round(w0 * h / h0)
    if w and h:
        l.scale(int(w), int(h), True)
    if P['rotate_degrees']:
        l = l.transform_rotate(_math.radians(P['rotate_degrees']), True, 0, 0)
    if P['flip'] != 'none':
        l = l.transform_flip_simple(Gimp.OrientationType.HORIZONTAL if P['flip'] == 'horizontal'
                                    else Gimp.OrientationType.VERTICAL, True, 0)
    if P['move_x'] is not None or P['move_y'] is not None:
        x0, y0 = _offsets(l)
        l.set_offsets(P['move_x'] if P['move_x'] is not None else x0,
                      P['move_y'] if P['move_y'] is not None else y0)
    if P['layer_to_image_size']:
        l.resize_to_image_size()
result = _layer_info(l)
""", layer_id=layer_id, image_id=image_id, scale_width=scale_width, scale_height=scale_height,
                scale_percent=scale_percent, rotate_degrees=rotate_degrees, flip=flip,
                move_x=move_x, move_y=move_y, layer_to_image_size=layer_to_image_size)


@mcp.tool(annotations=EDIT)
def gimp_layer_mask(layer_id: int, action: Literal["add", "apply", "remove"] = "add",
                    mask_type: Literal["white", "black", "alpha", "selection", "copy", "grayscale"] = "white") -> dict:
    """Máscaras de capa: add (white=todo visible, black=todo oculto, selection=desde la selección,
    alpha, copy, grayscale), apply (aplica y elimina la máscara) o remove (la descarta)."""
    return _run("""
l = _item(P['layer_id'])
types = {'white': Gimp.AddMaskType.WHITE, 'black': Gimp.AddMaskType.BLACK,
         'alpha': Gimp.AddMaskType.ALPHA, 'selection': Gimp.AddMaskType.SELECTION,
         'copy': Gimp.AddMaskType.COPY, 'grayscale': Gimp.AddMaskType.COPY}
if P['action'] == 'add':
    if not l.has_alpha(): l.add_alpha()
    l.add_mask(l.create_mask(types[P['mask_type']]))
else:
    l.remove_mask(Gimp.MaskApplyMode.APPLY if P['action'] == 'apply' else Gimp.MaskApplyMode.DISCARD)
result = _layer_info(l)
""", layer_id=layer_id, action=action, mask_type=mask_type)


# ---------------------------------------------------------------- selección

@mcp.tool(annotations=EDIT)
def gimp_select(shape: Literal["rectangle", "ellipse", "rounded_rectangle", "polygon", "all", "none",
                               "invert", "from_alpha"],
                x: float = 0, y: float = 0, width: float = 0, height: float = 0,
                radius: float = 20, points: list[float] | None = None,
                operation: Literal["replace", "add", "subtract", "intersect"] = "replace",
                feather: float = 0, layer_id: int | None = None, image_id: int | None = None) -> dict:
    """Crea o modifica la selección. rectangle/ellipse/rounded_rectangle usan x,y,width,height
    (radius para las esquinas); polygon usa points=[x1,y1,x2,y2,...]; from_alpha selecciona lo
    opaco de layer_id. operation combina con la selección previa. feather difumina el borde (px)."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
op = _enum(Gimp.ChannelOps, P['operation'])
s = P['shape']
with _undo(im):
    if P['feather']:
        Gimp.context_set_feather(True)
        Gimp.context_set_feather_radius(P['feather'], P['feather'])
    else:
        Gimp.context_set_feather(False)
    if s == 'rectangle':
        im.select_rectangle(op, P['x'], P['y'], P['width'], P['height'])
    elif s == 'ellipse':
        im.select_ellipse(op, P['x'], P['y'], P['width'], P['height'])
    elif s == 'rounded_rectangle':
        im.select_round_rectangle(op, P['x'], P['y'], P['width'], P['height'], P['radius'], P['radius'])
    elif s == 'polygon':
        im.select_polygon(op, P['points'])
    elif s == 'all':
        Gimp.Selection.all(im)
    elif s == 'none':
        Gimp.Selection.none(im)
    elif s == 'invert':
        Gimp.Selection.invert(im)
    elif s == 'from_alpha':
        im.select_item(op, _drawable(im, P['layer_id']))
    Gimp.context_set_feather(False)
result = _image_info(im, layers=False)['selection']
""", shape=shape, x=x, y=y, width=width, height=height, radius=radius, points=points,
                operation=operation, feather=feather, layer_id=layer_id, image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_select_by_color(color: str | None = None, x: float | None = None, y: float | None = None,
                         threshold: float = 15, contiguous: bool = False,
                         operation: Literal["replace", "add", "subtract", "intersect"] = "replace",
                         sample_merged: bool = False, layer_id: int | None = None,
                         image_id: int | None = None) -> dict:
    """Selecciona por color. Con color: todos los píxeles de ese color. Con x,y: la varita
    mágica (contiguous=True) o el color de ese punto en toda la imagen. threshold 0-255."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
d = _drawable(im, P['layer_id'])
op = _enum(Gimp.ChannelOps, P['operation'])
Gimp.context_push()
Gimp.context_set_sample_threshold(P['threshold'] / 255.0)
Gimp.context_set_sample_merged(P['sample_merged'])
if P['color'] is not None:
    im.select_color(op, d, _color(P['color']))
elif P['contiguous']:
    im.select_contiguous_color(op, d, P['x'], P['y'])
else:
    col = d.get_pixel(int(P['x']) - _offsets(d)[0], int(P['y']) - _offsets(d)[1])
    im.select_color(op, d, col)
Gimp.context_pop()
result = _image_info(im, layers=False)['selection']
""", color=color, x=x, y=y, threshold=threshold, contiguous=contiguous, operation=operation,
                sample_merged=sample_merged, layer_id=layer_id, image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_modify_selection(action: Literal["grow", "shrink", "feather", "border", "sharpen"],
                          amount: float = 5, image_id: int | None = None) -> dict:
    """Modifica la selección actual: grow/shrink/border (px), feather (radio px), sharpen (quita difuminado)."""
    return _run("""
im = _img(P['image_id'])
a, n = P['action'], P['amount']
if a == 'grow': Gimp.Selection.grow(im, int(n))
elif a == 'shrink': Gimp.Selection.shrink(im, int(n))
elif a == 'feather': Gimp.Selection.feather(im, float(n))
elif a == 'border': Gimp.Selection.border(im, int(n))
else: Gimp.Selection.sharpen(im)
result = _image_info(im, layers=False)['selection']
""", action=action, amount=amount, image_id=image_id)


# ---------------------------------------------------------------- pintura

@mcp.tool(annotations=EDIT)
def gimp_set_colors(foreground: str | None = None, background: str | None = None,
                    brush: str | None = None, brush_size: float | None = None,
                    opacity: float | None = None, paint_mode: str | None = None) -> dict:
    """Ajusta el contexto de pintura: color de frente/fondo, pincel (nombre), tamaño, opacidad
    y modo. Sin argumentos devuelve los valores actuales."""
    return _run("""
if P['foreground']: Gimp.context_set_foreground(_color(P['foreground']))
if P['background']: Gimp.context_set_background(_color(P['background']))
if P['brush']:
    b = Gimp.Brush.get_by_name(P['brush'])
    if b is None: raise ValueError('Pincel no encontrado: ' + P['brush'])
    Gimp.context_set_brush(b)
if P['brush_size']: Gimp.context_set_brush_size(P['brush_size'])
if P['opacity'] is not None: Gimp.context_set_opacity(P['opacity'])
if P['paint_mode']: Gimp.context_set_paint_mode(_enum(Gimp.LayerMode, P['paint_mode']))
result = {'foreground': _hex(Gimp.context_get_foreground()),
          'background': _hex(Gimp.context_get_background()),
          'brush': Gimp.context_get_brush().get_name(),
          'brush_size': Gimp.context_get_brush_size(),
          'opacity': Gimp.context_get_opacity(),
          'paint_mode': Gimp.context_get_paint_mode().value_nick}
""", foreground=foreground, background=background, brush=brush, brush_size=brush_size,
                opacity=opacity, paint_mode=paint_mode)


@mcp.tool(annotations=EDIT)
def gimp_fill(fill: str = "foreground", layer_id: int | None = None, image_id: int | None = None) -> str:
    """Rellena la selección (o la capa entera si no hay selección). fill: 'foreground',
    'background', 'white', 'transparent', 'pattern' o un color concreto ('#ff0000')."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
d = _drawable(im, P['layer_id'])
f = P['fill']
types = {'foreground': Gimp.FillType.FOREGROUND, 'background': Gimp.FillType.BACKGROUND,
         'white': Gimp.FillType.WHITE, 'transparent': Gimp.FillType.TRANSPARENT,
         'pattern': Gimp.FillType.PATTERN}
Gimp.context_push()
if f not in types:
    Gimp.context_set_foreground(_color(f)); t = Gimp.FillType.FOREGROUND
else:
    t = types[f]
d.edit_fill(t)
Gimp.context_pop()
result = 'relleno aplicado en ' + d.get_name()
""", fill=fill, layer_id=layer_id, image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_stroke_selection(width: float = 3, color: str | None = None, layer_id: int | None = None,
                          image_id: int | None = None) -> str:
    """Dibuja el contorno de la selección actual con el grosor (px) y color indicados."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
d = _drawable(im, P['layer_id'])
Gimp.context_push()
if P['color']: Gimp.context_set_foreground(_color(P['color']))
Gimp.context_set_stroke_method(Gimp.StrokeMethod.LINE)
Gimp.context_set_line_width(P['width'])
d.edit_stroke_selection()
Gimp.context_pop()
result = 'contorno dibujado'
""", width=width, color=color, layer_id=layer_id, image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_draw(points: list[float], tool: Literal["paintbrush", "pencil", "airbrush", "eraser",
                                                   "smudge", "clone", "dodge", "burn"] = "paintbrush",
              size: float | None = None, color: str | None = None, opacity: float | None = None,
              closed: bool = False, layer_id: int | None = None, image_id: int | None = None) -> str:
    """Pinta un trazo que pasa por points=[x1,y1,x2,y2,...] (coordenadas de imagen) con la
    herramienta indicada. closed=True cierra el trazo volviendo al primer punto."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
d = _drawable(im, P['layer_id'])
pts = list(P['points'])
if P['closed']: pts += pts[:2]
ox, oy = _offsets(d)
pts = [v - (ox if i % 2 == 0 else oy) for i, v in enumerate(pts)]
Gimp.context_push()
if P['size']: Gimp.context_set_brush_size(P['size'])
if P['color']: Gimp.context_set_foreground(_color(P['color']))
if P['opacity'] is not None: Gimp.context_set_opacity(P['opacity'])
t = P['tool']
if t == 'paintbrush': Gimp.paintbrush_default(d, pts)
elif t == 'pencil': Gimp.pencil(d, pts)
elif t == 'airbrush': Gimp.airbrush_default(d, pts)
elif t == 'eraser': Gimp.eraser_default(d, pts)
elif t == 'smudge': Gimp.smudge_default(d, pts)
elif t == 'dodge': Gimp.dodgeburn(d, 50.0, Gimp.DodgeBurnType.DODGE, Gimp.TransferMode.MIDTONES, pts)
elif t == 'burn': Gimp.dodgeburn(d, 50.0, Gimp.DodgeBurnType.BURN, Gimp.TransferMode.MIDTONES, pts)
else: Gimp.clone_default(d, pts)
Gimp.context_pop()
result = 'trazo con %s de %d puntos' % (t, len(pts) // 2)
""", points=points, tool=tool, size=size, color=color, opacity=opacity, closed=closed,
                layer_id=layer_id, image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_gradient(x1: float, y1: float, x2: float, y2: float,
                  start_color: str | None = None, end_color: str | None = None,
                  shape: Literal["linear", "bilinear", "radial", "square", "conical-symmetric",
                                 "conical-asymmetric", "shapeburst-angular", "shapeburst-spherical",
                                 "shapeburst-dimpled", "spiral-clockwise", "spiral-anticlockwise"] = "linear",
                  gradient: str | None = None, reverse: bool = False,
                  layer_id: int | None = None, image_id: int | None = None) -> str:
    """Rellena con degradado de (x1,y1) a (x2,y2). Por defecto va de start_color a end_color
    (o frente→fondo); gradient permite usar un degradado de GIMP por nombre."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
d = _drawable(im, P['layer_id'])
Gimp.context_push()
if P['start_color']: Gimp.context_set_foreground(_color(P['start_color']))
if P['end_color']: Gimp.context_set_background(_color(P['end_color']))
if P['gradient']:
    g = Gimp.Gradient.get_by_name(P['gradient'])
    if g is None: raise ValueError('Degradado no encontrado: ' + P['gradient'])
    Gimp.context_set_gradient(g)
else:
    Gimp.context_set_gradient_fg_bg_rgb()
Gimp.context_set_gradient_reverse(P['reverse'])
d.edit_gradient_fill(_enum(Gimp.GradientType, P['shape']), 0.0, False, 1, 0.0, True,
                     P['x1'], P['y1'], P['x2'], P['y2'])
Gimp.context_pop()
result = 'degradado aplicado'
""", x1=x1, y1=y1, x2=x2, y2=y2, start_color=start_color, end_color=end_color, shape=shape,
                gradient=gradient, reverse=reverse, layer_id=layer_id, image_id=image_id)


# ---------------------------------------------------------------- texto

@mcp.tool(annotations=EDIT)
def gimp_add_text(text: str, x: float = 0, y: float = 0, font: str = "Sans-serif", size: float = 48,
                  color: str = "black", justify: Literal["left", "right", "center", "fill"] = "left",
                  box_width: float | None = None, letter_spacing: float | None = None,
                  line_spacing: float | None = None, image_id: int | None = None) -> dict:
    """Añade una capa de texto editable en (x,y). size en px. box_width fija el ancho de caja
    para que el texto haga saltos de línea. Usa gimp_list_resources('fonts') para ver fuentes."""
    return _run("""
im = _img(P['image_id'])
f = Gimp.Font.get_by_name(P['font'])
if f is None:
    raise ValueError('Fuente no encontrada: %s' % P['font'])
with _undo(im):
    l = Gimp.TextLayer.new(im, P['text'], f, P['size'], Gimp.Unit.pixel())
    im.insert_layer(l, None, 0)
    l.set_offsets(int(P['x']), int(P['y']))
    l.set_color(_color(P['color']))
    l.set_justification(_enum(Gimp.TextJustification, P['justify']))
    if P['letter_spacing'] is not None: l.set_letter_spacing(P['letter_spacing'])
    if P['line_spacing'] is not None: l.set_line_spacing(P['line_spacing'])
    if P['box_width']:
        l.resize(P['box_width'], l.get_height())
    im.set_selected_layers([l])
result = _layer_info(l)
""", text=text, x=x, y=y, font=font, size=size, color=color, justify=justify,
                box_width=box_width, letter_spacing=letter_spacing, line_spacing=line_spacing,
                image_id=image_id)


@mcp.tool(annotations=EDIT)
def gimp_edit_text(layer_id: int, text: str | None = None, font: str | None = None,
                   size: float | None = None, color: str | None = None,
                   justify: Literal["left", "right", "center", "fill"] | None = None) -> dict:
    """Modifica una capa de texto existente."""
    return _run("""
l = _item(P['layer_id'])
if not l.is_text_layer(): raise ValueError('La capa no es de texto')
if P['text'] is not None: l.set_text(P['text'])
if P['font']:
    f = Gimp.Font.get_by_name(P['font'])
    if f is None: raise ValueError('Fuente no encontrada: ' + P['font'])
    l.set_font(f)
if P['size']: l.set_font_size(P['size'], Gimp.Unit.pixel())
if P['color']: l.set_color(_color(P['color']))
if P['justify']: l.set_justification(_enum(Gimp.TextJustification, P['justify']))
result = _layer_info(l)
""", layer_id=layer_id, text=text, font=font, size=size, color=color, justify=justify)


@mcp.tool(annotations=READ)
def gimp_list_resources(kind: Literal["fonts", "brushes", "gradients", "patterns", "palettes"],
                        query: str = "", limit: int = 100) -> list:
    """Lista recursos de GIMP (fuentes, pinceles, degradados, patrones, paletas) filtrando por texto."""
    return _run("""
fn = {'fonts': Gimp.fonts_get_list, 'brushes': Gimp.brushes_get_list,
      'gradients': Gimp.gradients_get_list, 'patterns': Gimp.patterns_get_list,
      'palettes': Gimp.palettes_get_list}[P['kind']]
names = [r.get_name() for r in fn(P['query'])]
result = names[:P['limit']]
""", kind=kind, query=query, limit=limit)


# ---------------------------------------------------------------- ajustes de color

@mcp.tool(annotations=EDIT)
def gimp_adjust(operation: Literal["brightness_contrast", "hue_saturation", "levels", "auto_levels",
                                   "white_balance", "desaturate", "invert", "threshold", "posterize",
                                   "colorize", "color_balance", "curves", "exposure",
                                   "color_temperature", "shadows_highlights"],
                brightness: float = 0, contrast: float = 0, hue: float = 0, saturation: float = 0,
                lightness: float = 0, low: float = 0, high: float = 1, gamma: float = 1,
                levels: int = 4, color: str | None = None, cyan_red: float = 0,
                magenta_green: float = 0, yellow_blue: float = 0,
                curve_points: list[float] | None = None, exposure: float = 0,
                temperature: float = 6500, shadows: float = 0, highlights: float = 0,
                layer_id: int | None = None, image_id: int | None = None) -> str:
    """Ajustes de color sobre la capa (respetando la selección):
    - brightness_contrast: brightness, contrast (-1..1)
    - hue_saturation: hue (-180..180), saturation y lightness (-100..100)
    - levels: low, high (0..1), gamma (0.1..10) · auto_levels / white_balance: automáticos
    - desaturate, invert · threshold: low, high (0..1) · posterize: levels (2..256)
    - colorize: color (tono) · color_balance: cyan_red, magenta_green, yellow_blue (-100..100)
    - curves: curve_points=[x1,y1,x2,y2,...] en 0..1 (valor) · exposure: exposure (EV)
    - color_temperature: temperature (K, <6500 enfría, >6500 calienta)
    - shadows_highlights: shadows, highlights (-100..100)"""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
d = _drawable(im, P['layer_id'])
op = P['operation']
with _undo(im):
    if op == 'brightness_contrast': d.brightness_contrast(P['brightness'], P['contrast'])
    elif op == 'hue_saturation': d.hue_saturation(Gimp.HueRange.ALL, P['hue'], P['lightness'], P['saturation'], 0.0)
    elif op == 'levels': d.levels(Gimp.HistogramChannel.VALUE, P['low'], P['high'], True, P['gamma'], 0.0, 1.0, True)
    elif op == 'auto_levels': d.levels_stretch()
    elif op == 'white_balance': _pdb_run('gimp-drawable-levels-stretch', drawable=d)
    elif op == 'desaturate': d.desaturate(Gimp.DesaturateMode.LUMINANCE)
    elif op == 'invert': d.invert(False)
    elif op == 'threshold': d.threshold(Gimp.HistogramChannel.VALUE, P['low'], P['high'])
    elif op == 'posterize': d.posterize(P['levels'])
    elif op == 'colorize':
        c = _color(P['color'] or 'orange')
        h, s, _l = _hsl(c)
        _gegl(d, 'gimp:colorize', {'hue': h / 360.0, 'saturation': max(s, 0.3), 'lightness': 0.0})
    elif op == 'color_balance':
        d.color_balance(Gimp.TransferMode.MIDTONES, True, P['cyan_red'], P['magenta_green'], P['yellow_blue'])
    elif op == 'curves':
        d.curves_spline(Gimp.HistogramChannel.VALUE, P['curve_points'])
    elif op == 'exposure': _gegl(d, 'gegl:exposure', {'exposure': P['exposure']})
    elif op == 'color_temperature':
        _gegl(d, 'gegl:color-temperature', {'original-temperature': 6500.0,
                                             'intended-temperature': float(13000 - P['temperature'])})
    elif op == 'shadows_highlights':
        _gegl(d, 'gegl:shadows-highlights', {'shadows': P['shadows'], 'highlights': P['highlights']})
result = op + ' aplicado en ' + d.get_name()
""", operation=operation, brightness=brightness, contrast=contrast, hue=hue, saturation=saturation,
                lightness=lightness, low=low, high=high, gamma=gamma, levels=levels, color=color,
                cyan_red=cyan_red, magenta_green=magenta_green, yellow_blue=yellow_blue,
                curve_points=curve_points, exposure=exposure, temperature=temperature,
                shadows=shadows, highlights=highlights, layer_id=layer_id, image_id=image_id)


# ---------------------------------------------------------------- filtros GEGL

@mcp.tool(annotations=EDIT)
def gimp_apply_filter(operation: str, properties: dict | None = None, layer_id: int | None = None,
                      image_id: int | None = None, non_destructive: bool = False) -> str:
    """Aplica cualquier filtro GEGL a una capa (respeta la selección). Ejemplos:
    gegl:gaussian-blur {std-dev-x, std-dev-y} · gegl:unsharp-mask {std-dev, scale} ·
    gegl:dropshadow {x, y, radius, color, opacity} · gegl:pixelize {size-x, size-y} ·
    gegl:noise-reduction {iterations} · gegl:vignette {radius, softness, color} ·
    gegl:cartoon · gegl:emboss · gegl:edge-sobel · gegl:mosaic · gegl:waves · gegl:ripple ·
    gegl:lens-distortion · gegl:bloom · gegl:high-pass · gegl:oilify · gegl:motion-blur-linear
    {length, angle} · gegl:sepia {scale} · gegl:mono-mixer · gimp:threshold-alpha...
    Usa gimp_filter_info para ver las propiedades de una operación.
    non_destructive=True lo deja como filtro editable en vez de fusionarlo con la capa."""
    return _run("""
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else _img(P['image_id'])
d = _drawable(im, P['layer_id'])
with _undo(im):
    _gegl(d, P['operation'], P['properties'], merge=not P['non_destructive'])
result = P['operation'] + ' aplicado en ' + d.get_name()
""", operation=operation, properties=properties or {}, layer_id=layer_id, image_id=image_id,
                non_destructive=non_destructive)


@mcp.tool(annotations=READ)
def gimp_filter_info(operation: str | None = None, query: str = "") -> Any:
    """Sin operation: lista las operaciones GEGL disponibles que contengan `query`.
    Con operation: muestra sus propiedades (nombre, tipo, valor por defecto, rango, descripción)."""
    return _run("""
if not P['operation']:
    gimp_ops = ['gimp:colorize', 'gimp:threshold', 'gimp:levels', 'gimp:curves',
                'gimp:hue-saturation', 'gimp:color-balance', 'gimp:brightness-contrast',
                'gimp:desaturate', 'gimp:posterize', 'gimp:threshold-alpha']
    ops = list(Gegl.list_operations()) + gimp_ops
    result = sorted(o for o in ops if P['query'].lower() in o.lower())
else:
    desc = None
    try:
        desc = Gegl.Operation.get_key(P['operation'], 'description')
    except Exception:
        pass
    result = {'operation': P['operation'], 'description': desc,
              'properties': _filter_props(P['operation'])}
""", operation=operation, query=query)


# ---------------------------------------------------------------- acceso total

@mcp.tool(annotations=READ)
def gimp_pdb_search(query: str, limit: int = 50) -> list:
    """Busca procedimientos de la PDB de GIMP (incluye plugins y Script-Fu) por nombre."""
    return _run("""
names = Gimp.get_pdb().query_procedures(P['query'], '', '', '', '', '', '', '')
out = []
for n in names[:P['limit']]:
    proc = Gimp.get_pdb().lookup_procedure(n)
    out.append({'name': n, 'blurb': proc.get_blurb() if proc else ''})
result = out
""", query=query, limit=limit)


@mcp.tool(annotations=READ)
def gimp_pdb_info(name: str) -> dict:
    """Muestra los argumentos y valores de retorno de un procedimiento PDB."""
    return _run("""
proc = Gimp.get_pdb().lookup_procedure(P['name'])
if proc is None: raise ValueError('No existe: ' + P['name'])
def spec(p):
    return {'name': p.name, 'type': p.value_type.name, 'blurb': p.get_blurb()}
result = {'name': P['name'], 'blurb': proc.get_blurb(), 'help': proc.get_help(),
          'menu': proc.get_menu_paths(),
          'arguments': [spec(a) for a in proc.get_arguments()],
          'returns': [spec(a) for a in proc.get_return_values()]}
""", name=name)


@mcp.tool(annotations=DANGER)
def gimp_pdb_call(name: str, arguments: dict | None = None, image_id: int | None = None,
                  layer_id: int | None = None) -> Any:
    """Llama a cualquier procedimiento PDB por nombre con argumentos por nombre.
    Valores especiales: 'image' se rellena con image_id, 'drawable' con layer_id y 'drawables'
    con [layer_id]; 'run-mode' es NONINTERACTIVE por defecto; 'file' acepta una ruta; colores
    como texto. Consulta antes gimp_pdb_info."""
    return _run("""
proc = Gimp.get_pdb().lookup_procedure(P['name'])
if proc is None: raise ValueError('No existe: ' + P['name'])
args = dict(P['arguments'] or {})
names = [a.name for a in proc.get_arguments()]
im = _item(P['layer_id']).get_image() if P['layer_id'] is not None else (
    _img(P['image_id']) if P['image_id'] is not None or Gimp.get_images() else None)
if 'run-mode' in names: args.setdefault('run-mode', Gimp.RunMode.NONINTERACTIVE)
if 'image' in names and im is not None: args.setdefault('image', im)
if 'drawable' in names and im is not None: args.setdefault('drawable', _drawable(im, P['layer_id']))
if 'drawables' in names and im is not None: args.setdefault('drawables', [_drawable(im, P['layer_id'])])
cfg = proc.create_config()
for k, v in args.items():
    pspec = cfg.find_property(k)
    if pspec is None: raise ValueError('Argumento desconocido: %s (válidos: %s)' % (k, ', '.join(names)))
    t = pspec.value_type
    if t == Gio.File.__gtype__ and isinstance(v, str): v = _file(v)
    elif t == Gegl.Color.__gtype__ and not isinstance(v, Gegl.Color): v = _color(v)
    elif t.is_a(Gimp.Image.__gtype__) and isinstance(v, int): v = _img(v)
    elif t.is_a(Gimp.Item.__gtype__) and isinstance(v, int): v = _item(v)
    elif t.is_a(GObject.TYPE_ENUM) and isinstance(v, str):
        v = [e for e in pspec.enum_class.__enum_values__.values() if v in (e.value_nick, e.value_name)][0]
    elif isinstance(v, list) and v and isinstance(v[0], int) and 'drawable' in k:
        v = [_item(i) for i in v]
    cfg.set_property(k, v)
res = proc.run(cfg)
status = res.index(0)
if status != Gimp.PDBStatusType.SUCCESS:
    raise RuntimeError('%s falló: %s' % (P['name'], res.index(1) if res.length() > 1 else status.value_nick))
result = [res.index(i) for i in range(1, res.length())]
""", name=name, arguments=arguments or {}, image_id=image_id, layer_id=layer_id)


@mcp.tool(annotations=DANGER)
def gimp_run_python(code: str) -> Any:
    """Ejecuta código Python dentro de GIMP 3 (API GObject: Gimp, Gegl, Gio, GLib ya importados).
    Asigna la respuesta a la variable `result` (o usa print). Helpers disponibles:
    _img(id) imagen, _item(id) capa, _drawable(img, id) capa activa, _color('#hex'),
    _gegl(drawable, 'gegl:op', {props}), _pdb_run('proc', **args), _image_info(img), _undo(img).
    Las variables persisten entre llamadas."""
    return _run(code)


def main() -> None:
    import sys

    if "--install-plugin" in sys.argv:
        from .bridge import install_plugin
        print(f"Plugin instalado en {install_plugin()}")
        return
    mcp.run()


if __name__ == "__main__":
    main()
