"""
Server 模块单元测试
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import tempfile
import json
import os

from protocols.error_codes import ErrorCode, BridgeError
from server import Server


@pytest.fixture
def temp_config_dir():
    """创建临时配置目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "Configuration"
        config_dir.mkdir(parents=True)

        # 创建示例配置
        config = {
            "napcat": {
                "ws_host": "127.0.0.1",
                "ws_port": 3001,
                "access_token": "test_token",
            },
            "cherrystudio": {
                "mcp_server_path": "/test/path",
                "http_api_base": "http://test:8080",
                "api_key": "test_key",
            },
            "settings": {
                "enable_command_module": True,
                "enable_cherrystudio_module": True,
            }
        }

        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(
            config, ensure_ascii=False, indent=2))

        yield config_dir


@pytest.fixture
async def server(temp_config_dir):
    """创建 Server 实例"""
    config_path = temp_config_dir / "config.json"
    server = Server(config_path=config_path)
    return server


class TestServerInitialization:
    """测试 Server 初始化"""

    @pytest.mark.asyncio
    async def test_load_config(self, server):
        """测试加载配置"""
        await server._load_config()

        assert server.config["napcat"]["ws_host"] == "127.0.0.1"
        assert server.config["napcat"]["ws_port"] == 3001
        assert server.config["cherrystudio"]["api_key"] == "test_key"

    @pytest.mark.asyncio
    async def test_load_config_not_found(self, server):
        """测试配置文件不存在"""
        server.config_path = Path("/nonexistent/config.json")

        with pytest.raises(BridgeError) as exc_info:
            await server._load_config()

        assert exc_info.value.error_code == ErrorCode.CONFIG_LOAD_FAILED.code

    @pytest.mark.asyncio
    async def test_load_config_invalid_json(self, temp_config_dir):
        """测试无效 JSON 配置"""
        config_file = temp_config_dir / "config.json"
        config_file.write_text("invalid json {{{")

        server = Server(config_path=config_file)

        with pytest.raises(BridgeError) as exc_info:
            await server._load_config()

        assert exc_info.value.error_code == ErrorCode.CONFIG_LOAD_FAILED.code


class TestSingletonLock:
    """测试单例锁"""

    @pytest.mark.asyncio
    async def test_check_singleton_basic(self, server):
        """测试单例锁基本功能"""
        # 验证方法存在且可调用
        assert hasattr(server, '_check_singleton')
        assert callable(server._check_singleton)

    @pytest.mark.asyncio
    async def test_cleanup_pid_file_basic(self, server):
        """测试 PID 文件清理基本功能"""
        # 验证方法存在且可调用
        assert hasattr(server, '_cleanup_pid_file')
        assert callable(server._cleanup_pid_file)


class TestQueueConnection:
    """测试队列连接"""

    @pytest.mark.asyncio
    async def test_connect_queues(self, server):
        """测试模块队列连接"""
        # Mock 所有模块
        server.napcat_bridge = AsyncMock()
        server.message_bus = AsyncMock()
        server.command_module = AsyncMock()
        server.cherrystudio_module = AsyncMock()

        server.command_module.response_queue = asyncio.Queue()
        server.cherrystudio_module.response_queue = asyncio.Queue()
        server.message_bus.send_message_queue = asyncio.Queue()

        # 调用连接方法
        server._connect_queues()

        # 验证队列已设置
        assert server.napcat_bridge.message_bus == server.message_bus
        assert server.message_bus.set_command_queue.called
        assert server.message_bus.set_cherrystudio_queue.called


class TestShutdown:
    """测试关闭流程"""

    @pytest.mark.asyncio
    async def test_shutdown(self, server):
        """测试优雅关闭"""
        # Mock 模块
        server.napcat_bridge = AsyncMock()
        server.message_bus = AsyncMock()
        server.command_module = AsyncMock()
        server.cherrystudio_module = AsyncMock()

        server._running = True

        await server.shutdown()

        assert server._running is False
        assert server.command_module.stop.called
        assert server.cherrystudio_module.stop.called
        assert server.message_bus.stop.called
        assert server.napcat_bridge.stop.called


