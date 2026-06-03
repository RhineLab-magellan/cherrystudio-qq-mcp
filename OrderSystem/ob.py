""".ob — 旁观者管理"""

from .base import Command, CommandContext
from Built_in.napcat_client import QQMessage


class ObCommand(Command):
    name = "ob"
    description = "旁观模式 (join/exit/list/clr/on/off)"
    reminder = "使用 .ob join 加入旁观，发言不计入日志；.ob on/off 开关旁观模式"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type

        if msg_type != "group":
            return ".ob 仅在群聊中有效。"

        action = args.strip().lower()
        if not action or action in ("join",):
            return ar.ob_join(target, msg.sender_id)
        elif action in ("exit",):
            return ar.ob_exit(target, msg.sender_id)
        elif action in ("list",):
            return ar.ob_list(target)
        elif action in ("clr",):
            return ar.ob_clear(target)
        elif action in ("on",):
            return ar.ob_toggle(target, True)
        elif action in ("off",):
            return ar.ob_toggle(target, False)
        elif action in ("help", "?"):
            return ".ob 子命令:\n  .ob / .ob join  - 加入旁观\n  .ob exit       - 退出旁观\n  .ob list       - 查看旁观者\n  .ob clr        - 清除所有旁观者\n  .ob on/off     - 开关旁观模式"
        else:
            return f"未知子命令: .ob {args}\n用法: .ob join/exit/list/clr/on/off"
