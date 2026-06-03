""".dismiss — 退群指令"""

from .base import Command, CommandContext
from Built_in.napcat_client import QQMessage


class DismissCommand(Command):
    name = "dismiss"
    description = "退出群聊（验证群号后四位）"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        nc = ctx.nc
        target = msg.group_id if msg.message_type == "group" else msg.sender_id

        if msg.message_type != "group":
            return ".dismiss 仅在群聊中有效。"

        return await ar.dismiss_leave(nc, target, args)
