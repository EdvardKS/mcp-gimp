---
name: gimp-studio
description: "Estudio de imagen completo: generar con IA (image_generate), editar a fondo en GIMP (MCP gimp_*), exportar y enviar el resultado por Telegram."
metadata:
  {
    "openclaw":
      {
        "emoji": "🎨",
        "requires": { "config": ["mcp.servers.gimp"] },
      },
  }
---

# GIMP Studio

Tienes un estudio de imagen completo. No estás limitado a lo que hace GIMP por sí solo:
combinas **generación con IA**, **edición precisa en GIMP** y **envío del resultado** al usuario.
Cuando te pidan cualquier cosa con imágenes (crear, retocar, montar, convertir, mejorar,
poner texto, quitar fondo, hacer un cartel, un logo, una miniatura…), usa este flujo.

## Tus tres motores

| Motor | Herramientas | Para qué |
|---|---|---|
| **IA generativa** | `image_generate` (prompt, `image`/`images` de referencia, `aspectRatio`, `resolution` 1K/2K/4K, `count`) | Crear imágenes desde cero, reimaginar una foto, cambiar estilo, quitar/cambiar fondos, inpainting por descripción |
| **GIMP** (MCP `gimp_*`, 42 herramientas) | ver tabla abajo | Control pixel a pixel: capas, texto real, recortes exactos, color, filtros, formatos, tamaños concretos |
| **Entrega** | respuesta con línea `MEDIA:` o herramienta `message` | Que el usuario reciba el archivo en Telegram |

Si `image_generate` no aparece o falla por cuota, dilo en una frase y haz todo lo posible con GIMP.

## Flujo de trabajo estándar

1. **Entiende el encargo**: tamaño final, formato, para qué es (Instagram 1080×1080, story 1080×1920, banner web, impresión 300 ppp…). Si falta algo importante pregunta UNA vez; si es obvio, decide tú.
2. **Consigue la base**:
   - Imagen que te ha enviado el usuario → está en `media/inbound/` (ruta en el contexto del mensaje). Ábrela con `gimp_open_image`.
   - Imagen nueva → `image_generate` con un prompt detallado (sujeto, estilo, luz, encuadre, "sin texto" si vas a poner texto en GIMP). Luego `gimp_open_image` con la ruta devuelta.
   - Composición desde cero (cartel, tarjeta) → `gimp_new_image`.
3. **Edita en GIMP** con capas: cada elemento (fondo, foto, texto, logo) en su capa. Así puedes corregir sin rehacer.
4. **Comprueba con `gimp_preview`** después de cada cambio importante. Mira la imagen de verdad y sé crítico: ¿el texto se lee entero o queda cortado por un borde? ¿está centrado? ¿los colores encajan? Si algo falla, corrígelo (mueve con `gimp_transform_layer(align="center")`, cambia tamaño) y vuelve a mirar antes de exportar. No digas "perfecto" sin haberlo comprobado.
5. **Exporta** con `gimp_export_image` a `C:\Users\EdvardVM\Documents\playTorre_bot\media\outbound\<nombre-descriptivo>.<ext>`. Guarda también el `.xcf` con `gimp_save_image` si el usuario puede pedir cambios después.
6. **Envía** el archivo (ver "Entrega").

## Herramientas GIMP por tarea

- **Ver estado**: `gimp_status`, `gimp_list_images`, `gimp_image_info` (ids de imagen y capas; úsalos en las demás llamadas).
- **Archivo**: `gimp_open_image`, `gimp_save_image` (XCF con capas), `gimp_export_image` (png/jpg/webp/gif/tif/bmp/pdf, `quality` 1-100, `flatten`), `gimp_close_image`, `gimp_duplicate_image`.
- **Tamaño y encuadre**: `gimp_scale_image` (px o %, interpolación `nohalo` para calidad), `gimp_resize_canvas` (lienzo sin deformar, con `anchor` y `fill`), `gimp_crop` (rectángulo, `to_selection` o `autocrop`), `gimp_rotate_flip_image`, `gimp_convert_mode`.
- **Capas**: `gimp_new_layer` (color/transparente/grupo), `gimp_add_layer_from_file` (meter logo o foto, centra y escala), `gimp_set_layer` (opacidad, modo de fusión: multiply, screen, overlay, soft-light…, posición), `gimp_layer_order`, `gimp_duplicate_layer`, `gimp_delete_layer`, `gimp_merge_layers`, `gimp_transform_layer` (escalar, rotar, voltear, mover), `gimp_layer_mask`.
- **Selección**: `gimp_select` (rectangle, ellipse, rounded_rectangle, polygon, all, none, invert, from_alpha; `feather` para bordes suaves), `gimp_select_by_color` (color exacto o varita mágica con `x,y` + `contiguous`), `gimp_modify_selection` (grow, shrink, feather, border).
- **Pintura**: `gimp_set_colors`, `gimp_fill`, `gimp_stroke_selection` (bordes y marcos), `gimp_draw` (paintbrush, pencil, airbrush, eraser, smudge, clone, dodge, burn), `gimp_gradient` (linear, radial…).
- **Texto**: `gimp_add_text` (fuente, tamaño px, color, justificación, `box_width` para saltos de línea), `gimp_edit_text`, `gimp_list_resources("fonts", "Bold")` para elegir fuente. **Ojo**: `x,y` es la esquina superior izquierda de la capa, no su centro. Para centrar usa `align="center"` (o `align="center_x"` con la `y` que quieras). La respuesta trae `x, y, width, height`: si `x+width` supera el ancho de la imagen, el texto sale cortado; reduce `size` o centra.
- **Color**: `gimp_adjust` con brightness_contrast, hue_saturation, levels, auto_levels, curves, exposure, color_temperature, shadows_highlights, color_balance, desaturate, invert, threshold, posterize, colorize.
- **Filtros**: `gimp_apply_filter` con cualquier operación GEGL. Consulta propiedades con `gimp_filter_info("gegl:nombre")` o busca con `gimp_filter_info(query="blur")`.
- **Sin límite**: `gimp_pdb_search` / `gimp_pdb_info` / `gimp_pdb_call` para cualquier función de GIMP, y `gimp_run_python` para código Python dentro de GIMP (API `Gimp`, `Gegl`). Si una herramienta no hace exactamente lo que necesitas, estas sí.

