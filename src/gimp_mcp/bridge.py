"""Cliente del puente TCP que corre dentro de GIMP, y arranque automático de GIMP."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .prelude import PRELUDE

HOST = "127.0.0.1"
PORT = int(os.environ.get("GIMP_MCP_PORT", "9877"))
# gui: abre GIMP con ventana (ves lo que hace el bot). headless: GIMP sin ventana.
MODE = os.environ.get("GIMP_MCP_MODE", "gui").lower()
TIMEOUT = float(os.environ.get("GIMP_MCP_TIMEOUT", "300"))
STARTUP_TIMEOUT = float(os.environ.get("GIMP_MCP_STARTUP_TIMEOUT", "240"))


class GimpError(RuntimeError):
    pass


def _gimp_profile_dirs() -> list[Path]:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "GIMP"
    if not base.exists():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)


def _token() -> str:
    if os.environ.get("GIMP_MCP_TOKEN"):
        return os.environ["GIMP_MCP_TOKEN"]
    for d in _gimp_profile_dirs():
        f = d / "mcp-bridge.token"
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    return ""


def _gimp_bin_dir() -> Path:
    if os.environ.get("GIMP_MCP_GIMP_DIR"):
        return Path(os.environ["GIMP_MCP_GIMP_DIR"])
    for cand in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/GIMP 3/bin",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "GIMP 3/bin",
    ):
        if cand.exists():
            return cand
    found = shutil.which("gimp-3") or shutil.which("gimp")
    if found:
        return Path(found).parent
    raise GimpError("No encuentro GIMP 3. Define GIMP_MCP_GIMP_DIR con la carpeta bin de GIMP.")


def _gimp_exe(headless: bool) -> Path:
    bindir = _gimp_bin_dir()
    names = (["gimp-console-3.2.exe", "gimp-console-3.exe", "gimp-console.exe"] if headless
             else ["gimp-3.2.exe", "gimp-3.exe", "gimp.exe"])
    for n in names:
        if (bindir / n).exists():
            return bindir / n
    raise GimpError(f"No encuentro el ejecutable de GIMP en {bindir}")


PLUGIN_SRC = Path(__file__).parent / "gimp_plugin" / "mcp-bridge.py"


def install_plugin() -> Path:
    """Copia el plugin mcp-bridge al perfil de GIMP si falta o está desactualizado."""
    profiles = _gimp_profile_dirs()
    if profiles:
        profile = profiles[0]
    else:
        # GIMP nunca se ha abierto: deducimos la versión del ejecutable (gimp-console-3.2.exe -> 3.2)
        ver = "3.2"
        try:
            for exe in _gimp_bin_dir().glob("gimp-console-3.*.exe"):
                ver = exe.stem.rsplit("-", 1)[-1]
                break
        except GimpError:
            pass
        profile = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "GIMP" / ver
    dst = profile / "plug-ins" / "mcp-bridge" / "mcp-bridge.py"
    src = PLUGIN_SRC.read_bytes()
    if not dst.exists() or dst.read_bytes() != src:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src)
    return dst


def _spawn_detached(args: list[str], hidden: bool = False) -> None:
    """Arranca GIMP de forma que sobreviva al servidor MCP.

    Los clientes MCP en Windows meten al servidor en un Job Object con KILL_ON_JOB_CLOSE,
    que mataría a GIMP (y el trabajo sin guardar) al reiniciar el MCP. Win32_Process.Create
    (WMI) crea el proceso fuera de ese job."""
    if os.name != "nt":
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return
    cmdline = subprocess.list2cmdline(args).replace("'", "''")
    # ShowWindow: 0 = oculta (modo headless, sin ventana de consola), 4 = mostrar sin robar el foco
    show = 0 if hidden else 4
    ps = ("$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
          f"-Property @{{ShowWindow=[uint16]{show}}}; "
          "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          f"-Arguments @{{CommandLine='{cmdline}'; ProcessStartupInformation=$si}}; "
          "exit $r.ReturnValue")
    try:
        rc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                            creationflags=subprocess.CREATE_NO_WINDOW).returncode
        if rc == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Plan B: proceso desacoplado normal (puede morir con el job del cliente MCP)
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)


class Bridge:
    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self.file = None
        self.mode: str | None = None

    # -- conexión -------------------------------------------------------
    def _connect(self) -> None:
        s = socket.create_connection((HOST, PORT), timeout=3)
        s.settimeout(TIMEOUT)
        self.sock, self.file = s, s.makefile("rwb")

    def _close(self) -> None:
        for obj in (self.file, self.sock):
            try:
                if obj:
                    obj.close()
            except OSError:
                pass
        self.sock = self.file = None

    def _send(self, payload: dict) -> dict:
        payload["token"] = _token()
        self.file.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.file.flush()
        line = self.file.readline()
        if not line:
            raise ConnectionError("GIMP cerró la conexión")
        return json.loads(line)

    def _launch(self) -> None:
        # Si GIMP ya está abierto, lanzarlo otra vez le pasa la orden -b a la instancia existente.
        headless = MODE == "headless"
        exe = _gimp_exe(headless)
        install_plugin()
        args = [str(exe), "--no-splash", "--batch-interpreter=plug-in-script-fu-eval",
                "-b", f"(plug-in-mcp-bridge RUN-NONINTERACTIVE {PORT})"]
        if headless:
            args.insert(1, "-i")
        _spawn_detached(args, hidden=headless)
        self.mode = "headless" if headless else "gui"

    def ensure(self) -> None:
        if self.sock:
            return
        try:
            self._connect()
        except OSError:
            if os.environ.get("GIMP_MCP_AUTOSTART", "1") == "0":
                raise GimpError("El puente MCP de GIMP no está activo (127.0.0.1:%d)." % PORT)
            self._launch()
            deadline = time.time() + STARTUP_TIMEOUT
            while True:
                try:
                    self._connect()
                    break
                except OSError:
                    if time.time() > deadline:
                        raise GimpError("GIMP no abrió el puente MCP a tiempo. "
                                        "¿Está instalado el plugin mcp-bridge?")
                    time.sleep(1.5)
        resp = self._send({"cmd": "ping"})
        if not resp.get("ok"):
            self._close()
            raise GimpError(f"El puente rechazó la conexión: {resp.get('error')}")
        self._exec_raw(PRELUDE)

    def _exec_raw(self, code: str) -> dict:
        resp = self._send({"cmd": "exec", "code": code})
        if not resp.get("ok"):
            msg = resp.get("error", "error desconocido")
            tb = resp.get("traceback", "")
            # Mostramos solo las últimas líneas útiles del traceback
            tail = "\n".join(tb.strip().splitlines()[-4:]) if tb else ""
            raise GimpError(f"{msg}\n{tail}".strip())
        return resp

    def run(self, code: str, params: dict | None = None) -> dict:
        """Ejecuta código Python en GIMP. `P` contiene los parámetros, `result` es la respuesta."""
        header = f"P = json.loads({json.dumps(json.dumps(params or {}))})\n"
        for attempt in range(2):
            try:
                self.ensure()
                return self._exec_raw(header + code)
            except (OSError, ConnectionError, ValueError) as e:
                self._close()
                if attempt == 1:
                    raise GimpError(f"Se perdió la conexión con GIMP: {e}") from e
        raise AssertionError("unreachable")

    def ping(self) -> dict:
        for attempt in range(2):
            try:
                self.ensure()
                return self._send({"cmd": "ping"})
            except (OSError, ConnectionError, ValueError) as e:
                self._close()
                if attempt == 1:
                    raise GimpError(f"No hay conexión con GIMP: {e}") from e
        raise AssertionError("unreachable")


bridge = Bridge()
