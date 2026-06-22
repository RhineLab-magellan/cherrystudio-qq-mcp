"""
命令模块单元测试
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import tempfile

from protocols.messages import (
    ParsedMessage,
    RawMessage,
    ModuleResponse,
    MessageType,
    MessageSource,
)
from state.manager import StateManager
from modules.command_module import (
    CommandModule,
    CommandRegistry,
    SessionHandler,
    CommandContext,
    Command,
)
from modules.commands.builtin import (
    HelpCommand,
    BotCommand,
    OrderCommand,
    ModelCommand,
    ObCommand,
    WelcomeCommand,
)


@pytest.fixture
async def state_manager():
    """创建状态管理器"""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()
        yield manager


@pytest.fixture
def command_registry():
    """创建命令注册表"""
    return CommandRegistry()


@pytest.fixture
async def command_module(state_manager):
    """创建命令模块实例"""
    module = CommandModule(state_manager=state_manager)
    await module.initialize()
    return module


class TestCommandRegistry:
    """测试命令注册表"""

    def test_register_command(self, command_registry):
        """测试注册命令"""
        class TestCmd(Command):
            name = "test"
            description = "Test command"

            async def handle(self, args, msg, ctx):
                return "OK"

        cmd = TestCmd()
        command_registry.register(cmd)

        assert command_registry.get("test") is not None
        assert command_registry.get("TEST") is not None  # 大小写不敏感

    def test_unregister_command(self, command_registry):
        """测试注销命令"""
        class TestCmd(Command):
            name = "test"
            description = "Test command"

            async def handle(self, args, msg, ctx):
                return "OK"

        cmd = TestCmd()
        command_registry.register(cmd)
        command_registry.unregister("test")

        assert command_registry.get("test") is None

    def test_list_commands(self, command_registry):
        """测试列出所有命令"""
        class Cmd1(Command):
            name = "alpha"
            description = "First"

            async def handle(self, args, msg, ctx):
                return "OK"

        class Cmd2(Command):
            name = "beta"
            description = "Second"

            async def handle(self, args, msg, ctx):
                return "OK"

        command_registry.register(Cmd1())
        command_registry.register(Cmd2())

        commands = command_registry.list_all()
        assert len(commands) == 2
        assert commands[0].name == "alpha"  # 按名称排序
        assert commands[1].name == "beta"

    def test_discover_builtin(self, command_registry):
        """测试发现内置命令"""
        command_registry.discover_builtin()

        commands = command_registry.list_all()
        assert len(commands) >= 6  # help, bot, order, model, ob, dismiss

        # 检查特定命令是否存在
        assert command_registry.get("help") is not None
        assert command_registry.get("bot") is not None
        assert command_registry.get("order") is not None

    def test_clear_commands(self, command_registry):
        """测试清空所有命令"""
        command_registry.discover_builtin()
        initial_count = len(command_registry.list_all())
        assert initial_count > 0

        command_registry.clear()
        assert len(command_registry.list_all()) == 0


class TestBuiltInCommands:
    """测试内置命令"""

    @pytest.fixture
    def context(self, state_manager):
        """创建命令上下文 (含 command_registry)"""
        from modules.command_module import CommandRegistry
        registry = CommandRegistry()
        registry.discover_builtin()
        return CommandContext(
            state_manager=state_manager,
            command_registry=registry,
        )

    @pytest.fixture
    def group_message(self):
        """创建群消息"""
        return ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="123456789",
                sender_id="987654321",
                sender_name="TestUser",
                content=".help",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="help",
        )

    @pytest.mark.asyncio
    async def test_help_command(self, context, group_message):
        """测试 .help 命令 — 标准结构输出"""
        cmd = HelpCommand()
        result = await cmd.handle("", group_message, context)

        assert result is not None
        assert "----" in result           # 标准结构分隔线
        assert ".help" in result           # 包含 help 命令
        assert "系统" in result or "会话管理" in result  # 包含模块分组

    @pytest.mark.asyncio
    async def test_help_single_command(self, context, group_message):
        """测试 .help <命令名> — 单命令详细帮助"""
        cmd = HelpCommand()
        result = await cmd.handle("bot", group_message, context)

        assert result is not None
        assert "----" in result
        assert "命令：.bot" in result
        assert "会话管理" in result
        assert "on/off" in result

    @pytest.mark.asyncio
    async def test_help_unknown_command(self, context, group_message):
        """测试 .help <不存在> — 提示未找到"""
        cmd = HelpCommand()
        result = await cmd.handle("nonexistent", group_message, context)

        assert result is not None
        assert "未找到命令" in result

    @pytest.mark.asyncio
    async def test_bot_on_command(self, state_manager, group_message):
        """测试 .bot on 命令"""
        context = CommandContext(state_manager=state_manager)
        cmd = BotCommand()

        result = await cmd.handle("on", group_message, context)

        # 结果非空即可 (消息文本可能被 BotSettingConfig 自定义覆盖)
        assert result is not None and len(result) > 0
        assert not state_manager.is_in_blacklist("123456789")

    @pytest.mark.asyncio
    async def test_bot_off_command(self, state_manager, group_message):
        """测试 .bot off 命令"""
        context = CommandContext(state_manager=state_manager)
        cmd = BotCommand()

        result = await cmd.handle("off", group_message, context)

        # 结果非空即可 (消息文本可能被 BotSettingConfig 自定义覆盖)
        assert result is not None and len(result) > 0
        assert state_manager.is_in_blacklist("123456789")

    @pytest.mark.asyncio
    async def test_bot_private_message(self, state_manager):
        """测试 .bot 在私聊中无效"""
        context = CommandContext(state_manager=state_manager)
        private_msg = ParsedMessage(
            raw=RawMessage(
                msg_id="2",
                source=MessageSource.PRIVATE,
                target_id="987654321",
                sender_id="987654321",
                sender_name="User",
                content=".bot on",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="bot",
        )

        cmd = BotCommand()
        result = await cmd.handle("on", private_msg, context)

        assert "仅在群聊中有效" in result

    @pytest.mark.asyncio
    async def test_order_add_command(self, state_manager, group_message):
        """测试 .order add 命令"""
        context = CommandContext(state_manager=state_manager)
        cmd = OrderCommand()

        result = await cmd.handle("add 111222333", group_message, context)

        assert "添加到免@白名单" in result
        assert state_manager.is_in_whitelist("111222333")

    @pytest.mark.asyncio
    async def test_order_remove_command(self, state_manager, group_message):
        """测试 .order remove 命令"""
        await state_manager.add_to_whitelist("111222333")

        context = CommandContext(state_manager=state_manager)
        cmd = OrderCommand()

        result = await cmd.handle("remove 111222333", group_message, context)

        assert "从免@白名单移除" in result
        assert not state_manager.is_in_whitelist("111222333")

    @pytest.mark.asyncio
    async def test_order_list_command(self, state_manager, group_message):
        """测试 .order list 命令"""
        await state_manager.add_to_whitelist("111222333")
        await state_manager.add_to_whitelist("444555666")

        context = CommandContext(state_manager=state_manager)
        cmd = OrderCommand()

        result = await cmd.handle("list", group_message, context)

        assert "免@群列表" in result
        assert "111222333" in result
        assert "444555666" in result

    @pytest.mark.asyncio
    async def test_model_change_command(self, state_manager, group_message):
        """测试 .model change 命令 (持久化到 saved_models)"""
        context = CommandContext(state_manager=state_manager, config={"admin_qq": "987654321"})
        cmd = ModelCommand()

        result = await cmd.handle("change gpt-4", group_message, context)

        assert "已切换到模型" in result
        assert "持久化" in result
        # 验证写入 saved_models 而非 active_agents
        model = await state_manager.get_saved_model(group_message.session_key)
        assert model == "gpt-4"

    @pytest.mark.asyncio
    async def test_model_status_command(self, state_manager, group_message):
        """测试 .model status 命令 (读取 saved_models)"""
        await state_manager.set_saved_model(group_message.session_key, "claude-3")

        context = CommandContext(state_manager=state_manager)
        cmd = ModelCommand()

        result = await cmd.handle("status", group_message, context)

        assert "当前模型" in result
        assert "claude-3" in result

    @pytest.mark.asyncio
    async def test_model_status_default(self, state_manager, group_message):
        """测试 .model status 未设置偏好时显示默认"""
        context = CommandContext(state_manager=state_manager)
        cmd = ModelCommand()

        result = await cmd.handle("status", group_message, context)

        assert "默认模型" in result

    @pytest.mark.asyncio
    async def test_model_reset_command(self, state_manager, group_message):
        """测试 .model reset 命令"""
        await state_manager.set_saved_model(group_message.session_key, "gpt-4")

        context = CommandContext(state_manager=state_manager, config={"admin_qq": "987654321"})
        cmd = ModelCommand()

        result = await cmd.handle("reset", group_message, context)

        assert "清除模型偏好" in result
        model = await state_manager.get_saved_model(group_message.session_key)
        assert model is None

    @pytest.mark.asyncio
    async def test_model_list_command(self, state_manager, group_message):
        """测试 .model list 命令"""
        context = CommandContext(state_manager=state_manager)
        cmd = ModelCommand()

        result = await cmd.handle("list", group_message, context)

        # 测试环境中可能没有 llm_providers 配置，两种响应都接受
        assert ("可用模型" in result or "未配置模型列表" in result)
        assert ".model change" in result or ".model reset" in result

    @pytest.mark.asyncio
    async def test_ob_join_command(self, state_manager, group_message):
        """测试 .ob join 命令"""
        context = CommandContext(state_manager=state_manager)
        cmd = ObCommand()

        result = await cmd.handle("join", group_message, context)

        assert "已加入旁观者模式" in result
        user_id = group_message.raw.sender_id
        assert user_id in state_manager.state.observers.get("123456789", set())

    @pytest.mark.asyncio
    async def test_ob_exit_command(self, state_manager, group_message):
        """测试 .ob exit 命令"""
        user_id = group_message.raw.sender_id
        state_manager.state.observers["123456789"] = {user_id}

        context = CommandContext(state_manager=state_manager)
        cmd = ObCommand()

        result = await cmd.handle("exit", group_message, context)

        assert "已退出旁观者模式" in result
        assert user_id not in state_manager.state.observers.get(
            "123456789", set())


class TestSessionHandler:
    """测试会话处理器"""

    @pytest.mark.asyncio
    async def test_session_handler_lifecycle(self, state_manager):
        """测试会话处理器生命周期"""
        registry = CommandRegistry()
        registry.discover_builtin()

        send_queue = asyncio.Queue()
        context = CommandContext(state_manager=state_manager, send_queue=send_queue)

        handler = SessionHandler(
            session_key="group_123",
            registry=registry,
            context=context,
        )

        await handler.start()
        assert handler._running is True
        assert handler._task is not None

        await handler.stop()
        assert handler._running is False

    @pytest.mark.asyncio
    async def test_session_handler_execute_command(self, state_manager):
        """测试会话处理器执行命令"""
        registry = CommandRegistry()
        registry.discover_builtin()

        send_queue = asyncio.Queue()
        context = CommandContext(
            state_manager=state_manager,
            send_queue=send_queue,
            command_registry=registry,
        )

        handler = SessionHandler(
            session_key="group_456",
            registry=registry,
            context=context,
        )

        # 添加帮助命令消息
        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="456",
                sender_id="789",
                sender_name="User",
                content=".help",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="help",
        )

        await handler.start()
        await handler.add_message(msg)

        # 等待 OutgoingMessage 响应 (SessionHandler 直接推送到 send_queue)
        outgoing = await asyncio.wait_for(send_queue.get(), timeout=5.0)

        assert outgoing.content is not None
        assert "----" in outgoing.content  # 标准结构分隔线
        assert ".help" in outgoing.content

        await handler.stop()

    @pytest.mark.asyncio
    async def test_session_handler_invalid_command(self, state_manager):
        """测试会话处理器处理无效命令"""
        registry = CommandRegistry()
        registry.discover_builtin()

        send_queue = asyncio.Queue()
        context = CommandContext(state_manager=state_manager, send_queue=send_queue)

        handler = SessionHandler(
            session_key="group_789",
            registry=registry,
            context=context,
        )

        # 添加无效命令消息
        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="2",
                source=MessageSource.GROUP,
                target_id="789",
                sender_id="123",
                sender_name="User",
                content=".invalid_cmd",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="invalid_cmd",
        )

        await handler.start()
        await handler.add_message(msg)

        # 等待响应
        outgoing = await asyncio.wait_for(send_queue.get(), timeout=5.0)

        # 无效命令应返回错误响应 (包含错误码 BRG-3001)
        assert "BRG-3001" in outgoing.content or "未知命令" in outgoing.content

        await handler.stop()


class TestCommandModule:
    """测试命令模块"""

    @pytest.mark.asyncio
    async def test_initialize(self, command_module):
        """测试初始化"""
        commands = command_module.registry.list_all()
        assert len(commands) >= 8

    @pytest.mark.asyncio
    async def test_get_command_list(self, command_module):
        """测试获取命令列表"""
        cmd_list = command_module.get_command_list()

        assert len(cmd_list) >= 8
        assert any(cmd["name"] == "help" for cmd in cmd_list)
        assert any(cmd["name"] == "bot" for cmd in cmd_list)
        assert any(cmd["name"] == "send" for cmd in cmd_list)
        assert any(cmd["name"] == "master" for cmd in cmd_list)

    @pytest.mark.asyncio
    async def test_reload_config(self, command_module):
        """测试热重载配置"""
        initial_count = len(command_module.registry.list_all())

        await command_module.reload_config()

        new_count = len(command_module.registry.list_all())
        assert new_count == initial_count

    @pytest.mark.asyncio
    async def test_session_management(self, command_module):
        """测试会话管理"""
        # 模拟接收消息
        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="999",
                sender_id="888",
                sender_name="User",
                content=".help",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="help",
        )

        # 启动模块 (短时间运行)
        task = asyncio.create_task(command_module.start())
        await asyncio.sleep(0.2)

        # 发送消息
        await command_module.queue.put(msg)
        await asyncio.sleep(0.3)

        # 检查会话是否创建
        assert "group_999" in command_module.session_handlers

        # 停止模块
        await command_module.stop()

        # 取消任务并等待
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 验证会话已清理
        assert len(command_module.session_handlers) == 0


class TestOrderAgentCommands:
    """测试 .order 命令的 Agent 管理功能"""

    def _make_context(self, state_manager, cherrystudio_module=None):
        """创建带 cherrystudio_module 的命令上下文"""
        return CommandContext(
            state_manager=state_manager,
            cherrystudio_module=cherrystudio_module,
        )

    def _make_group_msg(self, target_id="123456789"):
        return ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id=target_id,
                sender_id="987654321",
                sender_name="TestUser",
                content=".order",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="order",
        )

    def _make_mock_cs_module(self, discovered_agents=None, session_handlers=None):
        """创建 Mock CherryStudioModule"""
        mock = MagicMock()
        mock.discovered_agents = discovered_agents or {}
        mock.session_handlers = session_handlers or {}
        mock.agent_id = "agent_default"
        mock.rebuild_session = AsyncMock()
        return mock

    @pytest.mark.asyncio
    async def test_order_help(self, state_manager):
        """.order help 应显示帮助信息"""
        ctx = self._make_context(state_manager)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("help", msg, ctx)
        assert "切换" in result
        assert "列表" in result
        assert "重建" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_order_no_args_shows_help(self, state_manager):
        """.order 无参数应显示帮助"""
        ctx = self._make_context(state_manager)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("", msg, ctx)
        assert "子命令" in result

    @pytest.mark.asyncio
    async def test_order_list_agents_with_discovered(self, state_manager):
        """.order 列表 应显示自动发现的 Agent"""
        mock_cs = self._make_mock_cs_module(discovered_agents={
            "AgentA": {"agent_id": "agent_001", "work_dirs": []},
            "AgentB": {"agent_id": "agent_002", "work_dirs": []},
        })
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("列表", msg, ctx)
        assert "AgentA" in result
        assert "AgentB" in result
        assert "切换指令" in result

    @pytest.mark.asyncio
    async def test_order_list_agents_empty(self, state_manager):
        """.order 列表 无自动发现时应显示默认 Agent"""
        mock_cs = self._make_mock_cs_module(discovered_agents={})
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("列表", msg, ctx)
        assert "agent_default" in result

    @pytest.mark.asyncio
    async def test_order_list_agents_no_module(self, state_manager):
        """.order 列表 无 CherryStudio 模块时应提示"""
        ctx = self._make_context(state_manager, None)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("列表", msg, ctx)
        assert "未就绪" in result

    @pytest.mark.asyncio
    async def test_order_switch_agent(self, state_manager):
        """.order 切换 AgentA 应切换成功并持久化"""
        mock_cs = self._make_mock_cs_module(discovered_agents={
            "AgentA": {"agent_id": "agent_001", "work_dirs": []},
        })
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("切换 AgentA", msg, ctx)
        assert "已切换到 Agent「AgentA」" in result
        assert "持久化" in result

        # 验证活跃 Agent 已持久化到 StateManager
        active = await state_manager.get_active_agent(msg.session_key)
        assert active == "AgentA"

    @pytest.mark.asyncio
    async def test_order_switch_agent_not_found(self, state_manager):
        """.order 切换 不存在的 Agent 应提示"""
        mock_cs = self._make_mock_cs_module(discovered_agents={
            "AgentA": {"agent_id": "agent_001", "work_dirs": []},
        })
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("切换 NonExistent", msg, ctx)
        assert "未找到" in result
        assert "AgentA" in result

    @pytest.mark.asyncio
    async def test_order_switch_no_name_lists_agents(self, state_manager):
        """.order 切换 (无名称) 应列出可用 Agent"""
        mock_cs = self._make_mock_cs_module(discovered_agents={
            "AgentA": {"agent_id": "agent_001", "work_dirs": []},
        })
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("切换", msg, ctx)
        assert "AgentA" in result

    @pytest.mark.asyncio
    async def test_order_switch_rebuilds_existing_session(self, state_manager):
        """.order 切换 应重建现有会话"""
        mock_handler = MagicMock()
        mock_cs = self._make_mock_cs_module(
            discovered_agents={"AgentB": {"agent_id": "b1", "work_dirs": []}},
            session_handlers={"group_123456789": mock_handler},
        )
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("switch AgentB", msg, ctx)
        assert "已切换到 Agent「AgentB」" in result
        mock_cs.rebuild_session.assert_called_once_with("group_123456789")

    @pytest.mark.asyncio
    async def test_order_rebuild_session(self, state_manager):
        """.order 重建 应重建活跃会话"""
        mock_handler = MagicMock()
        mock_cs = self._make_mock_cs_module(
            session_handlers={"group_123456789": mock_handler}
        )
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("重建", msg, ctx)
        assert "会话已重建" in result
        mock_cs.rebuild_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_order_rebuild_no_session(self, state_manager):
        """.order 重建 无活跃会话时应提示"""
        mock_cs = self._make_mock_cs_module(session_handlers={})
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("rebuild", msg, ctx)
        assert "无需重建" in result

    @pytest.mark.asyncio
    async def test_order_status(self, state_manager):
        """.order status 应显示会话状态 (含模型偏好)"""
        mock_cs = self._make_mock_cs_module(discovered_agents={
            "A1": {"agent_id": "a1", "work_dirs": []},
        })
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("status", msg, ctx)
        assert "会话状态" in result
        assert "group_123456789" in result
        assert "模型偏好" in result  # 新增: 显示模型偏好行

    @pytest.mark.asyncio
    async def test_order_status_with_handler(self, state_manager):
        """.order status 有活跃 handler 时应显示详细信息"""
        from modules.cherrystudio_module import SessionData

        mock_handler = MagicMock()
        mock_handler.session_data = SessionData("group_123456789", "TestAgent")
        mock_handler.session_data.session_id = "sess_abc123_long_id_here"

        mock_cs = self._make_mock_cs_module(
            session_handlers={"group_123456789": mock_handler}
        )
        # 设置持久化模型偏好
        await state_manager.set_saved_model("group_123456789", "gpt-4-turbo")
        ctx = self._make_context(state_manager, mock_cs)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("状态", msg, ctx)
        assert "活跃" in result
        assert "TestAgent" in result
        assert "gpt-4-turbo" in result  # 显示持久化的模型偏好
        assert "sess_abc123_long_id_here" in result[:100] or "sess_abc123" in result

    @pytest.mark.asyncio
    async def test_order_whitelist_still_works(self, state_manager):
        """.order list/add/remove 白名单功能应继续正常工作"""
        ctx = self._make_context(state_manager)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        # add
        result = await cmd.handle("add 999888777", msg, ctx)
        assert "添加到免@白名单" in result
        assert state_manager.is_in_whitelist("999888777")

        # list
        result = await cmd.handle("list", msg, ctx)
        assert "免@群列表" in result
        assert "999888777" in result

        # remove
        result = await cmd.handle("remove 999888777", msg, ctx)
        assert "从免@白名单移除" in result
        assert not state_manager.is_in_whitelist("999888777")

    @pytest.mark.asyncio
    async def test_order_unknown_subcommand(self, state_manager):
        """.order 未知子命令应提示帮助"""
        ctx = self._make_context(state_manager)
        cmd = OrderCommand()
        msg = self._make_group_msg()

        result = await cmd.handle("foobar", msg, ctx)
        assert "未知子命令" in result
        assert "子命令" in result


class TestWelcomeCommand:
    """测试 .welcome 命令"""

    def _make_context(self, state_manager):
        return CommandContext(state_manager=state_manager)

    def _make_group_msg(self, target_id="group_123"):
        return ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id=target_id,
                sender_id="user_1",
                sender_name="Admin",
                content=".welcome",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="welcome",
        )

    def _make_private_msg(self):
        return ParsedMessage(
            raw=RawMessage(
                msg_id="2",
                source=MessageSource.PRIVATE,
                target_id="friend_1",
                sender_id="user_1",
                sender_name="Admin",
                content=".welcome",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="welcome",
        )

    @pytest.mark.asyncio
    async def test_private_chat_rejected(self, state_manager):
        """私聊中应拒绝"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_private_msg()
        result = await cmd.handle("open", msg, ctx)
        assert "仅在群聊" in result

    @pytest.mark.asyncio
    async def test_open(self, state_manager):
        """开启欢迎"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        result = await cmd.handle("open", msg, ctx)
        assert "已开启" in result
        entry = state_manager.get_welcome("group_123")
        assert entry["enabled"] is True

    @pytest.mark.asyncio
    async def test_close(self, state_manager):
        """关闭欢迎"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        await cmd.handle("open", msg, ctx)
        result = await cmd.handle("close", msg, ctx)
        assert "已关闭" in result
        entry = state_manager.get_welcome("group_123")
        assert entry["enabled"] is False

    @pytest.mark.asyncio
    async def test_set_message(self, state_manager):
        """设置欢迎语"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        result = await cmd.handle("set 欢迎来到{at}的群！", msg, ctx)
        assert "已设置" in result
        assert "欢迎来到{at}的群！" in result
        entry = state_manager.get_welcome("group_123")
        assert entry["message"] == "欢迎来到{at}的群！"

    @pytest.mark.asyncio
    async def test_set_empty_rejected(self, state_manager):
        """空欢迎语应拒绝"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        result = await cmd.handle("set", msg, ctx)
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_status(self, state_manager):
        """查看状态"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        await cmd.handle("open", msg, ctx)
        await cmd.handle("set 你好新人", msg, ctx)
        result = await cmd.handle("status", msg, ctx)
        assert "已开启" in result
        assert "你好新人" in result

    @pytest.mark.asyncio
    async def test_status_default(self, state_manager):
        """默认状态"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        result = await cmd.handle("status", msg, ctx)
        assert "已关闭" in result

    @pytest.mark.asyncio
    async def test_help(self, state_manager):
        """帮助信息"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        result = await cmd.handle("help", msg, ctx)
        assert "open" in result
        assert "close" in result
        assert "set" in result

    @pytest.mark.asyncio
    async def test_empty_args_shows_help(self, state_manager):
        """无参数显示帮助"""
        ctx = self._make_context(state_manager)
        cmd = WelcomeCommand()
        msg = self._make_group_msg()
        result = await cmd.handle("", msg, ctx)
        assert "子命令" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
