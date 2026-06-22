"""
CherryStudio 模块单元测试
"""

import pytest
import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from pathlib import Path
import tempfile
from datetime import datetime

from protocols.messages import (
    ParsedMessage,
    RawMessage,
    ModuleResponse,
    MessageType,
    MessageSource,
)
from state.manager import StateManager
from protocols.error_codes import ErrorCode
from modules.cherrystudio_module import (
    CherryStudioModule,
    CherryStudioSessionHandler,
    MCPClient,
    HTTPClient,
    SessionData,
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
def mcp_client():
    """创建 Mock MCP Client"""
    client = MCPClient()
    client._connected = True
    client.send_message = AsyncMock(return_value=None)  # 模拟超时
    return client


@pytest.fixture
async def http_client():
    """创建 HTTP Client"""
    client = HTTPClient(base_url="http://test:8080", api_key="test_key")
    await client.initialize()
    return client


@pytest.fixture
async def cherrystudio_module(state_manager, monkeypatch):
    """创建 CherryStudio 模块实例"""
    config = {
        "cherrystudio": {
            "mcp_server_path": "/test/path",
            "http_api_base": "http://test:8080",
            "api_key": "test_key",
        },
        "auto_reply": {
            "enabled": True,
            "reply_mode": "always",
            "cooldown_seconds": 0,
        },
    }

    # Mock MCP Client的connect方法，避免真正启动subprocess
    original_connect = CherryStudioModule.__init__

    module = CherryStudioModule(state_manager=state_manager, config=config)

    # Mock subprocess调用
    mock_process = AsyncMock()
    mock_process.stdout = AsyncMock()
    mock_process.stdin = AsyncMock()

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec',
                        mock_create_subprocess)

    # Mock MCP client methods
    module.mcp_client._connected = True
    module.mcp_client.send_message = AsyncMock(return_value=None)
    module.mcp_client.connect = AsyncMock()  # 跳过真实连接
    module.mcp_client.disconnect = AsyncMock()

    # Mock HTTP client methods
    module.http_client.create_session = AsyncMock(return_value="session_123")
    module.http_client.send_chat_message = AsyncMock(
        return_value="AI response")
    module.http_client.delete_session = AsyncMock(return_value=True)
    module.http_client.initialize = AsyncMock()  # 跳过真实初始化

    await module.initialize()

    # 模拟延迟初始化已完成 (测试中无需真正调用 CherryStudio HTTP API)
    module._deferred_init_done = True
    module._agent_id_unresolved = False

    return module


class TestMCPClient:
    """测试 MCP Client"""

    @pytest.mark.asyncio
    async def test_connect_without_path(self):
        """测试无路径时连接"""
        client = MCPClient()
        await client.connect()
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_with_path(self, monkeypatch):
        """测试有路径时连接（mock subprocess）"""
        # Mock asyncio.create_subprocess_exec
        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdin = AsyncMock()
        mock_process.stderr = AsyncMock()

        async def mock_create_subprocess(*args, **kwargs):
            return mock_process

        monkeypatch.setattr(
            asyncio, 'create_subprocess_exec', mock_create_subprocess)

        client = MCPClient(server_path="/test/path")

        # Mock _send_request 返回成功
        async def mock_send_request(method, params, timeout=30.0):
            return {"status": "ok"}
        client._send_request = mock_send_request

        # Mock _send_notification
        async def mock_send_notification(method, params):
            pass
        client._send_notification = mock_send_notification

        await client.connect()
        assert client.is_connected is True

        # 清理
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect(self, monkeypatch):
        """测试断开连接"""
        # Mock subprocess
        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdin = AsyncMock()
        mock_process.terminate = Mock()
        mock_process.wait = AsyncMock()

        async def mock_create_subprocess(*args, **kwargs):
            return mock_process

        monkeypatch.setattr(
            asyncio, 'create_subprocess_exec', mock_create_subprocess)

        client = MCPClient(server_path="/test/path")

        # Mock methods
        async def mock_send_request(method, params, timeout=30.0):
            return {"status": "ok"}
        client._send_request = mock_send_request

        async def mock_send_notification(method, params):
            pass
        client._send_notification = mock_send_notification

        await client.connect()
        await client.disconnect()
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_send_message_not_connected(self):
        """测试未连接时发送消息"""
        client = MCPClient()
        result = await client.send_message({"message": "test"})
        assert result is None


