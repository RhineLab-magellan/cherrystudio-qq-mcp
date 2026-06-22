"""
骰子核心模块单元测试

覆盖:
- dice_parser: parse_and_roll, check_result, check_critical_d6
- character_store: CRUD, load_or_default, format_card, deep copy 修复
- dice commands: .r .rh .ra .show .del .pc .nn .st 命令
"""

import asyncio
import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.dice_core.dice_parser import parse_and_roll, check_result, check_critical_d6
from modules.dice_core import character_store
from modules.dice_core.character_store import (
    DEFAULT_CARDS, load_or_default, save_card, load_card,
    list_cards, get_active_card, set_active_card,
    delete_card, delete, rename_card, save,
    set_skill, format_card, load_player, save_player,
)


# ================================================================
# dice_parser 测试
# ================================================================

class TestParseAndRoll:
    def test_basic_3d6(self):
        formatted, total, values = parse_and_roll("3d6")
        assert len(values) == 3
        assert all(1 <= v <= 6 for v in values)
        assert total == sum(values)

    def test_d100(self):
        formatted, total, values = parse_and_roll("d100")
        assert len(values) == 1
        assert 1 <= total <= 100

    def test_bonus(self):
        formatted, total, values = parse_and_roll("2d6+3")
        assert len(values) == 2
        assert total == sum(values) + 3
        assert "+3" in formatted

    def test_repeat(self):
        formatted, total, values = parse_and_roll("2d6#3")
        assert len(values) == 6  # 2 dice * 3 repeats

    def test_invalid(self):
        formatted, total, values = parse_and_roll("xyz")
        assert values == []
        assert total == 0

    def test_empty(self):
        formatted, total, values = parse_and_roll("")
        assert values == []

    def test_1d6_single_die(self):
        formatted, total, values = parse_and_roll("1d6")
        assert len(values) == 1
        assert 1 <= total <= 6


class TestCheckResult:
    def test_critical_success(self):
        assert "大成功" in check_result(3, 50)

    def test_critical_failure(self):
        assert "大失败" in check_result(98, 50)

    def test_extreme_success(self):
        assert "极难成功" in check_result(10, 50)

    def test_hard_success(self):
        assert "困难成功" in check_result(20, 50)

    def test_regular_success(self):
        assert check_result(45, 50) == "成功"

    def test_failure(self):
        assert check_result(80, 50) == "失败"

    def test_no_dc(self):
        result = check_result(50, 0)
        assert result == "失败"  # dc=0 means no DC check, only crit


class TestCheckCriticalD6:
    def test_all_max(self):
        success, fail = check_critical_d6([6, 6, 6], 6)
        assert success is True
        assert fail is False

    def test_all_min(self):
        success, fail = check_critical_d6([1, 1, 1], 6)
        assert success is False
        assert fail is True

    def test_normal(self):
        success, fail = check_critical_d6([3, 4, 5], 6)
        assert success is False
        assert fail is False

    def test_half_max(self):
        success, fail = check_critical_d6([6, 6, 3], 6)
        assert success is True

    def test_single_die_max(self):
        success, fail = check_critical_d6([6], 6)
        assert success is True

    def test_single_die_min(self):
        success, fail = check_critical_d6([1], 6)
        assert fail is True


# ================================================================
# character_store 测试
# ================================================================

@pytest.fixture
def temp_data_dir(tmp_path):
    """临时替换 DATA_DIR 防止污染真实数据"""
    original = character_store.DATA_DIR
    character_store.DATA_DIR = tmp_path / "data"
    yield tmp_path / "data"
    character_store.DATA_DIR = original


