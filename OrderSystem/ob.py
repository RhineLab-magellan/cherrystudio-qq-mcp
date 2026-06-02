""".ob — 日志黑名单管理"""

from .base import Command, CommandContext
from napcat_client import QQMessage


class ObCommand(Command):
    name = "ob"
    description = "日志黑名单 (将发送者加入本群日志排除列表)"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type

        if msg_type != "group":
            return ".ob 仅在群聊中有效。"

        # 解析 @ 目标
        target_user = msg.sender_id
        mentioned = ar.extract_at_targets(msg)
        if mentioned:
            if not ar.check_admin(msg.sender_id):
                return "指定他人加入黑名单需要管理员权限。"
            target_user = mentioned[0]

        return ar.ob_add(target, target_user)
