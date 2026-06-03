""".send — 发送消息给 Master"""

from .base import Command, CommandContext
from Built_in.napcat_client import QQMessage


class SendCommand(Command):
    name = "send"
    description = "发送消息给管理员 (报告/反馈)"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        nc = ctx.nc
        target = msg.group_id if msg.message_type == "group" else msg.sender_id

        if not args.strip():
            return "用法: .send <消息内容>"

        admin_qq = ar.admin_qq
        if not admin_qq:
            return "未设置管理员 QQ。"

        source = f"群({target})" if msg.message_type == "group" else f"私聊({msg.sender_id})"
        full_msg = f"[来自 {source} · {msg.sender_name}]\n{args.strip()}"
        await nc.send_msg("private", admin_qq, full_msg)
        return "已发送给管理员。"
