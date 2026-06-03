"""命令基类与上下文"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Built_in.napcat_client import NapCatClient, QQMessage


@dataclass
class CommandContext:
    """传递给每个命令的上下文，包含所有必要的引用"""
    nc: "NapCatClient"
    auto_reply: "AutoReply"  # type: ignore[name-defined]  # noqa: F821


class Command:
    """命令基类。子类需设置 name 和 description，实现 handle()"""
    name: str = ""
    description: str = ""
    reminder: str = ""  # 欢迎消息中的特殊提醒

    async def handle(self, args: str, msg: "QQMessage", ctx: CommandContext) -> str | None:
        """处理命令，返回回复文本或 None"""
        raise NotImplementedError
