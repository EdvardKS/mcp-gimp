#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Bridge para GIMP 3.

Abre un servidor TCP en 127.0.0.1 que recibe código Python (una petición JSON
por línea) y lo ejecuta dentro de GIMP con acceso completo a la API (Gimp, Gegl,
Gio...). Lo usa el servidor MCP `gimp-mcp` para que un agente controle GIMP.

Seguridad:
  - Solo escucha en 127.0.0.1.
  - Cada petición debe llevar el token guardado en <perfil GIMP>/mcp-bridge.token
    (se genera la primera vez y solo lo puede leer el usuario de Windows).

Arranque:
  - Menú  Filtros > Desarrollo > MCP Bridge  (deja el puente escuchando), o
  - gimp-3.2.exe -b "(plug-in-mcp-bridge RUN-NONINTERACTIVE 9877)"
"""

import io
import json
import os
import secrets
import selectors
import socket
import sys
import traceback
import contextlib

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, Gegl, GObject, GLib, Gio  # noqa: E402

PROC_NAME = "plug-in-mcp-bridge"
DEFAULT_PORT = 9877


def _token_path():
    return os.path.join(Gimp.directory(), "mcp-bridge.token")


def _load_or_create_token():
    path = _token_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    tok = secrets.token_hex(24)
    with open(path, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


def _jsonable(value):
    """Convierte objetos de GIMP/GLib en algo serializable."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, Gimp.Item):
        return {"type": type(value).__name__, "id": value.get_id(), "name": value.get_name()}
    if isinstance(value, Gimp.Image):
        return {"type": "Image", "id": value.get_id()}
    if isinstance(value, Gegl.Color):
        r, g, b, a = value.get_rgba_with_space(None)
        return {"r": r, "g": g, "b": b, "a": a}
    if isinstance(value, Gio.File):
        return value.get_path()
    if isinstance(value, Gimp.Resource):
        return {"type": type(value).__name__, "name": value.get_name()}
    return repr(value)


class Bridge:
    def __init__(self, port):
        self.port = port
        self.token = _load_or_create_token()
        self.namespace = {
            "Gimp": Gimp, "Gegl": Gegl, "GObject": GObject, "GLib": GLib, "Gio": Gio,
            "os": os, "json": json,
            "__name__": "__mcp__",
        }
        self.running = True

    def execute(self, code):
        out = io.StringIO()
        ns = self.namespace
        ns.pop("result", None)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            exec(compile(code, "<mcp>", "exec"), ns)
        try:
            Gimp.displays_flush()
        except Exception:
            pass
        return {"ok": True, "result": _jsonable(ns.get("result")), "stdout": out.getvalue()}

    def handle(self, line):
        try:
            req = json.loads(line)
        except ValueError as e:
            return {"ok": False, "error": "JSON inválido: %s" % e}
        if not secrets.compare_digest(str(req.get("token", "")), self.token):
            return {"ok": False, "error": "token incorrecto"}
        cmd = req.get("cmd", "exec")
        if cmd == "ping":
            return {"ok": True, "result": {"gimp": Gimp.version(), "pid": os.getpid()}}
        if cmd == "stop":
            self.running = False
            return {"ok": True, "result": "bridge detenido"}
        try:
            return self.execute(req.get("code", ""))
        except Exception as e:  # el error se devuelve al cliente, el puente sigue vivo
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                    "traceback": traceback.format_exc()}

    def serve(self):
        """Atiende varios clientes a la vez (p. ej. varias instancias del servidor MCP).

        Las órdenes se ejecutan de una en una en este hilo, que es el único que puede
        hablar con GIMP; un cliente inactivo no bloquea a los demás."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        srv.bind(("127.0.0.1", self.port))
        srv.listen(16)
        srv.setblocking(False)
        sel = selectors.DefaultSelector()
        sel.register(srv, selectors.EVENT_READ, None)
        buffers = {}
        while self.running:
            for key, _ in sel.select(timeout=1.0):
                if key.data is None:
                    conn, _ = srv.accept()
                    conn.setblocking(False)
                    buffers[conn] = b""
                    sel.register(conn, selectors.EVENT_READ, "client")
                    continue
                conn = key.fileobj
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    sel.unregister(conn)
                    buffers.pop(conn, None)
                    conn.close()
                    continue
                buffers[conn] += chunk
                while b"\n" in buffers[conn]:
                    line, buffers[conn] = buffers[conn].split(b"\n", 1)
                    resp = self.handle(line.decode("utf-8"))
                    data = (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
                    conn.setblocking(True)
                    try:
                        conn.sendall(data)
                    except OSError:
                        pass
                    finally:
                        conn.setblocking(False)
        for key in list(sel.get_map().values()):
            key.fileobj.close()
        sel.close()


class McpBridgePlugIn(Gimp.PlugIn):
    def do_query_procedures(self):
        return [PROC_NAME]

    def do_set_i18n(self, name):
        return False

    def do_create_procedure(self, name):
        proc = Gimp.Procedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
        proc.set_menu_label("MCP Bridge")
        proc.add_menu_path("<Image>/Filters/Development/")
        proc.set_documentation(
            "Puente para controlar GIMP desde un servidor MCP",
            "Escucha en 127.0.0.1 y ejecuta código Python enviado por gimp-mcp.",
            name)
        proc.set_attribution("PlayTorre", "PlayTorre", "2026")
        proc.add_enum_argument("run-mode", "Run mode", "Modo de ejecución",
                               Gimp.RunMode, Gimp.RunMode.NONINTERACTIVE,
                               GObject.ParamFlags.READWRITE)
        proc.add_int_argument("port", "Puerto", "Puerto TCP local",
                              1024, 65535, DEFAULT_PORT, GObject.ParamFlags.READWRITE)
        return proc

    def run(self, procedure, config, run_data):
        port = config.get_property("port") or DEFAULT_PORT
        try:
            # En arranque automático (no interactivo) no mostramos diálogo para no molestar
            if config.get_property("run-mode") == Gimp.RunMode.INTERACTIVE:
                Gimp.message("MCP Bridge escuchando en 127.0.0.1:%d" % port)
            Bridge(port).serve()
        except OSError as e:
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR,
                GLib.Error.new_literal(GLib.quark_from_string("mcp-bridge"),
                                       "No se pudo abrir el puerto %d: %s" % (port, e), 0))
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)


Gimp.main(McpBridgePlugIn.__gtype__, sys.argv)