class TestCharacterStore:
    def test_default_cards_deep_copy(self):
        """验证 DEFAULT_CARDS 使用 deepcopy 不会污染模板"""
        card = copy.deepcopy(DEFAULT_CARDS["ark"])
        card["skills"]["test_skill"] = 99
        assert "test_skill" not in DEFAULT_CARDS["ark"]["skills"]

    def test_load_or_default_creates_default(self, temp_data_dir):
        char = load_or_default("user1", system="ark")
        assert char["system"] == "ark"
        assert char["hp"] == 10

    def test_load_or_default_coc(self, temp_data_dir):
        char = load_or_default("user2", system="coc")
        assert char["system"] == "coc"
        assert "san" in char

    def test_save_and_load(self, temp_data_dir):
        char = {"name": "Test", "hp": 20, "skills": {"侦查": 50}, "attributes": {}}
        save_card("user3", "test_card", char)
        loaded = load_card("user3", "test_card")
        assert loaded is not None
        assert loaded["name"] == "Test"
        assert loaded["hp"] == 20

    def test_list_cards(self, temp_data_dir):
        save_card("user4", "alpha", {"name": "A"})
        save_card("user4", "beta", {"name": "B"})
        cards = list_cards("user4")
        assert "alpha" in cards
        assert "beta" in cards

    def test_delete_card(self, temp_data_dir):
        save_card("user5", "to_delete", {"name": "Del"})
        assert delete_card("user5", "to_delete") is True
        assert load_card("user5", "to_delete") is None

    def test_delete_nonexistent(self, temp_data_dir):
        assert delete_card("user6", "nope") is False

    def test_active_card(self, temp_data_dir):
        assert get_active_card("user7") == "默认"
        set_active_card("user7", "my_card")
        assert get_active_card("user7") == "my_card"

    def test_rename_card(self, temp_data_dir):
        save_card("user8", "old_name", {"name": "Old"})
        assert rename_card("user8", "old_name", "new_name") is True
        assert load_card("user8", "new_name") is not None
        assert load_card("user8", "old_name") is None

    def test_rename_active_card(self, temp_data_dir):
        save_card("user9", "active", {"name": "Active"})
        set_active_card("user9", "active")
        rename_card("user9", "active", "renamed")
        assert get_active_card("user9") == "renamed"

    def test_set_skill(self, temp_data_dir):
        result = set_skill("user10", "侦查", 50)
        assert "侦查" in result
        char = load_or_default("user10")
        assert char.get("skills", {}).get("侦查") == 50

    def test_set_attribute(self, temp_data_dir):
        load_or_default("user11", system="ark")
        set_skill("user11", "物理强度", 8)
        char = load_or_default("user11")
        # 物理强度 is a skill unless it's in attributes
        assert char.get("skills", {}).get("物理强度") == 8 or char.get("attributes", {}).get("物理强度") == 8

    def test_format_card_ark(self):
        char = {"name": "TestHero", "hp": 10, "hp_max": 10, "sp": 5, "sp_max": 5,
                "attributes": {"物理强度": 8}, "skills": {"侦查": 50}}
        result = format_card(char, "ark")
        assert "TestHero" in result
        assert "HP" in result

    def test_format_card_coc(self):
        char = {"name": "Investigator", "attributes": {"str": 50, "con": 60, "siz": 70, "dex": 40, "app": 55, "int": 80, "pow": 65, "edu": 75, "luc": 45},
                "san": 60, "san_max": 99, "hp": 12, "hp_max": 14, "skills": {}}
        result = format_card(char, "coc")
        assert "Investigator" in result
        assert "SAN" in result

    def test_player_data(self, temp_data_dir):
        data = load_player("user12")
        assert data["active_card"] == "默认"
        save_player("user12", {"name": "Test", "active_card": "hero"})
        data = load_player("user12")
        assert data["active_card"] == "hero"


# ================================================================
# dice commands 测试
# ================================================================