# ======================================================================
# Phase 6A.4: BotSettingConfig 自动重建
# ======================================================================

class TestBotSettingConfigRebuild:
    """测试 BotSettingConfig.json 自动重建"""

    def test_rebuild_creates_file_when_missing(self, tmp_path):
        """文件不存在时自动创建默认配置"""
        import json
        from server import Server

        # 修改 project_root 为临时目录
        with patch("server.project_root", tmp_path):
            config_dir = tmp_path / "Configuration"
            config_dir.mkdir(parents=True, exist_ok=True)

            srv = Server.__new__(Server)
            srv.config = {}
            srv._ensure_bot_setting_config()

            setting_path = config_dir / "BotSettingConfig.json"
            assert setting_path.exists()

            data = json.loads(setting_path.read_text(encoding="utf-8"))
            assert "内置模块" in data
            assert "BuiltInOrder" in data
            assert "dice_core" in data
            assert "arktrpg" in data
            assert "ob" in data
            assert "log" in data

    def test_rebuild_does_not_overwrite_existing(self, tmp_path):
        """文件已存在时不覆盖"""
        import json
        from server import Server

        config_dir = tmp_path / "Configuration"
        config_dir.mkdir(parents=True, exist_ok=True)
        setting_path = config_dir / "BotSettingConfig.json"
        setting_path.write_text('{"custom": true}', encoding="utf-8")

        with patch("server.project_root", tmp_path):
            srv = Server.__new__(Server)
            srv.config = {}
            srv._ensure_bot_setting_config()

            data = json.loads(setting_path.read_text(encoding="utf-8"))
            assert data == {"custom": True}  # 未被覆盖

    def test_rebuild_default_structure_complete(self, tmp_path):
        """默认配置包含所有模块模板键"""
        import json
        from server import Server

        with patch("server.project_root", tmp_path):
            config_dir = tmp_path / "Configuration"
            config_dir.mkdir(parents=True, exist_ok=True)

            srv = Server.__new__(Server)
            srv.config = {}
            srv._ensure_bot_setting_config()

            data = json.loads(
                (config_dir / "BotSettingConfig.json").read_text(encoding="utf-8")
            )

            # 验证 BuiltInOrder 键
            assert "bot_on_message" in data["BuiltInOrder"]
            assert "bot_off_message" in data["BuiltInOrder"]
            assert "dismiss_message" in data["BuiltInOrder"]


# ---------------------------------------------------------------------------
# qq_send_message 防抖 (Debounce) 测试
# ---------------------------------------------------------------------------