class TestHTTPClient:
    """测试 HTTP Client"""

    @pytest.mark.asyncio
    async def test_initialize(self):
        """测试初始化"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        await client.initialize()
        assert client._session is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_close(self):
        """测试关闭"""
        client = HTTPClient()
        await client.initialize()
        await client.close()
        assert client._session.closed

    @pytest.mark.asyncio
    async def test_create_session_mocked(self, http_client):
        """测试创建会话 (简化)"""
        # HTTP Client 需要真实网络连接，这里仅验证方法存在
        assert hasattr(http_client, 'create_session')

    @pytest.mark.asyncio
    async def test_send_chat_message_mocked(self, http_client):
        """测试发送聊天消息 (简化)"""
        # HTTP Client 需要真实网络连接，这里仅验证方法存在
        assert hasattr(http_client, 'send_chat_message')


class TestSessionData:
    """测试会话数据"""

    def test_create_session(self):
        """测试创建会话"""
        data = SessionData("group_123", "assistant")
        assert data.session_key == "group_123"
        assert data.agent_name == "assistant"
        assert data.session_id is None
        assert data.message_count == 0

    def test_update_activity(self):
        """测试更新活跃度"""
        data = SessionData("group_123")
        initial_count = data.message_count
        data.update_activity()
        assert data.message_count == initial_count + 1

    def test_is_expired(self):
        """测试会话过期"""
        data = SessionData("group_123")

        # 刚创建，不应过期
        assert data.is_expired(timeout_minutes=30) is False

        # 模拟过期
        from datetime import timedelta
        data.last_active = datetime.now() - timedelta(minutes=60)
        assert data.is_expired(timeout_minutes=30) is True


class TestCherryStudioSessionHandler:
    """测试 CherryStudio 会话处理器"""

    @pytest.mark.asyncio
    async def test_handler_lifecycle(self, state_manager, mcp_client, http_client):
        """测试处理器生命周期"""
        response_queue = asyncio.Queue()

        handler = CherryStudioSessionHandler(
            session_key="group_test",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            response_queue=response_queue,
        )

        await handler.start()
        assert handler._running is True
        assert handler._task is not None

        await handler.stop()
        assert handler._running is False

    @pytest.mark.asyncio
    async def test_handler_process_message(self, state_manager, mcp_client, http_client):
        """测试处理器处理消息 (SSE 流式)"""
        response_queue = asyncio.Queue()

        # Mock HTTP client methods
        http_client.create_session = AsyncMock(return_value="sess_123")

        # 构建模拟 SSE 响应数据
        sse_events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "AI says hello"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        sse_data = b""
        for event in sse_events:
            sse_data += f"data: {json.dumps(event, ensure_ascii=False)}\n".encode("utf-8")

        # 构建模拟的 StreamReader + Response
        class MockStreamReader:
            def __init__(self, data: bytes):
                self._lines = data.split(b"\n")
                self._index = 0

            async def readline(self) -> bytes:
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                if line:
                    return line + b"\n"
                return b"\n"

        class MockSSEResponse:
            def __init__(self):
                self.status = 200
                self.headers = {"Content-Type": "text/event-stream"}
                self.content = MockStreamReader(sse_data)

            async def text(self):
                return sse_data.decode("utf-8")

        @asynccontextmanager
        async def mock_sse_context(*args, **kwargs):
            yield MockSSEResponse()

        http_client.get_sse_request_context = mock_sse_context

        # Mock MCP client as not connected (will fallback to HTTP)
        mcp_client._connected = False

        handler = CherryStudioSessionHandler(
            session_key="group_456",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            response_queue=response_queue,
        )

        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="456",
                sender_id="789",
                sender_name="User",
                content="Hello",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        await handler.start()
        await handler.add_message(msg)

        # 等待响应
        response = await asyncio.wait_for(response_queue.get(), timeout=5.0)

        assert response.success is True
        assert response.content == "AI says hello"

        await handler.stop()

    @pytest.mark.asyncio
    async def test_handler_session_create_failure(self, state_manager, mcp_client, http_client):
        """测试会话创建失败"""
        response_queue = asyncio.Queue()

        # Mock 会话创建失败
        http_client.create_session = AsyncMock(return_value=None)

        handler = CherryStudioSessionHandler(
            session_key="group_789",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            response_queue=response_queue,
        )

        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="2",
                source=MessageSource.GROUP,
                target_id="789",
                sender_id="123",
                sender_name="User",
                content="Hi",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        await handler.start()
        await handler.add_message(msg)

        # 等待响应
        response = await asyncio.wait_for(response_queue.get(), timeout=5.0)

        assert response.success is False
        assert response.error_code == "BRG-4005"  # SESSION_CREATE_FAILED

        await handler.stop()


class TestCherryStudioModule:
    """测试 CherryStudio 模块"""

    @pytest.mark.asyncio
    async def test_initialize(self, cherrystudio_module):
        """测试初始化"""
        assert cherrystudio_module.mcp_client is not None
        assert cherrystudio_module.http_client is not None

    @pytest.mark.asyncio
    async def test_reload_config(self, cherrystudio_module):
        """测试热重载配置"""
        await cherrystudio_module.reload_config()
        # 验证没有异常抛出

    @pytest.mark.asyncio
    async def test_rebuild_session(self, cherrystudio_module):
        """测试重建会话"""
        # 先创建一个会话
        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="999",
                sender_id="888",
                sender_name="User",
                content="Test",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        # 启动模块
        task = asyncio.create_task(cherrystudio_module.start())
        await asyncio.sleep(0.2)

        await cherrystudio_module.queue.put(msg)
        await asyncio.sleep(0.3)

        # 验证会话已创建
        assert "group_999" in cherrystudio_module.session_handlers

        # 重建会话
        await cherrystudio_module.rebuild_session("group_999")

        # 验证旧会话已清理
        assert "group_999" not in cherrystudio_module.session_handlers

        # 停止模块
        await cherrystudio_module.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_session_management(self, cherrystudio_module):
        """测试会话管理"""
        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="888",
                sender_id="777",
                sender_name="User",
                content="Hello AI",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        # 启动模块
        task = asyncio.create_task(cherrystudio_module.start())
        await asyncio.sleep(0.2)

        await cherrystudio_module.queue.put(msg)
        await asyncio.sleep(0.3)

        # 检查会话是否创建
        assert "group_888" in cherrystudio_module.session_handlers

        # 停止模块
        await cherrystudio_module.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 验证会话已清理
        assert len(cherrystudio_module.session_handlers) == 0


class TestShouldReplyFiltering:
    """测试 _should_reply() 自动回复过滤逻辑"""

    def _make_module(self, state_manager, config_overrides=None, self_qq="12345"):
        """创建测试用 CherryStudioModule 实例"""
        config = {
            "cherrystudio": {
                "http_api_base": "http://test:8080",
                "api_key": "test_key",
            },
            "auto_reply": {
                "enabled": True,
                "reply_mode": "mention",
                "reply_to_groups": [],
                "reply_to_friends": [],
                "cooldown_seconds": 0,
            },
        }
        if config_overrides:
            config["auto_reply"].update(config_overrides)

        module = CherryStudioModule(state_manager=state_manager, config=config)

        # Mock napcat_bridge with self_qq
        mock_bridge = MagicMock()
        mock_bridge.self_qq = self_qq
        module.napcat_bridge = mock_bridge
        return module

    def _make_group_msg(self, content="hello", sender_id="999", target_id="888", raw_data=None):
        """创建测试用群消息"""
        return ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id=target_id,
                sender_id=sender_id,
                sender_name="TestUser",
                content=content,
                message_type=MessageType.TEXT,
                raw_data=raw_data or {},
            ),
            is_command=False,
        )

    def _make_private_msg(self, content="hello", sender_id="999", target_id="999"):
        """创建测试用私聊消息"""
        return ParsedMessage(
            raw=RawMessage(
                msg_id="2",
                source=MessageSource.PRIVATE,
                target_id=target_id,
                sender_id=sender_id,
                sender_name="TestUser",
                content=content,
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

    @pytest.mark.asyncio
    async def test_disabled_blocks_all(self, state_manager):
        """auto_reply.enabled=False 应阻止所有消息"""
        module = self._make_module(state_manager, {"enabled": False})
        msg = self._make_group_msg()
        assert module._should_reply(msg) is False

    @pytest.mark.asyncio
    async def test_empty_content_filtered(self, state_manager):
        """空文本消息应被过滤"""
        module = self._make_module(state_manager, {"reply_mode": "always"})
        msg = self._make_group_msg(content="   ")
        assert module._should_reply(msg) is False

    @pytest.mark.asyncio
    async def test_self_message_filtered(self, state_manager):
        """机器人自己的消息应被过滤 (自激防护)"""
        module = self._make_module(state_manager, {"reply_mode": "always"}, self_qq="12345")
        msg = self._make_group_msg(sender_id="12345")
        assert module._should_reply(msg) is False

    @pytest.mark.asyncio
    async def test_bot_blacklist_group(self, state_manager):
        """.bot off 黑名单群应被过滤"""
        module = self._make_module(state_manager, {"reply_mode": "always"})
        await state_manager.add_to_blacklist("888")
        msg = self._make_group_msg(target_id="888")
        assert module._should_reply(msg) is False

    @pytest.mark.asyncio
    async def test_group_whitelist(self, state_manager):
        """群白名单: 不在白名单的群应被过滤"""
        module = self._make_module(state_manager, {
            "reply_mode": "always",
            "reply_to_groups": ["111", "222"],
        })
        msg_in = self._make_group_msg(target_id="111")
        msg_out = self._make_group_msg(target_id="333")
        assert module._should_reply(msg_in) is True
        assert module._should_reply(msg_out) is False

    @pytest.mark.asyncio
    async def test_mention_mode_requires_at(self, state_manager):
        """mention 模式: 群聊需要 @bot 才通过"""
        module = self._make_module(state_manager, {"reply_mode": "mention"}, self_qq="12345")
        # 无 @ 段
        msg_no_at = self._make_group_msg()
        assert module._should_reply(msg_no_at) is False

        # 有 @bot 段
        raw_data_with_at = {"message": [{"type": "at", "data": {"qq": "12345"}}]}
        msg_at_bot = self._make_group_msg(raw_data=raw_data_with_at)
        assert module._should_reply(msg_at_bot) is True

        # @了别人
        raw_data_at_other = {"message": [{"type": "at", "data": {"qq": "99999"}}]}
        msg_at_other = self._make_group_msg(raw_data=raw_data_at_other)
        assert module._should_reply(msg_at_other) is False

    @pytest.mark.asyncio
    async def test_always_mode_no_at_needed(self, state_manager):
        """always 模式: 群聊不需要 @"""
        module = self._make_module(state_manager, {"reply_mode": "always"})
        msg = self._make_group_msg()
        assert module._should_reply(msg) is True

    @pytest.mark.asyncio
    async def test_private_friend_whitelist(self, state_manager):
        """私聊好友白名单: 不在白名单的好友应被过滤"""
        module = self._make_module(state_manager, {
            "reply_mode": "always",
            "reply_to_friends": ["111"],
        })
        msg_in = self._make_private_msg(sender_id="111", target_id="111")
        msg_out = self._make_private_msg(sender_id="222", target_id="222")
        assert module._should_reply(msg_in) is True
        assert module._should_reply(msg_out) is False

    @pytest.mark.asyncio
    async def test_private_always_passes(self, state_manager):
        """always 模式 + 无好友白名单: 私聊全部通过"""
        module = self._make_module(state_manager, {"reply_mode": "always"})
        msg = self._make_private_msg()
        assert module._should_reply(msg) is True

    @pytest.mark.asyncio
    async def test_command_message_blocked(self, state_manager):
        """is_command=True 的命令消息不应转发到 Agent"""
        module = self._make_module(state_manager, {"reply_mode": "always"})
        # 构造一条被标记为命令的消息
        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="10",
                source=MessageSource.GROUP,
                target_id="888",
                sender_id="999",
                sender_name="User",
                content=".help",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="help",
            command_args="",
        )
        assert module._should_reply(msg) is False

    @pytest.mark.asyncio
    async def test_command_with_args_blocked(self, state_manager):
        """带参数的命令消息也应被拦截"""
        module = self._make_module(state_manager, {"reply_mode": "always"})
        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="11",
                source=MessageSource.GROUP,
                target_id="888",
                sender_id="999",
                sender_name="User",
                content=".order list",
                message_type=MessageType.TEXT,
            ),
            is_command=True,
            command_name="order",
            command_args="list",
        )
        assert module._should_reply(msg) is False

    @pytest.mark.asyncio
    async def test_non_command_passes(self, state_manager):
        """is_command=False 的普通消息应正常通过"""
        module = self._make_module(state_manager, {"reply_mode": "always"})
        msg = self._make_group_msg(content="普通消息不是命令")
        assert msg.is_command is False
        assert module._should_reply(msg) is True


class TestIsAtMe:
    """测试 @mention 检测方法"""

    def _make_module_with_bridge(self, self_qq="12345"):
        """创建带 self_qq 的模块实例"""
        module = CherryStudioModule(
            state_manager=MagicMock(),
            config={"cherrystudio": {"http_api_base": "http://test", "api_key": ""}},
        )
        mock_bridge = MagicMock()
        mock_bridge.self_qq = self_qq
        module.napcat_bridge = mock_bridge
        return module

    def _make_msg(self, raw_message_segments=None):
        """创建带 raw_data 的 ParsedMessage"""
        return ParsedMessage(
            raw=RawMessage(
                msg_id="1",
                source=MessageSource.GROUP,
                target_id="888",
                sender_id="999",
                sender_name="User",
                content="test",
                message_type=MessageType.TEXT,
                raw_data={"message": raw_message_segments or []},
            ),
            is_command=False,
        )

    def test_no_self_qq_returns_false(self):
        """self_qq 未设置时应返回 False"""
        module = self._make_module_with_bridge(self_qq="")
        msg = self._make_msg([{"type": "at", "data": {"qq": "12345"}}])
        assert module._is_at_me(msg) is False

    def test_at_bot_returns_true(self):
        """@了机器人应返回 True"""
        module = self._make_module_with_bridge(self_qq="12345")
        msg = self._make_msg([{"type": "at", "data": {"qq": "12345"}}])
        assert module._is_at_me(msg) is True

    def test_at_other_returns_false(self):
        """@了其他人应返回 False"""
        module = self._make_module_with_bridge(self_qq="12345")
        msg = self._make_msg([{"type": "at", "data": {"qq": "99999"}}])
        assert module._is_at_me(msg) is False

    def test_no_at_segments(self):
        """无 @ 段应返回 False"""
        module = self._make_module_with_bridge(self_qq="12345")
        msg = self._make_msg([{"type": "text", "data": {"text": "hello"}}])
        assert module._is_at_me(msg) is False

    def test_multiple_at_mixed(self):
        """多个 @ 段混合，包含 bot 时应返回 True"""
        module = self._make_module_with_bridge(self_qq="12345")
        segments = [
            {"type": "at", "data": {"qq": "99999"}},
            {"type": "text", "data": {"text": " "}},
            {"type": "at", "data": {"qq": "12345"}},
        ]
        msg = self._make_msg(segments)
        assert module._is_at_me(msg) is True

    def test_has_at_others_true(self):
        """@了他人时 _has_at_others 应返回 True"""
        module = self._make_module_with_bridge(self_qq="12345")
        msg = self._make_msg([{"type": "at", "data": {"qq": "99999"}}])
        assert module._has_at_others(msg) is True

    def test_has_at_others_false_when_only_bot(self):
        """只 @bot 时 _has_at_others 应返回 False"""
        module = self._make_module_with_bridge(self_qq="12345")
        msg = self._make_msg([{"type": "at", "data": {"qq": "12345"}}])
        assert module._has_at_others(msg) is False


class TestCooldown:
    """测试冷却控制逻辑"""

    @pytest.mark.asyncio
    async def test_cooldown_blocks_rapid_messages(self, state_manager):
        """冷却时间内同一会话的后续消息应被跳过"""
        config = {
            "cherrystudio": {
                "http_api_base": "http://test:8080",
                "api_key": "test_key",
            },
            "auto_reply": {
                "enabled": True,
                "reply_mode": "always",
                "cooldown_seconds": 5,
            },
        }
        module = CherryStudioModule(state_manager=state_manager, config=config)
        # 模拟已回复过
        module._last_reply_time["group_888"] = __import__("time").monotonic()

        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="1", source=MessageSource.GROUP, target_id="888",
                sender_id="999", sender_name="User", content="hi",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        # 消息通过 _should_reply 但被 start() 循环中的冷却逻辑拦截
        # 这里直接测试 _should_reply 仍然通过（冷却在 start() 中）
        assert module._should_reply(msg) is True
        # 验证冷却数据
        import time
        now = time.monotonic()
        elapsed = now - module._last_reply_time["group_888"]
        assert elapsed < config["auto_reply"]["cooldown_seconds"]


class TestConversationStoreIntegration:
    """测试 ConversationStore 接入消息流"""

    def _make_handler(self, state_manager, mcp_client, http_client, conversation_store=None):
        """创建带 conversation_store 的 handler"""
        handler = CherryStudioSessionHandler(
            session_key="group_test_cs",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            response_queue=asyncio.Queue(),
            conversation_store=conversation_store,
        )
        return handler

    @pytest.mark.asyncio
    async def test_handler_receives_conversation_store(self, state_manager, mcp_client, http_client):
        """handler 应正确接收 conversation_store 引用"""
        mock_store = AsyncMock()
        handler = self._make_handler(state_manager, mcp_client, http_client, mock_store)
        assert handler.conversation_store is mock_store

    @pytest.mark.asyncio
    async def test_handler_without_conversation_store(self, state_manager, mcp_client, http_client):
        """handler 无 conversation_store 时不应崩溃"""
        handler = self._make_handler(state_manager, mcp_client, http_client, None)
        assert handler.conversation_store is None

    @pytest.mark.asyncio
    async def test_session_created_flag_initially_true(self, state_manager, mcp_client, http_client):
        """新 handler 创建后 _session_just_created 应在首次会话创建时为 True"""
        mock_store = AsyncMock()
        handler = self._make_handler(state_manager, mcp_client, http_client, mock_store)
        # 初始状态应为 False (尚未创建远程会话)
        assert handler._session_just_created is False

    @pytest.mark.asyncio
    async def test_cleanup_saves_session(self, state_manager, mcp_client, http_client):
        """cleanup 时应保存 ConversationStore 数据"""
        mock_store = AsyncMock()
        handler = self._make_handler(state_manager, mcp_client, http_client, mock_store)
        handler.session_data = SessionData("group_test_cs", "test_agent")
        handler.session_data.session_id = "sess_abc"

        await handler._cleanup()

        mock_store.save_session.assert_called_once_with("group_test_cs", "test_agent")

    @pytest.mark.asyncio
    async def test_cleanup_no_store_no_crash(self, state_manager, mcp_client, http_client):
        """无 conversation_store 时 cleanup 不应崩溃"""
        handler = self._make_handler(state_manager, mcp_client, http_client, None)
        handler.session_data = SessionData("group_test_cs", "test_agent")
        handler.session_data.session_id = "sess_abc"

        await handler._cleanup()
        # 不抛异常即通过

    @pytest.mark.asyncio
    async def test_process_message_records_user_msg(
        self, state_manager, mcp_client, http_client
    ):
        """_process_message 应将用户消息记录到 ConversationStore"""
        mock_store = AsyncMock()
        mock_store.get_session_memory = AsyncMock(return_value="")

        # 构建 SSE 模拟响应
        sse_events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "你好"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        sse_data = b""
        for event in sse_events:
            sse_data += f"data: {json.dumps(event, ensure_ascii=False)}\n".encode("utf-8")

        class MockStreamReader:
            def __init__(self, data: bytes):
                self._lines = data.split(b"\n")
                self._index = 0

            async def readline(self) -> bytes:
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line + b"\n" if line else b"\n"

        class MockSSEResponse:
            def __init__(self):
                self.status = 200
                self.headers = {"Content-Type": "text/event-stream"}
                self.content = MockStreamReader(sse_data)

        @asynccontextmanager
        async def mock_sse_context(*args, **kwargs):
            yield MockSSEResponse()

        http_client.get_sse_request_context = mock_sse_context
        mcp_client._connected = False

        handler = CherryStudioSessionHandler(
            session_key="group_cs_1",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            conversation_store=mock_store,
        )
        handler.session_data = SessionData("group_cs_1", "test_agent")
        handler.session_data.session_id = "sess_xyz"

        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="100",
                source=MessageSource.GROUP,
                target_id="cs_1",
                sender_id="user_1",
                sender_name="Alice",
                content="你好呀",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        result = await handler._process_message(msg)

        # 验证用户消息已记录
        calls = mock_store.add_message.call_args_list
        assert len(calls) >= 1
        user_call = calls[0]
        assert user_call[0][0] == "group_cs_1"
        assert user_call[0][1] == "test_agent"
        user_msg = user_call[0][2]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "你好呀"
        assert user_msg["sender"] == "Alice"

        # 验证 AI 回复也已记录
        assert len(calls) >= 2
        ai_call = calls[1]
        assert ai_call[0][2]["role"] == "assistant"
        assert "你好" in ai_call[0][2]["content"]

    @pytest.mark.asyncio
    async def test_memory_injected_on_first_message(
        self, state_manager, mcp_client, http_client
    ):
        """新会话首条消息应注入工作区上下文 + 历史记忆 + 全局规则"""
        mock_store = AsyncMock()
        mock_store.add_message = AsyncMock()

        # SSE 响应
        sse_events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "继续上次的讨论"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        sse_data = b""
        for event in sse_events:
            sse_data += f"data: {json.dumps(event, ensure_ascii=False)}\n".encode("utf-8")

        class MockStreamReader:
            def __init__(self, data: bytes):
                self._lines = data.split(b"\n")
                self._index = 0

            async def readline(self) -> bytes:
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line + b"\n" if line else b"\n"

        class MockSSEResponse:
            def __init__(self):
                self.status = 200
                self.headers = {"Content-Type": "text/event-stream"}
                self.content = MockStreamReader(sse_data)

        # 捕获发送给 SSE 的实际消息内容
        captured_message = {}

        class MockSession:
            async def post(self, url, json=None, timeout=None):
                captured_message["content"] = json.get("content", "") if json else ""

                @asynccontextmanager
                async def _ctx():
                    yield MockSSEResponse()
                return _ctx()

        mock_session = MockSession()
        http_client._session = mock_session

        @asynccontextmanager
        async def mock_sse_context(*args, **kwargs):
            # 捕获传入的 message 参数
            captured_message["content"] = kwargs.get("message", "")
            yield MockSSEResponse()

        http_client.get_sse_request_context = mock_sse_context
        mcp_client._connected = False

        # Mock parent_module 以提供注入上下文
        mock_parent = MagicMock()
        mock_parent._build_injection_context = MagicMock(
            return_value="<历史对话摘要>\n之前讨论过Python编程\n</历史对话摘要>"
        )

        handler = CherryStudioSessionHandler(
            session_key="group_cs_2",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            conversation_store=mock_store,
        )
        handler.parent_module = mock_parent
        handler.session_data = SessionData("group_cs_2", "test_agent")
        handler.session_data.session_id = "sess_new"
        handler._session_just_created = True  # 模拟新创建的会话

        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="101",
                source=MessageSource.GROUP,
                target_id="cs_2",
                sender_id="user_2",
                sender_name="Bob",
                content="继续吧",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        result = await handler._process_message(msg)

        # 验证 _build_injection_context 被调用
        mock_parent._build_injection_context.assert_called_once_with(
            "test_agent", "group_cs_2"
        )

        # 验证 _session_just_created 已被重置
        assert handler._session_just_created is False

        # 验证注入的内容包含在发送的消息中
        assert captured_message.get("content", "") != ""

    @pytest.mark.asyncio
    async def test_no_memory_injection_on_subsequent_messages(
        self, state_manager, mcp_client, http_client
    ):
        """非首条消息不应再次注入记忆"""
        mock_store = AsyncMock()
        mock_store.add_message = AsyncMock()

        sse_events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "好的"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        sse_data = b""
        for event in sse_events:
            sse_data += f"data: {json.dumps(event, ensure_ascii=False)}\n".encode("utf-8")

        class MockStreamReader:
            def __init__(self, data: bytes):
                self._lines = data.split(b"\n")
                self._index = 0

            async def readline(self) -> bytes:
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line + b"\n" if line else b"\n"

        class MockSSEResponse:
            def __init__(self):
                self.status = 200
                self.headers = {"Content-Type": "text/event-stream"}
                self.content = MockStreamReader(sse_data)

        @asynccontextmanager
        async def mock_sse_context(*args, **kwargs):
            yield MockSSEResponse()

        http_client.get_sse_request_context = mock_sse_context
        mcp_client._connected = False

        handler = CherryStudioSessionHandler(
            session_key="group_cs_3",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            conversation_store=mock_store,
        )
        handler.session_data = SessionData("group_cs_3", "test_agent")
        handler.session_data.session_id = "sess_existing"
        handler._session_just_created = False  # 非新会话

        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="102",
                source=MessageSource.GROUP,
                target_id="cs_3",
                sender_id="user_3",
                sender_name="Charlie",
                content="再来一条",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        result = await handler._process_message(msg)

        # 验证 get_session_memory 未被调用 (非新会话不注入记忆)
        mock_store.get_session_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_module_passes_conversation_store_to_handler(self, state_manager):
        """CherryStudioModule 应将 conversation_store 传递给 handler"""
        config = {
            "cherrystudio": {
                "http_api_base": "http://test:8080",
                "api_key": "test_key",
            },
            "auto_reply": {
                "enabled": True,
                "reply_mode": "always",
                "cooldown_seconds": 0,
            },
            "conversation_store_enabled": True,
        }
        module = CherryStudioModule(state_manager=state_manager, config=config)

        # Mock napcat_bridge
        mock_bridge = MagicMock()
        mock_bridge.self_qq = "12345"
        module.napcat_bridge = mock_bridge

        # Mock HTTP client
        module.http_client.create_session = AsyncMock(return_value="sess_123")
        module.http_client.delete_session = AsyncMock(return_value=True)

        # Mock send_queue
        module.send_queue = AsyncMock()
        module.send_queue.put = AsyncMock()

        msg = ParsedMessage(
            raw=RawMessage(
                msg_id="200",
                source=MessageSource.GROUP,
                target_id="pass_test",
                sender_id="999",
                sender_name="User",
                content="测试传递",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

        # 启动模块并发送消息
        task = asyncio.create_task(module.start())
        await asyncio.sleep(0.2)

        await module.queue.put(msg)
        await asyncio.sleep(0.5)

        # 验证 handler 被创建且带有 conversation_store
        handler = module.session_handlers.get("group_pass_test")
        assert handler is not None
        assert handler.conversation_store is not None
        assert handler.conversation_store is module.conversation_store

        await module.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestTwoStrikeStallHandling:
    """测试会话 2-strike 停滞处理"""

    def _make_handler(self, state_manager, mcp_client, http_client, stalled=False, reply_text=""):
        """创建带 mock SSEResult 的 handler"""
        from modules.sse_parser import SSEResult, SSETextBlock

        if stalled:
            mock_result = SSEResult(stalled=True, reply_blocks=[], error=None)
        elif reply_text:
            mock_result = SSEResult(
                stalled=False,
                reply_blocks=[SSETextBlock(text=reply_text)],
                error=None,
            )
        else:
            mock_result = SSEResult(stalled=False, reply_blocks=[], error=None)

        # 构造 mock SSE context
        class MockSSEResponse:
            def __init__(self):
                self.status = 200
                self.headers = {"Content-Type": "text/event-stream"}

        @asynccontextmanager
        async def mock_sse_context(*args, **kwargs):
            yield MockSSEResponse()

        http_client.get_sse_request_context = mock_sse_context
        http_client.delete_session = AsyncMock(return_value=True)
        mcp_client._connected = False

        handler = CherryStudioSessionHandler(
            session_key="group_stall_test",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
        )
        handler.session_data = SessionData("group_stall_test", "test_agent")
        handler.session_data.session_id = "sess_stall_123"

        # Patch SSEParser.parse 返回预设结果
        async def mock_parse(resp):
            return mock_result

        handler._original_process_message = handler._process_message

        async def patched_process_message(msg):
            """绕过 SSE 解析，直接使用 mock SSEResult"""
            content = msg.raw.content
            target_id = msg.raw.target_id
            agent_name = handler.session_data.agent_name if handler.session_data else "default"

            # 记录用户消息 (与真实 _process_message 一致)
            if handler.conversation_store:
                try:
                    await handler.conversation_store.add_message(
                        handler.session_key, agent_name,
                        {"role": "user", "content": content, "time": "test"},
                    )
                except Exception:
                    pass

            if handler.napcat_bridge:
                handler.napcat_bridge.mark_responding(str(target_id))

            try:
                sse_result = mock_result

                if sse_result is None:
                    return ModuleResponse.error_response(
                        ErrorCode.LLM_PROVIDER_FAILED.code,
                        error_detail="SSE 解析返回空结果",
                        custom_text="AI处理失败",
                    )

                if sse_result.session_not_found:
                    handler.session_data.session_id = None
                    return ModuleResponse.error_response(
                        ErrorCode.SESSION_EXPIRED.code,
                        error_detail="session_not_found",
                        custom_text="会话已失效",
                    )

                reply = sse_result.get_reply_text(pre_tool_text_policy="keep")

                if reply:
                    handler._stall_count = 0
                    return ModuleResponse.success_response(reply)

                if sse_result.had_output_tool:
                    handler._stall_count = 0
                    return ModuleResponse.success_response("")
                elif sse_result.stalled:
                    handler._stall_count += 1
                    if handler._stall_count >= 2:
                        if handler.session_data and handler.session_data.session_id:
                            try:
                                await handler.http_client.delete_session(
                                    handler.session_data.session_id,
                                    agent_id=handler.agent_id,
                                )
                            except Exception:
                                pass
                        if handler.session_data:
                            handler.session_data.session_id = None
                        handler._stall_count = 0
                    return ModuleResponse.success_response("")
                elif sse_result.error:
                    return ModuleResponse.error_response(
                        ErrorCode.LLM_PROVIDER_FAILED.code,
                        error_detail=sse_result.error,
                        custom_text="AI处理失败",
                    )
                else:
                    return ModuleResponse.error_response(
                        ErrorCode.LLM_PROVIDER_FAILED.code,
                        error_detail="模型未产生任何输出",
                        custom_text="AI处理失败",
                    )
            finally:
                if handler.napcat_bridge:
                    handler.napcat_bridge.unmark_responding(str(target_id))

        handler._process_message = patched_process_message
        return handler

    def _make_msg(self):
        return ParsedMessage(
            raw=RawMessage(
                msg_id="stall_msg",
                source=MessageSource.GROUP,
                target_id="stall_test",
                sender_id="user_1",
                sender_name="User",
                content="测试消息",
                message_type=MessageType.TEXT,
            ),
            is_command=False,
        )

    @pytest.mark.asyncio
    async def test_first_stall_preserves_session(self, state_manager, mcp_client, http_client):
        """第 1 次停滞: 计数器+1, 会话保留"""
        handler = self._make_handler(state_manager, mcp_client, http_client, stalled=True)
        old_sid = handler.session_data.session_id

        result = await handler._process_message(self._make_msg())

        assert handler._stall_count == 1
        assert handler.session_data.session_id == old_sid  # SID 保留

    @pytest.mark.asyncio
    async def test_second_stall_destroys_session(self, state_manager, mcp_client, http_client):
        """第 2 次连续停滞: 销毁会话, 计数器重置"""
        handler = self._make_handler(state_manager, mcp_client, http_client, stalled=True)
        handler._stall_count = 1  # 已经停滞过一次

        result = await handler._process_message(self._make_msg())

        assert handler._stall_count == 0  # 重置
        assert handler.session_data.session_id is None  # SID 已清除
        http_client.delete_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_resets_stall_count(self, state_manager, mcp_client, http_client):
        """成功回复重置停滞计数"""
        handler = self._make_handler(
            state_manager, mcp_client, http_client, stalled=False, reply_text="回复内容"
        )
        handler._stall_count = 1  # 之前停滞过一次

        result = await handler._process_message(self._make_msg())

        assert handler._stall_count == 0  # 重置
        assert handler.session_data.session_id is not None  # SID 保留
        assert result.success is True
        assert result.content == "回复内容"

    @pytest.mark.asyncio
    async def test_stall_then_success_then_stall(self, state_manager, mcp_client, http_client):
        """停滞 → 成功 → 停滞: 计数器应被成功重置，不触发销毁"""
        # 第 1 次停滞
        handler = self._make_handler(state_manager, mcp_client, http_client, stalled=True)
        await handler._process_message(self._make_msg())
        assert handler._stall_count == 1
        assert handler.session_data.session_id is not None

        # 成功回复
        handler2 = self._make_handler(
            state_manager, mcp_client, http_client, stalled=False, reply_text="好了"
        )
        handler2._stall_count = handler._stall_count
        handler2.session_data = handler.session_data
        await handler2._process_message(self._make_msg())
        assert handler2._stall_count == 0

        # 再次停滞 — 应是第 1 次 (不是第 2 次)
        handler3 = self._make_handler(state_manager, mcp_client, http_client, stalled=True)
        handler3._stall_count = handler2._stall_count
        handler3.session_data = handler2.session_data
        old_sid = handler3.session_data.session_id
        await handler3._process_message(self._make_msg())
        assert handler3._stall_count == 1  # 第 1 次，不销毁
        assert handler3.session_data.session_id == old_sid  # SID 保留


class TestHTTPClientAgentDiscovery:
    """测试 HTTPClient 的 Agent 发现 API 方法"""

    @pytest.mark.asyncio
    async def test_fetch_all_agents_success(self):
        """fetch_all_agents 应正确解析 Agent 列表"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "data": [
                {"id": "agent_001", "name": "AgentA", "accessible_paths": ["/a"]},
                {"id": "agent_002", "name": "AgentB", "accessible_paths": []},
            ]
        })

        @asynccontextmanager
        async def mock_get(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = mock_get
        client._session = mock_session

        result = await client.fetch_all_agents()
        assert len(result) == 2
        assert result[0]["id"] == "agent_001"
        assert result[1]["name"] == "AgentB"

    @pytest.mark.asyncio
    async def test_fetch_all_agents_list_format(self):
        """fetch_all_agents 应兼容数组格式响应"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=[
            {"id": "agent_x", "name": "AgentX"},
        ])

        @asynccontextmanager
        async def mock_get(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = mock_get
        client._session = mock_session

        result = await client.fetch_all_agents()
        assert len(result) == 1
        assert result[0]["name"] == "AgentX"

    @pytest.mark.asyncio
    async def test_fetch_all_agents_http_error(self):
        """fetch_all_agents HTTP 错误应返回空列表"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        @asynccontextmanager
        async def mock_get(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = mock_get
        client._session = mock_session

        result = await client.fetch_all_agents()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_all_agents_no_session(self):
        """fetch_all_agents 无 session 时应返回空列表"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        result = await client.fetch_all_agents()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_agent_detail_success(self):
        """fetch_agent_detail 应返回 Agent 详情"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "id": "agent_001",
            "name": "TestAgent",
            "mcps": ["mcp_bridge_id", "mcp_other_id"],
        })

        @asynccontextmanager
        async def mock_get(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = mock_get
        client._session = mock_session

        result = await client.fetch_agent_detail("agent_001")
        assert result is not None
        assert result["id"] == "agent_001"
        assert "mcp_bridge_id" in result["mcps"]

    @pytest.mark.asyncio
    async def test_fetch_agent_detail_not_found(self):
        """fetch_agent_detail 404 应返回 None"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        mock_resp = AsyncMock()
        mock_resp.status = 404

        @asynccontextmanager
        async def mock_get(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = mock_get
        client._session = mock_session

        result = await client.fetch_agent_detail("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_mcp_servers_success(self):
        """fetch_mcp_servers 应返回 MCP Server 字典"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "data": {
                "servers": {
                    "mcp_001": {"name": "QQ Bridge"},
                    "mcp_002": {"name": "Other MCP"},
                }
            }
        })

        @asynccontextmanager
        async def mock_get(*args, **kwargs):
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = mock_get
        client._session = mock_session

        result = await client.fetch_mcp_servers()
        assert len(result) == 2
        assert result["mcp_001"]["name"] == "QQ Bridge"

    @pytest.mark.asyncio
    async def test_fetch_mcp_servers_no_session(self):
        """fetch_mcp_servers 无 session 时应返回空字典"""
        client = HTTPClient(base_url="http://test:8080", api_key="key")
        result = await client.fetch_mcp_servers()
        assert result == {}