## Recetas de calidad

- **Quitar fondo (producto, retrato)**: `image_generate` con `image=<foto>` y prompt "same subject, isolated on pure white background, keep details" → abrir en GIMP → `gimp_select_by_color("#ffffff", threshold=12)` → `gimp_select("invert")` → `gimp_layer_mask(add, mask_type="selection")` → exportar PNG. Para fondo transparente sin IA: varita mágica (`contiguous=True`) sobre el fondo + máscara.
- **Mejorar una foto floja**: `gimp_adjust("auto_levels")` → `gimp_adjust("shadows_highlights", shadows=25)` → `gimp_apply_filter("gegl:noise-reduction", {"iterations": 2})` → `gimp_apply_filter("gegl:unsharp-mask", {"std-dev": 1.5, "scale": 0.6})`. Compara con `gimp_preview` antes/después.
- **Ampliar sin perder calidad**: `gimp_scale_image(percent=200, interpolation="nohalo")` y después un `gegl:unsharp-mask` suave. Para ampliaciones grandes, mejor `image_generate` con la imagen de referencia y `resolution="2K"` o `"4K"` y prompt "upscale, same content, sharper details".
- **Cartel / post con texto**: fondo (IA o degradado `gimp_gradient`) → capa oscura semitransparente donde irá el texto (`gimp_new_layer(fill="#000000")` + `gimp_set_layer(opacity=45)`) → `gimp_add_text` grande con fuente Bold → `gimp_apply_filter("gegl:dropshadow", {"x":4,"y":4,"radius":8,"opacity":0.7})` sobre la capa de texto → logo con `gimp_add_layer_from_file`.
- **Marco / borde**: `gimp_select("all")` → `gimp_modify_selection("border", 20)` → `gimp_fill("#ffffff")`.
- **Viñeta y look**: `gegl:vignette` {radius, softness}; sepia `gegl:sepia`; look cálido `gimp_adjust("color_temperature", temperature=7500)`.
- **Varias versiones**: `gimp_duplicate_image` por variante, o `image_generate` con `count=3` y deja elegir al usuario (envíalas como álbum).
- **Miniatura / redes**: exporta JPG `quality=88` para fotos, PNG para gráficos con texto o transparencia, WebP `quality=85` para web.
- **Impresión**: trabaja al tamaño final en px (A4 a 300 ppp = 2480×3508) y exporta PNG o PDF.

## Entrega al usuario (Telegram)

Al terminar, en tu respuesta final pon un breve comentario y después **una línea sola** con la ruta absoluta:

```
Listo: cartel 1080×1080 con el texto que pediste.

MEDIA:C:\Users\EdvardVM\Documents\playTorre_bot\media\outbound\cartel-evento.png
```

Reglas:
- La línea `MEDIA:` va sola, sin negrita, sin comillas de código, una por archivo. Varias líneas `MEDIA:` seguidas con fotos se agrupan en un álbum.
- Telegram comprime las fotos. Si el usuario quiere el **original a máxima calidad** (impresión, logo con transparencia, XCF, PDF), envíalo como documento con la herramienta `message` (`action=send`, `media=<ruta>`, `forceDocument=true`) o avisa de que va comprimido.
- Si una generación con `image_generate` es asíncrona, el resultado llega solo; no vuelvas a pedirlo.
- Guarda todo en `media/outbound/` con nombres descriptivos (`logo-playtorre-transparente.png`), nunca en el escritorio del usuario.

## Comportamiento

- Actúa, no describas: la gente quiere la imagen, no el plan. Un mensaje de "voy con ello" como mucho si va a tardar.
- Revisa siempre con `gimp_preview` antes de enviar. Si no te gusta, corrígelo tú antes de preguntar.
- Si el resultado de la IA no es bueno, repite con otro prompt (máx. 2 intentos) y luego explica qué has conseguido.
- No cierres imágenes del usuario que ya estuvieran abiertas en GIMP y no uses `discard_changes` sobre trabajo que no sea tuyo.
- Cuando la petición sea ambigua en algo que cambia el resultado (tamaño, idioma del texto, estilo), pregunta una vez y con opciones concretas.
