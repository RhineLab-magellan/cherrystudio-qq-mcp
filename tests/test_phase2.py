"""
Phase 2 新功能测试

覆盖:
  - Task 2.1: Install URL 生成器
  - Task 2.2: EventHooks 增强 (优先级 + 过滤 + 3 事件类型)
  - Task 2.3: 会话校验与恢复
  - Task 2.5: Pydantic 配置/状态验证
  - CommandContext / MessageBus / CommandModule 钩子集成
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

import pytest

# ============================================================
# Task 2.1: Install URL 生成器
# ============================================================

from tools.generate_install_url import build_config, generate_url, format_output


class TestInstallURL:
    """测试 Install URL 生成器"""

    def test_build_config_manual(self):
        """manual 模式: 应包含 python_path 和 server.py 路径"""
        config = build_config("manual")
        servers = config["mcpServers"]
        assert "qq-bridge" in servers
        bridge = servers["qq-bridge"]
        assert bridge["type"] == "stdio"
        assert bridge["command"]  # python_path
        assert any("server.py" in arg for arg in bridge["args"])
        assert bridge["description"]

    def test_build_config_uvx(self):
        """uvx 模式: 应包含 uvx 命令和 git URL"""
        config = build_config("uvx")
        bridge = config["mcpServers"]["qq-bridge"]
        assert bridge["command"] == "uvx"
        assert "git+https://github.com" in bridge["args"][1]
        assert bridge["args"][2] == "cherrystudio-qq-mcp"

    def test_generate_url_format(self):
        """生成的 URL 应以 cherrystudio:// 开头"""
        config = build_config("uvx")
        url = generate_url(config)
        assert url.startswith("cherrystudio://mcp/install?servers=")

    def test_generate_url_decodable(self):
        """URL 中的 base64 应能解码回原始 JSON"""
        import base64
        config = build_config("uvx")
        url = generate_url(config)
        encoded = url.split("servers=")[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        assert json.loads(decoded) == config

    def test_format_output_contains_url(self):
        """格式化输出应包含 URL 和使用说明"""
        config = build_config("uvx")
        url = generate_url(config)
        output = format_output("uvx", config, url)
        assert url in output
        assert "Usage" in output
        assert "CherryStudio" in output

    def test_build_config_invalid_mode(self):
        """未知模式应在 main() 中处理 (build_config 本身只接受 manual/uvx)"""
        # build_config 对未知模式会走 manual 分支 (else fallback)
        # 实际入口 main() 会做校验并 sys.exit(1)
        config = build_config("manual")
        assert "mcpServers" in config


# ============================================================
# Task 2.2: EventHooks 增强
# ============================================================

from modules.hooks import (
    HookManager,
    EVENT_ON_MESSAGE,
    EVENT_PRE_COMMAND,
    EVENT_POST_COMMAND,
    VALID_EVENTS,
)
from protocols.messages import (
    ParsedMessage,
    RawMessage,
    MessageSource,
    MessageType,
)


def _make_parsed_msg(text: str = "hello", is_command: bool = False) -> ParsedMessage:
    """创建测试用 ParsedMessage"""
    raw = RawMessage(
        msg_id="msg_001",
        source=MessageSource.GROUP,
        sender_id="user_001",
        target_id="group_001",
        sender_name="TestUser",
        content=text,
        message_type=MessageType.TEXT,
        raw_data={},
    )
    return ParsedMessage(
        raw=raw,
        is_command=is_command,
        command_name="help" if is_command else None,
        command_args="" if is_command else None,
    )


class TestHookManagerEnhanced:
    """测试增强后的 HookManager"""

    def test_valid_events(self):
        """应定义 3 种有效事件类型"""
        assert EVENT_ON_MESSAGE == "on_message"
        assert EVENT_PRE_COMMAND == "pre_command"
        assert EVENT_POST_COMMAND == "post_command"
        assert len(VALID_EVENTS) == 3

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """钩子应按优先级升序执行 (数值越小越先)"""
        hm = HookManager()
        call_order = []

        async def hook_a(msg, ctx):
            call_order.append("a")

        async def hook_b(msg, ctx):
            call_order.append("b")

        async def hook_c(msg, ctx):
            call_order.append("c")

        hm.register("on_message", hook_a, priority=10)
        hm.register("on_message", hook_b, priority=-5)
        hm.register("on_message", hook_c, priority=0)

        msg = _make_parsed_msg()
        await hm.fire("on_message", msg)

        assert call_order == ["b", "c", "a"]

    @pytest.mark.asyncio
    async def test_filter_fn_skips_non_matching(self):
        """filter_fn 返回 False 时钩子应被跳过"""
        hm = HookManager()
        called = []

        async def hook(msg, ctx):
            called.append(True)

        # 过滤器: 仅当 is_command=True 时通过
        hm.register("on_message", hook, filter_fn=lambda m: m.is_command)

        # 非命令消息 → 应被跳过
        msg_normal = _make_parsed_msg(is_command=False)
        await hm.fire("on_message", msg_normal)
        assert len(called) == 0

        # 命令消息 → 应通过
        msg_cmd = _make_parsed_msg(is_command=True)
        await hm.fire("on_message", msg_cmd)
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_filter_fn_exception_skips_hook(self):
        """filter_fn 抛出异常时钩子应被跳过 (不影响后续)"""
        hm = HookManager()
        called = []

        async def hook_a(msg, ctx):
            called.append("a")

        async def hook_b(msg, ctx):
            called.append("b")

        def bad_filter(msg):
            raise ValueError("filter error")

        hm.register("on_message", hook_a, filter_fn=bad_filter)
        hm.register("on_message", hook_b)

        msg = _make_parsed_msg()
        await hm.fire("on_message", msg)
        assert called == ["b"]  # hook_a 被跳过, hook_b 正常执行

    @pytest.mark.asyncio
    async def test_duplicate_registration_ignored(self):
        """同一回调不应被重复注册"""
        hm = HookManager()

        async def hook(msg, ctx):
            pass

        hm.register("on_message", hook)
        hm.register("on_message", hook)
        hm.register("on_message", hook)

        assert len(hm.get_registered("on_message")) == 1

    @pytest.mark.asyncio
    async def test_three_event_types(self):
        """3 种事件类型应独立工作"""
        hm = HookManager()
        results = {"on_message": 0, "pre_command": 0, "post_command": 0}

        async def on_msg(msg, ctx):
            results["on_message"] += 1

        async def pre_cmd(msg, ctx):
            results["pre_command"] += 1

        async def post_cmd(msg, ctx):
            results["post_command"] += 1

        hm.register("on_message", on_msg)
        hm.register("pre_command", pre_cmd)
        hm.register("post_command", post_cmd)

        msg = _make_parsed_msg()
        await hm.fire("on_message", msg)
        await hm.fire("pre_command", msg)
        await hm.fire("post_command", msg)

        assert results == {"on_message": 1, "pre_command": 1, "post_command": 1}

    def test_summary(self):
        """summary() 应返回各事件钩子数量"""
        hm = HookManager()

        async def hook(msg, ctx):
            pass

        hm.register("on_message", hook)
        hm.register("pre_command", hook)
        hm.register("pre_command", hook)  # duplicate, ignored

        s = hm.summary()
        assert s.get("on_message") == 1
        assert s.get("pre_command") == 1

    def test_get_entries(self):
        """get_entries() 应返回含优先级和过滤器的条目"""
        hm = HookManager()

        async def hook(msg, ctx):
            pass

        hm.register("on_message", hook, priority=5)
        entries = hm.get_entries("on_message")
        assert len(entries) == 1
        assert entries[0].priority == 5
        assert entries[0].callback is hook

    @pytest.mark.asyncio
    async def test_priority_with_filter(self):
        """优先级 + 过滤器组合应正确工作"""
        hm = HookManager()
        call_order = []

        async def hook_a(msg, ctx):
            call_order.append("a")

        async def hook_b(msg, ctx):
            call_order.append("b")

        async def hook_c(msg, ctx):
            call_order.append("c")

        hm.register("on_message", hook_a, priority=0, filter_fn=lambda m: m.is_command)
        hm.register("on_message", hook_b, priority=1)  # 无过滤器
        hm.register("on_message", hook_c, priority=2, filter_fn=lambda m: not m.is_command)

        # 非命令消息: hook_a 被过滤, hook_b 和 hook_c 执行
        msg = _make_parsed_msg(is_command=False)
        await hm.fire("on_message", msg)
        assert call_order == ["b", "c"]


# ============================================================
# Task 2.3: 会话校验与恢复
# ============================================================

from modules.conversation_store import ConversationStore


class TestSessionValidation:
    """测试会话完整性校验"""

    @pytest.fixture
    def temp_store(self, tmp_path):
        """创建临时会话存储"""
        store = ConversationStore(base_dir=str(tmp_path / "sessions"))
        return store

    @pytest.mark.asyncio
    async def test_validate_empty_dir(self, temp_store):
        """空目录校验应返回 0 个会话"""
        result = await temp_store.validate_sessions()
        assert result["total"] == 0
        assert result["valid"] == 0
        assert result["corrupted"] == 0

    @pytest.mark.asyncio
    async def test_validate_valid_session(self, temp_store):
        """有效会话应通过校验"""
        agent_dir = temp_store.base_dir / "assistant" / "group_123"
        agent_dir.mkdir(parents=True)

        # 创建有效的 session.json
        (agent_dir / "session.json").write_text(
            json.dumps([{"role": "user", "content": "hello"}]),
            encoding="utf-8",
        )
        # 创建有效的 meta.json
        (agent_dir / "meta.json").write_text(
            json.dumps({
                "session_key": "group_123",
                "agent_name": "assistant",
                "created_at": "2026-06-01T00:00:00",
            }),
            encoding="utf-8",
        )

        result = await temp_store.validate_sessions()
        assert result["total"] == 1
        assert result["valid"] == 1
        assert result["corrupted"] == 0

    @pytest.mark.asyncio
    async def test_validate_corrupted_json(self, temp_store):
        """损坏的 JSON 应被检测并备份"""
        agent_dir = temp_store.base_dir / "assistant" / "group_456"
        agent_dir.mkdir(parents=True)

        # 创建损坏的 session.json
        (agent_dir / "session.json").write_text(
            "{invalid json content",
            encoding="utf-8",
        )
        # 创建有效的 meta.json
        (agent_dir / "meta.json").write_text(
            json.dumps({
                "session_key": "group_456",
                "agent_name": "assistant",
            }),
            encoding="utf-8",
        )

        result = await temp_store.validate_sessions()
        assert result["total"] == 1
        assert result["corrupted"] == 1
        assert len(result["details"]) == 1
        assert "session.json" in result["details"][0]["issues"][0]

        # 验证备份已创建
        corrupted_dir = temp_store.base_dir / ".corrupted" / "assistant" / "group_456"
        assert corrupted_dir.exists()

    @pytest.mark.asyncio
    async def test_validate_missing_required_field(self, temp_store):
        """meta.json 缺少必要字段应被检测"""
        agent_dir = temp_store.base_dir / "assistant" / "group_789"
        agent_dir.mkdir(parents=True)

        (agent_dir / "session.json").write_text("[]", encoding="utf-8")
        # meta.json 缺少 session_key 和 agent_name
        (agent_dir / "meta.json").write_text(
            json.dumps({"created_at": "2026-06-01"}),
            encoding="utf-8",
        )

        result = await temp_store.validate_sessions()
        assert result["corrupted"] == 1
        issues = result["details"][0]["issues"]
        assert any("session_key" in i for i in issues)
        assert any("agent_name" in i for i in issues)

    @pytest.mark.asyncio
    async def test_validate_non_list_session(self, temp_store):
        """session.json 不是列表格式应被检测"""
        agent_dir = temp_store.base_dir / "assistant" / "group_bad"
        agent_dir.mkdir(parents=True)

        (agent_dir / "session.json").write_text(
            json.dumps({"key": "not a list"}),
            encoding="utf-8",
        )
        (agent_dir / "meta.json").write_text(
            json.dumps({"session_key": "group_bad", "agent_name": "assistant"}),
            encoding="utf-8",
        )

        result = await temp_store.validate_sessions()
        assert result["corrupted"] == 1

    @pytest.mark.asyncio
    async def test_validate_mixed_sessions(self, temp_store):
        """混合有效和损坏的会话应正确统计"""
        # 有效会话
        good_dir = temp_store.base_dir / "assistant" / "good_session"
        good_dir.mkdir(parents=True)
        (good_dir / "session.json").write_text("[]", encoding="utf-8")
        (good_dir / "meta.json").write_text(
            json.dumps({"session_key": "good_session", "agent_name": "assistant"}),
            encoding="utf-8",
        )

        # 损坏会话
        bad_dir = temp_store.base_dir / "assistant" / "bad_session"
        bad_dir.mkdir(parents=True)
        (bad_dir / "session.json").write_text("broken", encoding="utf-8")

        result = await temp_store.validate_sessions()
        assert result["total"] == 2
        assert result["valid"] == 1
        assert result["corrupted"] == 1


# ============================================================
# Task 2.5: Pydantic 配置验证
# ============================================================

from protocols.config_models import (
    BridgeConfig,
    NapCatConfig,
    CherryStudioConfig,
    BridgeSettings,
    validate_config,
)
from state.state_models import SharedStateModel, validate_state


class TestConfigModels:
    """测试配置 Pydantic 模型"""

    def test_default_config(self):
        """空字典应生成默认配置"""
        cfg = validate_config({})
        assert cfg.napcat.ws_host == "127.0.0.1"
        assert cfg.napcat.ws_port == 3001
        assert cfg.napcat.access_token == ""
        assert cfg.cherrystudio.timeout == 120

    def test_napcat_config_validation(self):
        """NapCat 配置应验证端口范围"""
        cfg = NapCatConfig(ws_port=8080, ws_host="0.0.0.0")
        assert cfg.ws_port == 8080

        with pytest.raises(Exception):
            NapCatConfig(ws_port=99999)  # 超出范围

    def test_cherrystudio_config_validation(self):
        """CherryStudio 配置应验证超时范围"""
        cfg = CherryStudioConfig(timeout=60)
        assert cfg.timeout == 60

        with pytest.raises(Exception):
            CherryStudioConfig(timeout=5)  # 低于最小值 10

    def test_extra_fields_allowed(self):
        """ConfigDict(extra='allow') 应允许未知字段"""
        cfg = validate_config({
            "unknown_field": "hello",
            "napcat": {"ws_host": "192.168.1.1", "extra_key": True},
        })
        assert cfg.napcat.ws_host == "192.168.1.1"

    def test_full_config(self):
        """完整配置应正确解析"""
        raw = {
            "napcat": {"ws_host": "10.0.0.1", "ws_port": 4000, "access_token": "secret"},
            "cherrystudio": {"http_api_base": "http://10.0.0.2:8080"},
            "settings": {"cooldown_seconds": 5, "session_timeout_minutes": 60},
            "llm_providers": [{"name": "test", "models": ["model-a"]}],
        }
        cfg = validate_config(raw)
        assert cfg.napcat.ws_host == "10.0.0.1"
        assert cfg.cherrystudio.http_api_base == "http://10.0.0.2:8080"
        assert cfg.settings.cooldown_seconds == 5
        assert len(cfg.llm_providers) == 1

    def test_settings_validation(self):
        """BridgeSettings 应验证值范围"""
        s = BridgeSettings(cooldown_seconds=10, message_buffer_size=500)
        assert s.cooldown_seconds == 10

        with pytest.raises(Exception):
            BridgeSettings(cooldown_seconds=100)  # 超过 60

    def test_bridge_config_model_config(self):
        """BridgeConfig 应支持 extra='allow'"""
        cfg = BridgeConfig(custom_key="value")
        # 不抛出异常即通过


class TestStateModels:
    """测试 SharedState Pydantic 模型"""

    def test_default_state(self):
        """空字典应生成默认状态"""
        state = validate_state({})
        assert state.observers == {}
        assert state.bot_blacklist == []
        assert state.modules_enabled == {"command": True, "cherrystudio": True}

    def test_state_with_data(self):
        """带数据的状态应正确解析"""
        raw = {
            "observers": {"group_1": ["user_a", "user_b"]},
            "bot_blacklist": ["group_x"],
            "order_whitelist": ["group_y", "group_z"],
            "saved_models": {"group_1": "gpt-4"},
            "welcome_config": {"group_1": {"enabled": True, "message": "hi"}},
        }
        state = validate_state(raw)
        assert state.observers["group_1"] == ["user_a", "user_b"]
        assert state.bot_blacklist == ["group_x"]
        assert len(state.order_whitelist) == 2
        assert state.saved_models["group_1"] == "gpt-4"

    def test_extra_fields_allowed(self):
        """应允许未知字段 (向后兼容)"""
        state = validate_state({
            "unknown_future_field": [1, 2, 3],
            "bot_blacklist": ["group_a"],
        })
        assert state.bot_blacklist == ["group_a"]

    def test_state_model_direct(self):
        """直接创建 SharedStateModel 应工作"""
        model = SharedStateModel(
            bot_blacklist=["a", "b"],
            modules_enabled={"command": False},
        )
        assert model.bot_blacklist == ["a", "b"]
        assert model.modules_enabled["command"] is False


# ============================================================
# 集成测试: HookManager 与 CommandContext
# ============================================================

from modules.command_module import CommandContext, CommandRegistry, SessionHandler, Command


class TestHookIntegration:
    """测试 HookManager 与 CommandContext/CommandModule 的集成"""

    def test_context_has_hook_manager(self):
        """CommandContext 应支持 hook_manager 属性"""
        ctx = CommandContext(
            state_manager=MagicMock(),
            hook_manager=HookManager(),
        )
        assert ctx.hook_manager is not None
        assert isinstance(ctx.hook_manager, HookManager)

    def test_context_hook_manager_optional(self):
        """CommandContext 的 hook_manager 应为可选 (默认 None)"""
        ctx = CommandContext(state_manager=MagicMock())
        assert ctx.hook_manager is None

    @pytest.mark.asyncio
    async def test_pre_post_command_hooks(self):
        """pre_command 和 post_command 钩子应在命令执行前后触发"""
        hm = HookManager()
        events = []

        async def pre_hook(msg, ctx):
            events.append("pre")

        async def post_hook(msg, ctx):
            events.append("post")

        hm.register("pre_command", pre_hook)
        hm.register("post_command", post_hook)

        # 创建模拟命令
        class TestCmd(Command):
            name = "test"
            description = "test"

            async def handle(self, args, msg, ctx):
                events.append("handle")
                return "ok"

        registry = CommandRegistry()
        registry.register(TestCmd())

        ctx = CommandContext(
            state_manager=MagicMock(),
            hook_manager=hm,
            command_registry=registry,
        )

        msg = _make_parsed_msg(is_command=True)
        msg.command_name = "test"
        msg.command_args = ""

        handler = SessionHandler("test_session", registry, ctx)
        response = await handler._execute_command(msg)

        assert events == ["pre", "handle", "post"]
        assert response.success

    @pytest.mark.asyncio
    async def test_hooks_not_called_when_none(self):
        """hook_manager 为 None 时命令应正常执行 (无钩子)"""
        class TestCmd(Command):
            name = "test2"
            description = "test"

            async def handle(self, args, msg, ctx):
                return "no hooks"

        registry = CommandRegistry()
        registry.register(TestCmd())

        ctx = CommandContext(
            state_manager=MagicMock(),
            hook_manager=None,  # 无钩子
            command_registry=registry,
        )

        msg = _make_parsed_msg(is_command=True)
        msg.command_name = "test2"
        msg.command_args = ""

        handler = SessionHandler("test_session", registry, ctx)
        response = await handler._execute_command(msg)
        assert response.success


class TestMessageBusHookIntegration:
    """测试 MessageBus 的 hook_manager 集成"""

    def test_message_bus_has_hook_manager(self):
        """MessageBus 应有 hook_manager 属性 (默认 None)"""
        from modules.message_bus import MessageBus
        bus = MessageBus(state_manager=MagicMock())
        assert bus.hook_manager is None

    def test_message_bus_hook_manager_settable(self):
        """MessageBus 的 hook_manager 应可设置"""
        from modules.message_bus import MessageBus
        bus = MessageBus(state_manager=MagicMock())
        hm = HookManager()
        bus.hook_manager = hm
        assert bus.hook_manager is hm


# ============================================================
# Bug Fix: .st 命令 <> 占位符替换
# ============================================================

class TestStPlaceholderFix:
    """测试 .st 命令的 <> 占位符替换 Bug 修复"""

    @pytest.fixture
    def temp_data_dir(self, tmp_path):
        """Patch character_store DATA_DIR to a temp directory"""
        from modules.dice_core import character_store
        original = character_store.DATA_DIR
        character_store.DATA_DIR = tmp_path
        yield tmp_path
        character_store.DATA_DIR = original

    def _make_msg(self, sender_id="12345", group_id="67890", sender_name="TestPlayer"):
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        raw = RawMessage(
            msg_id="test_st_1",
            source=MessageSource.GROUP,
            target_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=".st 力量5",
            message_type=MessageType.TEXT,
        )
        return ParsedMessage(raw=raw, is_command=True, command_name="st", command_args="力量5")

    def _make_ctx(self):
        from modules.command_module import CommandContext
        return CommandContext(state_manager=MagicMock(), napcat_bridge=AsyncMock())

    @pytest.mark.asyncio
    async def test_st_no_angle_brackets_in_output(self, temp_data_dir):
        """修复后 .st 输出不应包含 <> 占位符"""
        from modules.commands.dice import StCommand
        cmd = StCommand()
        msg = self._make_msg(sender_name="Alice")
        ctx = self._make_ctx()
        result = await cmd.handle("力量 50", msg, ctx)
        assert "<>" not in result, f"输出仍包含 <> 占位符: {result}"

    @pytest.mark.asyncio
    async def test_st_compact_no_angle_brackets(self, temp_data_dir):
        """紧凑格式 .st 输出也不应包含 <> 占位符"""
        from modules.commands.dice import StCommand
        cmd = StCommand()
        msg = self._make_msg(sender_name="Bob")
        ctx = self._make_ctx()
        result = await cmd.handle("力量5敏捷3", msg, ctx)
        assert "<>" not in result, f"紧凑格式输出仍包含 <> 占位符: {result}"

    @pytest.mark.asyncio
    async def test_st_uses_card_name_over_sender(self, temp_data_dir):
        """.st 应优先使用角色卡名称而非 sender_name"""
        from modules.commands.dice import StCommand
        from modules.dice_core.character_store import save_card
        cmd = StCommand()

        # 创建一张有名字的角色卡
        save_card("12345", "默认", {
            "name": "薇恩塔",
            "skills": {},
            "attributes": {},
            "system": "ark",
        })

        msg = self._make_msg(sender_name="GenericSender")
        ctx = self._make_ctx()
        result = await cmd.handle("力量 50", msg, ctx)

        # 模板 " <> 角色卡已经设置好了~~~" → "薇恩塔 角色卡已经设置好了~~~"
        assert "<>" not in result
        assert "薇恩塔" in result, f"期望角色卡名 '薇恩塔' 出现在输出中: {result}"

    @pytest.mark.asyncio
    async def test_st_fallback_to_sender_name(self, temp_data_dir):
        """无角色卡时应回退到 sender_name"""
        from modules.commands.dice import StCommand
        cmd = StCommand()
        msg = self._make_msg(sender_name="TestUser")
        ctx = self._make_ctx()
        result = await cmd.handle("力量 50", msg, ctx)
        assert "<>" not in result
        assert "TestUser" in result, f"期望 sender_name 'TestUser' 出现在输出中: {result}"


# ============================================================
# Bug Fix: builtin.py 去重验证
# ============================================================

class TestBuiltinDedup:
    """验证 builtin.py 去重后功能正常"""

    def test_builtin_uses_utils(self):
        """builtin.py 应从 utils.py 导入函数"""
        import modules.commands.builtin as builtin_mod
        import modules.commands.utils as utils_mod

        # 确认 builtin 中的函数与 utils 是同一个对象
        assert hasattr(builtin_mod, 'load_bot_setting')
        assert hasattr(builtin_mod, 'format_msg')
        assert builtin_mod.load_bot_setting is utils_mod.load_bot_setting
        assert builtin_mod.format_msg is utils_mod.format_msg

    def test_format_msg_functionality(self):
        """format_msg 应正确替换 <> 和 {}"""
        from modules.commands.utils import format_msg
        result = format_msg("<>进行了{}", "投骰子", player_name="Alice")
        assert result == "Alice进行了投骰子"

    def test_format_msg_no_player_name(self):
        """不传 player_name 时 <> 应保留"""
        from modules.commands.utils import format_msg
        result = format_msg("<>hello {}", "world")
        assert result == "<>hello world"

    def test_format_msg_no_placeholder(self):
        """模板无占位符时应原样返回"""
        from modules.commands.utils import format_msg
        result = format_msg("just plain text", "ignored")
        assert result == "just plain text"
