"""
命令模块 (CommandModule)

职责:
1. 命令解析与分发
2. 会话管理 (按群/私聊独立任务)
3. 热重载支持
4. 长等待超时处理 (5分钟)
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from protocols.messages import (
    ParsedMessage,
    ModuleResponse,
    OutgoingMessage,
    MessageSource,
    MessageType,
)
from protocols.error_codes import ErrorCode, BridgeError
from state.manager import StateManager

logger = logging.getLogger(__name__)


class Command:
    """
    命令基类

    子类需设置 name 和 description，实现 handle() 方法。

    Attributes:
        name:        命令名称 (不含 . 前缀)
        description: 一行简短说明
        group:       所属模块分组 (用于 .help 分组显示)
        usage:       使用规范 (如 ".bot on/off")
        reminder:    帮助文本中的特殊提醒 (可选)
    """
    name: str = ""
    description: str = ""
    group: str = "其他"
    usage: str = ""
    reminder: str = ""

    async def handle(self, args: str, msg: ParsedMessage, ctx: "CommandContext") -> str | None:
        """
        处理命令

        Args:
            args: 命令参数
            msg: 解析后的消息
            ctx: 命令上下文

        Returns:
            回复文本或 None (不回复)
        """
        raise NotImplementedError


class CommandContext:
    """
    命令上下文

    提供命令执行所需的依赖注入。
    """

    def __init__(
        self,
        state_manager: StateManager,
        napcat_bridge: Any = None,
        config: dict | None = None,
        send_queue: asyncio.Queue[OutgoingMessage] | None = None,
        command_registry: "CommandRegistry | None" = None,
        cherrystudio_module: Any = None,  # CherryStudioModule 引用 (供 .order 命令使用)
        hook_manager: Any = None,  # HookManager 引用 (供 pre/post_command 钩子使用)
    ):
        self.state_manager = state_manager
        self.napcat_bridge = napcat_bridge
        self.config = config or {}
        self.send_queue = send_queue
        self.command_registry = command_registry
        self.cherrystudio_module = cherrystudio_module
        self.hook_manager = hook_manager


class CommandRegistry:
    """
    命令注册表

    自动发现和注册命令，支持热重载。
    """

    def __init__(self):
        self._commands: dict[str, Command] = {}
        self._loaded = False

    def register(self, command: Command):
        """注册命令"""
        if not command.name:
            raise ValueError("命令名称不能为空")
        self._commands[command.name.lower()] = command
        logger.info(f"Registered command: .{command.name}")

    def unregister(self, name: str):
        """注销命令"""
        self._commands.pop(name.lower(), None)

    def get(self, name: str) -> Command | None:
        """获取命令"""
        return self._commands.get(name.lower())

    def list_all(self) -> list[Command]:
        """列出所有命令"""
        return sorted(self._commands.values(), key=lambda c: c.name)

    def clear(self):
        """清空所有命令"""
        self._commands.clear()
        self._loaded = False

    def discover_builtin(self):
        """发现并注册内置命令"""
        from modules.commands import (
            # 内置管理命令
            HelpCommand, BotCommand, OrderCommand, ModelCommand,
            ObCommand, DismissCommand, SendCommand, MasterCommand, WelcomeCommand,
            # 骰子命令 (dice_core)
            RDiceCommand, RhCommand, RaCommand, ShowCommand,
            DelCommand, PcCommand, NnCommand, StCommand,
            # 行于泰拉命令 (ark_trpg)
            RkCommand, RkbCommand, RkpCommand, SckCommand, ArkCommand, SnCommand,
            # 日志命令
            LogCommand,
        )

        all_commands = [
            HelpCommand, BotCommand, OrderCommand, ModelCommand,
            ObCommand, DismissCommand, SendCommand, MasterCommand, WelcomeCommand,
            RDiceCommand, RhCommand, RaCommand, ShowCommand,
            DelCommand, PcCommand, NnCommand, StCommand,
            RkCommand, RkbCommand, RkpCommand, SckCommand, ArkCommand, SnCommand,
            LogCommand,
        ]

        for cmd_class in all_commands:
            self.register(cmd_class())

        self._loaded = True
        logger.info(f"Built-in commands loaded: {len(self._commands)}")


class SessionHandler:
    """
    会话处理器

    为每个会话 (群/私聊) 创建独立的异步任务，
    处理该会话的所有命令消息。
    处理完成后直接将 OutgoingMessage 推送到 send_queue。
    """

    def __init__(
        self,
        session_key: str,
        registry: CommandRegistry,
        context: CommandContext,
    ):
        self.session_key = session_key
        self.registry = registry
        self.context = context

        # 消息队列
        self.message_queue: asyncio.Queue[ParsedMessage] = asyncio.Queue()

        # 运行状态
        self._running = False
        self._task: asyncio.Task | None = None

        # 超时配置 (5分钟)
        self.timeout = 300

    async def start(self):
        """启动会话处理器"""
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.debug(f"Session handler started: {self.session_key}")

    async def stop(self):
        """停止会话处理器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.debug(f"Session handler stopped: {self.session_key}")

    async def add_message(self, msg: ParsedMessage):
        """添加消息到队列"""
        await self.message_queue.put(msg)

    async def _run(self):
        """主循环"""
        while self._running:
            try:
                # 等待消息 (带超时)
                msg = await asyncio.wait_for(
                    self.message_queue.get(),
                    timeout=self.timeout
                )

                # 执行命令
                response = await self._execute_command(msg)

                # 直接构建 OutgoingMessage 并发送到 send_queue
                if response and (response.content or response.error_code):
                    if self.context.send_queue is not None:
                        outgoing = OutgoingMessage(
                            target_source=msg.raw.source,
                            target_id=msg.raw.target_id,
                            content=response.user_message,
                            message_type=MessageType.TEXT,
                            reply_to_msg_id=msg.raw.msg_id,
                            metadata={
                                "success": response.success,
                                "error_code": response.error_code,
                            },
                            skip_doc=True,
                        )
                        await self.context.send_queue.put(outgoing)
                    else:
                        logger.warning(
                            f"send_queue not set, cannot send response [{self.session_key}]")

            except asyncio.TimeoutError:
                # 超时，清理会话
                logger.info(f"Session timed out, cleanup: {self.session_key}")
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Session handler exception [{self.session_key}]: {e}", exc_info=True)

    async def _execute_command(self, msg: ParsedMessage) -> ModuleResponse:
        """
        执行命令

        Args:
            msg: 解析后的消息

        Returns:
            模块响应
        """
        if not msg.command_name:
            return ModuleResponse.error_response(
                ErrorCode.COMMAND_NOT_FOUND.code,
                error_detail="命令名称为空",
                custom_text="未知命令",
            )

        # 获取命令
        cmd = self.registry.get(msg.command_name)
        if cmd is None:
            return ModuleResponse.error_response(
                ErrorCode.COMMAND_NOT_FOUND.code,
                error_detail=f"命令 '{msg.command_name}' 不存在",
                custom_text="未知命令",
            )

        try:
            # 触发 pre_command 钩子
            if self.context.hook_manager:
                await self.context.hook_manager.fire(
                    "pre_command", msg,
                    {"command_name": msg.command_name, "command_args": msg.command_args},
                )

            # 执行命令
            result = await cmd.handle(msg.command_args or "", msg, self.context)

            # 触发 post_command 钩子
            if self.context.hook_manager:
                await self.context.hook_manager.fire(
                    "post_command", msg,
                    {
                        "command_name": msg.command_name,
                        "command_args": msg.command_args,
                        "result": result,
                    },
                )

            if result is None:
                # 命令执行成功但不需要回复
                return ModuleResponse.success_response("")
            else:
                return ModuleResponse.success_response(result)

        except BridgeError as e:
            # 桥接错误
            return ModuleResponse.error_response(
                e.error_code,
                error_detail=e.detail,
                custom_text=e.custom_text,
            )
        except Exception as e:
            # 其他异常
            logger.error(f"Command execution failed [{msg.command_name}]: {e}", exc_info=True)
            return ModuleResponse.error_response(
                ErrorCode.COMMAND_EXECUTION_FAILED.code,
                error_detail=str(e),
                custom_text="命令执行失败",
            )


