"""
消息协议定义
定义所有模块间通信的标准消息格式
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Any


class MessageType(Enum):
    """消息类型"""
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    MIXED = "mixed"
    AT = "at"
    REPLY = "reply"


class MessageSource(Enum):
    """消息来源"""
    GROUP = "group"
    PRIVATE = "private"


@dataclass
class RawMessage:
    """
    原始消息 (来自 NapCat)

    这是从 NapCatQQ WebSocket 接收到的原始消息，
    尚未经过任何解析或处理。
    """
    msg_id: str                          # 消息唯一ID
    source: MessageSource                # 消息来源 (群聊/私聊)
    target_id: str                       # 目标ID (群号或QQ号)
    sender_id: str                       # 发送者QQ号
    sender_name: str                     # 发送者昵称
    content: str                         # 文本内容
    message_type: MessageType            # 消息类型
    attachments: list[dict] = field(default_factory=list)  # 附件信息 (图片、文件等)
    timestamp: datetime = field(default_factory=datetime.now)  # 时间戳
    raw_data: dict = field(default_factory=dict)  # 原始 OneBot 数据 (保留完整信息)

    # 图片和文件的提取信息 (由 NapCatBridge._parse_raw_message 填充)
    image_files: list[str] = field(default_factory=list)   # NapCat 图片 file ID 列表
    file_infos: list[dict] = field(default_factory=list)   # [{url, name, size}] 列表

    # 群聊额外信息 (仅 source == GROUP 时有值)
    group_name: str = ""                 # 群名称 (来自 sender.group_name 或 raw_data)

    def to_dict(self) -> dict:
        """转换为字典 (用于序列化)"""
        return {
            "msg_id": self.msg_id,
            "source": self.source.value,
            "target_id": self.target_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "message_type": self.message_type.value,
            "attachments": self.attachments,
            "timestamp": self.timestamp.isoformat(),
            "raw_data": self.raw_data,
            "image_files": self.image_files,
            "file_infos": self.file_infos,
            "group_name": self.group_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RawMessage":
        """从字典创建 (用于反序列化)"""
        return cls(
            msg_id=data["msg_id"],
            source=MessageSource(data["source"]),
            target_id=data["target_id"],
            sender_id=data["sender_id"],
            sender_name=data["sender_name"],
            content=data["content"],
            message_type=MessageType(data["message_type"]),
            attachments=data.get("attachments", []),
            timestamp=datetime.fromisoformat(data["timestamp"]) if isinstance(
                data["timestamp"], str) else data["timestamp"],
            raw_data=data.get("raw_data", {}),
            image_files=data.get("image_files", []),
            file_infos=data.get("file_infos", []),
            group_name=data.get("group_name", ""),
        )

    def get_reply_id(self) -> str:
        """
        从消息段中提取被引用的消息 ID（如果有）

        用于回复链解析，从 raw_data 的 message 数组中
        查找 type == "reply" 的段并返回其 data.id
        """
        message = self.raw_data.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "reply":
                    return str(seg.get("data", {}).get("id", ""))
        return ""

    def format_for_ai(self) -> str:
        """
        格式化为 AI 可读的字符串

        格式:
        - 群聊: [HH:MM:SS] 群(群名或群号) 发送者名(发送者ID): 内容
        - 私聊: [HH:MM:SS] 私聊 发送者名(发送者ID): 内容
        """
        ts = self.timestamp.strftime("%H:%M:%S") if self.timestamp else ""
        if self.source == MessageSource.GROUP:
            group_label = self.group_name or self.target_id
            return f"[{ts}] 群({group_label}) {self.sender_name}({self.sender_id}): {self.content}"
        else:
            return f"[{ts}] 私聊 {self.sender_name}({self.sender_id}): {self.content}"

    @property
    def group_id(self) -> str:
        """获取群号 (仅群聊消息有值，私聊返回空字符串)"""
        if self.source == MessageSource.GROUP:
            return self.target_id
        return ""

    def extract_at_targets(self) -> list[str]:
        """
        从消息段中提取所有被 @的 QQ 号码

        Returns:
            被 @的 QQ 号列表
        """
        targets = []
        message = self.raw_data.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = seg.get("data", {}).get("qq", "")
                    if qq and qq != "all":
                        targets.append(str(qq))
        return targets

    def is_at_me(self, bot_qq: str) -> bool:
        """检查消息是否 @了指定的机器人"""
        return bot_qq in self.extract_at_targets()

    def has_at_others(self, bot_qq: str) -> bool:
        """检查消息是否 @了除机器人以外的其他人"""
        targets = self.extract_at_targets()
        return any(t != bot_qq for t in targets)

    # OneBot at 段转文本后为 @QQ号 (QQ 号纯数字)，此正则精确匹配
    _AT_PATTERN = re.compile(r"@\d+")

    def strip_at_mentions(self, text: str | None = None) -> str:
        """
        从消息文本中移除所有 @QQ号 提及。

        OneBot v11 的 at 段在 _extract_text() 中被转为 @QQ号 格式，
        QQ 号均为纯数字，因此 @\\d+ 模式可精确匹配而不影响正文。

        Args:
            text: 指定文本，默认使用 self.content

        Returns:
            剥离 @ 提及后的文本 (首尾空白也已去除)
        """
        src = text if text is not None else self.content
        return self._AT_PATTERN.sub("", src).strip()


@dataclass
class ParsedMessage:
    """
    解析后的消息 (传递给模块)

    经过 MessageBus 解析和路由后的消息，
    包含命令识别结果和元数据。
    """
    raw: RawMessage                      # 原始消息
    is_command: bool = False             # 是否为命令
    command_name: str | None = None      # 命令名称 (如 "help", "bot")
    command_args: str | None = None      # 命令参数
    metadata: dict = field(default_factory=dict)  # 额外元数据

    @property
    def session_key(self) -> str:
        """生成会话键 (用于会话管理)"""
        return f"{self.raw.source.value}_{self.raw.target_id}"

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "raw": self.raw.to_dict(),
            "is_command": self.is_command,
            "command_name": self.command_name,
            "command_args": self.command_args,
            "metadata": self.metadata,
        }


@dataclass
class OutgoingMessage:
    """
    待发送消息 (从模块到 NapCat)

    模块处理后生成的待发送消息，
    由 MessageBus 收集并发送给 NapCatBridge。
    """
    target_source: MessageSource         # 目标来源 (群聊/私聊)
    target_id: str                       # 目标ID (群号或QQ号)
    content: str                         # 消息内容
    message_type: MessageType = MessageType.TEXT  # 消息类型
    attachments: list[dict] = field(default_factory=list)  # 附件
    reply_to_msg_id: str | None = None   # 回复的消息ID (可选)
    metadata: dict = field(default_factory=dict)  # 元数据
    skip_doc: bool = False               # 跳过自动转文档 (命令系统回复等场景)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "target_source": self.target_source.value,
            "target_id": self.target_id,
            "content": self.content,
            "message_type": self.message_type.value,
            "attachments": self.attachments,
            "reply_to_msg_id": self.reply_to_msg_id,
            "metadata": self.metadata,
            "skip_doc": self.skip_doc,
        }


@dataclass
class ModuleResponse:
    """
    模块响应

    模块处理消息后返回的标准化响应，
    包含成功/失败状态和内容。
    """
    success: bool                        # 是否成功
    content: str | None = None           # 响应内容 (成功时)
    error_code: str | None = None        # 错误码 (失败时，如 "BRG-1001")
    error_detail: str | None = None      # 错误详情 (仅用于日志，不展示给用户)
    requires_confirmation: bool = False  # 是否需要用户确认
    metadata: dict = field(default_factory=dict)  # 元数据

    @property
    def user_message(self) -> str:
        """
        获取展示给用户的消息

        成功时返回 content，失败时返回 "[自定义文本]+[错误码]"
        """
        if self.success:
            return self.content or ""
        else:
            custom_text = self.metadata.get("custom_error_text", "处理失败")
            return f"{custom_text} [{self.error_code}]"

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "success": self.success,
            "content": self.content,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
            "requires_confirmation": self.requires_confirmation,
            "metadata": self.metadata,
        }

    @classmethod
    def success_response(cls, content: str, **kwargs) -> "ModuleResponse":
        """创建成功响应"""
        return cls(success=True, content=content, **kwargs)

    @classmethod
    def error_response(cls, error_code: str, error_detail: str = "", custom_text: str = "处理失败", **kwargs) -> "ModuleResponse":
        """创建错误响应"""
        return cls(
            success=False,
            error_code=error_code,
            error_detail=error_detail,
            metadata={"custom_error_text": custom_text},
            **kwargs
        )
