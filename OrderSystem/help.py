""".help — 列出所有可用命令"""

from .base import Command, CommandContext
from . import list_commands
from Built_in.napcat_client import QQMessage


class HelpCommand(Command):
    name = "help"
    description = "显示所有可用命令及简介"

    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
        cmds = list_commands()
        lines = ["可用命令:"]
        for cmd in cmds:
            lines.append(f"  .{cmd.name:<10} - {cmd.description}")
        return "\n".join(lines)
