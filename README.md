# gimp-mcp

Servidor [MCP](https://modelcontextprotocol.io) para controlar **GIMP 3** con detalle desde un agente
(Claude, OpenClaw, Claude Code, Claude Desktop...): capas, selecciones, pintura, texto, ajustes de
color, filtros GEGL, cualquier procedimiento de la PDB y Python libre dentro de GIMP. Incluye una
vista previa en imagen para que el agente **vea** el resultado de cada paso.

## Cómo funciona

```
Agente ──MCP (stdio)──> gimp-mcp ──TCP 127.0.0.1:9877──> plugin mcp-bridge (dentro de GIMP)
```

- **mcp-bridge** es un plugin Python de GIMP 3 que escucha solo en `127.0.0.1` y ejecuta las
  órdenes con la API de GIMP. Cada petición lleva un token aleatorio guardado en el perfil de GIMP
  (`%APPDATA%\GIMP\3.x\mcp-bridge.token`), así que otros programas no pueden usarlo sin él.
- **gimp-mcp** instala/actualiza el plugin automáticamente y arranca GIMP si no está abierto.
  Si GIMP ya está abierto, le pasa la orden de activar el puente a esa misma ventana.
- GIMP se lanza fuera del *job* del cliente MCP, así que **no se cierra** (ni pierdes trabajo)
  cuando el cliente reinicia el servidor.

## Requisitos

- GIMP 3.0 o posterior (probado con 3.2.6 en Windows 11)
- Python 3.10+ y [uv](https://docs.astral.sh/uv/)

## Instalación

```powershell
git clone https://github.com/EdvardKS/mcp-gimp.git
cd mcp-gimp
uv sync
uv run gimp-mcp --install-plugin   # opcional: se hace solo al primer uso
```

> Si GIMP estaba abierto al instalar el plugin por primera vez, ciérralo y vuelve a abrirlo
> para que lo registre.

### OpenClaw

```powershell
openclaw mcp set gimp '{"command":"C:/ruta/a/mcp-gimp/.venv/Scripts/gimp-mcp.exe","args":[],"env":{"GIMP_MCP_MODE":"gui"}}'
openclaw mcp probe
```

Para que el agente sepa **cuándo y cómo** usar todo esto (flujo de trabajo, recetas de calidad y
entrega del archivo por Telegram/WhatsApp con `MEDIA:`), copia la skill incluida a tu workspace:

```powershell
Copy-Item -Recurse openclaw/skills/gimp-studio ~/.openclaw/workspace/skills/
openclaw skills list   # debe aparecer "gimp-studio ✓ ready"
```

La skill combina el MCP de GIMP con la generación de imágenes nativa de OpenClaw
(`image_generate`). Para activarla configura un proveedor de imagen, por ejemplo Gemini
(requiere facturación activa en Google AI Studio):

```powershell
openclaw config patch --file imagegen.json5
# imagegen.json5:
# { agents: { defaults: { mediaModels: { image: { primary: "google/gemini-3.1-flash-image" } } } } }
```

### Claude Code / Claude Desktop

```json
{
  "mcpServers": {
    "gimp": {
      "command": "C:/ruta/a/mcp-gimp/.venv/Scripts/gimp-mcp.exe",
      "env": { "GIMP_MCP_MODE": "gui" }
    }
  }
}
```

## Configuración (variables de entorno)

| Variable | Por defecto | Uso |
|---|---|---|
| `GIMP_MCP_MODE` | `gui` | `gui` abre GIMP con ventana (ves lo que hace el agente); `headless` lo usa sin ventana |
| `GIMP_MCP_PORT` | `9877` | Puerto local del puente |
| `GIMP_MCP_AUTOSTART` | `1` | `0` para no arrancar GIMP automáticamente |
| `GIMP_MCP_GIMP_DIR` | autodetectado | Carpeta `bin` de GIMP si no está en la ruta estándar |
| `GIMP_MCP_TIMEOUT` | `300` | Segundos máximos por operación |
| `GIMP_MCP_STARTUP_TIMEOUT` | `240` | Segundos de espera al arrancar GIMP |
| `GIMP_MCP_FORWARD_TIMEOUT` | `20` | Si GIMP ya está abierto y no activa el puente en este tiempo (p. ej. tiene un diálogo abierto), se levanta un GIMP oculto sin ventana como plan B |

## Herramientas (42)

| Área | Herramientas |
|---|---|
| Estado e imágenes | `gimp_status`, `gimp_list_images`, `gimp_image_info`, `gimp_new_image`, `gimp_open_image`, `gimp_save_image` (XCF), `gimp_export_image` (png/jpg/webp/gif/tif/pdf…), `gimp_close_image`, `gimp_duplicate_image`, `gimp_preview` |
| Imagen completa | `gimp_scale_image`, `gimp_resize_canvas`, `gimp_crop` (rectángulo, selección o autocrop), `gimp_rotate_flip_image`, `gimp_convert_mode` |
| Capas | `gimp_new_layer` (y grupos), `gimp_add_layer_from_file`, `gimp_set_layer` (opacidad, modo de fusión, posición…), `gimp_layer_order`, `gimp_duplicate_layer`, `gimp_delete_layer`, `gimp_merge_layers`, `gimp_transform_layer`, `gimp_layer_mask` |
| Selección | `gimp_select` (rectángulo, elipse, redondeada, polígono, desde alfa, todo/nada/invertir, con difuminado), `gimp_select_by_color` (color o varita mágica), `gimp_modify_selection` |
| Pintura | `gimp_set_colors`, `gimp_fill`, `gimp_stroke_selection`, `gimp_draw` (pincel, lápiz, aerógrafo, goma, dedo, clonar, sobreexponer/subexponer), `gimp_gradient` |
| Texto | `gimp_add_text`, `gimp_edit_text`, `gimp_list_resources` (fuentes, pinceles, degradados, patrones, paletas) |
| Color | `gimp_adjust`: brillo/contraste, tono/saturación, niveles, auto-niveles, desaturar, invertir, umbral, posterizar, colorear, balance de color, curvas, exposición, temperatura, sombras/luces |
| Filtros | `gimp_apply_filter` (cualquier operación GEGL, destructiva o no destructiva), `gimp_filter_info` |
| Acceso total | `gimp_pdb_search`, `gimp_pdb_info`, `gimp_pdb_call`, `gimp_run_python` |

Las operaciones de edición se agrupan en un único paso de deshacer de GIMP, y las herramientas
llevan anotaciones MCP (`readOnlyHint` / `destructiveHint`) para que el cliente sepa cuáles
solo leen y cuáles pueden borrar o sobrescribir.

## Pruebas

Con GIMP sin ventana y en otro puerto para no tocar tu sesión:

```powershell
$env:GIMP_MCP_MODE="headless"; $env:GIMP_MCP_PORT="9878"
uv run python tests/smoke_test.py      # ejecuta todas las herramientas
uv run python tests/mcp_client_test.py # prueba por el protocolo MCP real
```

## Seguridad

`gimp_run_python` y `gimp_pdb_call` permiten ejecutar código arbitrario dentro de GIMP (que puede
leer y escribir archivos del usuario). El puente solo acepta conexiones locales con token, pero
da al agente el mismo poder que tendría un script de GIMP: actívalo solo para agentes de confianza.

## Licencia

MIT
