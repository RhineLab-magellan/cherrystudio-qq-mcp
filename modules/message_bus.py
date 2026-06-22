"""
消息互联桥模块 (MessageBus)

职责:
1. 接收原始消息 (来自 NapCatBridge)
2. 应用过滤规则 (黑白名单、模块启用状态)
3. 识别命令特征
4. 分发到对应模块 (CommandModule / CherryStudioModule) — 非阻塞
5. 提供 send_response() 供模块直接发送回复
"""

import asyncio
import logging
import re
from typing import Any, Callable

from protocols.messages import (
    RawMessage,
    ParsedMessage,
    OutgoingMessage,
    ModuleResponse,
    MessageType,
    MessageSource,
)
from protocols.error_codes import ErrorCode, BridgeError
from state.manager import StateManager

logger = logging.getLogger(__name__)


class MessageFilter:
    """
    消息过滤器基类

    子类可以实现不同的过滤规则。
    """

    async def should_pass(self, msg: RawMessage) -> bool:
        """
        判断消息是否应该通过过滤

        Args:
            msg: 原始消息

        Returns:
            True 表示通过，False 表示被过滤
        """
        return True


class BlacklistFilter(MessageFilter):
    """
    黑名单过滤器

    .bot off 群的消息被过滤，但以下情况例外:
    - 命令消息 (由调用方在解析后单独检查，此处不处理)
    - @机器人 的消息: 显式 @bot 时黑名单不生效
    """

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        # 获取机器人自身 QQ 号的回调 (由 _connect_queues 注入)
        self._self_qq_getter: Callable[[], str] = lambda: ""

    async def should_pass(self, msg: RawMessage) -> bool:
        if msg.source == MessageSource.GROUP:
            if self.state_manager.is_in_blacklist(msg.target_id):
                # @机器人 时黑名单不生效
                if self._is_at_bot(msg):
                    logger.debug(
                        f"Blacklist group {msg.target_id} message allowed (@bot detected)")
                    return True
                logger.debug(f"Message filtered by blacklist: group={msg.target_id}")
                return False
        return True

    def _is_at_bot(self, msg: RawMessage) -> bool:
        """检查消息是否 @ 了机器人"""
        self_qq = self._self_qq_getter()
        if not self_qq:
            return False
        raw_message = getattr(msg, 'raw_data', {}).get("message", [])
        if isinstance(raw_message, list):
            for seg in raw_message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq == self_qq:
                        return True
        return False


class ModuleEnabledFilter(MessageFilter):
    """模块启用状态过滤器"""

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager

    async def should_pass(self, msg: RawMessage) -> bool:
        # 至少有一个模块启用
        return (
            self.state_manager.is_module_enabled("command") or
            self.state_manager.is_module_enabled("cherrystudio")
        )


