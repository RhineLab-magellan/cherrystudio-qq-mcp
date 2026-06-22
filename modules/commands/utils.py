"""
命令共享工具

从 BotSettingConfig.json 读取模板和格式化消息的公共函数，
供 dice/ark_trpg/log 等命令模块复用。

移植自旧项目 auto_reply._load_module_message() / format_msg()
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_bot_setting(module_key: str, setting_key: str, default: str = "") -> str:
    """
    从 BotSettingConfig.json 读取可定制消息模板。

    Args:
        module_key:  模块键名 (如 "dice_core", "arktrpg", "log")
        setting_key: 模板键名 (如 "r_message", "rk_message")
        default:     读取失败时的默认值

    Returns:
        消息模板字符串，空或不存在时返回 default
    """
    try:
        # 优先查找 Configuration/ 子目录
        setting_path = Path(__file__).parent.parent.parent / "Configuration" / "BotSettingConfig.json"
        if not setting_path.exists():
            setting_path = Path(__file__).parent.parent.parent / "BotSettingConfig.json"
        if not setting_path.exists():
            return default

        settings = json.loads(setting_path.read_text(encoding="utf-8"))
        value = settings.get(module_key, {}).get(setting_key, "")
        return value.strip() if value else default
    except Exception as e:
        logger.debug(f"Read BotSettingConfig failed [{module_key}.{setting_key}]: {e}")
        return default


def format_msg(template: str, result: str = "", player_name: str = "") -> str:
    """
    BotSettingConfig 模板格式化。

    占位符:
      {}  -> 命令执行结果 (无 {} 则不追加 result)
      <>  -> 玩家名称 (角色卡名 or sender_name)

    移植自旧系统 auto_reply.format_msg()
    """
    text = template
    if player_name:
        text = text.replace("<>", player_name)
    if "{}" in text:
        text = text.replace("{}", result)
    return text