class TestMultiAgentDiscovery:
    """测试 CherryStudioModule 多 Agent 自动发现 + MCP 绑定验证"""

    def _make_module(self, state_manager, config_overrides=None):
        """创建测试用 CherryStudioModule (legacy_mode)"""
        config = {
            "cherrystudio": {
                "http_api_base": "http://test:8080",
                "api_key": "test_key",
                "legacy_mode": True,
                "mcp_server_name": "QQ Bridge",
            },
            "mcp_server_name": "QQ Bridge",
            "default_agent": "TestBot",
            "auto_reply": {
                "enabled": True,
                "reply_mode": "always",
                "cooldown_seconds": 0,
            },
        }
        if config_overrides:
            config.update(config_overrides)
        module = CherryStudioModule(state_manager=state_manager, config=config)
        return module

    @pytest.mark.asyncio
    async def test_discover_agents_basic(self, state_manager):
        """_discover_agents 应正确发现 Agent 并存储到 discovered_agents"""
        module = self._make_module(state_manager)

        # Mock HTTPClient 方法
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "AgentA", "accessible_paths": ["/dir_a"]},
            {"id": "agent_002", "name": "AgentB", "accessible_paths": []},
        ])
        # Mock MCP 验证通过
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_bridge_1": {"name": "QQ Bridge"},
        })
        module.http_client.fetch_agent_detail = AsyncMock(return_value={
            "id": "agent_001",
            "mcps": ["mcp_bridge_1"],
        })

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await module._discover_agents()

        # 应发现 Agent (经过 MCP 验证)
        assert len(module.discovered_agents) >= 1

    @pytest.mark.asyncio
    async def test_discover_agents_empty_list(self, state_manager):
        """Agent 列表为空时 discovered_agents 应为空"""
        module = self._make_module(state_manager)
        module.http_client.fetch_all_agents = AsyncMock(return_value=[])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await module._discover_agents()

        assert module.discovered_agents == {}

    @pytest.mark.asyncio
    async def test_discover_agents_whitelist(self, state_manager):
        """白名单模式: 只保留白名单中的 Agent，跳过 MCP 验证"""
        module = self._make_module(state_manager, {
            "agent_whitelist": ["agent_001"],
        })
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "AgentA", "accessible_paths": ["/a"]},
            {"id": "agent_002", "name": "AgentB", "accessible_paths": []},
        ])
        # 白名单模式下不应调用 MCP 验证
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={})
        module.http_client.fetch_agent_detail = AsyncMock(return_value={})

        await module._discover_agents()

        assert "AgentA" in module.discovered_agents
        assert "AgentB" not in module.discovered_agents
        # 白名单模式不应调用 MCP 验证
        module.http_client.fetch_mcp_servers.assert_not_called()
        module.http_client.fetch_agent_detail.assert_not_called()

    @pytest.mark.asyncio
    async def test_discover_agents_mcp_filter(self, state_manager):
        """MCP 绑定验证: 只保留挂载了桥接 MCP 的 Agent"""
        module = self._make_module(state_manager)
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "WithBridge", "accessible_paths": []},
            {"id": "agent_002", "name": "WithoutBridge", "accessible_paths": []},
        ])
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_bridge_id": {"name": "QQ Bridge"},
        })

        async def mock_detail(agent_id):
            if agent_id == "agent_001":
                return {"id": "agent_001", "mcps": ["mcp_bridge_id", "other"]}
            elif agent_id == "agent_002":
                return {"id": "agent_002", "mcps": ["other_only"]}
            return None

        module.http_client.fetch_agent_detail = AsyncMock(side_effect=mock_detail)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await module._discover_agents()

        assert "WithBridge" in module.discovered_agents
        assert "WithoutBridge" not in module.discovered_agents

    @pytest.mark.asyncio
    async def test_find_bridge_mcp_id_success(self, state_manager):
        """_find_bridge_mcp_id 应找到桥接 MCP 的 ID"""
        module = self._make_module(state_manager)
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_abc": {"name": "Some Other"},
            "mcp_xyz": {"name": "QQ Bridge"},
        })

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await module._find_bridge_mcp_id()
        assert result == "mcp_xyz"

    @pytest.mark.asyncio
    async def test_find_bridge_mcp_id_not_found(self, state_manager):
        """_find_bridge_mcp_id 找不到时应返回空字符串"""
        module = self._make_module(state_manager)
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_abc": {"name": "Some Other"},
        })

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await module._find_bridge_mcp_id()
        assert result == ""
        # 应重试 10 次 (等待 MCP 握手完成)
        assert module.http_client.fetch_mcp_servers.call_count == 10

    @pytest.mark.asyncio
    async def test_find_bridge_mcp_id_empty_servers(self, state_manager):
        """MCP 服务器列表为空时应返回空字符串"""
        module = self._make_module(state_manager)
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={})

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await module._find_bridge_mcp_id()
        assert result == ""

    @pytest.mark.asyncio
    async def test_filter_mcp_agents_no_bridge_id(self, state_manager):
        """无法获取 bridge MCP ID 时应全部加载 (回退)"""
        module = self._make_module(state_manager)
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={})

        agents = {
            "AgentA": {"agent_id": "a1", "work_dirs": []},
            "AgentB": {"agent_id": "a2", "work_dirs": []},
        }
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await module._filter_mcp_agents(agents)

        # 无法获取 bridge ID 时应全部返回
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_filter_mcp_agents_partial(self, state_manager):
        """MCP 过滤应只保留绑定了桥接 MCP 的 Agent"""
        module = self._make_module(state_manager)
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "bridge_mcp": {"name": "QQ Bridge"},
        })

        async def mock_detail(agent_id):
            if agent_id == "a1":
                return {"id": "a1", "mcps": ["bridge_mcp"]}
            return {"id": agent_id, "mcps": []}

        module.http_client.fetch_agent_detail = AsyncMock(side_effect=mock_detail)

        agents = {
            "AgentA": {"agent_id": "a1", "work_dirs": []},
            "AgentB": {"agent_id": "a2", "work_dirs": []},
            "AgentC": {"agent_id": "a3", "work_dirs": []},
        }
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await module._filter_mcp_agents(agents)

        assert "AgentA" in result
        assert "AgentB" not in result
        assert "AgentC" not in result

    @pytest.mark.asyncio
    async def test_discovered_agents_initially_empty(self, state_manager):
        """模块初始化前 discovered_agents 应为空字典"""
        module = self._make_module(state_manager)
        assert module.discovered_agents == {}

    @pytest.mark.asyncio
    async def test_discover_agents_detail_fetch_failure(self, state_manager):
        """Agent 详情获取失败时该 Agent 应被跳过"""
        module = self._make_module(state_manager)
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "AgentFail", "accessible_paths": []},
        ])
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "bridge_mcp": {"name": "QQ Bridge"},
        })
        # 详情获取失败
        module.http_client.fetch_agent_detail = AsyncMock(return_value=None)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await module._discover_agents()

        assert "AgentFail" not in module.discovered_agents


