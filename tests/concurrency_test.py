"""El puente debe atender a varios clientes: uno conectado e inactivo no bloquea a otro."""

import json
import socket

from gimp_mcp.bridge import HOST, PORT, _token, bridge

bridge.ping()  # asegura que GIMP y el puente están activos
idle = socket.create_connection((HOST, PORT), timeout=5)  # cliente que no envía nada
other = socket.create_connection((HOST, PORT), timeout=10)
other.sendall((json.dumps({"cmd": "ping", "token": _token()}) + "\n").encode())
resp = json.loads(other.makefile("rb").readline())
assert resp["ok"], resp
print("OK concurrencia:", resp["result"])
bad = socket.create_connection((HOST, PORT), timeout=10)
bad.sendall(b'{"cmd": "exec", "code": "result=1", "token": "malo"}\n')
resp = json.loads(bad.makefile("rb").readline())
assert not resp["ok"] and "token" in resp["error"], resp
print("OK token incorrecto rechazado")
idle.close(); other.close(); bad.close()
