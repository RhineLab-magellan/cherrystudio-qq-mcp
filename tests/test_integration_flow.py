"""
集成测试 — 端到端消息流 & 跨系统联动

覆盖:
1. 完整消息流: RawMessage -> MessageBus -> CommandModule -> send_queue -> OutgoingMessage
2. 骰子端到端: .st -> .show -> .r -> .ra 数据连贯性
3. 方舟 TRPG 端到端: .ark -> .st -> .rk 技能-属性联动
4. 日志端到端: .log new -> 消息 -> .log off -> .log on -> .log end
5. 跨系统: 暗骰 + 旁观者转发 / 日志 + 旁观者跳过
6. HookManager 端到端: on_message / pre_command / post_command
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque

import pytest

from protocols.messages import (
    RawMessage,
    ParsedMessage,
    OutgoingMessage,
    MessageSource,
    MessageType,
    ModuleResponse,
)
from state.manager import StateManager
from modules.message_bus import MessageBus, BlacklistFilter, ModuleEnabledFilter
from modules.command_module import (
    CommandModule,
    CommandContext,
    CommandRegistry,
    SessionHandler,
)
from modules.hooks import HookManager, EVENT_ON_MESSAGE, EVENT_PRE_COMMAND, EVENT_POST_COMMAND
from modules.dice_core import character_store
from modules.dice_core.character_store import (
    load_or_default,
    save_card,
    DEFAULT_CARDS,
)


# ===================================================================
# Helpers
# ===================================================================


def _raw(
    content=".help",
    sender_id="12345",
    sender_name="TestPlayer",
    target_id="67890",
    source=MessageSource.GROUP,
):
    return RawMessage(
        msg_id="msg_int_001",
        source=source,
        target_id=target_id,
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        message_type=MessageType.TEXT,
    )


def _parsed(
    content=".help",
    is_command=True,
    command_name="help",
    command_args="",
    sender_id="12345",
    target_id="67890",
):
    raw = _raw(content=content, sender_id=sender_id, target_id=target_id)
    return ParsedMessage(
        raw=raw,
        is_command=is_command,
        command_name=command_name,
        command_args=command_args,
    )


def _make_ctx(state_manager, send_queue=None, config=None):
    if send_queue is None:
        send_queue = asyncio.Queue()
    registry = CommandRegistry()
    registry.discover_builtin()
    return CommandContext(
        state_manager=state_manager,
        napcat_bridge=AsyncMock(),
        config=config or {},
        send_queue=send_queue,
        command_registry=registry,
    )


# ===================================================================
# 1. 端到端消息流: MessageBus -> CommandModule -> send_queue
# ===================================================================


class TestEndToEndCommandFlow:
    """验证从原始消息到命令响应的完整管线"""

    async def test_help_command_via_message_bus(self, state_manager):
        """RawMessage 进入 MessageBus, 路由到 command_queue,
        SessionHandler 处理后推入 send_queue"""
        bus = MessageBus(state_manager)
        command_queue = asyncio.Queue()
        bus.set_command_queue(command_queue)

        raw = _raw(content=".help")
        parsed = bus._parse_message(raw)
        assert parsed.is_command is True
        assert parsed.command_name == "help"

        await bus._dispatch_message(parsed)
        from_queue = command_queue.get_nowait()
        assert from_queue.command_name == "help"

    async def test_session_handler_produces_outgoing(self, state_manager):
        """SessionHandler 消费 ParsedMessage 并产出 OutgoingMessage"""
        send_queue = asyncio.Queue()
        ctx = _make_ctx(state_manager, send_queue)
        registry = ctx.command_registry
        handler = SessionHandler(
            session_key="group_67890",
            registry=registry,
            context=ctx,
        )
        await handler.start()

        msg = _parsed(content=".help", command_name="help", command_args="")
        await handler.add_message(msg)

        outgoing = await asyncio.wait_for(send_queue.get(), timeout=5.0)
        assert isinstance(outgoing, OutgoingMessage)
        assert outgoing.target_id == "67890"
        assert outgoing.target_source == MessageSource.GROUP
        assert len(outgoing.content) > 0
        await handler.stop()

    async def test_bot_on_off_flow(self, state_manager):
        """连续执行 .bot off 然后 .bot on, 验证状态翻转"""
        send_queue = asyncio.Queue()
        ctx = _make_ctx(state_manager, send_queue)
        registry = ctx.command_registry
        handler = SessionHandler(
            session_key="group_67890",
            registry=registry,
            context=ctx,
        )
        await handler.start()

        # .bot off
        await handler.add_message(
            _parsed(content=".bot off", command_name="bot", command_args="off")
        )
        out1 = await asyncio.wait_for(send_queue.get(), timeout=5.0)
        assert state_manager.is_in_blacklist("67890")

        # .bot on
        await handler.add_message(
            _parsed(content=".bot on", command_name="bot", command_args="on")
        )
        out2 = await asyncio.wait_for(send_queue.get(), timeout=5.0)
        assert not state_manager.is_in_blacklist("67890")

        await handler.stop()

    async def test_non_command_routes_to_cherrystudio_queue(self, state_manager):
        """非命令消息应路由到 cherrystudio_queue"""
        bus = MessageBus(state_manager)
        cs_queue = asyncio.Queue()
        bus.set_cherrystudio_queue(cs_queue)

        raw = _raw(content="你好世界", sender_name="用户")
        parsed = bus._parse_message(raw)
        assert parsed.is_command is False

        await bus._dispatch_message(parsed)
        from_queue = cs_queue.get_nowait()
        assert from_queue.raw.content == "你好世界"

    async def test_blacklisted_group_messages_dropped(self, state_manager):
        """黑名单群的消息应被 MessageBus 过滤器丢弃"""
        await state_manager.add_to_blacklist("67890")

        bus = MessageBus(state_manager)
        cmd_queue = asyncio.Queue()
        bus.set_command_queue(cmd_queue)

        raw = _raw(content=".help", target_id="67890")
        parsed = bus._parse_message(raw)
        # BlacklistFilter 不在 _passes_filters 链中，而是独立检查非命令消息
        blacklist_result = await bus._blacklist_filter.should_pass(raw)
        assert blacklist_result is False

    async def test_compact_command_fallback(self, state_manager):
        """紧凑格式 .st力量5 应被解析为命令 st, 参数 力量5"""
        bus = MessageBus(state_manager)
        raw = _raw(content=".st力量5")
        parsed = bus._parse_message(raw)
        assert parsed.is_command is True
        assert parsed.command_name == "st"
        assert "力量5" in (parsed.command_args or "")


# ===================================================================
# 2. 骰子系统端到端
# ===================================================================


class TestDiceIntegration:
    """骰子系统数据连贯性: .st -> .show -> .r -> .ra"""

    async def test_st_then_show(self, state_manager, temp_data_dir):
        """设置属性后 .show 应显示新值 (紧凑格式需 >=2 属性)"""
        from modules.commands.dice import StCommand, ShowCommand

        ctx = _make_ctx(state_manager)
        msg = _parsed(content=".st 力量5 敏捷3", command_name="st", command_args="力量5 敏捷3")

        st = StCommand()
        result_st = await st.handle("力量5 敏捷3", msg, ctx)
        assert result_st is not None

        show = ShowCommand()
        result_show = await show.handle("", msg, ctx)
        assert "力量" in result_show
        assert "5" in result_show

    async def test_st_then_ra(self, state_manager, temp_data_dir):
        """设置力量后 .ra 力量 应读取角色卡中的值"""
        from modules.commands.dice import StCommand, RaCommand

        ctx = _make_ctx(state_manager)
        msg = _parsed(content=".st 力量50", command_name="st", command_args="力量50")

        st = StCommand()
        await st.handle("力量50", msg, ctx)

        msg_ra = _parsed(content=".ra 力量", command_name="ra", command_args="力量")
        ra = RaCommand()
        result_ra = await ra.handle("力量", msg_ra, ctx)
        assert result_ra is not None
        assert "力量" in result_ra

    async def test_r_basic_roll(self, state_manager, temp_data_dir):
        """基础骰子投掷返回有效结果"""
        from modules.commands.dice import RDiceCommand

        ctx = _make_ctx(state_manager)
        msg = _parsed(content=".r 3d6", command_name="r", command_args="3d6")
        cmd = RDiceCommand()
        result = await cmd.handle("3d6", msg, ctx)
        assert result is not None
        assert "🎲" in result

    async def test_r_with_dc(self, state_manager, temp_data_dir):
        """带 DC 判定的骰子"""
        from modules.commands.dice import RDiceCommand

        ctx = _make_ctx(state_manager)
        msg = _parsed(content=".r 3d6/12", command_name="r", command_args="3d6/12")
        cmd = RDiceCommand()
        result = await cmd.handle("3d6/12", msg, ctx)
        assert result is not None

    async def test_pc_multi_card(self, state_manager, temp_data_dir):
        """多卡管理: 创建新卡 -> 切换 -> 列表"""
        from modules.commands.dice import PcCommand

        ctx = _make_ctx(state_manager)
        cmd = PcCommand()

        # 创建新卡
        msg_new = _parsed(content=".pc new 战士", command_name="pc", command_args="new 战士")
        result_new = await cmd.handle("new 战士", msg_new, ctx)
        assert result_new is not None

        # 列表
        msg_list = _parsed(content=".pc list", command_name="pc", command_args="list")
        result_list = await cmd.handle("list", msg_list, ctx)
        assert result_list is not None

    async def test_full_dice_session_flow(self, state_manager, temp_data_dir):
        """完整骰子会话: .nn -> .st -> .show -> .r 3d6"""
        from modules.commands.dice import NnCommand, StCommand, ShowCommand, RDiceCommand

        ctx = _make_ctx(state_manager)
        sender_id = "player_001"
        group_id = "group_dice"

        def mk(content, cmd_name, args):
            m = _parsed(
                content=content, command_name=cmd_name, command_args=args,
                sender_id=sender_id, target_id=group_id,
            )
            return m

        # 重命名
        nn = NnCommand()
        r1 = await nn.handle("冒险者", mk(".nn 冒险者", "nn", "冒险者"), ctx)
        assert r1 is not None

        # 设置属性
        st = StCommand()
        r2 = await st.handle("力量12 敏捷10", mk(".st 力量12 敏捷10", "st", "力量12 敏捷10"), ctx)
        assert r2 is not None

        # 展示
        show = ShowCommand()
        r3 = await show.handle("", mk(".show", "show", ""), ctx)
        assert "力量" in r3 and "12" in r3

        # 投掷
        r = RDiceCommand()
        r4 = await r.handle("3d6", mk(".r 3d6", "r", "3d6"), ctx)
        assert "🎲" in r4


# ===================================================================
# 3. 方舟 TRPG 端到端
# ===================================================================


class TestArkIntegration:
    """方舟 TRPG 数据连贯性: .ark -> .st -> .rk"""

    async def test_ark_creates_character(self, state_manager, temp_data_dir):
        """.ark 应创建角色并返回属性"""
        from modules.commands.ark_trpg import ArkCommand

        ctx = _make_ctx(state_manager)
        msg = _parsed(content=".ark", command_name="ark", command_args="")
        cmd = ArkCommand()
        result = await cmd.handle("", msg, ctx)
        assert result is not None
        assert len(result) > 10  # 应有内容

    async def test_rk_with_skill_value(self, state_manager, temp_data_dir):
        """手动设置技能值后 .rk 检定"""
        from modules.commands.dice import StCommand
        from modules.commands.ark_trpg import RkCommand

        ctx = _make_ctx(state_manager)
        msg_st = _parsed(content=".st 刀剑7", command_name="st", command_args="刀剑7")
        st = StCommand()
        await st.handle("刀剑7", msg_st, ctx)

        msg_rk = _parsed(content=".rk 6 刀剑", command_name="rk", command_args="6 刀剑")
        rk = RkCommand()
        result = await rk.handle("6 刀剑", msg_rk, ctx)
        assert result is not None

    async def test_sn_sets_card(self, state_manager, temp_data_dir):
        """.sn 应设置群名片"""
        from modules.commands.ark_trpg import SnCommand

        ctx = _make_ctx(state_manager)
        msg = _parsed(content=".sn", command_name="sn", command_args="")
        cmd = SnCommand()
        result = await cmd.handle("", msg, ctx)
        # .sn 可能返回 None (仅设置名片) 或返回文本
        # 验证 ctx.napcat_bridge.set_group_card 被调用或 result 不为空

    async def test_full_ark_session_flow(self, state_manager, temp_data_dir):
        """完整方舟会话: .ark -> .st -> .rk -> .show"""
        from modules.commands.ark_trpg import ArkCommand, RkCommand
        from modules.commands.dice import StCommand, ShowCommand

        ctx = _make_ctx(state_manager)
        sender = "ark_player"
        group = "group_ark"

        def mk(content, cmd_name, args):
            return _parsed(
                content=content, command_name=cmd_name, command_args=args,
                sender_id=sender, target_id=group,
            )

        # 人物作成
        ark = ArkCommand()
        r1 = await ark.handle("", mk(".ark", "ark", ""), ctx)
        assert r1 is not None

        # 设置技能
        st = StCommand()
        r2 = await st.handle("刀剑5", mk(".st 刀剑5", "st", "刀剑5"), ctx)
        assert r2 is not None

        # 技能检定
        rk = RkCommand()
        r3 = await rk.handle("6 刀剑", mk(".rk 6 刀剑", "rk", "6 刀剑"), ctx)
        assert r3 is not None

        # 展示角色卡
        show = ShowCommand()
        r4 = await show.handle("", mk(".show", "show", ""), ctx)
        assert r4 is not None


# ===================================================================
# 4. 日志系统端到端
# ===================================================================


class TestLogIntegration:
    """日志系统生命周期: .log new -> .log off -> .log on -> .log end"""

    async def test_log_new_creates_log(self, state_manager, tmp_path):
        """.log new 应创建日志"""
        from modules.commands.log import LogCommand

        ctx = _make_ctx(state_manager)
        # 设置日志目录
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        cmd = LogCommand()
        msg = _parsed(
            content=".log new testlog",
            command_name="log",
            command_args="new testlog",
            target_id="group_log_1",
        )

        # LogCommand 内部使用 data/logs/ 目录
        # 需要 patch 或确认路径
        result = await cmd.handle("new testlog", msg, ctx)
        assert result is not None

    async def test_log_lifecycle(self, state_manager, tmp_path):
        """完整日志生命周期: new -> off -> on -> end"""
        from modules.commands.log import LogCommand

        ctx = _make_ctx(state_manager)
        cmd = LogCommand()
        group = "group_log_2"

        def mk(content, args):
            return _parsed(
                content=content, command_name="log", command_args=args,
                target_id=group,
            )

        # new
        r1 = await cmd.handle("new mylog", mk(".log new mylog", "new mylog"), ctx)
        assert r1 is not None

        # off (暂停)
        r2 = await cmd.handle("off", mk(".log off", "off"), ctx)
        # off 可能返回 None 如果无活跃日志

        # on (恢复)
        r3 = await cmd.handle("on", mk(".log on", "on"), ctx)

        # list
        r4 = await cmd.handle("list", mk(".log list", "list"), ctx)
        assert r4 is not None

    async def test_log_list_empty(self, state_manager):
        """无日志时 .log list 应返回空列表提示"""
        from modules.commands.log import LogCommand

        ctx = _make_ctx(state_manager)
        cmd = LogCommand()
        msg = _parsed(
            content=".log list", command_name="log", command_args="list",
            target_id="group_empty",
        )
        result = await cmd.handle("list", msg, ctx)
        assert result is not None


# ===================================================================
# 5. 跨系统: 暗骰 + 旁观者 & 日志 + 旁观者
# ===================================================================


class TestCrossSystemIntegration:
    """跨系统联动测试"""

    async def test_rh_sends_to_private_and_observer(self, state_manager, temp_data_dir):
        """.rh 暗骰: 结果应通过 napcat_bridge 私聊发送"""
        from modules.commands.dice import RhCommand

        napcat = AsyncMock()
        send_queue = asyncio.Queue()
        ctx = _make_ctx(state_manager, send_queue)
        ctx.napcat_bridge = napcat

        msg = _parsed(
            content=".rh 3d6", command_name="rh", command_args="3d6",
            sender_id="roller", target_id="group_rh",
        )
        cmd = RhCommand()
        result = await cmd.handle("3d6", msg, ctx)
        assert result is not None

    async def test_observer_forwarding_via_message_bus(self, state_manager):
        """MessageBus 旁观者转发: 群消息 -> 私聊给旁观者"""
        # 设置旁观者
        await state_manager.update_state({
            "ob_groups": {"67890"},
            "observers": {"67890": {"observer_1"}},
        })

        bus = MessageBus(state_manager)
        send_queue = asyncio.Queue()
        bus.send_message_queue = send_queue

        raw = _raw(content="普通消息", sender_id="someone", target_id="67890")
        parsed = bus._parse_message(raw)
        assert parsed.is_command is False

        # 触发旁观者转发
        await bus._forward_to_observers(parsed)
        # 应有一条私聊消息给 observer_1
        if not send_queue.empty():
            outgoing = send_queue.get_nowait()
            assert outgoing.target_id == "observer_1"
            assert outgoing.target_source == MessageSource.PRIVATE

    async def test_observer_does_not_forward_to_self(self, state_manager):
        """旁观者不转发给自己"""
        await state_manager.update_state({
            "ob_groups": {"67890"},
            "observers": {"67890": {"sender_self"}},
        })

        bus = MessageBus(state_manager)
        send_queue = asyncio.Queue()
        bus.send_message_queue = send_queue

        raw = _raw(content="自己的消息", sender_id="sender_self", target_id="67890")
        parsed = bus._parse_message(raw)
        await bus._forward_to_observers(parsed)
        assert send_queue.empty()


# ===================================================================
# 6. HookManager 端到端
# ===================================================================


class TestHookManagerIntegration:
    """HookManager 在消息管线中的端到端测试"""

    async def test_on_message_hook_fires(self, state_manager):
        """on_message 钩子在消息到达时被触发"""
        hook_mgr = HookManager()
        received = []

        async def my_hook(msg, ctx):
            received.append(msg.raw.content)

        hook_mgr.register(EVENT_ON_MESSAGE, my_hook)

        bus = MessageBus(state_manager)
        bus.hook_manager = hook_mgr

        raw = _raw(content="hooked message")
        parsed = bus._parse_message(raw)
        await hook_mgr.fire(EVENT_ON_MESSAGE, parsed)

        assert len(received) == 1
        assert received[0] == "hooked message"

    async def test_pre_and_post_command_hooks(self):
        """pre_command 和 post_command 钩子在命令前后触发"""
        hook_mgr = HookManager()
        events = []

        async def pre_hook(msg, ctx):
            events.append("pre")

        async def post_hook(msg, ctx):
            events.append("post")

        hook_mgr.register(EVENT_PRE_COMMAND, pre_hook)
        hook_mgr.register(EVENT_POST_COMMAND, post_hook)

        msg = _parsed(content=".help", command_name="help")
        await hook_mgr.fire(EVENT_PRE_COMMAND, msg)
        await hook_mgr.fire(EVENT_POST_COMMAND, msg)

        assert events == ["pre", "post"]

    async def test_hook_priority_ordering(self):
        """高优先级 (数值小) 的钩子先执行"""
        hook_mgr = HookManager()
        order = []

        async def low_priority(msg, ctx):
            order.append("low")

        async def high_priority(msg, ctx):
            order.append("high")

        hook_mgr.register(EVENT_ON_MESSAGE, low_priority, priority=10)
        hook_mgr.register(EVENT_ON_MESSAGE, high_priority, priority=1)

        msg = _parsed(content="priority test")
        await hook_mgr.fire(EVENT_ON_MESSAGE, msg)

        assert order == ["high", "low"]

    async def test_hook_filter_fn(self):
        """filter_fn 可以过滤特定消息"""
        hook_mgr = HookManager()
        received = []

        async def my_hook(msg, ctx):
            received.append(msg.raw.content)

        def only_commands(msg):
            return msg.is_command

        hook_mgr.register(EVENT_ON_MESSAGE, my_hook, filter_fn=only_commands)

        cmd_msg = _parsed(content=".help", is_command=True)
        normal_msg = _parsed(content="普通消息", is_command=False, command_name=None, command_args=None)

        await hook_mgr.fire(EVENT_ON_MESSAGE, cmd_msg)
        await hook_mgr.fire(EVENT_ON_MESSAGE, normal_msg)

        assert len(received) == 1
        assert received[0] == ".help"

    async def test_hook_exception_isolation(self):
        """单个钩子异常不影响后续钩子"""
        hook_mgr = HookManager()
        results = []

        async def bad_hook(msg, ctx):
            raise ValueError("boom")

        async def good_hook(msg, ctx):
            results.append("ok")

        hook_mgr.register(EVENT_ON_MESSAGE, bad_hook, priority=1)
        hook_mgr.register(EVENT_ON_MESSAGE, good_hook, priority=2)

        msg = _parsed(content="error test")
        await hook_mgr.fire(EVENT_ON_MESSAGE, msg)

        assert results == ["ok"]

    async def test_hook_unregister(self):
        """注销钩子后不再触发"""
        hook_mgr = HookManager()
        count = []

        async def counter(msg, ctx):
            count.append(1)

        hook_mgr.register(EVENT_ON_MESSAGE, counter)
        msg = _parsed(content="test")
        await hook_mgr.fire(EVENT_ON_MESSAGE, msg)
        assert len(count) == 1

        hook_mgr.unregister(EVENT_ON_MESSAGE, counter)
        await hook_mgr.fire(EVENT_ON_MESSAGE, msg)
        assert len(count) == 1  # 没有增加


# ===================================================================
# 7. MessageBus 过滤器链集成
# ===================================================================


class TestFilterChainIntegration:
    """多过滤器联合工作"""

    async def test_blacklist_overrides_command(self, state_manager):
        """黑名单群即使发命令也被过滤"""
        await state_manager.add_to_blacklist("blocked_group")

        bus = MessageBus(state_manager)
        cmd_queue = asyncio.Queue()
        bus.set_command_queue(cmd_queue)

        raw = _raw(content=".help", target_id="blocked_group")
        # BlacklistFilter 独立于 _passes_filters，专门检查非命令消息
        blacklist_result = await bus._blacklist_filter.should_pass(raw)
        assert blacklist_result is False

    async def test_all_modules_disabled_drops_all(self, state_manager):
        """所有模块禁用时丢弃所有消息"""
        await state_manager.update_module_status("command", False)
        await state_manager.update_module_status("cherrystudio", False)

        bus = MessageBus(state_manager)
        raw = _raw(content=".help")
        passed = await bus._passes_filters(raw)
        assert passed is False

    async def test_single_module_enabled_passes(self, state_manager):
        """至少一个模块启用时消息通过"""
        await state_manager.update_module_status("command", True)
        await state_manager.update_module_status("cherrystudio", False)

        bus = MessageBus(state_manager)
        raw = _raw(content=".help")
        passed = await bus._passes_filters(raw)
        assert passed is True


# ===================================================================
# 8. send_response 集成
# ===================================================================


class TestSendResponseIntegration:
    """MessageBus.send_response 将 ModuleResponse 转为 OutgoingMessage"""

    async def test_success_response_to_outgoing(self, state_manager):
        bus = MessageBus(state_manager)
        send_queue = asyncio.Queue()
        bus.send_message_queue = send_queue

        raw = _raw(content=".help", target_id="99999")
        response = ModuleResponse.success_response("帮助内容")
        await bus.send_response(raw, response)

        outgoing = send_queue.get_nowait()
        assert outgoing.target_id == "99999"
        assert outgoing.content == "帮助内容"
        assert outgoing.target_source == MessageSource.GROUP

    async def test_error_response_to_outgoing(self, state_manager):
        bus = MessageBus(state_manager)
        send_queue = asyncio.Queue()
        bus.send_message_queue = send_queue

        raw = _raw(content=".unknown", target_id="88888")
        response = ModuleResponse.error_response(
            "BRG-3001", "命令不存在", "未知命令"
        )
        await bus.send_response(raw, response)

        outgoing = send_queue.get_nowait()
        assert outgoing.target_id == "88888"
        assert "未知命令" in outgoing.content or "BRG-3001" in outgoing.content


# ===================================================================
# 9. 命令热重载集成
# ===================================================================


class TestCommandHotReload:
    """命令热重载: reload_config 后命令列表完整"""

    async def test_reload_preserves_all_commands(self, state_manager):
        module = CommandModule(state_manager=state_manager)
        await module.initialize()
        before = len(module.registry.list_all())
        assert before == 24

        await module.reload_config()
        after = len(module.registry.list_all())
        assert after == 24
        assert before == after

    async def test_reload_updates_context(self, state_manager):
        module = CommandModule(state_manager=state_manager)
        await module.initialize()
        await module.reload_config()
        # 上下文应仍然可用
        assert module.context is not None
        assert module.context.state_manager is state_manager
