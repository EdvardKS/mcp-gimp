"""Funciones auxiliares que se cargan una vez en el intérprete de GIMP (dentro del puente)."""

PRELUDE = r'''
import contextlib as _ctx, math as _math, tempfile as _tempfile

def _img(image_id=None):
    if image_id is None:
        imgs = Gimp.get_images()
        if not imgs:
            raise ValueError("No hay imágenes abiertas")
        return imgs[0]
    im = Gimp.Image.get_by_id(int(image_id))
    if im is None or not im.is_valid():
        raise ValueError("La imagen %s no existe" % image_id)
    return im

def _item(item_id):
    it = Gimp.Item.get_by_id(int(item_id))
    if it is None or not it.is_valid():
        raise ValueError("La capa/elemento %s no existe" % item_id)
    return it

def _drawable(im, layer_id=None):
    if layer_id is not None:
        return _item(layer_id)
    sel = im.get_selected_drawables()
    if sel:
        return sel[0]
    layers = im.get_layers()
    if not layers:
        raise ValueError("La imagen no tiene capas")
    return layers[0]

def _color(c):
    if isinstance(c, Gegl.Color):
        return c
    if isinstance(c, (list, tuple)):
        v = [float(x) for x in c]
        if any(x > 1.0 for x in v[:3]):
            v = [x / 255.0 for x in v[:3]] + ([v[3] / 255.0 if v[3] > 1 else v[3]] if len(v) > 3 else [])
        col = Gegl.Color.new("black")
        col.set_rgba_with_space(v[0], v[1], v[2], v[3] if len(v) > 3 else 1.0, None)
        return col
    return Gegl.Color.new(str(c))

def _hex(col):
    r, g, b, a = col.get_rgba_with_space(None)
    cl = lambda x: max(0, min(255, int(round(x * 255))))
    return "#%02x%02x%02x" % (cl(r), cl(g), cl(b)) + ("" if a >= 0.999 else "%02x" % cl(a))

def _hsl(col):
    """Devuelve (tono 0-360, saturación 0-1, luminosidad 0-1) de un Gegl.Color."""
    import colorsys
    r, g, b, _a = col.get_rgba_with_space(None)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360.0, s, l

def _offsets(it):
    r = it.get_offsets()
    return int(r[-2]), int(r[-1])

def _align(l, mode, y=None):
    """Centra una capa en la imagen: 'center' (ambos ejes), 'center_x' o 'center_y'.
    Si se da y con center_x, se mantiene esa y (útil para texto centrado a una altura)."""
    im = l.get_image()
    x0, y0 = _offsets(l)
    if mode in ("center", "center_x"):
        x0 = (im.get_width() - l.get_width()) // 2
    if mode in ("center", "center_y"):
        y0 = (im.get_height() - l.get_height()) // 2
    elif y is not None:
        y0 = int(y)
    l.set_offsets(int(x0), int(y0))

def _layer_info(l):
    x, y = _offsets(l)
    d = {"id": l.get_id(), "name": l.get_name(), "kind": type(l).__name__,
         "visible": l.get_visible(), "x": x, "y": y,
         "width": l.get_width(), "height": l.get_height()}
    if isinstance(l, Gimp.Layer):
        d.update(opacity=round(l.get_opacity(), 2), mode=l.get_mode().value_nick,
                 has_alpha=l.has_alpha(), has_mask=l.get_mask() is not None)
    if l.is_text_layer():
        d.update(text=l.get_text(), font=l.get_font().get_name(), font_size=l.get_font_size()[0],
                 color=_hex(l.get_color()))
    if l.is_group():
        d["children"] = [_layer_info(c) for c in l.get_children()]
    return d

def _image_info(im, layers=True):
    f = im.get_file()
    d = {"id": im.get_id(), "name": im.get_name(), "file": f.get_path() if f else None,
         "width": im.get_width(), "height": im.get_height(),
         "mode": im.get_base_type().value_nick,
         "precision": im.get_precision().value_nick,
         "resolution": [round(v, 1) for v in im.get_resolution()[-2:]],
         "dirty": im.is_dirty(),
         "selected_layers": [it.get_id() for it in im.get_selected_drawables()]}
    r = Gimp.Selection.bounds(im)
    non_empty, x1, y1, x2, y2 = r[-5:]
    d["selection"] = {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1} if non_empty else None
    if layers:
        d["layers"] = [_layer_info(l) for l in im.get_layers()]
    return d

@_ctx.contextmanager
def _undo(im):
    im.undo_group_start()
    try:
        yield
    finally:
        im.undo_group_end()

def _show(im):
    """Abre una ventana para la imagen si GIMP tiene interfaz (en modo consola no hace nada)."""
    try:
        if not Gimp.Display.list() or not any(d.get_image() == im for d in Gimp.Display.list()):
            Gimp.Display.new(im)
    except Exception:
        pass

def _file(path):
    return Gio.File.new_for_path(os.path.abspath(os.path.expanduser(path)))

def _enum(enum_cls, nick):
    if isinstance(nick, enum_cls):
        return nick
    key = str(nick).strip().upper().replace("-", "_").replace(" ", "_")
    if hasattr(enum_cls, key):
        return getattr(enum_cls, key)
    for v in enum_cls.__enum_values__.values():
        if v.value_nick == str(nick).lower().replace("_", "-"):
            return v
    raise ValueError("Valor '%s' no válido para %s" % (nick, enum_cls.__name__))

def _pdb_run(name, **props):
    proc = Gimp.get_pdb().lookup_procedure(name)
    if proc is None:
        raise ValueError("Procedimiento PDB '%s' no existe" % name)
    cfg = proc.create_config()
    for k, v in props.items():
        cfg.set_property(k.replace("_", "-"), v)
    res = proc.run(cfg)
    status = res.index(0)
    if status != Gimp.PDBStatusType.SUCCESS:
        err = res.index(1) if res.length() > 1 else None
        raise RuntimeError("%s falló: %s" % (name, err if err else status.value_nick))
    return [res.index(i) for i in range(1, res.length())]

def _gegl(drawable, op, props=None, merge=True, name=None):
    f = Gimp.DrawableFilter.new(drawable, op, name or "")
    cfg = f.get_config()
    for k, v in (props or {}).items():
        key = k if cfg.find_property(k) is not None else k.replace("_", "-")
        pspec = cfg.find_property(key)
        if pspec is None:
            f.delete()
            raise ValueError("'%s' no tiene la propiedad '%s'. Disponibles: %s" % (
                op, k, ", ".join(p.name for p in cfg.list_properties())))
        if pspec.value_type == Gegl.Color.__gtype__:
            v = _color(v)
        elif pspec.value_type.is_a(GObject.TYPE_ENUM) and isinstance(v, str):
            for ev in pspec.enum_class.__enum_values__.values():
                if v in (ev.value_nick, ev.value_name):
                    v = ev
                    break
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and hasattr(pspec, "minimum"):
            if not (pspec.minimum <= v <= pspec.maximum):
                f.delete()
                raise ValueError("%s: '%s' debe estar entre %s y %s (recibido %s)" % (
                    op, key, pspec.minimum, pspec.maximum, v))
        cfg.set_property(key, v)
    f.update()
    if merge:
        drawable.merge_filter(f)
    else:
        drawable.append_filter(f)
    return f

def _filter_props(op):
    """Propiedades de una operación GEGL/GIMP, leídas de un filtro de prueba."""
    im = Gimp.Image.new(8, 8, Gimp.ImageBaseType.RGB)
    try:
        l = Gimp.Layer.new(im, "probe", 8, 8, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
        im.insert_layer(l, None, 0)
        f = Gimp.DrawableFilter.new(l, op, "")
        out = []
        for p in f.get_config().list_properties():
            info = {"name": p.name, "type": p.value_type.name}
            if p.get_blurb():
                info["description"] = p.get_blurb()
            dv = getattr(p, "default_value", None)
            if dv is not None:
                info["default"] = _hex(dv) if isinstance(dv, Gegl.Color) else (
                    dv.value_nick if hasattr(dv, "value_nick") else dv)
            if hasattr(p, "minimum") and abs(p.minimum) < 1e300:
                info["min"] = p.minimum
            if hasattr(p, "maximum") and abs(p.maximum) < 1e300:
                info["max"] = p.maximum
            out.append(info)
        f.delete()
        return out
    finally:
        im.delete()

try:
    Gegl.init(None)
except Exception:
    pass

_mcp_ready = True
'''
