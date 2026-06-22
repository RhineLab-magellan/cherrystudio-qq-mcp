"""
CherryStudio MCP 一键安装链接生成器

支持两种安装模式:
  manual : Python 路径 + server.py (本地开发)
  uvx    : UVX-based install (git 直装)

用法:
  python tools/generate_install_url.py [mode]

模式:
  manual (默认)  使用当前 Python 解释器 + server.py 路径生成 URL
  uvx            使用 uvx --from git 直装模式生成 URL

输出:
  install_info.txt (已 gitignore) + 控制台输出
"""

import json
import base64
import sys
import os
from pathlib import Path

# 仓库信息
REPO_URL = "git+https://github.com/RhineLab-magellan/cherrystudio-qq-mcp.git"
ENTRY_COMMAND = "cherrystudio-qq-mcp"
BRIDGE_NAME = "QQ Bridge"
BRIDGE_DESC = "QQ Bridge - send/receive QQ private and group messages via NapCat"


def build_config(mode: str) -> dict:
    """
    构建 MCP Server 配置 JSON

    Args:
        mode: 'manual' 或 'uvx'

    Returns:
        CherryStudio MCP 服务器配置字典
    """
    if mode == "uvx":
        return {
            "mcpServers": {
                "qq-bridge": {
                    "name": BRIDGE_NAME,
                    "type": "stdio",
                    "command": "uvx",
                    "args": ["--from", REPO_URL, ENTRY_COMMAND],
                    "env": {},
                    "description": BRIDGE_DESC,
                }
            }
        }
    else:  # manual
        python_path = sys.executable
        # tools/ 的父目录即项目根
        project_root = str(Path(__file__).resolve().parent.parent)
        server_path = os.path.join(project_root, "server.py")

        if not os.path.exists(server_path):
            # 回退: 尝试从 CWD 查找
            server_path = os.path.join(os.getcwd(), "server.py")

        return {
            "mcpServers": {
                "qq-bridge": {
                    "name": BRIDGE_NAME,
                    "type": "stdio",
                    "command": python_path,
                    "args": [server_path],
                    "env": {},
                    "description": BRIDGE_DESC,
                }
            }
        }


def generate_url(config: dict) -> str:
    """将配置编码为 cherrystudio:// 安装 URL"""
    json_str = json.dumps(config, ensure_ascii=True)
    encoded = base64.b64encode(json_str.encode("utf-8")).decode("ascii")
    return f"cherrystudio://mcp/install?servers={encoded}"


def format_output(mode: str, config: dict, url: str) -> str:
    """格式化完整输出文本"""
    lines = [
        "=" * 60,
        f"  JSON config (mode: {mode})",
        "=" * 60,
        json.dumps(config, ensure_ascii=False, indent=2),
        "",
        "=" * 60,
        "  One-click Install URL (paste in browser)",
        "=" * 60,
        url,
        "",
        "=" * 60,
        "  Usage",
        "=" * 60,
        "  1. Copy the cherrystudio:// URL above into your browser address bar",
        "  2. CherryStudio will auto-open and install the MCP server",
        "  3. Go to Settings -> MCP Server -> enable 'QQ Bridge'",
        "  4. In chat, click the MCP button to activate tools",
    ]
    return "\n".join(lines)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "manual"
    if mode not in ("manual", "uvx"):
        print(f"Unknown mode: {mode}. Use 'manual' or 'uvx'.", file=sys.stderr)
        sys.exit(1)

    config = build_config(mode)
    url = generate_url(config)
    output = format_output(mode, config, url)

    # 写入文件 (项目根/install_info.txt)
    project_root = Path(__file__).resolve().parent.parent
    out_path = project_root / "install_info.txt"
    out_path.write_text(output, encoding="utf-8")

    print(output)
    print(f"\nOutput written to: {out_path}")


if __name__ == "__main__":
    main()
