"""
消息互联桥单元测试
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from protocols.messages import (
    RawMessage,
    ParsedMessage,
    OutgoingMessage,
    ModuleResponse,
    MessageType,
    MessageSource,
)
from protocols.error_codes import ErrorCode
from state.manager import StateManager
from modules.message_bus import MessageBus, BlacklistFilter, ModuleEnabledFilter


@pytest.fixture
async def state_manager():
    """创建状态管理器"""
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()
        yield manager


@pytest.fixture
async def message_bus(state_manager):
    """创建消息总线实例"""
    bus = MessageBus(state_manager)
    return bus


class TestMessageBusInitialization:
    """测试消息总线初始化"""

    @pytest.mark.asyncio
    async def test_initialize(self, message_bus):
        """测试初始化"""
        assert message_bus.raw_message_queue is not None
        assert message_bus.send_message_queue is not None
        assert message_bus.command_queue is None  # 由外部设置
        assert message_bus.cherrystudio_queue is None  # 由外部设置

    @pytest.mark.asyncio
    async def test_set_queues(self, message_bus):
        """测试设置模块队列"""
        command_queue = asyncio.Queue()
        cherrystudio_queue = asyncio.Queue()

        message_bus.set_command_queue(command_queue)
        message_bus.set_cherrystudio_queue(cherrystudio_queue)

        assert message_bus.command_queue == command_queue
        assert message_bus.cherrystudio_queue == cherrystudio_queue


class TestMessageParsing:
    """测试消息解析"""

    @pytest.mark.asyncio
    async def test_parse_command_with_dot(self, message_bus):
        """测试解析点号开头的命令"""
        raw_msg = RawMessage(
            msg_id="1",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content=".help",
            message_type=MessageType.TEXT,
        )

        parsed = message_bus._parse_message(raw_msg)

        assert parsed.is_command is True
        assert parsed.command_name == "help"
        assert parsed.command_args == ""

    @pytest.mark.asyncio
    async def test_parse_command_with_chinese_dot(self, message_bus):
        """测试解析句号开头的命令"""
        raw_msg = RawMessage(
            msg_id="2",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content="。bot on",
            message_type=MessageType.TEXT,
        )

        parsed = message_bus._parse_message(raw_msg)

        assert parsed.is_command is True
        assert parsed.command_name == "bot"
        assert parsed.command_args == "on"

    @pytest.mark.asyncio
    async def test_parse_command_with_args(self, message_bus):
        """测试解析带参数的命令"""
        raw_msg = RawMessage(
            msg_id="3",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content=".order switch agent_x",
            message_type=MessageType.TEXT,
        )

        parsed = message_bus._parse_message(raw_msg)

        assert parsed.is_command is True
        assert parsed.command_name == "order"
        assert parsed.command_args == "switch agent_x"

    @pytest.mark.asyncio
    async def test_parse_normal_message(self, message_bus):
        """测试解析普通消息"""
        raw_msg = RawMessage(
            msg_id="4",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content="Hello world",
            message_type=MessageType.TEXT,
        )

        parsed = message_bus._parse_message(raw_msg)

        assert parsed.is_command is False
        assert parsed.command_name is None

    @pytest.mark.asyncio
    async def test_parse_command_case_insensitive(self, message_bus):
        """测试命令名称大小写不敏感"""
        raw_msg = RawMessage(
            msg_id="5",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content=".HELP",
            message_type=MessageType.TEXT,
        )

        parsed = message_bus._parse_message(raw_msg)

        assert parsed.command_name == "help"


class TestMessageFilters:
    """测试消息过滤器"""

    @pytest.mark.asyncio
    async def test_blacklist_filter_pass(self, state_manager):
        """测试黑名单过滤器 - 通过"""
        filter = BlacklistFilter(state_manager)
        msg = RawMessage(
            msg_id="1",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content="Test",
            message_type=MessageType.TEXT,
        )

        result = await filter.should_pass(msg)
        assert result is True

    @pytest.mark.asyncio
    async def test_blacklist_filter_blocked(self, state_manager):
        """测试黑名单过滤器 - 拦截"""
        await state_manager.add_to_blacklist("123456")

        filter = BlacklistFilter(state_manager)
        msg = RawMessage(
            msg_id="2",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content="Test",
            message_type=MessageType.TEXT,
        )

        result = await filter.should_pass(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_module_enabled_filter_all_disabled(self, state_manager):
        """测试模块启用过滤器 - 全部禁用"""
        await state_manager.update_module_status("command", False)
        await state_manager.update_module_status("cherrystudio", False)

        filter = ModuleEnabledFilter(state_manager)
        msg = RawMessage(
            msg_id="3",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content="Test",
            message_type=MessageType.TEXT,
        )

        result = await filter.should_pass(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_module_enabled_filter_one_enabled(self, state_manager):
        """测试模块启用过滤器 - 至少一个启用"""
        await state_manager.update_module_status("command", True)
        await state_manager.update_module_status("cherrystudio", False)

        filter = ModuleEnabledFilter(state_manager)
        msg = RawMessage(
            msg_id="4",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content="Test",
            message_type=MessageType.TEXT,
        )

        result = await filter.should_pass(msg)
        assert result is True


class TestMessageRouting:
    """测试消息分发 (非阻塞模型)"""

    @pytest.mark.asyncio
    async def test_route_command_message(self, message_bus, state_manager):
        """测试分发命令消息到 command_queue"""
        command_queue = asyncio.Queue()
        message_bus.set_command_queue(command_queue)

        parsed = ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="123456",
                sender_id="789",
                sender_name="User",
                content=".help",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="help",
        )

        await message_bus._dispatch_message(parsed)

        # 消息应被放入 command_queue
        msg = command_queue.get_nowait()
        assert msg.is_command is True
        assert msg.command_name == "help"

    @pytest.mark.asyncio
    async def test_route_normal_message(self, message_bus, state_manager):
        """测试分发普通消息到 cherrystudio_queue"""
        cherrystudio_queue = asyncio.Queue()
        message_bus.set_cherrystudio_queue(cherrystudio_queue)

        parsed = ParsedMessage(
            raw=RawMessage(
                msg_id="2",
                source=MessageSource.GROUP,
                target_id="123456",
                sender_id="789",
                sender_name="User",
                content="Hello",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        await message_bus._dispatch_message(parsed)

        msg = cherrystudio_queue.get_nowait()
        assert msg.is_command is False

    @pytest.mark.asyncio
    async def test_route_command_module_disabled(self, message_bus, state_manager):
        """测试分发命令消息 - 模块禁用时静默忽略"""
        await state_manager.update_module_status("command", False)
        await state_manager.update_module_status("cherrystudio", False)

        command_queue = asyncio.Queue()
        message_bus.set_command_queue(command_queue)

        parsed = ParsedMessage(
            raw=RawMessage(
                msg_id="3",
                source=MessageSource.GROUP,
                target_id="123456",
                sender_id="789",
                sender_name="User",
                content=".help",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="help",
        )

        await message_bus._dispatch_message(parsed)

        # 模块禁用时队列应为空
        assert command_queue.empty()

    @pytest.mark.asyncio
    async def test_route_cherrystudio_module_disabled(self, message_bus, state_manager):
        """测试分发普通消息 - 模块禁用时静默忽略"""
        await state_manager.update_module_status("cherrystudio", False)

        cherrystudio_queue = asyncio.Queue()
        message_bus.set_cherrystudio_queue(cherrystudio_queue)

        parsed = ParsedMessage(
            raw=RawMessage(
                msg_id="4",
                source=MessageSource.GROUP,
                target_id="123456",
                sender_id="789",
                sender_name="User",
                content="Hello",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        await message_bus._dispatch_message(parsed)

        assert cherrystudio_queue.empty()


class TestSendResponse:
    """测试 send_response 方法"""

    @pytest.mark.asyncio
    async def test_build_success_response(self, message_bus):
        """测试发送成功响应"""
        raw_msg = RawMessage(
            msg_id="msg123",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content=".help",
            message_type=MessageType.TEXT,
        )

        response = ModuleResponse.success_response("Help content")

        await message_bus.send_response(raw_msg, response)

        outgoing = message_bus.send_message_queue.get_nowait()
        assert outgoing.target_source == MessageSource.GROUP
        assert outgoing.target_id == "123456"
        assert outgoing.content == "Help content"
        assert outgoing.reply_to_msg_id == "msg123"

    @pytest.mark.asyncio
    async def test_build_error_response(self, message_bus):
        """测试发送错误响应"""
        raw_msg = RawMessage(
            msg_id="msg456",
            source=MessageSource.GROUP,
            target_id="123456",
            sender_id="789",
            sender_name="User",
            content=".invalid",
            message_type=MessageType.TEXT,
        )

        response = ModuleResponse.error_response(
            ErrorCode.COMMAND_NOT_FOUND.code,
            error_detail="Command 'invalid' not found",
            custom_text="未知命令",
        )

        await message_bus.send_response(raw_msg, response)

        outgoing = message_bus.send_message_queue.get_nowait()
        assert outgoing.content == "未知命令 [BRG-3001]"


class TestFilterManagement:
    """测试过滤器管理"""

    @pytest.mark.asyncio
    async def test_add_filter(self, message_bus):
        """测试添加过滤器"""
        class CustomFilter:
            async def should_pass(self, msg):
                return True

        initial_count = len(message_bus.filters)
        message_bus.add_filter(CustomFilter())

        assert len(message_bus.filters) == initial_count + 1

    @pytest.mark.asyncio
    async def test_remove_filter(self, message_bus):
        """测试移除过滤器"""
        custom_filter = BlacklistFilter(message_bus.state_manager)
        message_bus.add_filter(custom_filter)

        initial_count = len(message_bus.filters)
        message_bus.remove_filter(custom_filter)

        assert len(message_bus.filters) == initial_count - 1


# ======================================================================
# Phase 4A.6: 旁观者消息转发
# ======================================================================

class TestObserverForwarding:
    """测试旁观者消息转发机制"""

    @pytest.fixture
    def make_group_msg(self):
        """创建群消息工厂"""
        def _factory(group_id="g123", sender_id="u001", content="hello"):
            raw = RawMessage(
                msg_id=f"msg_{sender_id}",
                sender_id=sender_id,
                sender_name=f"User_{sender_id}",
                content=content,
                source=MessageSource.GROUP,
                target_id=group_id,
                message_type=MessageType.TEXT,
                timestamp=datetime.now(),
            )
            return ParsedMessage(raw=raw, is_command=False)
        return _factory

    @pytest.mark.asyncio
    async def test_no_observers_no_forward(self, state_manager, make_group_msg):
        """无旁观者时不转发"""
        bus = MessageBus(state_manager=state_manager)
        msg = make_group_msg(group_id="g_no_obs")
        await bus._forward_to_observers(msg)
        assert bus.send_message_queue.empty()

    @pytest.mark.asyncio
    async def test_observer_receives_forward(self, state_manager, make_group_msg):
        """旁观者收到转发消息"""
        # 设置旁观模式
        state_manager.state.ob_groups = {"g_obs"}
        state_manager.state.observers = {"g_obs": {"observer_1"}}

        bus = MessageBus(state_manager=state_manager)
        msg = make_group_msg(group_id="g_obs", sender_id="u001", content="测试消息")
        await bus._forward_to_observers(msg)

        assert not bus.send_message_queue.empty()
        forwarded = await bus.send_message_queue.get()
        assert forwarded.target_id == "observer_1"
        assert forwarded.target_source == MessageSource.PRIVATE
        assert "旁观" in forwarded.content
        assert "g_obs" in forwarded.content
        assert "测试消息" in forwarded.content

    @pytest.mark.asyncio
    async def test_sender_not_forwarded_to_self(self, state_manager, make_group_msg):
        """发送者不会收到自己的旁观转发"""
        state_manager.state.ob_groups = {"g_self"}
        state_manager.state.observers = {"g_self": {"u001"}}  # 发送者自己是旁观者

        bus = MessageBus(state_manager=state_manager)
        msg = make_group_msg(group_id="g_self", sender_id="u001")
        await bus._forward_to_observers(msg)

        assert bus.send_message_queue.empty()

    @pytest.mark.asyncio
    async def test_multiple_observers_all_receive(self, state_manager, make_group_msg):
        """多个旁观者都收到转发"""
        state_manager.state.ob_groups = {"g_multi"}
        state_manager.state.observers = {
            "g_multi": {"obs_a", "obs_b", "obs_c"}
        }

        bus = MessageBus(state_manager=state_manager)
        msg = make_group_msg(
            group_id="g_multi", sender_id="u001", content="全体消息"
        )
        await bus._forward_to_observers(msg)

        forwarded_ids = set()
        while not bus.send_message_queue.empty():
            f = await bus.send_message_queue.get()
            forwarded_ids.add(f.target_id)

        assert forwarded_ids == {"obs_a", "obs_b", "obs_c"}

    @pytest.mark.asyncio
    async def test_ob_group_disabled_no_forward(self, state_manager, make_group_msg):
        """群未开启旁观模式时不转发"""
        state_manager.state.ob_groups = set()  # 空
        state_manager.state.observers = {"g_disabled": {"obs_1"}}

        bus = MessageBus(state_manager=state_manager)
        msg = make_group_msg(group_id="g_disabled")
        await bus._forward_to_observers(msg)

        assert bus.send_message_queue.empty()

    @pytest.mark.asyncio
    async def test_forward_format_includes_sender_name(self, state_manager, make_group_msg):
        """转发消息格式包含发送者名"""
        state_manager.state.ob_groups = {"g_fmt"}
        state_manager.state.observers = {"g_fmt": {"obs_1"}}

        bus = MessageBus(state_manager=state_manager)
        msg = make_group_msg(
            group_id="g_fmt", sender_id="u001", content="格式化测试"
        )
        msg.raw.sender_name = "小明"
        await bus._forward_to_observers(msg)

        forwarded = await bus.send_message_queue.get()
        assert "小明" in forwarded.content
        assert "g_fmt" in forwarded.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