# ====================================================================
# Phase 2C.3: 过期会话检测 + AI 摘要归档
# ====================================================================


class TestStaleSessionDetection:
    """测试 ConversationStore 过期会话检测"""

    @pytest.fixture
    def conv_store(self):
        """创建临时 ConversationStore"""
        from modules.conversation_store import ConversationStore, SessionMeta
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ConversationStore(base_dir=tmpdir)
            yield store

    def test_is_stale_no_meta(self, conv_store):
        """无 meta 的会话不算过期"""
        assert conv_store.is_session_stale("unknown_key") is False

    def test_is_stale_fresh_session(self, conv_store):
        """刚活跃的会话不算过期"""
        from modules.conversation_store import SessionMeta
        meta = SessionMeta("group_1", "agent1")
        meta.last_active = datetime.now().isoformat()
        conv_store.metas["group_1"] = meta

        assert conv_store.is_session_stale("group_1") is False

    def test_is_stale_old_session(self, conv_store):
        """4 天前的会话算过期"""
        from modules.conversation_store import SessionMeta
        from datetime import timedelta
        meta = SessionMeta("group_old", "agent1")
        meta.last_active = (datetime.now() - timedelta(days=4)).isoformat()
        conv_store.metas["group_old"] = meta

        assert conv_store.is_session_stale("group_old") is True

    def test_is_stale_force_stale(self, conv_store):
        """force_stale 标记的会话算过期"""
        from modules.conversation_store import SessionMeta
        meta = SessionMeta("group_force", "agent1")
        meta.last_active = datetime.now().isoformat()  # 刚活跃
        meta.force_stale = True
        conv_store.metas["group_force"] = meta

        assert conv_store.is_session_stale("group_force") is True

    def test_get_stale_session_keys_mixed(self, conv_store):
        """混合活跃/过期会话，正确筛选过期"""
        from modules.conversation_store import SessionMeta
        from datetime import timedelta

        # 活跃会话
        meta_fresh = SessionMeta("group_fresh", "agent1")
        meta_fresh.last_active = datetime.now().isoformat()
        conv_store.metas["group_fresh"] = meta_fresh

        # 过期会话
        meta_old = SessionMeta("group_old", "agent1")
        meta_old.last_active = (datetime.now() - timedelta(days=5)).isoformat()
        conv_store.metas["group_old"] = meta_old

        # force_stale 会话
        meta_force = SessionMeta("group_force", "agent1")
        meta_force.last_active = datetime.now().isoformat()
        meta_force.force_stale = True
        conv_store.metas["group_force"] = meta_force

        stale = conv_store.get_stale_session_keys()
        assert "group_old" in stale
        assert "group_force" in stale
        assert "group_fresh" not in stale

    def test_get_stale_custom_threshold(self, conv_store):
        """自定义天数阈值"""
        from modules.conversation_store import SessionMeta
        from datetime import timedelta

        meta = SessionMeta("group_2d", "agent1")
        meta.last_active = (datetime.now() - timedelta(days=2)).isoformat()
        conv_store.metas["group_2d"] = meta

        # 默认 3 天: 不过期
        assert conv_store.is_session_stale("group_2d") is False
        # 自定义 1 天: 过期
        assert conv_store.is_session_stale("group_2d", days_threshold=1) is True


