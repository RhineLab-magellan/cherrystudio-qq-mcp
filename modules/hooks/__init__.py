"""
钩子管理器 (HookManager)

提供事件钩子系统，供日志记录等模块使用。
移植自旧项目 OrderSystem/base.py 中的 EventHooks dataclass。

适配新架构:
  - 异步回调 (async def)
  - 由 MessageBus 或 CommandModule 在消息分发时调用
  - 日志命令 (.log) 通过此管理器注册/注销消息钩子

支持的事件类型:
  - on_message:    每条消息到达时 (用于日志录制、消息统计等)
  - pre_command:   命令执行前 (用于权限检查、频率限制等)
  - post_command:  命令执行后 (用于审计日志、响应修改等)

Phase 2 增强:
  - 优先级排序 (priority 数值越小越先执行)
  - 过滤函数 (filter_fn) — 仅匹配特定条件的消息才触发
"""

import asyncio
import logging
from typing import Any, Callable, Awaitable
from dataclasses import dataclass, field

from protocols.messages import ParsedMessage

logger = logging.getLogger(__name__)

# 钩子回调签名: async def hook(msg: ParsedMessage, context: dict) -> None
HookCallback = Callable[[ParsedMessage, dict[str, Any]], Awaitable[None]]

# 过滤函数签名: def filter(msg: ParsedMessage) -> bool
HookFilter = Callable[[ParsedMessage], bool]

# 支持的事件类型
EVENT_ON_MESSAGE = "on_message"
EVENT_PRE_COMMAND = "pre_command"
EVENT_POST_COMMAND = "post_command"
VALID_EVENTS = {EVENT_ON_MESSAGE, EVENT_PRE_COMMAND, EVENT_POST_COMMAND}


@dataclass
class _HookEntry:
    """内部钩子条目，包含优先级和过滤器"""
    callback: HookCallback
    priority: int = 0
    filter_fn: HookFilter | None = None

    def __hash__(self):
        return id(self.callback)

    def __eq__(self, other):
        if isinstance(other, _HookEntry):
            return self.callback is other.callback
        return NotImplemented


class HookManager:
    """
    事件钩子管理器

    支持注册/注销异步回调，在消息到达时按优先级序执行。
    设计为单例，由 server.py 初始化后注入 CommandContext。

    用法:
        hook_manager = HookManager()
        hook_manager.register("on_message", my_async_handler)
        hook_manager.register("pre_command", auth_check, priority=-10)
        hook_manager.register("post_command", audit_log, priority=100)

        # 在消息分发时:
        await hook_manager.fire("on_message", parsed_msg, context)
    """

    def __init__(self):
        self._hooks: dict[str, list[_HookEntry]] = {}
        self._lock = asyncio.Lock()

    def register(
        self,
        event: str,
        callback: HookCallback,
        priority: int = 0,
        filter_fn: HookFilter | None = None,
    ):
        """
        注册事件钩子

        Args:
            event:     事件名称 ('on_message' | 'pre_command' | 'post_command')
            callback:  异步回调函数
            priority:  优先级 (数值越小越先执行, 默认 0)
            filter_fn: 过滤函数，返回 True 才触发此钩子 (可选)
        """
        if event not in self._hooks:
            self._hooks[event] = []

        # 防止重复注册同一回调
        for entry in self._hooks[event]:
            if entry.callback is callback:
                logger.debug(f"Hook already exists, skipping duplicate registration: {event} -> {callback.__qualname__}")
                return

        entry = _HookEntry(callback=callback, priority=priority, filter_fn=filter_fn)
        self._hooks[event].append(entry)
        # 按优先级排序 (升序)
        self._hooks[event].sort(key=lambda e: e.priority)
        logger.debug(
            f"Hook registered: {event} -> {callback.__qualname__} "
            f"(priority={priority}, filter={'yes' if filter_fn else 'no'})"
        )

    def unregister(self, event: str, callback: HookCallback):
        """注销事件钩子"""
        if event in self._hooks:
            self._hooks[event] = [
                e for e in self._hooks[event] if e.callback is not callback
            ]
            logger.debug(f"Hook unregistered: {event} -> {callback.__qualname__}")

    async def fire(
        self,
        event: str,
        msg: ParsedMessage,
        context: dict[str, Any] | None = None,
    ):
        """
        触发事件钩子

        按优先级顺序依次执行，filter_fn 不匹配的钩子会跳过。
        单个钩子异常不影响后续钩子执行。

        Args:
            event:   事件名称
            msg:     解析后的消息
            context: 附加上下文 (可选)
        """
        entries = self._hooks.get(event, [])
        if not entries:
            return
        ctx = context or {}

        for entry in entries:
            # 过滤器检查
            if entry.filter_fn is not None:
                try:
                    if not entry.filter_fn(msg):
                        continue
                except Exception as e:
                    logger.warning(
                        f"Hook filter exception [{event}] {entry.callback.__qualname__}: {e}"
                    )
                    continue

            # 执行钩子
            try:
                await entry.callback(msg, ctx)
            except Exception as e:
                logger.error(
                    f"Hook execution failed [{event}] {entry.callback.__qualname__}: {e}",
                    exc_info=True,
                )

    def get_registered(self, event: str) -> list[HookCallback]:
        """获取指定事件的所有已注册钩子回调"""
        return [e.callback for e in self._hooks.get(event, [])]

    def get_entries(self, event: str) -> list[_HookEntry]:
        """获取指定事件的所有钩子条目 (含优先级和过滤器)"""
        return list(self._hooks.get(event, []))

    def clear(self, event: str | None = None):
        """清空钩子 (指定事件或全部)"""
        if event:
            self._hooks.pop(event, None)
        else:
            self._hooks.clear()

    def summary(self) -> dict[str, int]:
        """返回各事件的钩子数量摘要"""
        return {event: len(entries) for event, entries in self._hooks.items()}
