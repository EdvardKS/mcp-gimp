"""Prueba extremo a extremo por el protocolo MCP (stdio), como lo usaría un agente.

Uso:  uv run python tests/mcp_client_test.py
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    # Pasamos el entorno completo (GIMP_MCP_PORT, GIMP_MCP_MODE...): el cliente MCP solo hereda unas pocas variables
    params = StdioServerParameters(command=sys.executable, args=["-m", "gimp_mcp.server"], env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        print(f"{len(tools)} herramientas:", ", ".join(t.name for t in tools))

        async def call(name, **args):
            res = await session.call_tool(name, args)
            kinds = [c.type for c in res.content]
            text = " ".join(getattr(c, "text", "") for c in res.content)[:200]
            print(f"{'ERR' if res.is_error else 'OK '} {name}: {kinds} {text}")
            if res.is_error:
                raise SystemExit(1)
            return res

        await call("gimp_status")
        res = await call("gimp_new_image", width=640, height=360, background="#102030")
        await call("gimp_add_text", text="Hola desde MCP", x=40, y=140, size=60, color="#ffd23f")
        prev = await call("gimp_preview", max_size=320)
        assert any(c.type == "image" for c in prev.content), "la vista previa no devolvió imagen"
        await call("gimp_close_image", discard_changes=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
