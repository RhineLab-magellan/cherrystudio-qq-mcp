""".model — 模型管理（从 .order master 中拆出）"""

from .base import Command, CommandContext
from Built_in.napcat_client import QQMessage


class ModelCommand(Command):
    name = "model"
    description = "模型管理"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type

        if not ar.check_admin(msg.sender_id):
            return "⛔ 权限不足。.model 指令仅限管理员使用。"

        parts = args.split(None, 1)
        action = parts[0] if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if action in ("list", "列表"):
            return ar.model_list()
        elif action in ("change", "切换"):
            return await ar.model_change(msg_type, target, sub_args)
        elif action in ("status", "状态"):
            return ar.model_status()
        elif action in ("help", "?"):
            return self._sub_help()
        else:
            return f"未知指令: .model {args}\n\n{self._sub_help()}"

    def _sub_help(self) -> str:
        return (
            ".model 子命令:\n"
            "  .model list            - 查看可用模型列表\n"
            "  .model change <模型名>  - 切换当前会话模型\n"
            "  .model status          - 查看当前模型与 provider 状态\n"
            "  .model help            - 显示此帮助"
        )
