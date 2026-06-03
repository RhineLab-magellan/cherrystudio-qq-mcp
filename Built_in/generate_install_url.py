"""Generate CherryStudio MCP one-click install URL.

Supports three installation modes:
  manual : Python path + server.py (legacy)
  npx    : NPX-based install
  uvx    : UVX-based install

Usage:
  python generate_install_url.py [mode]

Modes:
  manual (default)  Generate a cherrystudio:// URL using the current Python interpreter
  npx               Generate a cherrystudio:// URL using npx
  uvx               Generate a cherrystudio:// URL using uvx
"""
import json
import base64
import sys
import os

MODE = sys.argv[1] if len(sys.argv) > 1 else "manual"

def build_config(mode: str) -> dict:
    if mode == "npx":
        return {
            "mcpServers": {
                "qq-bridge": {
                    "name": "QQ Bridge",
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "qq-mcp-bridge"],
                    "env": {},
                    "description": "QQ Bridge - send/receive QQ private and group messages via NapCat",
                }
            }
        }
    elif mode == "uvx":
        return {
            "mcpServers": {
                "qq-bridge": {
                    "name": "QQ Bridge",
                    "type": "stdio",
                    "command": "uvx",
                    "args": ["qq-mcp-bridge"],
                    "env": {},
                    "description": "QQ Bridge - send/receive QQ private and group messages via NapCat",
                }
            }
        }
    else:  # manual
        python_path = sys.executable
        script_dir = os.path.dirname(os.path.abspath(__file__))
        server_path = os.path.join(os.path.dirname(script_dir), "server.py")
        if not os.path.exists(server_path):
            server_path = os.path.join(os.getcwd(), "server.py")
        return {
            "mcpServers": {
                "qq-bridge": {
                    "name": "QQ Bridge",
                    "type": "stdio",
                    "command": python_path,
                    "args": [server_path],
                    "env": {},
                    "description": "QQ Bridge - send/receive QQ private and group messages via NapCat",
                }
            }
        }

config = build_config(MODE)

json_str = json.dumps(config, ensure_ascii=True)
encoded = base64.b64encode(json_str.encode("utf-8")).decode("ascii")
url = f"cherrystudio://mcp/install?servers={encoded}"

lines = []
lines.append("=" * 60)
lines.append(f"  JSON config (mode: {MODE})")
lines.append("=" * 60)
lines.append(json.dumps(config, ensure_ascii=False, indent=2))
lines.append("")
lines.append("=" * 60)
lines.append("  One-click Install URL (paste in browser)")
lines.append("=" * 60)
lines.append(url)
lines.append("")
lines.append("=" * 60)
lines.append("  Usage")
lines.append("=" * 60)
lines.append("  1. Copy the cherrystudio:// URL above into your browser address bar")
lines.append("  2. CherryStudio will auto-open and install the MCP server")
lines.append("  3. Go to Settings -> MCP Server -> enable 'QQ Bridge'")
lines.append("  4. In chat, click the MCP button to activate tools")

output = "\n".join(lines)

# Write to file (gitignored)
script_dir = os.path.dirname(os.path.abspath(__file__))
out_path = os.path.join(script_dir, "..", "install_info.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(output)

print(output)
