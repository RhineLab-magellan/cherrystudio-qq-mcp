"""OrderSystem — 命令注册表、自动发现、分发"""

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Command, CommandContext

if TYPE_CHECKING:
    from Built_in.napcat_client import QQMessage

logger = logging.getLogger("order-system")

_registry: dict[str, Command] = {}
_loaded = False


def _discover_commands():
    """扫描 OrderSystem 目录，自动发现所有 Command 子类并注册"""
    global _loaded
    if _loaded:
        return
    _loaded = True

    pkg_dir = Path(__file__).parent
    for py_file in sorted(pkg_dir.glob("*.py")):
        mod_name = py_file.stem
        if mod_name.startswith("_") or mod_name in ("base",):
            continue
        try:
            mod = importlib.import_module(f".{mod_name}", package=__package__)
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, Command)
                    and obj is not Command
                    and obj.name
                ):
                    _registry[obj.name] = obj()
                    logger.debug(f"已注册命令: .{obj.name}")
        except Exception as e:
            logger.warning(f"加载命令模块 {mod_name} 失败: {e}")


def get_command(name: str) -> Command | None:
    """按名称获取命令实例"""
    _discover_commands()
    return _registry.get(name)


def list_commands() -> list[Command]:
    """获取所有已注册的命令（排序）"""
    _discover_commands()
    return sorted(_registry.values(), key=lambda c: c.name)


async def dispatch(text: str, msg: "QQMessage", ctx: CommandContext) -> str | None:
    """解析并分发命令。返回回复文本或 None（不回复）"""
    if not text.startswith("."):
        return None

    # 提取命令名和参数
    rest = text[1:].strip()
    if not rest:
        return None

    parts = rest.split(None, 1)
    cmd_name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    cmd = get_command(cmd_name)
    if cmd is None:
        return None

    try:
        return await cmd.handle(args, msg, ctx)
    except Exception as e:
        logger.error(f"命令 .{cmd_name} 执行异常: {e}", exc_info=True)
        return f".{cmd_name} 执行出错: {e}"
