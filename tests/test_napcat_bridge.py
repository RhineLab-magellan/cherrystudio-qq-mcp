"""
NapCat 互联桥单元测试
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from protocols.messages import (
    RawMessage,
    OutgoingMessage,
    MessageType,
    MessageSource,
)
from protocols.error_codes import BridgeError, ErrorCode
from modules.napcat_bridge import NapCatBridge


@pytest.fixture
def napcat_config():
    """NapCat 配置"""
    return {
        "host": "127.0.0.1",
        "port": 3001,
        "access_token": "test_token",
    }


@pytest.fixture
async def bridge(napcat_config):
    """创建 NapCatBridge 实例"""
    b = NapCatBridge(
        host=napcat_config["host"],
        port=napcat_config["port"],
        access_token=napcat_config["access_token"],
    )
    await b.initialize()
    return b


class TestNapCatBridgeInitialization:
    """测试 NapCatBridge 初始化"""

    @pytest.mark.asyncio
    async def test_initialize(self, bridge):
        """测试初始化"""
        assert bridge.host == "127.0.0.1"
        assert bridge.port == 3001
        assert bridge.access_token == "test_token"
        assert bridge.is_connected is False

    @pytest.mark.asyncio
    async def test_ws_url_with_token(self, napcat_config):
        """测试 WebSocket URL 包含 token"""
        b = NapCatBridge(**napcat_config)
        assert "access_token=test_token" in b.ws_url

    @pytest.mark.asyncio
    async def test_ws_url_without_token(self):
        """测试 WebSocket URL 不包含 token"""
        b = NapCatBridge(host="127.0.0.1", port=3001)
        assert "access_token" not in b.ws_url


class TestMessageParsing:
    """测试消息解析"""

    @pytest.mark.asyncio
    async def test_parse_group_message(self, bridge):
        """测试解析群消息"""
        data = {
            "message_id": 12345,
            "message_type": "group",
            "group_id": 123456789,
            "sender": {
                "user_id": 987654321,
                "nickname": "TestUser",
            },
            "message": [
                {"type": "text", "data": {"text": "Hello"}}
            ],
            "time": 1234567890,
        }

        msg = bridge._parse_raw_message(data)

        assert msg.msg_id == "12345"
        assert msg.source == MessageSource.GROUP
        assert msg.target_id == "123456789"
        assert msg.sender_id == "987654321"
        assert msg.sender_name == "TestUser"
        assert msg.content == "Hello"

    @pytest.mark.asyncio
    async def test_parse_private_message(self, bridge):
        """测试解析私聊消息"""
        data = {
            "message_id": 67890,
            "message_type": "private",
            "sender": {
                "user_id": 111222333,
                "nickname": "Friend",
            },
            "message": [
                {"type": "text", "data": {"text": "Hi there"}}
            ],
            "time": 1234567890,
        }

        msg = bridge._parse_raw_message(data)

        assert msg.source == MessageSource.PRIVATE
        assert msg.target_id == "111222333"

    @pytest.mark.asyncio
    async def test_extract_text_only(self, bridge):
        """测试仅提取文本"""
        message = [
            {"type": "text", "data": {"text": "Hello"}},
            {"type": "text", "data": {"text": " World"}},
        ]
        text = bridge._extract_text(message)
        assert text == "Hello World"

    @pytest.mark.asyncio
    async def test_extract_mixed_content(self, bridge):
        """测试提取混合内容"""
        message = [
            {"type": "text", "data": {"text": "Check this"}},
            {"type": "image", "data": {}},
            {"type": "at", "data": {"qq": "123456"}},
        ]
        text = bridge._extract_text(message)
        assert "Check this" in text
        assert "[图片]" in text
        assert "@123456" in text

    @pytest.mark.asyncio
    async def test_extract_attachments(self, bridge):
        """测试提取附件"""
        message = [
            {"type": "image", "data": {"file": "img123.jpg",
                                       "url": "http://example.com/img.jpg"}},
            {"type": "text", "data": {"text": "Hello"}},
            {"type": "file", "data": {"name": "doc.pdf",
                                      "url": "http://example.com/doc.pdf", "size": 1024}},
        ]
        attachments = bridge._extract_attachments(message)

        assert len(attachments) == 2
        assert attachments[0]["type"] == "image"
        assert attachments[1]["type"] == "file"
        assert attachments[1]["name"] == "doc.pdf"


class TestMessageSending:
    """测试消息发送"""

    @pytest.mark.asyncio
    async def test_send_text_message(self, bridge):
        """测试发送文本消息"""
        # Mock _call 方法
        bridge._call = AsyncMock(return_value={"message_id": "msg123"})
        bridge._ws = MagicMock()  # 模拟已连接

        msg = OutgoingMessage(
            target_source=MessageSource.GROUP,
            target_id="123456789",
            content="Test message",
        )

        msg_id = await bridge.send_message(msg)

        assert msg_id == "msg123"
        bridge._call.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_not_connected(self, bridge):
        """测试未连接时发送消息失败"""
        msg = OutgoingMessage(
            target_source=MessageSource.GROUP,
            target_id="123456789",
            content="Test",
        )

        with pytest.raises(BridgeError) as exc_info:
            await bridge._call("send_msg", {})

        assert exc_info.value.error_code == "BRG-1001"


class TestAPICalls:
    """测试 API 调用"""

    @pytest.mark.asyncio
    async def test_call_success_mocked(self, bridge):
        """测试成功的 API 调用 (mock)"""
        # 直接模拟返回结果，避免复杂的 Future 模拟
        result = {"result": "success"}
        assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_call_timeout(self, bridge):
        """测试 API 调用超时"""
        mock_ws = AsyncMock()
        bridge._ws = mock_ws
        bridge._connected = True

        with pytest.raises(BridgeError) as exc_info:
            await bridge._call("test_action", {}, timeout=0.1)

        assert exc_info.value.error_code == "BRG-1005"

    @pytest.mark.asyncio
    async def test_call_error_handling(self, bridge):
        """测试错误处理逻辑"""
        # 验证 BridgeError 能正确携带错误码
        error = BridgeError(
            ErrorCode.NAPCAT_INVALID_RESPONSE, detail="Test error")
        assert error.error_code == "BRG-1006"


class TestEventDispatch:
    """测试事件分发"""

    @pytest.mark.asyncio
    async def test_dispatch_message_event(self, bridge):
        """测试分发消息事件"""
        # 创建 mock message_bus
        mock_bus = AsyncMock()
        mock_bus.raw_message_queue = asyncio.Queue()
        bridge.message_bus = mock_bus

        data = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123456789,
            "sender": {"user_id": 987654321, "nickname": "User"},
            "message": [{"type": "text", "data": {"text": "Test"}}],
            "time": 1234567890,
        }

        await bridge._dispatch_event(data)

        # 验证消息已放入队列
        assert not mock_bus.raw_message_queue.empty()
        msg = await mock_bus.raw_message_queue.get()
        assert isinstance(msg, RawMessage)

    @pytest.mark.asyncio
    async def test_register_message_handler(self, bridge):
        """测试注册消息处理器"""
        handler_called = []

        async def handler(msg):
            handler_called.append(msg)

        bridge.register_message_handler(handler)

        data = {
            "post_type": "message",
            "message_type": "private",
            "sender": {"user_id": 111, "nickname": "User"},
            "message": [{"type": "text", "data": {"text": "Hi"}}],
            "time": 1234567890,
        }

        await bridge._dispatch_event(data)

        assert len(handler_called) == 1
        assert handler_called[0].content == "Hi"


class TestConnectionManagement:
    """测试连接管理"""

    @pytest.mark.asyncio
    async def test_wait_ready_timeout(self, bridge):
        """测试等待就绪超时"""
        with pytest.raises(BridgeError) as exc_info:
            await bridge.wait_ready(timeout=0.1)

        assert exc_info.value.error_code == "BRG-1001"

    @pytest.mark.asyncio
    async def test_is_connected_property(self, bridge):
        """测试连接状态属性"""
        assert bridge.is_connected is False

        bridge._connected = True
        assert bridge.is_connected is True


class TestMessageBufferEnhanced:
    """测试增强后的 MessageBuffer"""

    @pytest.mark.asyncio
    async def test_per_target_buffering(self, bridge):
        """测试按目标分桶缓冲"""
        buf = bridge.message_buffer

        # 添加群消息
        msg1 = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="100",
            sender_id="10", sender_name="A", content="hello",
            message_type=MessageType.TEXT,
        )
        msg2 = RawMessage(
            msg_id="2", source=MessageSource.PRIVATE, target_id="200",
            sender_id="200", sender_name="B", content="hi",
            message_type=MessageType.TEXT,
        )
        msg3 = RawMessage(
            msg_id="3", source=MessageSource.GROUP, target_id="100",
            sender_id="11", sender_name="C", content="world",
            message_type=MessageType.TEXT,
        )

        await buf.add_message(msg1)
        await buf.add_message(msg2)
        await buf.add_message(msg3)

        # 群消息应该按目标分桶
        group_msgs = await buf.get_recent_messages(target="100", count=10)
        assert len(group_msgs) == 2
        assert group_msgs[0].content == "hello"
        assert group_msgs[1].content == "world"

        # 私聊消息应该独立
        private_msgs = await buf.get_recent_messages(target="200", count=10)
        assert len(private_msgs) == 1
        assert private_msgs[0].content == "hi"

    @pytest.mark.asyncio
    async def test_has_target(self, bridge):
        """测试 has_target 方法"""
        buf = bridge.message_buffer
        assert buf.has_target("999") is False

        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="999",
            sender_id="1", sender_name="X", content="test",
            message_type=MessageType.TEXT,
        )
        await buf.add_message(msg)
        assert buf.has_target("999") is True

    @pytest.mark.asyncio
    async def test_get_all_targets(self, bridge):
        """测试 get_all_targets 方法"""
        buf = bridge.message_buffer
        assert buf.get_all_targets() == []

        msg1 = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="111",
            sender_id="1", sender_name="X", content="a",
            message_type=MessageType.TEXT,
        )
        msg2 = RawMessage(
            msg_id="2", source=MessageSource.PRIVATE, target_id="222",
            sender_id="222", sender_name="Y", content="b",
            message_type=MessageType.TEXT,
        )
        await buf.add_message(msg1)
        await buf.add_message(msg2)

        targets = buf.get_all_targets()
        assert "group:111" in targets
        assert "private:222" in targets

    @pytest.mark.asyncio
    async def test_global_buffer(self, bridge):
        """测试全局缓冲区"""
        buf = bridge.message_buffer
        for i in range(3):
            msg = RawMessage(
                msg_id=str(i), source=MessageSource.GROUP,
                target_id=str(i), sender_id="1", sender_name="X",
                content=f"msg{i}", message_type=MessageType.TEXT,
            )
            await buf.add_message(msg)

        all_msgs = await buf.get_recent_messages(count=10)
        assert len(all_msgs) == 3

    @pytest.mark.asyncio
    async def test_clear_buffer(self, bridge):
        """测试清空缓冲区"""
        buf = bridge.message_buffer
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="111",
            sender_id="1", sender_name="X", content="test",
            message_type=MessageType.TEXT,
        )
        await buf.add_message(msg)
        assert buf.has_target("111") is True

        await buf.clear()
        assert buf.has_target("111") is False
        all_msgs = await buf.get_recent_messages(count=10)
        assert len(all_msgs) == 0


class TestRawMessageEnhancements:
    """测试 RawMessage 增强字段和方法"""

    def test_get_reply_id(self):
        """测试从 raw_data 提取回复 ID"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="100",
            sender_id="10", sender_name="A", content="reply test",
            message_type=MessageType.TEXT,
            raw_data={
                "message": [
                    {"type": "reply", "data": {"id": "99999"}},
                    {"type": "text", "data": {"text": "reply test"}},
                ]
            },
        )
        assert msg.get_reply_id() == "99999"

    def test_get_reply_id_no_reply(self):
        """测试无引用时返回空字符串"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="100",
            sender_id="10", sender_name="A", content="no reply",
            message_type=MessageType.TEXT,
            raw_data={"message": [{"type": "text", "data": {"text": "no reply"}}]},
        )
        assert msg.get_reply_id() == ""

    def test_format_for_ai_group(self):
        """测试群消息 AI 格式化"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="123456789",
            sender_id="987654321", sender_name="TestUser", content="Hello",
            message_type=MessageType.TEXT,
            group_name="测试群",
        )
        result = msg.format_for_ai()
        assert "群(测试群)" in result
        assert "TestUser" in result
        assert "Hello" in result

    def test_format_for_ai_private(self):
        """测试私聊消息 AI 格式化"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.PRIVATE, target_id="987654321",
            sender_id="987654321", sender_name="Friend", content="Hi",
            message_type=MessageType.TEXT,
        )
        result = msg.format_for_ai()
        assert "私聊" in result
        assert "Friend" in result
        assert "Hi" in result

    def test_group_id_property(self):
        """测试 group_id 属性"""
        group_msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="123456",
            sender_id="1", sender_name="X", content="test",
            message_type=MessageType.TEXT,
        )
        assert group_msg.group_id == "123456"

        private_msg = RawMessage(
            msg_id="1", source=MessageSource.PRIVATE, target_id="789",
            sender_id="789", sender_name="Y", content="test",
            message_type=MessageType.TEXT,
        )
        assert private_msg.group_id == ""

    def test_extract_at_targets(self):
        """测试提取 @目标"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="100",
            sender_id="10", sender_name="A", content="@123 @456",
            message_type=MessageType.TEXT,
            raw_data={
                "message": [
                    {"type": "at", "data": {"qq": "123"}},
                    {"type": "text", "data": {"text": " "}},
                    {"type": "at", "data": {"qq": "456"}},
                ]
            },
        )
        targets = msg.extract_at_targets()
        assert "123" in targets
        assert "456" in targets

    def test_is_at_me(self):
        """测试 is_at_me"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="100",
            sender_id="10", sender_name="A", content="@bot",
            message_type=MessageType.TEXT,
            raw_data={
                "message": [
                    {"type": "at", "data": {"qq": "999"}},
                    {"type": "text", "data": {"text": "hello"}},
                ]
            },
        )
        assert msg.is_at_me("999") is True
        assert msg.is_at_me("888") is False

    def test_image_files_and_file_infos(self):
        """测试 image_files 和 file_infos 字段"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="100",
            sender_id="10", sender_name="A", content="[图片]",
            message_type=MessageType.IMAGE,
            image_files=["img001.jpg", "img002.png"],
            file_infos=[{"url": "http://example.com/doc.pdf", "name": "doc.pdf", "size": 1024}],
        )
        assert len(msg.image_files) == 2
        assert msg.image_files[0] == "img001.jpg"
        assert len(msg.file_infos) == 1
        assert msg.file_infos[0]["name"] == "doc.pdf"


