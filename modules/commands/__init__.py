"""
内置命令包

导出所有命令类供 CommandRegistry.discover_builtin() 使用。
"""

from .builtin import (
    HelpCommand,
    BotCommand,
    OrderCommand,
    ModelCommand,
    ObCommand,
    DismissCommand,
    SendCommand,
    MasterCommand,
    WelcomeCommand,
)
from .dice import (
    RDiceCommand,
    RhCommand,
    RaCommand,
    ShowCommand,
    DelCommand,
    PcCommand,
    NnCommand,
    StCommand,
)
from .ark_trpg import (
    RkCommand,
    RkbCommand,
    RkpCommand,
    SckCommand,
    ArkCommand,
    SnCommand,
)
from .log import LogCommand

__all__ = [
    # 内置命令
    "HelpCommand",
    "BotCommand",
    "OrderCommand",
    "ModelCommand",
    "ObCommand",
    "DismissCommand",
    "SendCommand",
    "MasterCommand",
    "WelcomeCommand",
    # 骰子命令
    "RDiceCommand",
    "RhCommand",
    "RaCommand",
    "ShowCommand",
    "DelCommand",
    "PcCommand",
    "NnCommand",
    "StCommand",
    # 行于泰拉命令
    "RkCommand",
    "RkbCommand",
    "RkpCommand",
    "SckCommand",
    "ArkCommand",
    "SnCommand",
    # 日志命令
    "LogCommand",
]
