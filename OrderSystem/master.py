""".master — 管理与重置操作"""

from .base import Command, CommandContext
from napcat_client import QQMessage


class MasterCommand(Command):
    name = "master"
    description = "管理操作 "

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        ar = ctx.auto_reply
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type
        sender_id = msg.sender_id

        parts = args.split(None, 1)
        action = parts[0] if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if action == "LLMList":
            return ar.master_llm_list()
        elif action == "LLMChange":
            return await ar.master_llm_change(msg_type, target, sub_args)
        elif action == "LLMStatus":
            return ar.master_llm_status()
        elif action == "LLMReset":
            return ar.master_llm_reset()
        elif action == "AllResetAgent":
            return await ar.master_all_reset_agent(sender_id)
        elif action == "OnlyResetAgent":
            return await ar.master_only_reset_agent(sender_id)
        elif action in ("help", "Help"):
            return self._sub_help()
        else:
            return self._sub_help()

    def _sub_help(self) -> str:
        return (
            ".master 子命令:\n"
            "  LLMList                - 查看可用模型列表\n"
            "  LLMChange <模型名>      - 切换当前会话模型（下次对话生效）\n"
            "  LLMStatus              - 查看当前模型与KEY状态\n"
            "  LLMReset               - 重置主KEY（切换回主端点）\n"
            "  AllResetAgent          - 删除所有API会话+清空本地（管理员）\n"
            "  OnlyResetAgent         - 仅删除API会话，保留本地（管理员）\n"
            "  help                   - 显示此帮助"
        )