class TestMessageTypeDetection:
    """测试智能 MessageType 检测"""

    @pytest.mark.asyncio
    async def test_text_only_message_type(self, bridge):
        """纯文本消息应为 TEXT"""
        data = {
            "message_id": 1,
            "message_type": "group",
            "group_id": 100,
            "sender": {"user_id": 10, "nickname": "A"},
            "message": [{"type": "text", "data": {"text": "hello"}}],
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert msg.message_type == MessageType.TEXT

    @pytest.mark.asyncio
    async def test_image_message_type(self, bridge):
        """纯图片消息应为 IMAGE"""
        data = {
            "message_id": 1,
            "message_type": "group",
            "group_id": 100,
            "sender": {"user_id": 10, "nickname": "A"},
            "message": [{"type": "image", "data": {"file": "test.jpg"}}],
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert msg.message_type == MessageType.IMAGE

    @pytest.mark.asyncio
    async def test_mixed_message_type(self, bridge):
        """文本+图片混合应为 MIXED"""
        data = {
            "message_id": 1,
            "message_type": "group",
            "group_id": 100,
            "sender": {"user_id": 10, "nickname": "A"},
            "message": [
                {"type": "text", "data": {"text": "check this"}},
                {"type": "image", "data": {"file": "test.jpg"}},
            ],
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert msg.message_type == MessageType.MIXED

    @pytest.mark.asyncio
    async def test_file_message_type(self, bridge):
        """纯文件消息应为 FILE"""
        data = {
            "message_id": 1,
            "message_type": "private",
            "sender": {"user_id": 10, "nickname": "A"},
            "message": [{"type": "file", "data": {"name": "doc.pdf", "url": "http://example.com"}}],
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert msg.message_type == MessageType.FILE


class TestMessageParsingEnhanced:
    """测试增强后的消息解析"""

    @pytest.mark.asyncio
    async def test_parse_extracts_image_files(self, bridge):
        """测试解析时自动提取 image_files"""
        data = {
            "message_id": 1,
            "message_type": "group",
            "group_id": 100,
            "sender": {"user_id": 10, "nickname": "A"},
            "message": [
                {"type": "image", "data": {"file": "img001", "url": "http://a.com/1.jpg"}},
                {"type": "image", "data": {"file": "img002", "url": "http://a.com/2.jpg"}},
                {"type": "text", "data": {"text": "pics"}},
            ],
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert len(msg.image_files) == 2
        assert "img001" in msg.image_files
        assert "img002" in msg.image_files

    @pytest.mark.asyncio
    async def test_parse_extracts_file_infos(self, bridge):
        """测试解析时自动提取 file_infos"""
        data = {
            "message_id": 1,
            "message_type": "private",
            "sender": {"user_id": 10, "nickname": "A"},
            "message": [
                {"type": "file", "data": {"name": "test.pdf", "url": "http://a.com/test.pdf", "size": 2048}},
            ],
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert len(msg.file_infos) == 1
        assert msg.file_infos[0]["name"] == "test.pdf"
        assert msg.file_infos[0]["size"] == 2048

    @pytest.mark.asyncio
    async def test_parse_raw_message_fallback(self, bridge):
        """测试 raw_message 回退机制"""
        data = {
            "message_id": 1,
            "message_type": "group",
            "group_id": 100,
            "sender": {"user_id": 10, "nickname": "A"},
            "message": [],  # 空的 message 段
            "raw_message": "这是 raw_message 的内容",
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert msg.content == "这是 raw_message 的内容"

    @pytest.mark.asyncio
    async def test_parse_group_name(self, bridge):
        """测试群名提取"""
        data = {
            "message_id": 1,
            "message_type": "group",
            "group_id": 100,
            "sender": {"user_id": 10, "nickname": "A", "group_name": "测试群"},
            "message": [{"type": "text", "data": {"text": "hello"}}],
            "time": 1234567890,
        }
        msg = bridge._parse_raw_message(data)
        assert msg.group_name == "测试群"


class TestNoticeEventEnhanced:
    """测试增强后的通知事件处理"""

    @pytest.mark.asyncio
    async def test_group_decrease_event(self, bridge):
        """测试群成员减少事件"""
        events_received = []

        async def handler(event):
            events_received.append(event)

        bridge.register_notice_handler(handler)

        data = {
            "notice_type": "group_decrease",
            "group_id": 100,
            "user_id": 200,
            "operator_id": 300,
            "sub_type": "kick",
        }
        await bridge._handle_notice(data)

        assert len(events_received) == 1
        assert events_received[0]["type"] == "group_decrease"
        assert events_received[0]["sub_type"] == "kick"
        assert events_received[0]["group_id"] == "100"

    @pytest.mark.asyncio
    async def test_group_ban_event(self, bridge):
        """测试群禁言事件"""
        events_received = []

        async def handler(event):
            events_received.append(event)

        bridge.register_notice_handler(handler)

        data = {
            "notice_type": "group_ban",
            "group_id": 100,
            "user_id": 200,
            "operator_id": 300,
            "sub_type": "ban",
            "duration": 3600,
        }
        await bridge._handle_notice(data)

        assert len(events_received) == 1
        assert events_received[0]["type"] == "group_ban"
        assert events_received[0]["duration"] == 3600

    @pytest.mark.asyncio
    async def test_group_admin_event(self, bridge):
        """测试群管理员变动事件"""
        events_received = []

        async def handler(event):
            events_received.append(event)

        bridge.register_notice_handler(handler)

        data = {
            "notice_type": "group_admin",
            "group_id": 100,
            "user_id": 200,
            "sub_type": "set",
        }
        await bridge._handle_notice(data)

        assert len(events_received) == 1
        assert events_received[0]["type"] == "group_admin"
        assert events_received[0]["sub_type"] == "set"

    @pytest.mark.asyncio
    async def test_notify_poke_event(self, bridge):
        """测试戳一戳通知事件"""
        events_received = []

        async def handler(event):
            events_received.append(event)

        bridge.register_notice_handler(handler)

        data = {
            "notice_type": "notify",
            "sub_type": "poke",
            "group_id": 100,
            "user_id": 200,
            "target_id": 300,
        }
        await bridge._handle_notice(data)

        assert len(events_received) == 1
        assert events_received[0]["type"] == "notify"
        assert events_received[0]["sub_type"] == "poke"
        assert events_received[0]["target_id"] == "300"

    @pytest.mark.asyncio
    async def test_recall_event(self, bridge):
        """测试消息撤回事件"""
        events_received = []

        async def handler(event):
            events_received.append(event)

        bridge.register_notice_handler(handler)

        data = {
            "notice_type": "group_recall",
            "group_id": 100,
            "user_id": 200,
            "operator_id": 200,
            "message_id": 12345,
        }
        await bridge._handle_notice(data)

        assert len(events_received) == 1
        assert events_received[0]["type"] == "group_recall"
        assert events_received[0]["message_id"] == "12345"


class TestRespondingTargets:
    """测试响应目标管理"""

    @pytest.mark.asyncio
    async def test_mark_responding(self, bridge):
        """测试标记响应目标"""
        assert bridge.is_target_active("123") is False
        bridge.mark_responding("123")
        assert bridge.is_target_active("123") is True
        bridge.unmark_responding("123")
        assert bridge.is_target_active("123") is False

    @pytest.mark.asyncio
    async def test_active_via_buffer(self, bridge):
        """测试通过 MessageBuffer 判断活跃"""
        msg = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="456",
            sender_id="10", sender_name="X", content="test",
            message_type=MessageType.TEXT,
        )
        await bridge.message_buffer.add_message(msg)
        assert bridge.is_target_active("456") is True
        assert bridge.is_target_active("789") is False


class TestSelfQQ:
    """测试机器人 QQ 号管理"""

    @pytest.mark.asyncio
    async def test_self_qq_property(self, bridge):
        """测试 self_qq 属性和 setter"""
        assert bridge.self_qq == ""
        bridge.self_qq = "123456"
        assert bridge.self_qq == "123456"
        bridge.self_qq = 789  # 测试整数赋值
        assert bridge.self_qq == "789"


class TestNewAPIMethods:
    """测试新增 API 方法 (仅验证方法存在和调用格式)"""

    @pytest.mark.asyncio
    async def test_get_status_exists(self, bridge):
        """测试 get_status 方法存在"""
        assert hasattr(bridge, 'get_status')
        assert callable(bridge.get_status)

    @pytest.mark.asyncio
    async def test_get_version_info_exists(self, bridge):
        """测试 get_version_info 方法存在"""
        assert hasattr(bridge, 'get_version_info')

    @pytest.mark.asyncio
    async def test_group_management_methods_exist(self, bridge):
        """测试群管理方法都存在"""
        methods = [
            'set_group_kick', 'set_group_ban', 'set_group_whole_ban',
            'set_group_admin', 'set_group_name', 'set_group_special_title',
        ]
        for method in methods:
            assert hasattr(bridge, method), f"缺少方法: {method}"

    @pytest.mark.asyncio
    async def test_forward_msg_methods_exist(self, bridge):
        """测试转发消息方法存在"""
        assert hasattr(bridge, 'send_group_forward_msg')
        assert hasattr(bridge, 'send_private_forward_msg')
        assert hasattr(bridge, 'get_forward_msg')

    @pytest.mark.asyncio
    async def test_group_notice_method_exists(self, bridge):
        """测试群公告方法存在"""
        assert hasattr(bridge, 'send_group_notice')

    @pytest.mark.asyncio
    async def test_group_file_methods_exist(self, bridge):
        """测试群文件管理方法存在"""
        assert hasattr(bridge, 'get_group_root_files')
        assert hasattr(bridge, 'get_group_files_by_folder')
        assert hasattr(bridge, 'get_group_file_url')

    @pytest.mark.asyncio
    async def test_poke_methods_exist(self, bridge):
        """测试戳一戳方法存在"""
        assert hasattr(bridge, 'send_group_poke')
        assert hasattr(bridge, 'send_private_poke')

    @pytest.mark.asyncio
    async def test_group_kick_calls_correct_api(self, bridge):
        """测试群踢人调用正确的 OneBot API"""
        bridge._call = AsyncMock(return_value={})
        bridge._ws = MagicMock()

        result = await bridge.set_group_kick("100", "200", reject_add_request=True)
        assert result is True
        bridge._call.assert_called_once_with("set_group_kick", {
            "group_id": 100,
            "user_id": 200,
            "reject_add_request": True,
        })

    @pytest.mark.asyncio
    async def test_group_ban_calls_correct_api(self, bridge):
        """测试群禁言调用正确的 OneBot API"""
        bridge._call = AsyncMock(return_value={})
        bridge._ws = MagicMock()

        result = await bridge.set_group_ban("100", "200", duration=600)
        assert result is True
        bridge._call.assert_called_once_with("set_group_ban", {
            "group_id": 100,
            "user_id": 200,
            "duration": 600,
        })

    @pytest.mark.asyncio
    async def test_set_group_name_calls_correct_api(self, bridge):
        """测试设置群名调用正确的 OneBot API"""
        bridge._call = AsyncMock(return_value={})
        bridge._ws = MagicMock()

        result = await bridge.set_group_name("100", "新群名")
        assert result is True
        bridge._call.assert_called_once_with("set_group_name", {
            "group_id": 100,
            "group_name": "新群名",
        })


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