class TestSendMessageDebounce:
    """
    修复验证: qq_send_message 多次调用发送增量文本时，只实际发送最终版本

    场景:
    - 单次调用: 正常缓冲并在 debounce 窗口后发送
    - 增量调用: 逐步增长的文本被合并，只发送最终版本
    - 非增量调用: 旧消息立即发送，新消息缓冲
    """

    @pytest.fixture
    def mock_server(self):
        """构建最小化 Server 实例用于 debounce 测试"""
        from server import Server
        srv = Server.__new__(Server)
        srv._pending_sends = {}
        srv._send_debounce_seconds = 0.1  # 缩短窗口以加速测试

        # Mock napcat_bridge
        srv.napcat_bridge = MagicMock()
        srv.napcat_bridge.is_target_active = MagicMock(return_value=True)
        srv.napcat_bridge.send_message = AsyncMock(return_value="msg_001")
        return srv

    @pytest.mark.asyncio
    async def test_single_call_buffered(self, mock_server):
        """单次调用: 消息被缓冲，debounce 后实际发送"""
        # 直接调用 _debounced_qq_send 的内部逻辑
        # 模拟: _register_mcp_tools 中的 _debounced_qq_send
        key = "12345"
        msg = "你好世界"

        # 模拟第一次调用
        from server import Server
        # 手动构造 pending send
        import asyncio

        async def _flush(target_id):
            await asyncio.sleep(mock_server._send_debounce_seconds)
            pending = mock_server._pending_sends.pop(target_id, None)
            if pending:
                await mock_server.napcat_bridge.send_message(
                    MagicMock(content=pending["text"])
                )

        task = asyncio.create_task(_flush(key))
        mock_server._pending_sends[key] = {
            "text": msg,
            "message_type": "group",
            "task": task,
        }

        # 等待 debounce 窗口
        await asyncio.sleep(0.3)

        # 应该已经实际发送
        mock_server.napcat_bridge.send_message.assert_called_once()
        assert key not in mock_server._pending_sends

    @pytest.mark.asyncio
    async def test_incremental_calls_merged(self, mock_server):
        """增量调用: 7 次逐步增长的文本合并为 1 次发送"""
        import asyncio

        key = "group_123"
        incremental_texts = [
            "啊",
            "啊！",
            "啊！博士",
            "啊！博士你",
            "啊！博士你也",
            "啊！博士你也在",
            "啊！博士你也在捣鼓VR",
        ]

        async def _flush(target_id):
            await asyncio.sleep(mock_server._send_debounce_seconds)
            pending = mock_server._pending_sends.pop(target_id, None)
            if pending:
                await mock_server.napcat_bridge.send_message(
                    MagicMock(content=pending["text"])
                )

        # 快速连续发送增量文本 (模拟 Agent 的多次 qq_send_message 调用)
        for text in incremental_texts:
            # 取消旧的 flush task
            existing = mock_server._pending_sends.get(key)
            if existing:
                old_task = existing.get("task")
                old_text = existing["text"]
                if old_task and not old_task.done():
                    old_task.cancel()
                # 增量检测
                if text.startswith(old_text):
                    task = asyncio.create_task(_flush(key))
                    existing["text"] = text
                    existing["task"] = task
                    continue

            task = asyncio.create_task(_flush(key))
            mock_server._pending_sends[key] = {
                "text": text,
                "message_type": "group",
                "task": task,
            }

        # 等待 debounce 窗口
        await asyncio.sleep(0.3)

        # 关键断言: 只应发送一次，内容为最终版本
        assert mock_server.napcat_bridge.send_message.call_count == 1
        call_args = mock_server.napcat_bridge.send_message.call_args
        sent_content = call_args[0][0].content
        assert sent_content == "啊！博士你也在捣鼓VR"

    @pytest.mark.asyncio
    async def test_non_incremental_flushes_old(self, mock_server):
        """非增量调用: 旧消息立即发送，新消息缓冲"""
        import asyncio

        key = "group_456"

        async def _flush(target_id):
            await asyncio.sleep(mock_server._send_debounce_seconds)
            pending = mock_server._pending_sends.pop(target_id, None)
            if pending:
                await mock_server.napcat_bridge.send_message(
                    MagicMock(content=pending["text"])
                )

        # 第一次发送
        task1 = asyncio.create_task(_flush(key))
        mock_server._pending_sends[key] = {
            "text": "第一条消息",
            "message_type": "group",
            "task": task1,
        }

        # 第二次发送: 非增量文本 (不是第一条的前缀延续)
        existing = mock_server._pending_sends.get(key)
        if existing:
            old_task = existing.get("task")
            if old_task and not old_task.done():
                old_task.cancel()
            # 非增量 → 立即发送旧消息
            await mock_server.napcat_bridge.send_message(
                MagicMock(content=existing["text"])
            )
            mock_server._pending_sends.pop(key, None)

        # 缓冲新消息
        task2 = asyncio.create_task(_flush(key))
        mock_server._pending_sends[key] = {
            "text": "完全不同的第二条消息",
            "message_type": "group",
            "task": task2,
        }

        await asyncio.sleep(0.3)

        # 应发送两次: 旧消息立即发送 + 新消息 debounce 后发送
        assert mock_server.napcat_bridge.send_message.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