class TestDiceCommands:
    """测试骰子命令的 handle 方法"""

    def _make_msg(self, sender_id="12345", group_id="67890", sender_name="TestPlayer"):
        """构造测试用 ParsedMessage"""
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        raw = RawMessage(
            msg_id="test_msg_1",
            source=MessageSource.GROUP,
            target_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=".r 3d6",
            message_type=MessageType.TEXT,
        )
        return ParsedMessage(raw=raw, is_command=True, command_name="r", command_args="3d6")

    def _make_ctx(self):
        """构造测试用 CommandContext"""
        from modules.command_module import CommandContext
        state_mgr = MagicMock()
        state_mgr.state = MagicMock()
        state_mgr.state.observers = {}
        napcat = AsyncMock()
        return CommandContext(
            state_manager=state_mgr,
            napcat_bridge=napcat,
        )

    @pytest.mark.asyncio
    async def test_r_command_basic(self, temp_data_dir):
        from modules.commands.dice import RDiceCommand
        cmd = RDiceCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("3d6", msg, ctx)
        assert result is not None
        assert "🎲" in result

    @pytest.mark.asyncio
    async def test_r_command_empty_args(self, temp_data_dir):
        from modules.commands.dice import RDiceCommand
        cmd = RDiceCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("", msg, ctx)
        assert "子命令" in result  # shows help

    @pytest.mark.asyncio
    async def test_r_command_with_dc(self, temp_data_dir):
        from modules.commands.dice import RDiceCommand
        cmd = RDiceCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("d100/50", msg, ctx)
        assert result is not None
        assert "vs 50" in result

    @pytest.mark.asyncio
    async def test_ra_command(self, temp_data_dir):
        from modules.commands.dice import RaCommand
        cmd = RaCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        # Set up a character with the skill
        save_card("12345", "默认", {"name": "Test", "skills": {"侦查": 50}, "attributes": {}, "system": "ark"})
        result = await cmd.handle("侦查", msg, ctx)
        assert result is not None
        assert "检定" in result

    @pytest.mark.asyncio
    async def test_ra_command_no_skill(self, temp_data_dir):
        from modules.commands.dice import RaCommand
        cmd = RaCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("不存在的技能", msg, ctx)
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_show_command(self, temp_data_dir):
        from modules.commands.dice import ShowCommand
        cmd = ShowCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("", msg, ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_st_command_set(self, temp_data_dir):
        from modules.commands.dice import StCommand
        cmd = StCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("侦查 50", msg, ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_st_command_query(self, temp_data_dir):
        from modules.commands.dice import StCommand
        cmd = StCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        save_card("12345", "默认", {"name": "Test", "skills": {"侦查": 50}, "attributes": {}, "system": "ark"})
        result = await cmd.handle("侦查", msg, ctx)
        assert "50" in result

    @pytest.mark.asyncio
    async def test_st_command_compact(self, temp_data_dir):
        from modules.commands.dice import StCommand
        cmd = StCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("力量5敏捷3智力7", msg, ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_pc_command_list(self, temp_data_dir):
        from modules.commands.dice import PcCommand
        cmd = PcCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("", msg, ctx)
        assert "当前角色卡" in result

    @pytest.mark.asyncio
    async def test_pc_command_new(self, temp_data_dir):
        from modules.commands.dice import PcCommand
        cmd = PcCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("new hero", msg, ctx)
        assert "已创建" in result

    @pytest.mark.asyncio
    async def test_pc_command_new_duplicate(self, temp_data_dir):
        from modules.commands.dice import PcCommand
        cmd = PcCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        save_card("12345", "hero", {"name": "Hero"})
        result = await cmd.handle("new hero", msg, ctx)
        assert "已存在" in result

    @pytest.mark.asyncio
    async def test_del_command_card(self, temp_data_dir):
        from modules.commands.dice import DelCommand
        cmd = DelCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        save_card("12345", "默认", {"name": "Test", "skills": {"侦查": 50}, "attributes": {}})
        result = await cmd.handle("card", msg, ctx)
        assert "已删除" in result

    @pytest.mark.asyncio
    async def test_nn_command(self, temp_data_dir):
        from modules.commands.dice import NnCommand
        cmd = NnCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        save_card("12345", "默认", {"name": "Old"})
        result = await cmd.handle("新名字", msg, ctx)
        assert "重命名" in result


# ================================================================
# ark_trpg commands 测试
# ================================================================

class TestArkCommands:
    def _make_msg(self, sender_id="12345", group_id="67890", sender_name="TestPlayer"):
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        raw = RawMessage(
            msg_id="test_msg_2",
            source=MessageSource.GROUP,
            target_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=".rk 6 觉察 7/12",
            message_type=MessageType.TEXT,
        )
        return ParsedMessage(raw=raw, is_command=True, command_name="rk", command_args="6 觉察 7/12")

    def _make_ctx(self):
        from modules.command_module import CommandContext
        state_mgr = MagicMock()
        napcat = AsyncMock()
        return CommandContext(state_manager=state_mgr, napcat_bridge=napcat)

    @pytest.mark.asyncio
    async def test_rk_command(self, temp_data_dir):
        from modules.commands.ark_trpg import RkCommand
        cmd = RkCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("6 觉察 7/12", msg, ctx)
        assert result is not None
        assert "检定" in result

    @pytest.mark.asyncio
    async def test_rk_help(self, temp_data_dir):
        from modules.commands.ark_trpg import RkCommand
        cmd = RkCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("help", msg, ctx)
        assert "ArkTRPG" in result

    @pytest.mark.asyncio
    async def test_rkb_command(self, temp_data_dir):
        from modules.commands.ark_trpg import RkbCommand
        cmd = RkbCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("2 觉察 7/12", msg, ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_rkp_command(self, temp_data_dir):
        from modules.commands.ark_trpg import RkpCommand
        cmd = RkpCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("1 觉察 7/12", msg, ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_sck_command(self, temp_data_dir):
        """测试 .sck 自控检定 — 验证 will 变量修复"""
        from modules.commands.ark_trpg import SckCommand
        cmd = SckCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        # Create character with 精神意志 attribute
        save_card("12345", "默认", {
            "name": "Test", "skills": {}, "system": "ark",
            "attributes": {"精神意志": 5},
            "hp": 10, "hp_max": 10, "sp": 10, "sp_max": 10,
        })
        result = await cmd.handle("2", msg, ctx)
        assert result is not None
        assert "自控检定" in result

    @pytest.mark.asyncio
    async def test_ark_command(self, temp_data_dir):
        from modules.commands.ark_trpg import ArkCommand
        cmd = ArkCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("2", msg, ctx)
        assert result is not None
        assert "第1组" in result
        assert "第2组" in result

    @pytest.mark.asyncio
    async def test_ark_command_max(self, temp_data_dir):
        from modules.commands.ark_trpg import ArkCommand
        cmd = ArkCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("11", msg, ctx)
        assert "最多" in result

    @pytest.mark.asyncio
    async def test_sn_command(self, temp_data_dir):
        """测试 .sn 名片设置 — 验证语法错误修复"""
        from modules.commands.ark_trpg import SnCommand
        cmd = SnCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("rk", msg, ctx)
        # Should not crash with SyntaxError; may succeed or fail on set_group_card
        assert result is not None

    @pytest.mark.asyncio
    async def test_sn_help(self, temp_data_dir):
        from modules.commands.ark_trpg import SnCommand
        cmd = SnCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("help", msg, ctx)
        assert "名片" in result


# ================================================================
# skills 测试
# ================================================================

class TestSkills:
    def test_find_attr_known_skill(self):
        from modules.ark_trpg.skills import find_attr
        assert find_attr("觉察") == "精神意志"
        assert find_attr("刀剑") == "物理强度"

    def test_find_attr_unknown(self):
        from modules.ark_trpg.skills import find_attr
        assert find_attr("不存在的技能") is None

    def test_is_attr(self):
        from modules.ark_trpg.skills import is_attr
        assert is_attr("精神意志") is True
        assert is_attr("物理强度") is True
        assert is_attr("侦查") is False

    def test_base_attrs_count(self):
        from modules.ark_trpg.skills import BASE_ATTRS
        assert len(BASE_ATTRS) == 6


# ================================================================
# log 命令测试
# ================================================================

class TestLogCommand:
    def _make_msg(self, sender_id="12345", group_id="67890", sender_name="TestPlayer"):
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        raw = RawMessage(
            msg_id="test_msg_3",
            source=MessageSource.GROUP,
            target_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=".log new test",
            message_type=MessageType.TEXT,
        )
        return ParsedMessage(raw=raw, is_command=True, command_name="log", command_args="new test")

    def _make_ctx(self):
        from modules.command_module import CommandContext
        state_mgr = MagicMock()
        napcat = AsyncMock()
        return CommandContext(state_manager=state_mgr, napcat_bridge=napcat)

    @pytest.mark.asyncio
    async def test_log_new(self, temp_data_dir):
        from modules.commands.log import LogCommand
        cmd = LogCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("new test_log", msg, ctx)
        assert "已创建" in result

    @pytest.mark.asyncio
    async def test_log_list(self, temp_data_dir):
        from modules.commands.log import LogCommand
        cmd = LogCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        await cmd.handle("new test_log", msg, ctx)
        result = await cmd.handle("list", msg, ctx)
        assert "test_log" in result

    @pytest.mark.asyncio
    async def test_log_pause_resume(self, temp_data_dir):
        from modules.commands.log import LogCommand
        cmd = LogCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        await cmd.handle("new test_log", msg, ctx)
        result = await cmd.handle("off", msg, ctx)
        assert "暂停" in result
        result = await cmd.handle("on", msg, ctx)
        assert "继续" in result

    @pytest.mark.asyncio
    async def test_log_end(self, temp_data_dir):
        from modules.commands.log import LogCommand
        cmd = LogCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        await cmd.handle("new test_log", msg, ctx)
        result = await cmd.handle("end", msg, ctx)
        assert "已完成" in result

    @pytest.mark.asyncio
    async def test_log_delete(self, temp_data_dir):
        from modules.commands.log import LogCommand
        cmd = LogCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        await cmd.handle("new test_log", msg, ctx)
        result = await cmd.handle("del test_log", msg, ctx)
        assert "已删除" in result

    @pytest.mark.asyncio
    async def test_log_help(self, temp_data_dir):
        from modules.commands.log import LogCommand
        cmd = LogCommand()
        msg = self._make_msg()
        ctx = self._make_ctx()
        result = await cmd.handle("help", msg, ctx)
        assert "子命令" in result

    @pytest.mark.asyncio
    async def test_log_private_chat(self, temp_data_dir):
        """日志命令在私聊应返回错误"""
        from modules.commands.log import LogCommand
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        cmd = LogCommand()
        raw = RawMessage(
            msg_id="test", source=MessageSource.PRIVATE, target_id="123",
            sender_id="456", sender_name="Test", content=".log new test",
            message_type=MessageType.TEXT,
        )
        msg = ParsedMessage(raw=raw, is_command=True, command_name="log", command_args="new test")
        ctx = self._make_ctx()
        result = await cmd.handle("new test", msg, ctx)
        assert "群聊" in result


# ================================================================
# HookManager 测试
# ================================================================

class TestHookManager:
    @pytest.mark.asyncio
    async def test_register_and_fire(self):
        from modules.hooks import HookManager
        hm = HookManager()
        received = []

        async def hook(msg, ctx):
            received.append(msg.raw.content)

        hm.register("on_message", hook)
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        raw = RawMessage(
            msg_id="1", source=MessageSource.GROUP, target_id="123",
            sender_id="456", sender_name="Test", content="Hello",
            message_type=MessageType.TEXT,
        )
        parsed = ParsedMessage(raw=raw)
        await hm.fire("on_message", parsed)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_unregister(self):
        from modules.hooks import HookManager
        hm = HookManager()
        received = []

        async def hook(msg, ctx):
            received.append(1)

        hm.register("test", hook)
        hm.unregister("test", hook)
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        raw = RawMessage(
            msg_id="2", source=MessageSource.GROUP, target_id="123",
            sender_id="456", sender_name="Test", content="X",
            message_type=MessageType.TEXT,
        )
        await hm.fire("test", ParsedMessage(raw=raw))
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_hook_error_handling(self):
        from modules.hooks import HookManager
        hm = HookManager()

        async def bad_hook(msg, ctx):
            raise ValueError("test error")

        hm.register("error_event", bad_hook)
        from protocols.messages import ParsedMessage, RawMessage, MessageSource, MessageType
        raw = RawMessage(
            msg_id="3", source=MessageSource.GROUP, target_id="123",
            sender_id="456", sender_name="Test", content="X",
            message_type=MessageType.TEXT,
        )
        # Should not raise
        await hm.fire("error_event", ParsedMessage(raw=raw))

    def test_get_registered(self):
        from modules.hooks import HookManager
        hm = HookManager()

        async def h1(msg, ctx): pass
        async def h2(msg, ctx): pass

        hm.register("ev", h1)
        hm.register("ev", h2)
        assert len(hm.get_registered("ev")) == 2

    def test_clear(self):
        from modules.hooks import HookManager
        hm = HookManager()

        async def h(msg, ctx): pass

        hm.register("ev1", h)
        hm.register("ev2", h)
        hm.clear("ev1")
        assert len(hm.get_registered("ev1")) == 0
        assert len(hm.get_registered("ev2")) == 1


# ================================================================
# utils 测试
# ================================================================

class TestUtils:
    def test_load_bot_setting_existing(self):
        from modules.commands.utils import load_bot_setting
        result = load_bot_setting("dice_core", "r_message", "{}")
        assert result != "{}"  # Should find the template
        assert "{}" in result

    def test_load_bot_setting_missing(self):
        from modules.commands.utils import load_bot_setting
        result = load_bot_setting("nonexistent", "key", "default_val")
        assert result == "default_val"

    def test_format_msg_basic(self):
        from modules.commands.utils import format_msg
        result = format_msg("Hello {}!", "world")
        assert result == "Hello world!"

    def test_format_msg_with_player(self):
        from modules.commands.utils import format_msg
        result = format_msg("<> rolled {}", "12", player_name="Alice")
        assert result == "Alice rolled 12"

    def test_format_msg_no_placeholder(self):
        from modules.commands.utils import format_msg
        result = format_msg("No placeholder here", "ignored")
        assert result == "No placeholder here"


# ================================================================
# CommandRegistry 集成测试
# ================================================================

class TestCommandRegistry:
    def test_all_24_commands_registered(self):
        from modules.command_module import CommandRegistry
        reg = CommandRegistry()
        reg.discover_builtin()
        assert len(reg.list_all()) == 24

    def test_new_commands_present(self):
        from modules.command_module import CommandRegistry
        reg = CommandRegistry()
        reg.discover_builtin()
        expected = ["r", "rh", "ra", "show", "del", "pc", "nn", "st",
                    "rk", "rkb", "rkp", "sck", "ark", "sn", "log"]
        for name in expected:
            cmd = reg.get(name)
            assert cmd is not None, f"Command .{name} not registered"

    def test_command_groups(self):
        from modules.command_module import CommandRegistry
        reg = CommandRegistry()
        reg.discover_builtin()
        groups = set(c.group for c in reg.list_all())
        assert "骰子" in groups
        assert "行于泰拉" in groups
        assert "日志" in groups
