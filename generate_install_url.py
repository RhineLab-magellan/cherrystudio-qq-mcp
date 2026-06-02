"""Generate CherryStudio MCP one-click install URL"""
import json
import base64
import sys

PYTHON_PATH = "C:/Users/magellan/Desktop/助手/.venv/Scripts/python.exe"
SERVER_PATH = "C:/Users/magellan/Desktop/助手/qq_mcp_bridge/server.py"

config = {
    "mcpServers": {
        "qq-bridge": {
            "name": "QQ Bridge",
            "type": "stdio",
            "command": PYTHON_PATH,
            "args": [SERVER_PATH],
            "env": {},
            "description": "QQ Bridge - send/receive QQ private and group messages",
        }
    }
}

json_str = json.dumps(config, ensure_ascii=True)
encoded = base64.b64encode(json_str.encode("utf-8")).decode("ascii")
url = f"cherrystudio://mcp/install?servers={encoded}"

# Write all output to file with utf-8
lines = []
lines.append("=" * 60)
lines.append("  JSON config (for manual setup)")
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
with open("install_info.txt", "w", encoding="utf-8") as f:
    f.write(output)
print(output)