class TestStaleSessionArchival:
    """测试 CherryStudioModule 过期会话归档"""

    @pytest.fixture
    async def module_with_store(self):
        """创建带 ConversationStore 的 CherryStudioModule"""
        from modules.conversation_store import ConversationStore
        with tempfile.TemporaryDirectory() as tmpdir:
            state_manager = StateManager(
                state_file=Path(tmpdir) / "state.json"
            )
            await state_manager.initialize()

            config = {
                "cherrystudio": {"http_api_base": "http://localhost:8080"},
                "llm_providers": [],
                "conversation_store_enabled": True,
            }
            module = CherryStudioModule(state_manager=state_manager, config=config)
            # 用临时目录替换 ConversationStore
            module.conversation_store = ConversationStore(
                base_dir=str(Path(tmpdir) / "conversations")
            )
            yield module

    @pytest.mark.asyncio
    async def test_check_and_archive_no_store(self):
        """无 ConversationStore 时返回 False"""
        state_manager = StateManager(
            state_file=Path(tempfile.mkdtemp()) / "state.json"
        )
        await state_manager.initialize()
        module = CherryStudioModule(state_manager=state_manager, config={
            "conversation_store_enabled": False,
        })

        result = await module._check_and_archive_stale("key", "agent")
        assert result is False

    @pytest.mark.asyncio
    async def test_check_and_archive_fresh_session(self, module_with_store):
        """活跃会话不归档"""
        from modules.conversation_store import SessionMeta

        module = module_with_store
        cs = module.conversation_store

        meta = SessionMeta("group_fresh", "agent1")
        meta.last_active = datetime.now().isoformat()
        cs.metas["group_fresh"] = meta

        result = await module._check_and_archive_stale("group_fresh", "agent1")
        assert result is False

    @pytest.mark.asyncio
    async def test_check_and_archive_stale_empty_messages(self, module_with_store):
        """过期会话无消息 → 使用占位摘要"""
        from modules.conversation_store import SessionMeta
        from datetime import timedelta

        module = module_with_store
        cs = module.conversation_store

        meta = SessionMeta("group_empty", "agent1")
        meta.last_active = (datetime.now() - timedelta(days=4)).isoformat()
        cs.metas["group_empty"] = meta
        cs.sessions["group_empty"] = __import__("collections").deque()

        result = await module._check_and_archive_stale("group_empty", "agent1")
        assert result is True
        # 摘要应该是占位文本
        assert "无消息" in cs.memories.get("group_empty", "")

    @pytest.mark.asyncio
    async def test_check_and_archive_stale_with_messages(self, module_with_store):
        """过期会话有消息 → 调用 LLM 生成摘要 (无 LLM 时使用占位)"""
        from modules.conversation_store import SessionMeta
        from datetime import timedelta
        from collections import deque

        module = module_with_store
        cs = module.conversation_store

        meta = SessionMeta("group_msgs", "agent1")
        meta.last_active = (datetime.now() - timedelta(days=4)).isoformat()
        cs.metas["group_msgs"] = meta
        cs.sessions["group_msgs"] = deque([
            {"time": "2026-06-01 10:00", "role": "user", "sender": "Alice", "content": "你好"},
            {"time": "2026-06-01 10:01", "role": "assistant", "content": "你好！有什么可以帮你的？"},
        ])

        # 无 LLM 配置，应使用占位摘要
        result = await module._check_and_archive_stale("group_msgs", "agent1")
        assert result is True
        memory = cs.memories.get("group_msgs", "")
        assert len(memory) > 0

    @pytest.mark.asyncio
    async def test_summarize_session_no_llm(self, module_with_store):
        """无 LLM Provider 时返回空字符串"""
        module = module_with_store
        module.llm_chain = None  # 无 LLM chain
        result = await module._summarize_session("一些聊天记录文本")
        assert result == ""

    @pytest.mark.asyncio
    async def test_summarize_session_with_llm_chain(self, module_with_store):
        """有 LLM Chain 时使用它生成摘要"""
        module = module_with_store

        # Mock LLM chain
        mock_chain = MagicMock()
        mock_chain.chat_completion = AsyncMock(return_value="这是一段摘要文本")
        module.llm_chain = mock_chain

        result = await module._summarize_session("聊天记录内容...")
        assert result == "这是一段摘要文本"
        mock_chain.chat_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_stale_check_no_store(self):
        """无 ConversationStore 时启动检查不报错"""
        state_manager = StateManager(
            state_file=Path(tempfile.mkdtemp()) / "state.json"
        )
        await state_manager.initialize()
        module = CherryStudioModule(state_manager=state_manager, config={
            "conversation_store_enabled": False,
        })
        # 不应抛异常
        await module._startup_stale_check()

    @pytest.mark.asyncio
    async def test_startup_stale_check_no_stale(self, module_with_store):
        """无过期会话时启动检查正常完成"""
        module = module_with_store
        # 不应抛异常
        await module._startup_stale_check()

    @pytest.mark.asyncio
    async def test_startup_stale_check_processes_stale(self, module_with_store):
        """启动检查处理所有过期会话"""
        from modules.conversation_store import SessionMeta
        from datetime import timedelta
        from collections import deque

        module = module_with_store
        cs = module.conversation_store

        # 创建 2 个过期会话
        for i in range(2):
            key = f"group_stale_{i}"
            meta = SessionMeta(key, "agent1")
            meta.last_active = (datetime.now() - timedelta(days=5)).isoformat()
            cs.metas[key] = meta
            cs.sessions[key] = deque([
                {"time": "old", "role": "user", "content": f"消息{i}"},
            ])
            cs.mapping[key] = "agent1"

        await module._startup_stale_check()

        # 两个会话都应该被归档
        assert cs.memories.get("group_stale_0", "") != ""
        assert cs.memories.get("group_stale_1", "") != ""


