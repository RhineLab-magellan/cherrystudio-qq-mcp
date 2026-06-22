"""
协议定义模块
包含所有模块间通信的消息协议和错误码定义
"""

from .messages import (
    MessageType,
    MessageSource,
    RawMessage,
    ParsedMessage,
    OutgoingMessage,
    ModuleResponse,
)
from .error_codes import ErrorCode, BridgeError

__all__ = [
    "MessageType",
    "MessageSource",
    "RawMessage",
    "ParsedMessage",
    "OutgoingMessage",
    "ModuleResponse",
    "ErrorCode",
    "BridgeError",
]
