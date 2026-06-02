""".log — 群聊日志记录"""

import random
import shutil
import string
import zipfile
from datetime import datetime
from pathlib import Path

from .base import Command, CommandContext
from napcat_client import QQMessage

PLAYER_LOG = Path(__file__).parent.parent / "PlayerLog"


def _random_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _group_dir(group_id: str) -> Path:
    return PLAYER_LOG / f"group_{group_id}"


def _find_log(group_id: str, log_name: str) -> Path | None:
    gd = _group_dir(group_id)
    if not gd.exists():
        return None
    for d in sorted(gd.iterdir()):
        if d.is_dir() and log_name in d.name:
            return d
    return None


def _list_log_names(group_id: str) -> list[str]:
    gd = _group_dir(group_id)
    if not gd.exists():
        return []
    result = []
    for d in sorted(gd.iterdir()):
        if d.is_dir() and d.name != "config.json":
            # 提取日志名：群号_日志名_随机后缀 → 日志名
            name = d.name[len(group_id) + 1:]  # 去掉 "群号_"
            name = name.rsplit("_", 1)[0]      # 去掉随机后缀
            result.append(name)
    return result


class LogCommand(Command):
    name = "log"
    description = "群聊日志 (new/on/off/end/list/get/del)"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        nc = ctx.nc
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type

        if msg_type != "group":
            return ".log 仅在群聊中有效。"

        parts = args.split(None, 1)
        action = parts[0] if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if action == "new":
            return ar.log_new(target, sub_args)
        elif action == "on":
            return ar.log_resume(target)
        elif action == "off":
            return ar.log_pause(target)
        elif action == "end":
            return await ar.log_end(target, nc)
        elif action == "list":
            return ar.log_list(target)
        elif action == "get":
            return await ar.log_get(target, sub_args, nc)
        elif action == "del":
            return ar.log_delete(target, sub_args)
        elif action in ("help", "?"):
            return self._sub_help()
        else:
            return f"未知子命令: .log {args}\n\n{self._sub_help()}"

    def _sub_help(self) -> str:
        return (
            ".log 子命令:\n"
            "  .log new <日志名>   - 新建日志并开始记录\n"
            "  .log on             - 继续记录\n"
            "  .log off            - 暂停记录\n"
            "  .log end            - 完成记录并发送日志文件\n"
            "  .log list           - 查看本群日志列表\n"
            "  .log get <日志名>    - 手动获取日志\n"
            "  .log del <日志名>    - 删除日志（不可逆）"
        )