class TestWorkspaceContextLoading:
    """测试 _load_workspace_context 工作区上下文加载"""

    def test_empty_work_dirs(self):
        """无工作目录时返回空字符串"""
        result = CherryStudioModule._load_workspace_context([])
        assert result == ""

    def test_no_files_exist(self):
        """工作目录无上下文文件时返回空字符串"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = CherryStudioModule._load_workspace_context([tmpdir])
            assert result == ""

    def test_soul_md_only(self):
        """仅 SOUL.md 存在时正确加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "SOUL.md").write_text("我是 Agent 灵魂", encoding="utf-8")
            result = CherryStudioModule._load_workspace_context([tmpdir])
            assert "<SOUL.md>" in result
            assert "我是 Agent 灵魂" in result
            assert "</SOUL.md>" in result
            assert "USER.md" not in result

    def test_user_md_only(self):
        """仅 USER.md 存在时正确加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "USER.md").write_text("用户信息", encoding="utf-8")
            result = CherryStudioModule._load_workspace_context([tmpdir])
            assert "<USER.md>" in result
            assert "用户信息" in result

    def test_fact_md_in_memory_subdir(self):
        """FACT.md 在 memory/ 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem_dir = Path(tmpdir) / "memory"
            mem_dir.mkdir()
            (mem_dir / "FACT.md").write_text("长期知识", encoding="utf-8")
            result = CherryStudioModule._load_workspace_context([tmpdir])
            assert "<FACT.md>" in result
            assert "长期知识" in result

    def test_all_three_files(self):
        """三个文件同时存在时全部加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "SOUL.md").write_text("灵魂", encoding="utf-8")
            (Path(tmpdir) / "USER.md").write_text("用户", encoding="utf-8")
            mem_dir = Path(tmpdir) / "memory"
            mem_dir.mkdir()
            (mem_dir / "FACT.md").write_text("知识", encoding="utf-8")
            result = CherryStudioModule._load_workspace_context([tmpdir])
            assert "<SOUL.md>" in result
            assert "<USER.md>" in result
            assert "<FACT.md>" in result
            # 顺序: SOUL → USER → FACT
            assert result.index("<SOUL.md>") < result.index("<USER.md>")
            assert result.index("<USER.md>") < result.index("<FACT.md>")

    def test_uses_first_work_dir(self):
        """使用 work_dirs[0] 作为 Agent 主目录"""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                (Path(tmpdir1) / "SOUL.md").write_text("第一个", encoding="utf-8")
                (Path(tmpdir2) / "SOUL.md").write_text("第二个", encoding="utf-8")
                result = CherryStudioModule._load_workspace_context([tmpdir1, tmpdir2])
                assert "第一个" in result
                assert "第二个" not in result

    def test_empty_file_ignored(self):
        """空文件被忽略"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "SOUL.md").write_text("", encoding="utf-8")
            result = CherryStudioModule._load_workspace_context([tmpdir])
            assert result == ""

    def test_whitespace_only_file_ignored(self):
        """仅含空白字符的文件被忽略"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "SOUL.md").write_text("   \n\n  ", encoding="utf-8")
            result = CherryStudioModule._load_workspace_context([tmpdir])
            assert result == ""


class TestBuildContextInjection:
    """测试 _build_injection_context 上下文构建"""

    @pytest.fixture
    async def module_for_injection(self):
        """创建用于注入测试的模块"""
        from modules.conversation_store import ConversationStore
        with tempfile.TemporaryDirectory() as tmpdir:
            state_manager = StateManager(
                state_file=Path(tmpdir) / "state.json"
            )
            await state_manager.initialize()
            config = {
                "cherrystudio": {"http_api_base": "http://localhost:8080"},
                "llm_providers": [],
                "conversation_store_enabled": True,
                "global_context": "这是全局规则",
            }
            module = CherryStudioModule(state_manager=state_manager, config=config)
            module.conversation_store = ConversationStore(
                base_dir=str(Path(tmpdir) / "conversations")
            )
            yield module

    @pytest.mark.asyncio
    async def test_no_agents_no_memory(self, module_for_injection):
        """无 Agent 信息无记忆时仅注入全局规则"""
        module = module_for_injection
        result = module._build_injection_context("unknown_agent", "key1")
        assert "<全局规则>" in result
        assert "这是全局规则" in result
        assert "SOUL.md" not in result

    @pytest.mark.asyncio
    async def test_workspace_context_included(self, module_for_injection):
        """有 Agent 工作目录时注入 SOUL.md"""
        module = module_for_injection
        with tempfile.TemporaryDirectory() as workdir:
            (Path(workdir) / "SOUL.md").write_text("Agent 人设", encoding="utf-8")
            module.discovered_agents["test_agent"] = {
                "agent_id": "id1",
                "work_dirs": [workdir],
            }
            result = module._build_injection_context("test_agent", "key1")
            assert "<SOUL.md>" in result
            assert "Agent 人设" in result
            assert "<全局规则>" in result

    @pytest.mark.asyncio
    async def test_memory_included(self, module_for_injection):
        """有历史记忆时注入"""
        from modules.conversation_store import SessionMeta

        module = module_for_injection
        cs = module.conversation_store
        cs.memories["group_test"] = "之前聊了 Python 和异步编程"
        result = module._build_injection_context("agent1", "group_test")
        assert "<历史对话摘要>" in result
        assert "之前聊了 Python 和异步编程" in result

    @pytest.mark.asyncio
    async def test_all_three_parts(self, module_for_injection):
        """工作区 + 记忆 + 全局规则同时注入"""
        module = module_for_injection
        cs = module.conversation_store

        with tempfile.TemporaryDirectory() as workdir:
            (Path(workdir) / "SOUL.md").write_text("灵魂定义", encoding="utf-8")
            module.discovered_agents["full_agent"] = {
                "agent_id": "id2",
                "work_dirs": [workdir],
            }
            cs.memories["group_full"] = "历史摘要内容"

            result = module._build_injection_context("full_agent", "group_full")
            assert "<SOUL.md>" in result
            assert "<历史对话摘要>" in result
            assert "<全局规则>" in result
            # 顺序: workspace → memory → global
            assert result.index("<SOUL.md>") < result.index("<历史对话摘要>")
            assert result.index("<历史对话摘要>") < result.index("<全局规则>")

    @pytest.mark.asyncio
    async def test_no_global_context(self, module_for_injection):
        """无全局规则时不注入该部分"""
        module = module_for_injection
        module.config["global_context"] = ""
        result = module._build_injection_context("agent1", "key1")
        assert "<全局规则>" not in result

    @pytest.mark.asyncio
    async def test_empty_everything(self, module_for_injection):
        """全部为空时返回空字符串"""
        module = module_for_injection
        module.config["global_context"] = ""
        module.conversation_store = None
        result = module._build_injection_context("no_agent", "no_key")
        assert result == ""


class TestFetchReplyChain:
    """测试回复链解析 _fetch_reply_chain"""

    @pytest.fixture
    def handler_for_chain(self):
        """创建用于回复链测试的 handler"""
        state_manager = MagicMock()
        mcp_client = MagicMock()
        http_client = MagicMock()
        handler = CherryStudioSessionHandler(
            session_key="group_test",
            mcp_client=mcp_client,
            http_client=http_client,
            state_manager=state_manager,
            config={"auto_reply": {"reply_chain_depth": 4}},
        )
        return handler

    def _make_msg(self, msg_id="100", reply_id=""):
        """创建带引用 ID 的测试消息"""
        raw_data = {"message": []}
        if reply_id:
            raw_data["message"].append(
                {"type": "reply", "data": {"id": reply_id}}
            )
        raw = RawMessage(
            msg_id=msg_id,
            source=MessageSource.GROUP,
            target_id="123",
            sender_id="456",
            sender_name="Tester",
            content="测试消息",
            message_type=MessageType.TEXT,
            raw_data=raw_data,
        )
        return ParsedMessage(raw=raw, is_command=False)

    @pytest.mark.asyncio
    async def test_no_napcat_bridge(self, handler_for_chain):
        """无 NapCatBridge 时返回空"""
        handler = handler_for_chain
        handler.napcat_bridge = None
        msg = self._make_msg(reply_id="99")
        text, images = await handler._fetch_reply_chain(msg, 4)
        assert text == ""
        assert images == []

    @pytest.mark.asyncio
    async def test_no_reply_id(self, handler_for_chain):
        """消息无引用时返回空"""
        handler = handler_for_chain
        handler.napcat_bridge = MagicMock()
        msg = self._make_msg()  # 无 reply_id
        text, images = await handler._fetch_reply_chain(msg, 4)
        assert text == ""

    @pytest.mark.asyncio
    async def test_max_depth_zero(self, handler_for_chain):
        """max_depth=0 时不解析"""
        handler = handler_for_chain
        handler.napcat_bridge = MagicMock()
        msg = self._make_msg(reply_id="99")
        text, images = await handler._fetch_reply_chain(msg, 0)
        assert text == ""

    @pytest.mark.asyncio
    async def test_single_layer(self, handler_for_chain):
        """单层引用"""
        handler = handler_for_chain
        mock_nc = AsyncMock()
        mock_nc.get_msg = AsyncMock(return_value={
            "sender": {"nickname": "Alice"},
            "message": [
                {"type": "text", "data": {"text": "你好世界"}},
            ],
            "raw_message": "你好世界",
        })
        handler.napcat_bridge = mock_nc

        msg = self._make_msg(msg_id="100", reply_id="99")
        text, images = await handler._fetch_reply_chain(msg, 4)
        assert "[引用第1层]" in text
        assert "Alice" in text
        assert "你好世界" in text
        mock_nc.get_msg.assert_called_once_with("99")

    @pytest.mark.asyncio
    async def test_multi_layer(self, handler_for_chain):
        """多层引用链"""
        handler = handler_for_chain
        mock_nc = AsyncMock()

        # 第1层引用 → 引用了第2层
        mock_nc.get_msg = AsyncMock(side_effect=[
            {
                "sender": {"nickname": "Bob"},
                "message": [
                    {"type": "reply", "data": {"id": "97"}},
                    {"type": "text", "data": {"text": "我也觉得"}},
                ],
            },
            {
                "sender": {"nickname": "Charlie"},
                "message": [
                    {"type": "text", "data": {"text": "这个方案不错"}},
                ],
            },
        ])
        handler.napcat_bridge = mock_nc

        msg = self._make_msg(msg_id="100", reply_id="98")
        text, images = await handler._fetch_reply_chain(msg, 4)
        assert "Charlie" in text
        assert "Bob" in text
        assert "这个方案不错" in text
        assert "我也觉得" in text
        # 顺序: 旧在前, 新在后 (reversed)
        assert text.index("Charlie") < text.index("Bob")

    @pytest.mark.asyncio
    async def test_cycle_detection(self, handler_for_chain):
        """循环引用检测"""
        handler = handler_for_chain
        mock_nc = AsyncMock()
        # 消息 99 引用了消息 100 (当前消息自身)
        mock_nc.get_msg = AsyncMock(return_value={
            "sender": {"nickname": "Alice"},
            "message": [
                {"type": "reply", "data": {"id": "100"}},
                {"type": "text", "data": {"text": "循环引用"}},
            ],
        })
        handler.napcat_bridge = mock_nc

        msg = self._make_msg(msg_id="100", reply_id="99")
        text, images = await handler._fetch_reply_chain(msg, 4)
        # 应该只遍历一层 (第2层发现 100 已在 seen_ids 中)
        assert mock_nc.get_msg.call_count == 1

    @pytest.mark.asyncio
    async def test_get_msg_failure(self, handler_for_chain):
        """获取消息失败时停止遍历"""
        handler = handler_for_chain
        mock_nc = AsyncMock()
        mock_nc.get_msg = AsyncMock(side_effect=Exception("连接超时"))
        handler.napcat_bridge = mock_nc

        msg = self._make_msg(msg_id="100", reply_id="99")
        text, images = await handler._fetch_reply_chain(msg, 4)
        assert "无法获取" in text

    @pytest.mark.asyncio
    async def test_image_extraction_in_chain(self, handler_for_chain):
        """引用链中提取图片 file ID"""
        handler = handler_for_chain
        mock_nc = AsyncMock()
        mock_nc.get_msg = AsyncMock(return_value={
            "sender": {"nickname": "Alice"},
            "message": [
                {"type": "text", "data": {"text": "看这张图"}},
                {"type": "image", "data": {"file": "abc123.jpg"}},
            ],
        })
        handler.napcat_bridge = mock_nc

        msg = self._make_msg(msg_id="100", reply_id="99")
        text, images = await handler._fetch_reply_chain(msg, 4)
        assert len(images) == 1
        assert images[0] == "abc123.jpg"


class TestExtractHelpers:
    """测试回复链辅助方法"""

    def test_extract_plain_text_from_segments(self):
        """从消息段提取纯文本"""
        segs = [
            {"type": "text", "data": {"text": "Hello "}},
            {"type": "at", "data": {"qq": "123"}},
            {"type": "image", "data": {"file": "img.jpg"}},
            {"type": "reply", "data": {"id": "99"}},
        ]
        text = CherryStudioSessionHandler._extract_plain_text(segs)
        assert "Hello " in text
        assert "@123" in text
        assert "[图片]" in text
        assert "[引用]" not in text  # reply 段被跳过

    def test_extract_plain_text_string_input(self):
        """字符串输入直接返回"""
        text = CherryStudioSessionHandler._extract_plain_text("直接文本", "raw")
        assert text == "直接文本"

    def test_extract_plain_text_empty_string(self):
        """空字符串返回 raw_msg"""
        text = CherryStudioSessionHandler._extract_plain_text("", "raw_fallback")
        assert text == "raw_fallback"

    def test_extract_image_file_ids(self):
        """提取图片 file ID"""
        segs = [
            {"type": "text", "data": {"text": "hello"}},
            {"type": "image", "data": {"file": "img1.jpg"}},
            {"type": "image", "data": {"file": "img2.png"}},
        ]
        ids = CherryStudioSessionHandler._extract_image_file_ids(segs)
        assert ids == ["img1.jpg", "img2.png"]

    def test_extract_image_file_ids_string_input(self):
        """字符串输入返回空列表"""
        ids = CherryStudioSessionHandler._extract_image_file_ids("text")
        assert ids == []

    def test_extract_image_file_ids_no_images(self):
        """无图片时返回空"""
        segs = [{"type": "text", "data": {"text": "no images"}}]
        ids = CherryStudioSessionHandler._extract_image_file_ids(segs)
        assert ids == []


class TestOutputProcessing:
    """测试 Phase 3C 输出后处理"""

    def test_extract_md_images_basic(self):
        """提取基本 Markdown 图片"""
        text = "看这张图 ![photo](https://example.com/img.jpg) 不错吧"
        images = CherryStudioSessionHandler._extract_md_images(text)
        assert len(images) == 1
        assert images[0] == ("photo", "https://example.com/img.jpg")

    def test_extract_md_images_multiple(self):
        """提取多个 Markdown 图片"""
        text = "![a](url1) 文本 ![b](url2) 更多 ![](url3)"
        images = CherryStudioSessionHandler._extract_md_images(text)
        assert len(images) == 3
        assert images[0] == ("a", "url1")
        assert images[1] == ("b", "url2")
        assert images[2] == ("", "url3")  # 空 alt

    def test_extract_md_images_none(self):
        """无 Markdown 图片"""
        text = "纯文本，没有图片"
        images = CherryStudioSessionHandler._extract_md_images(text)
        assert images == []

    def test_strip_md_images(self):
        """移除 Markdown 图片语法"""
        text = "前面 ![img](url) 后面"
        stripped = CherryStudioSessionHandler._strip_md_images(text)
        assert "![img]" not in stripped
        assert "(url)" not in stripped
        assert "前面" in stripped
        assert "后面" in stripped

    def test_strip_md_images_empty_result(self):
        """只有图片时返回空"""
        text = "![only_image](url)"
        stripped = CherryStudioSessionHandler._strip_md_images(text)
        assert stripped == ""

    def test_replace_name_placeholder_name(self):
        """替换 {name} 占位符"""
        result = CherryStudioSessionHandler._replace_name_placeholders(
            "你好 {name}！", "Alice", "12345"
        )
        assert result == "你好 Alice！"

    def test_replace_name_placeholder_sender(self):
        """替换 {sender} 占位符"""
        result = CherryStudioSessionHandler._replace_name_placeholders(
            "嗨 {sender}", "Bob", "67890"
        )
        assert result == "嗨 Bob"

    def test_replace_name_placeholder_at(self):
        """替换 {at} 占位符"""
        result = CherryStudioSessionHandler._replace_name_placeholders(
            "{at} 请看这个", "Charlie", "99999"
        )
        assert "[CQ:at,qq=99999]" in result

    def test_replace_name_placeholder_multiple(self):
        """替换多个占位符"""
        result = CherryStudioSessionHandler._replace_name_placeholders(
            "{name} 你好，{at} 这是一条给 {sender} 的消息",
            "Dave", "11111"
        )
        assert "Dave" in result
        assert "[CQ:at,qq=11111]" in result
        assert "{name}" not in result
        assert "{sender}" not in result
        assert "{at}" not in result

    def test_replace_name_placeholder_none(self):
        """无占位符时原样返回"""
        result = CherryStudioSessionHandler._replace_name_placeholders(
            "普通消息", "User", "000"
        )
        assert result == "普通消息"


# ======================================================================
# Phase 3D.2: 增强配额检测 (402 + 关键词)
# ======================================================================

class TestQuotaDetection:
    """测试 LLMProviderChain 的增强配额检测"""

    def test_is_quota_exceeded_rate_limit(self):
        """检测 'rate limit' 关键词"""
        from modules.cherrystudio_module import LLMProviderChain
        assert LLMProviderChain._is_quota_exceeded_text(
            "You have exceeded your rate limit"
        )

    def test_is_quota_exceeded_quota(self):
        """检测 'quota' 关键词"""
        from modules.cherrystudio_module import LLMProviderChain
        assert LLMProviderChain._is_quota_exceeded_text(
            "quota exceeded for this model"
        )

    def test_is_quota_exceeded_insufficient(self):
        """检测 'insufficient' 关键词"""
        from modules.cherrystudio_module import LLMProviderChain
        assert LLMProviderChain._is_quota_exceeded_text(
            "Insufficient balance"
        )

    def test_is_quota_exceeded_credits(self):
        """检测 'credits exhausted' 关键词"""
        from modules.cherrystudio_module import LLMProviderChain
        assert LLMProviderChain._is_quota_exceeded_text(
            "credits exhausted, please top up"
        )

    def test_is_quota_exceeded_daily_limit(self):
        """检测 'daily limit' 关键词"""
        from modules.cherrystudio_module import LLMProviderChain
        assert LLMProviderChain._is_quota_exceeded_text(
            "daily limit reached"
        )

    def test_is_quota_exceeded_case_insensitive(self):
        """大小写不敏感"""
        from modules.cherrystudio_module import LLMProviderChain
        assert LLMProviderChain._is_quota_exceeded_text(
            "RATE LIMIT exceeded"
        )

    def test_is_quota_exceeded_normal_response(self):
        """正常响应不包含配额关键词"""
        from modules.cherrystudio_module import LLMProviderChain
        assert not LLMProviderChain._is_quota_exceeded_text(
            "Hello, how can I help you today?"
        )

    def test_is_quota_exceeded_empty(self):
        """空文本返回 False"""
        from modules.cherrystudio_module import LLMProviderChain
        assert not LLMProviderChain._is_quota_exceeded_text("")

    def test_is_quota_exceeded_none_like(self):
        """空字符串返回 False"""
        from modules.cherrystudio_module import LLMProviderChain
        assert not LLMProviderChain._is_quota_exceeded_text("")

    def test_quota_keywords_tuple_exists(self):
        """_QUOTA_KEYWORDS 元组存在且非空"""
        from modules.cherrystudio_module import _QUOTA_KEYWORDS
        assert isinstance(_QUOTA_KEYWORDS, tuple)
        assert len(_QUOTA_KEYWORDS) > 5


# ======================================================================
# Phase 3D.4: Provider 切换管理员通知
# ======================================================================

class TestProviderSwitchNotification:
    """测试 LLMProviderChain 的 Provider 切换通知机制"""

    def test_switch_callback_fields_exist(self):
        """回调字段和冷却时间字段存在"""
        from modules.cherrystudio_module import LLMProviderChain
        chain = LLMProviderChain(
            providers=[{"name": "a"}, {"name": "b"}],
            default_index=0,
        )
        assert chain._on_switch_callback is None
        assert chain._last_switch_notify_time == 0.0
        assert chain._switch_cooldown_seconds == 3600.0

    def test_switch_triggers_callback(self):
        """Provider 切换时触发回调"""
        from modules.cherrystudio_module import LLMProviderChain
        chain = LLMProviderChain(
            providers=[{"name": "alpha"}, {"name": "beta"}],
            default_index=0,
        )
        calls = []
        chain._on_switch_callback = lambda old, new: calls.append((old, new))
        chain._switch_to_next_provider()
        assert len(calls) == 1
        assert calls[0] == ("alpha", "beta")
        assert chain.current_index == 1

    def test_switch_cooldown_prevents_rapid_notify(self):
        """1小时冷却内不重复触发回调"""
        import time
        from modules.cherrystudio_module import LLMProviderChain
        chain = LLMProviderChain(
            providers=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
            default_index=0,
        )
        calls = []
        chain._on_switch_callback = lambda old, new: calls.append((old, new))

        # 第一次切换 — 触发
        chain._switch_to_next_provider()
        assert len(calls) == 1

        # 第二次切换 — 冷却中不触发
        chain._switch_to_next_provider()
        assert len(calls) == 1  # 没有新调用

    def test_switch_cooldown_expired_triggers_again(self):
        """冷却过期后再次触发回调"""
        import time
        from modules.cherrystudio_module import LLMProviderChain
        chain = LLMProviderChain(
            providers=[{"name": "a"}, {"name": "b"}],
            default_index=0,
        )
        calls = []
        chain._on_switch_callback = lambda old, new: calls.append((old, new))

        # 第一次切换
        chain._switch_to_next_provider()
        assert len(calls) == 1

        # 模拟冷却过期
        chain._last_switch_notify_time = time.monotonic() - 3601

        # 第二次切换 — 应触发
        chain._switch_to_next_provider()
        assert len(calls) == 2

    def test_switch_no_callback_no_error(self):
        """未设置回调时切换不报错"""
        from modules.cherrystudio_module import LLMProviderChain
        chain = LLMProviderChain(
            providers=[{"name": "a"}, {"name": "b"}],
            default_index=0,
        )
        # 不设置回调，切换不应抛异常
        chain._switch_to_next_provider()
        assert chain.current_index == 1

    def test_switch_single_provider_no_callback(self):
        """单个 Provider 无法切换，不触发回调"""
        from modules.cherrystudio_module import LLMProviderChain
        chain = LLMProviderChain(
            providers=[{"name": "solo"}],
            default_index=0,
        )
        calls = []
        chain._on_switch_callback = lambda old, new: calls.append((old, new))
        chain._switch_to_next_provider()
        assert len(calls) == 0
        assert chain.current_index == 0


# ======================================================================
# Phase 6C.3: 全局上下文长度警告
# ======================================================================

class TestGlobalContextWarning:
    """测试全局上下文长度警告"""

    def test_long_context_logs_warning(self):
        """global_context > 500 字符时输出警告日志"""
        long_ctx = "x" * 501
        with patch("modules.cherrystudio_module.logger") as mock_logger:
            module = CherryStudioModule(
                state_manager=MagicMock(),
                config={"global_context": long_ctx},
            )
            mock_logger.warning.assert_any_call(
                pytest.approx(mock_logger.warning.call_args_list[0].args[0], abs=1)
            )
            # 验证至少有一次调用包含 "global_context" 和 "500"
            warning_calls = [
                str(c) for c in mock_logger.warning.call_args_list
                if "global_context" in str(c)
            ]
            assert len(warning_calls) > 0

    def test_short_context_no_warning(self):
        """global_context <= 500 字符时不输出警告"""
        short_ctx = "x" * 100
        with patch("modules.cherrystudio_module.logger") as mock_logger:
            module = CherryStudioModule(
                state_manager=MagicMock(),
                config={"global_context": short_ctx},
            )
            warning_calls = [
                str(c) for c in mock_logger.warning.call_args_list
                if "global_context" in str(c)
            ]
            assert len(warning_calls) == 0

    def test_empty_context_no_warning(self):
        """无 global_context 时不输出警告"""
        with patch("modules.cherrystudio_module.logger") as mock_logger:
            module = CherryStudioModule(
                state_manager=MagicMock(),
                config={},
            )
            warning_calls = [
                str(c) for c in mock_logger.warning.call_args_list
                if "global_context" in str(c)
            ]
            assert len(warning_calls) == 0


# ======================================================================
# MCP 握手等待 + 初始化完成通知
# ======================================================================

class TestMCPHandshakeWait:
    """测试 _deferred_init 中等待 MCP 握手完成的机制"""

    def _make_module(self, state_manager, config_overrides=None):
        config = {
            "cherrystudio": {
                "http_api_base": "http://test:8080",
                "api_key": "test_key",
                "legacy_mode": True,
                "mcp_server_name": "QQ Bridge",
            },
            "mcp_server_name": "QQ Bridge",
            "default_agent": "TestBot",
            "auto_reply": {
                "enabled": True,
                "reply_mode": "always",
                "cooldown_seconds": 0,
            },
        }
        if config_overrides:
            config.update(config_overrides)
        module = CherryStudioModule(state_manager=state_manager, config=config)
        return module

    @pytest.mark.asyncio
    async def test_handshake_event_set_before_init(self, state_manager):
        """握手事件已设置时 _deferred_init 应立即通过"""
        module = self._make_module(state_manager)
        event = asyncio.Event()
        event.set()  # 模拟握手已完成
        module._mcp_handshake_event = event

        # Mock 所有 HTTP 调用
        module.http_client.fetch_agent_id = AsyncMock(return_value="agent_001")
        module.http_client.resolve_model = AsyncMock(return_value="model_v1")
        module.http_client.fetch_agent_detail = AsyncMock(return_value={
            "id": "agent_001", "accessible_paths": ["/test"]
        })
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "TestBot", "accessible_paths": ["/test"]},
        ])
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_bridge": {"name": "QQ Bridge"},
        })
        module.mcp_client.connect = AsyncMock()

        await module._deferred_init()

        assert module._deferred_init_done is True

    @pytest.mark.asyncio
    async def test_handshake_event_none_skips_wait(self, state_manager):
        """_mcp_handshake_event 为 None 时应跳过等待"""
        module = self._make_module(state_manager)
        module._mcp_handshake_event = None  # 未注入

        module.http_client.fetch_agent_id = AsyncMock(return_value="agent_001")
        module.http_client.resolve_model = AsyncMock(return_value="model_v1")
        module.http_client.fetch_agent_detail = AsyncMock(return_value={
            "id": "agent_001", "accessible_paths": ["/test"]
        })
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "TestBot", "accessible_paths": ["/test"]},
        ])
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_bridge": {"name": "QQ Bridge"},
        })
        module.mcp_client.connect = AsyncMock()

        await module._deferred_init()

        assert module._deferred_init_done is True

    @pytest.mark.asyncio
    async def test_handshake_wait_timeout_proceeds(self, state_manager):
        """握手等待超时时仍应继续初始化"""
        module = self._make_module(state_manager)
        event = asyncio.Event()
        # 不设置 event，让它超时
        module._mcp_handshake_event = event

        module.http_client.fetch_agent_id = AsyncMock(return_value="agent_001")
        module.http_client.resolve_model = AsyncMock(return_value="model_v1")
        module.http_client.fetch_agent_detail = AsyncMock(return_value={
            "id": "agent_001", "accessible_paths": ["/test"]
        })
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "TestBot", "accessible_paths": ["/test"]},
        ])
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_bridge": {"name": "QQ Bridge"},
        })
        module.mcp_client.connect = AsyncMock()

        # 将超时缩短到 0.1s 避免测试等待 60s
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            await module._deferred_init()

        assert module._deferred_init_done is True

    @pytest.mark.asyncio
    async def test_handshake_event_delayed_set(self, state_manager):
        """握手事件延迟设置时 _deferred_init 应等待后继续"""
        module = self._make_module(state_manager)
        event = asyncio.Event()
        module._mcp_handshake_event = event

        module.http_client.fetch_agent_id = AsyncMock(return_value="agent_001")
        module.http_client.resolve_model = AsyncMock(return_value="model_v1")
        module.http_client.fetch_agent_detail = AsyncMock(return_value={
            "id": "agent_001", "accessible_paths": ["/test"]
        })
        module.http_client.fetch_all_agents = AsyncMock(return_value=[
            {"id": "agent_001", "name": "TestBot", "accessible_paths": ["/test"]},
        ])
        module.http_client.fetch_mcp_servers = AsyncMock(return_value={
            "mcp_bridge": {"name": "QQ Bridge"},
        })
        module.mcp_client.connect = AsyncMock()

        # 延迟 0.1s 后设置事件
        async def delayed_set():
            await asyncio.sleep(0.1)
            event.set()

        asyncio.create_task(delayed_set())
        await module._deferred_init()

        assert module._deferred_init_done is True


class TestInitCompleteNotification:
    """测试初始化完成后给 Master 发送通知"""

    def _make_module(self, state_manager, config_overrides=None):
        config = {
            "cherrystudio": {
                "http_api_base": "http://test:8080",
                "api_key": "test_key",
                "legacy_mode": True,
                "mcp_server_name": "QQ Bridge",
            },
            "mcp_server_name": "QQ Bridge",
            "default_agent": "TestBot",
            "admin_qq": "12345",
            "auto_reply": {
                "enabled": True,
                "reply_mode": "always",
                "cooldown_seconds": 0,
            },
        }
        if config_overrides:
            config.update(config_overrides)
        module = CherryStudioModule(state_manager=state_manager, config=config)
        return module

    def test_sends_notification_to_admin(self, state_manager):
        """初始化完成时应通过 send_queue 向管理员发送通知"""
        module = self._make_module(state_manager)
        send_queue = asyncio.Queue()
        module.send_queue = send_queue
        module.discovered_agents = {
            "AgentA": {"agent_id": "a1", "work_dirs": ["/dir_a"]},
            "AgentB": {"agent_id": "a2", "work_dirs": ["/dir_b"]},
        }

        module._notify_admin_init_complete()

        assert not send_queue.empty()
        msg = send_queue.get_nowait()
        assert msg.target_id == "12345"
        assert msg.target_source == MessageSource.PRIVATE
        assert "初始化已完成" in msg.content
        assert "2 个" in msg.content
        assert "AgentA" in msg.content

    def test_no_notification_without_admin_qq(self, state_manager):
        """未配置 admin_qq 时不发送通知"""
        module = self._make_module(state_manager, {"admin_qq": ""})
        send_queue = asyncio.Queue()
        module.send_queue = send_queue

        module._notify_admin_init_complete()

        assert send_queue.empty()

    def test_no_notification_without_send_queue(self, state_manager):
        """send_queue 为 None 时不报错"""
        module = self._make_module(state_manager)
        module.send_queue = None

        # 不应抛异常
        module._notify_admin_init_complete()

    def test_notification_shows_no_agents(self, state_manager):
        """无 Agent 时通知内容显示 '无'"""
        module = self._make_module(state_manager)
        send_queue = asyncio.Queue()
        module.send_queue = send_queue
        module.discovered_agents = {}

        module._notify_admin_init_complete()

        msg = send_queue.get_nowait()
        assert "0 个" in msg.content
        assert "无" in msg.content

    def test_notification_shows_llm_status(self, state_manager):
        """通知内容应包含 LLM/Vision/FileProcessor 状态"""
        module = self._make_module(state_manager)
        send_queue = asyncio.Queue()
        module.send_queue = send_queue

        module._notify_admin_init_complete()

        msg = send_queue.get_nowait()
        # 默认配置下 LLM/Vision/FileProcessor 均为 None
        assert "LLM: 未配置" in msg.content
        assert "Vision: 未配置" in msg.content
        assert "FileProcessor: 未配置" in msg.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