class CommandModule:
    """
    命令模块

    核心功能:
    1. 接收解析后的命令消息
    2. 为每个会话创建独立的 SessionHandler
    3. 路由消息到对应的会话处理器
    4. 收集响应并返回
    5. 支持热重载
    """

    def __init__(
        self,
        state_manager: StateManager,
        napcat_bridge: Any = None,
        config: dict | None = None,
    ):
        """
        初始化命令模块

        Args:
            state_manager: 状态管理器
            napcat_bridge: NapCat 互联桥 (可选)
            config: 配置字典 (可选)
        """
        self.state_manager = state_manager
        self.napcat_bridge = napcat_bridge
        self.config = config or {}

        # 命令注册表
        self.registry = CommandRegistry()

        # CherryStudioModule 引用 (由 server._connect_queues() 设置)
        self.cherrystudio_module: Any = None

        # 命令上下文
        self.context = CommandContext(
            state_manager=state_manager,
            napcat_bridge=napcat_bridge,
            config=config,
            command_registry=self.registry,
        )

        # 消息队列 (来自 MessageBus)
        self.queue: asyncio.Queue[ParsedMessage] = asyncio.Queue()

        # 会话处理器
        self.session_handlers: dict[str, SessionHandler] = {}

        # 运行状态
        self._running = False

    async def initialize(self):
        """初始化命令模块"""
        # 加载内置命令
        self.registry.discover_builtin()
        logger.info(f"CommandModule initialized: {len(self.registry.list_all())} commands")

    async def start(self):
        """启动命令模块"""
        self._running = True
        logger.info("CommandModule started")

        while self._running:
            try:
                # 从队列获取消息
                msg = await self.queue.get()

                # 获取或创建会话处理器
                session_key = msg.session_key
                if session_key not in self.session_handlers:
                    handler = SessionHandler(
                        session_key=session_key,
                        registry=self.registry,
                        context=self.context,
                    )
                    self.session_handlers[session_key] = handler
                    await handler.start()

                # 添加消息到会话队列
                await self.session_handlers[session_key].add_message(msg)

            except Exception as e:
                logger.error(f"CommandModule message handling failed: {e}", exc_info=True)

    async def stop(self):
        """停止命令模块"""
        self._running = False

        # 停止所有会话处理器
        for handler in self.session_handlers.values():
            await handler.stop()

        self.session_handlers.clear()
        logger.info("CommandModule stopped")

    async def reload_config(self):
        """
        热重载配置

        重新加载命令注册表和配置。
        """
        logger.info("Hot-reloading CommandModule...")

        # 清空现有命令
        self.registry.clear()

        # 重新加载内置命令
        self.registry.discover_builtin()

        # 更新上下文
        self.context = CommandContext(
            state_manager=self.state_manager,
            napcat_bridge=self.napcat_bridge,
            config=self.config,
            send_queue=self.context.send_queue,
            command_registry=self.registry,
            cherrystudio_module=self.cherrystudio_module,
        )

        logger.info(f"CommandModule hot-reload complete: {len(self.registry.list_all())} commands")

    def get_command_list(self) -> list[dict]:
        """
        获取命令列表 (用于 .help 命令)

        Returns:
            命令信息列表
        """
        return [
            {
                "name": cmd.name,
                "description": cmd.description,
                "reminder": cmd.reminder,
            }
            for cmd in self.registry.list_all()
        ]