class MessageBus:
    """
    消息互联桥 (非阻塞并发模型)

    核心路由逻辑:
    1. 从 raw_message_queue 获取原始消息
    2. 应用过滤规则
    3. 解析消息 (识别命令，支持紧凑格式回退)
    4. 分发到对应模块队列 (不阻塞等待响应)
    5. 模块自行通过 send_response() 发送回复
    """

    def __init__(self, state_manager: StateManager):
        """
        初始化消息互联桥

        Args:
            state_manager: 状态管理器实例
        """
        self.state_manager = state_manager

        # 消息队列
        self.raw_message_queue: asyncio.Queue[RawMessage] = asyncio.Queue()
        self.send_message_queue: asyncio.Queue[OutgoingMessage] = asyncio.Queue()

        # 模块队列 (由外部模块提供)
        self.command_queue: asyncio.Queue[ParsedMessage] | None = None
        self.cherrystudio_queue: asyncio.Queue[ParsedMessage] | None = None

        # 过滤器链 (仅包含解析前过滤器)
        # 注意: BlacklistFilter 不在此处，而是在命令解析之后单独检查，
        # 确保命令消息 (如 .bot on) 不受黑名单过滤
        self.filters: list[MessageFilter] = [
            ModuleEnabledFilter(state_manager),
        ]

        # 黑名单过滤器 (解析后对非命令消息单独检查)
        self._blacklist_filter = BlacklistFilter(state_manager)

        # 命令识别正则 (支持 . 和 。 前缀)
        self.command_pattern = re.compile(r"^[.。]\s*(\w+)\s*(.*)?$")

        # 紧凑格式回退正则: 提取 ASCII 前缀作为命令名
        # 例如 "st力量5" → ("st", "力量5")
        self.compact_pattern = re.compile(r"^([a-zA-Z]+)(.*)$")

        # 运行状态
        self._running = False

        # 事件钩子管理器 (可选, 由 server.py 注入)
        self.hook_manager = None

    def set_command_queue(self, queue: asyncio.Queue[ParsedMessage]):
        """设置命令模块队列"""
        self.command_queue = queue

    def set_cherrystudio_queue(self, queue: asyncio.Queue[ParsedMessage]):
        """设置 CherryStudio 模块队列"""
        self.cherrystudio_queue = queue

    async def start(self):
        """
        启动消息总线 (非阻塞分发)

        从 raw_message_queue 获取消息，过滤、解析后立即分发，
        不等待模块响应。模块自行将 OutgoingMessage 推送到 send_message_queue。
        """
        self._running = True
        logger.info("MessageBus started (non-blocking concurrent mode)")

        while self._running:
            try:
                # 1. 从队列获取原始消息
                raw_msg = await self.raw_message_queue.get()

                # 2. 应用解析前过滤规则 (模块启用状态等，不含黑名单)
                if not await self._passes_filters(raw_msg):
                    continue

                # 3. 解析消息 (识别命令)
                parsed = self._parse_message(raw_msg)

                # 4. 黑名单检查: 仅过滤非命令消息
                #    命令消息 (如 .bot on) 必须始终能通过，否则 .bot off 后无法恢复
                if not parsed.is_command:
                    if not await self._blacklist_filter.should_pass(raw_msg):
                        continue

                # 4.5 触发 on_message 钩子 (日志录制、消息统计等)
                if self.hook_manager:
                    await self.hook_manager.fire("on_message", parsed)

                # 5. 分发到模块 (不阻塞)
                await self._dispatch_message(parsed)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"MessageBus message handling failed: {e}", exc_info=True)

    async def stop(self):
        """停止消息总线"""
        self._running = False
        logger.info("MessageBus stopped")

    async def _passes_filters(self, msg: RawMessage) -> bool:
        """应用所有过滤器"""
        for filter in self.filters:
            if not await filter.should_pass(msg):
                return False
        return True

    def _parse_message(self, raw_msg: RawMessage) -> ParsedMessage:
        """
        解析消息 (识别命令)

        支持:
        - 标准格式: .help / 。help
        - 带参数: .order list
        - 带 @ 前缀: @机器人 .help (群消息中 @ 提及会被自动剥离)
        - 紧凑格式回退: .st力量5 → 命令 "st", 参数 "力量5"
        """
        is_command = False
        command_name = None
        command_args = None

        text = raw_msg.content.strip()

        # 剥离 @提及 (群消息 @机器人 后跟命令时，@QQ号 前缀会干扰命令匹配)
        # strip_at_mentions 基于 OneBot at 段的 @\\d+ 模式，精确且安全
        clean_text = raw_msg.strip_at_mentions(text)

        # 检查是否为命令 (标准格式)
        match = self.command_pattern.match(clean_text)
        if match:
            full_name = match.group(1)
            args_part = match.group(2).strip() if match.group(2) else ""

            # 如果命令名全是 ASCII，直接使用
            if full_name.isascii():
                is_command = True
                command_name = full_name.lower()
                command_args = args_part
            else:
                # 紧凑格式回退: 提取 ASCII 前缀作为命令名
                # 例如 "st力量5" → cmd="st", args="力量5"
                compact_match = self.compact_pattern.match(
                    full_name + (" " + args_part if args_part else "")
                )
                if compact_match:
                    is_command = True
                    command_name = compact_match.group(1).lower()
                    command_args = compact_match.group(2).strip()

        return ParsedMessage(
            raw=raw_msg,
            is_command=is_command,
            command_name=command_name,
            command_args=command_args,
        )

    async def _dispatch_message(self, parsed: ParsedMessage):
        """
        分发消息到对应模块 (非阻塞)

        命令消息 → command_queue
        普通消息 → cherrystudio_queue

        同时处理旁观者转发 (4A.6):
        如果消息来自开启了旁观模式的群，将消息转发给所有旁观者。
        """
        # ---- 旁观者消息转发 (4A.6) ----
        if parsed.raw.source == MessageSource.GROUP:
            await self._forward_to_observers(parsed)

        if parsed.is_command:
            if not self.state_manager.is_module_enabled("command"):
                logger.debug("CommandModule disabled, ignoring command message")
                return

            if self.command_queue:
                await self.command_queue.put(parsed)
                logger.debug(
                    f"Command message dispatched: {parsed.command_name} -> {parsed.session_key}"
                )
            else:
                logger.error("CommandModule queue not initialized")

        else:
            if not self.state_manager.is_module_enabled("cherrystudio"):
                return

            if self.cherrystudio_queue:
                await self.cherrystudio_queue.put(parsed)
                logger.debug(f"Normal message dispatched -> {parsed.session_key}")
            else:
                logger.debug("CherryStudio module queue not initialized")

    async def _forward_to_observers(self, parsed: ParsedMessage):
        """
        旁观者消息转发 (4A.6)

        如果消息来自开启了旁观模式的群，将消息内容以私聊形式
        转发给该群的所有旁观者。

        格式: "👁️ [旁观] 群 {group_id} — {sender_name}: {content}"
        """
        group_id = parsed.raw.target_id
        ob_groups = self.state_manager.state.ob_groups

        if group_id not in ob_groups:
            return

        observers = self.state_manager.state.observers.get(group_id, set())
        if not observers:
            return

        sender_name = parsed.raw.sender_name or parsed.raw.sender_id
        content = parsed.raw.content or "(非文本消息)"

        forward_text = f"👁️ [旁观] 群 {group_id} — {sender_name}:\n{content}"

        for observer_id in observers:
            # 不转发给自己
            if observer_id == parsed.raw.sender_id:
                continue
            try:
                outgoing = OutgoingMessage(
                    target_source=MessageSource.PRIVATE,
                    target_id=observer_id,
                    content=forward_text,
                    message_type=MessageType.TEXT,
                )
                await self.send_message_queue.put(outgoing)
            except Exception as e:
                logger.warning(f"Observer forwarding failed [{observer_id}]: {e}")

    async def send_response(
        self,
        raw_msg: RawMessage,
        response: ModuleResponse,
    ):
        """
        发送模块响应 (供模块直接调用)

        将 ModuleResponse 转换为 OutgoingMessage 并推送到 send_message_queue。

        Args:
            raw_msg: 原始消息 (用于确定回复目标)
            response: 模块响应
        """
        if not response.content and not response.error_code:
            return  # 无需发送

        outgoing = OutgoingMessage(
            target_source=raw_msg.source,
            target_id=raw_msg.target_id,
            content=response.user_message,
            message_type=MessageType.TEXT,
            reply_to_msg_id=raw_msg.msg_id if response.success else None,
            metadata={
                "success": response.success,
                "error_code": response.error_code,
            },
        )
        await self.send_message_queue.put(outgoing)

    def add_filter(self, filter: MessageFilter):
        """添加自定义过滤器"""
        self.filters.append(filter)

    def remove_filter(self, filter: MessageFilter):
        """移除过滤器"""
        if filter in self.filters:
            self.filters.remove(filter)
