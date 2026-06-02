""".bot — 群组指令模式开关"""

from .base import Command, CommandContext
from napcat_client import QQMessage


class BotCommand(Command):
    name = "bot"
    description = "群组指令模式 (on=正常回复 / off=仅响应指令)"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type

        action = args.strip().lower()
        if action in ("on",):
            return ar.bot_set(msg_type, target, True)
        elif action in ("off",):
            return ar.bot_set(msg_type, target, False)
        elif action in ("orderwhite", "orderWhite"):
            return ar.order_orderwhite(msg_type, target)
        elif action in ("help", "?"):
            return ".bot 子命令:\n  .bot on          - 恢复正常回复\n  .bot off         - 仅响应 .xxx 指令，不参与聊天\n  .bot orderwhite  - 切换本群指令免@模式"
        else:
            return f"未知子命令: .bot {args}\n用法: .bot on / .bot off / .bot orderwhite"
