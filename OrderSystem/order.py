""".order — 会话与 Agent 管理"""

from .base import Command, CommandContext
from napcat_client import QQMessage


class OrderCommand(Command):
    name = "order"
    description = "会话/Agent 管理 (切换/列表/重建/status/help)"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type

        parts = args.split(None, 1)
        action = parts[0] if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if action in ("切换", "switch"):
            return await ar.order_switch_agent(msg_type, target, sub_args)
        elif action in ("列表", "list"):
            return ar.order_list_agents()
        elif action in ("重建", "reset", "重建会话"):
            return await ar.order_rebuild_session(msg_type, target)
        elif action in ("status", "状态"):
            return ar.order_status(msg_type, target)

        elif action in ("help", "帮助", "?"):
            return self._sub_help()
        else:
            return f"未知指令: .order {args}\n\n{self._sub_help()}"

    def _sub_help(self) -> str:
        return (
            ".order 子命令:\n"
            "  .order 切换 <名称>     - 切换到指定 Agent\n"
            "  .order 列表            - 查看所有可用 Agent\n"
            "  .order 重建会话        - 删除当前会话，下次对话开启新上下文\n"
            "  .order status          - 查看当前会话状态\n"
            "  .order help            - 显示此帮助"
        )
