"""
状态管理器单元测试
"""

import pytest
import asyncio
import json
from pathlib import Path
import tempfile
import shutil

from state.manager import StateManager, SharedState


@pytest.fixture
def temp_dir():
    """创建临时目录"""
    dirpath = tempfile.mkdtemp()
    yield Path(dirpath)
    shutil.rmtree(dirpath)


@pytest.fixture
async def state_manager(temp_dir):
    """创建状态管理器实例"""
    state_file = temp_dir / "test_state.json"
    manager = StateManager(state_file=state_file)
    await manager.initialize()
    return manager


class TestSharedState:
    """测试 SharedState 数据类"""

    def test_default_values(self):
        """测试默认值"""
        state = SharedState()
        assert state.observers == {}
        assert state.ob_groups == set()
        assert state.bot_blacklist == set()
        assert state.order_whitelist == set()
        assert state.saved_models == {}
        assert state.active_agents == {}
        assert state.modules_enabled == {"command": True, "cherrystudio": True}
        assert state.log_blacklist == set()

    def test_to_dict(self):
        """测试序列化为字典"""
        state = SharedState(
            observers={"group1": {"user1", "user2"}},
            ob_groups={"group1"},
            bot_blacklist={"group2"},
            order_whitelist={"group3"},
            saved_models={"session1": "gpt-4"},
            active_agents={"session1": "assistant"},
            modules_enabled={"command": False, "cherrystudio": True},
            log_blacklist={"group4"},
        )

        data = state.to_dict()

        assert set(data["observers"]["group1"]) == {"user1", "user2"}
        assert set(data["ob_groups"]) == {"group1"}
        assert set(data["bot_blacklist"]) == {"group2"}
        assert set(data["order_whitelist"]) == {"group3"}
        assert data["saved_models"]["session1"] == "gpt-4"
        assert data["active_agents"]["session1"] == "assistant"
        assert data["modules_enabled"]["command"] is False
        assert set(data["log_blacklist"]) == {"group4"}

    def test_from_dict(self):
        """测试从字典反序列化"""
        data = {
            "observers": {"group1": ["user1", "user2"]},
            "ob_groups": ["group1"],
            "bot_blacklist": ["group2"],
            "order_whitelist": ["group3"],
            "saved_models": {"session1": "gpt-4"},
            "active_agents": {"session1": "assistant"},
            "modules_enabled": {"command": False, "cherrystudio": True},
            "log_blacklist": ["group4"],
        }

        state = SharedState.from_dict(data)

        assert state.observers == {"group1": {"user1", "user2"}}
        assert state.ob_groups == {"group1"}
        assert state.bot_blacklist == {"group2"}
        assert state.order_whitelist == {"group3"}
        assert state.saved_models == {"session1": "gpt-4"}
        assert state.active_agents == {"session1": "assistant"}
        assert state.modules_enabled == {
            "command": False, "cherrystudio": True}
        assert state.log_blacklist == {"group4"}

    def test_round_trip(self):
        """测试序列化/反序列化往返"""
        original = SharedState(
            observers={"group1": {"user1"}},
            ob_groups={"group1"},
            bot_blacklist={"group2"},
        )

        data = original.to_dict()
        restored = SharedState.from_dict(data)

        assert restored.observers == original.observers
        assert restored.ob_groups == original.ob_groups
        assert restored.bot_blacklist == original.bot_blacklist


class TestStateManager:
    """测试 StateManager 类"""

    @pytest.mark.asyncio
    async def test_initialize_creates_file(self, state_manager):
        """测试初始化创建文件"""
        assert state_manager.state_file.exists()

    @pytest.mark.asyncio
    async def test_load_existing_state(self, temp_dir):
        """测试加载已存在的状态"""
        # 先创建一个状态文件
        state_file = temp_dir / "test_state.json"
        initial_manager = StateManager(state_file=state_file)
        await initial_manager.initialize()
        await initial_manager.add_to_blacklist("group123")

        # 创建新的管理器并加载
        new_manager = StateManager(state_file=state_file)
        await new_manager.initialize()

        assert "group123" in new_manager.state.bot_blacklist

    @pytest.mark.asyncio
    async def test_update_module_status(self, state_manager):
        """测试更新模块状态"""
        await state_manager.update_module_status("command", False)

        assert state_manager.is_module_enabled("command") is False
        assert state_manager.is_module_enabled("cherrystudio") is True

    @pytest.mark.asyncio
    async def test_blacklist_operations(self, state_manager):
        """测试黑名单操作"""
        await state_manager.add_to_blacklist("group1")
        assert state_manager.is_in_blacklist("group1") is True

        await state_manager.remove_from_blacklist("group1")
        assert state_manager.is_in_blacklist("group1") is False

    @pytest.mark.asyncio
    async def test_whitelist_operations(self, state_manager):
        """测试白名单操作"""
        await state_manager.add_to_whitelist("group1")
        assert state_manager.is_in_whitelist("group1") is True

        await state_manager.remove_from_whitelist("group1")
        assert state_manager.is_in_whitelist("group1") is False

    @pytest.mark.asyncio
    async def test_active_agent_operations(self, state_manager):
        """测试活跃 Agent 操作"""
        session_key = "group_123456"

        await state_manager.set_active_agent(session_key, "assistant")
        agent = await state_manager.get_active_agent(session_key)

        assert agent == "assistant"

        # 测试不存在的会话
        non_existent = await state_manager.get_active_agent("non_existent")
        assert non_existent is None

    @pytest.mark.asyncio
    async def test_state_persistence(self, temp_dir):
        """测试状态持久化"""
        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()

        # 修改状态
        await manager.add_to_blacklist("group1")
        await manager.add_to_whitelist("group2")

        # 验证文件内容
        content = state_file.read_text(encoding="utf-8")
        data = json.loads(content)

        assert "group1" in data["bot_blacklist"]
        assert "group2" in data["order_whitelist"]

    @pytest.mark.asyncio
    async def test_change_callback(self, state_manager):
        """测试状态变更回调"""
        changes_received = []

        async def on_change(changed_fields):
            changes_received.append(changed_fields)

        state_manager.register_change_callback(on_change)

        await state_manager.update_module_status("command", False)

        assert len(changes_received) == 1
        assert "modules_enabled" in changes_received[0]

    @pytest.mark.asyncio
    async def test_reload(self, state_manager):
        """测试重新加载状态"""
        # 直接修改文件
        data = state_manager.state.to_dict()
        data["bot_blacklist"].append("group999")
        state_manager.state_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # 重新加载
        await state_manager.reload()

        assert "group999" in state_manager.state.bot_blacklist

    @pytest.mark.asyncio
    async def test_concurrent_access(self, state_manager):
        """测试并发访问"""
        async def add_to_list(group_id):
            await state_manager.add_to_blacklist(group_id)

        # 并发添加多个群到黑名单
        tasks = [add_to_list(f"group{i}") for i in range(10)]
        await asyncio.gather(*tasks)

        assert len(state_manager.state.bot_blacklist) == 10


class TestModelPersistence:
    """测试模型偏好持久化"""

    @pytest.mark.asyncio
    async def test_set_and_get_saved_model(self, state_manager):
        """测试设置和获取模型偏好"""
        session_key = "group:123456"
        model_name = "gpt-4"

        await state_manager.set_saved_model(session_key, model_name)
        result = await state_manager.get_saved_model(session_key)

        assert result == model_name

    @pytest.mark.asyncio
    async def test_get_saved_model_not_set(self, state_manager):
        """测试获取未设置的模型偏好"""
        result = await state_manager.get_saved_model("non_existent")
        assert result is None

    @pytest.mark.asyncio
    async def test_remove_saved_model(self, state_manager):
        """测试移除模型偏好"""
        session_key = "group:123456"
        await state_manager.set_saved_model(session_key, "gpt-4")
        await state_manager.remove_saved_model(session_key)

        result = await state_manager.get_saved_model(session_key)
        assert result is None

    @pytest.mark.asyncio
    async def test_remove_saved_model_not_exists(self, state_manager):
        """测试移除不存在的模型偏好 (不报错)"""
        await state_manager.remove_saved_model("non_existent")  # 不应抛异常

    @pytest.mark.asyncio
    async def test_model_persistence_round_trip(self, temp_dir):
        """测试模型偏好跨重启持久化"""
        state_file = temp_dir / "test_state.json"

        # 第一个管理器: 写入模型偏好
        manager1 = StateManager(state_file=state_file)
        await manager1.initialize()
        await manager1.set_saved_model("group:111", "claude-3-opus")
        await manager1.set_saved_model("private:222", "gpt-4")

        # 第二个管理器: 读取验证
        manager2 = StateManager(state_file=state_file)
        await manager2.initialize()

        assert await manager2.get_saved_model("group:111") == "claude-3-opus"
        assert await manager2.get_saved_model("private:222") == "gpt-4"
        assert await manager2.get_saved_model("group:333") is None

    @pytest.mark.asyncio
    async def test_model_overwrite(self, state_manager):
        """测试模型偏好覆盖"""
        session_key = "group:123456"

        await state_manager.set_saved_model(session_key, "gpt-4")
        await state_manager.set_saved_model(session_key, "claude-3-sonnet")

        result = await state_manager.get_saved_model(session_key)
        assert result == "claude-3-sonnet"

    @pytest.mark.asyncio
    async def test_model_persistence_in_file(self, state_manager):
        """测试模型偏好正确写入 JSON 文件"""
        await state_manager.set_saved_model("group:100", "my-model")

        content = state_manager.state_file.read_text(encoding="utf-8")
        data = json.loads(content)

        assert "saved_models" in data
        assert data["saved_models"]["group:100"] == "my-model"


class TestLegacyFileMigration:
    """测试双向合并旧持久化文件"""

    @pytest.mark.asyncio
    async def test_merge_order_whitelist(self, temp_dir):
        """测试合并 order_whitelist 旧文件"""
        # 创建旧格式的独立文件
        legacy_file = temp_dir / "order_whitelist.json"
        legacy_file.write_text(
            json.dumps(["group_A", "group_B"]), encoding="utf-8"
        )

        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()

        # 初始为空
        assert len(manager.state.order_whitelist) == 0

        # 合并旧文件
        await manager.merge_legacy_files()

        assert "group_A" in manager.state.order_whitelist
        assert "group_B" in manager.state.order_whitelist

    @pytest.mark.asyncio
    async def test_merge_bot_blacklist(self, temp_dir):
        """测试合并 bot_blacklist 旧文件"""
        legacy_file = temp_dir / "bot_blacklist.json"
        legacy_file.write_text(
            json.dumps(["blocked_1", "blocked_2"]), encoding="utf-8"
        )

        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()
        await manager.merge_legacy_files()

        assert "blocked_1" in manager.state.bot_blacklist
        assert "blocked_2" in manager.state.bot_blacklist

    @pytest.mark.asyncio
    async def test_merge_increments_only(self, temp_dir):
        """测试合并仅添加增量数据"""
        # SharedState 中已有 group_A
        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()
        await manager.add_to_whitelist("group_A")

        # 旧文件有 group_A + group_B
        legacy_file = temp_dir / "order_whitelist.json"
        legacy_file.write_text(
            json.dumps(["group_A", "group_B"]), encoding="utf-8"
        )

        await manager.merge_legacy_files()

        # 合并后应有两个 (group_A 不重复)
        assert len(manager.state.order_whitelist) == 2
        assert "group_A" in manager.state.order_whitelist
        assert "group_B" in manager.state.order_whitelist

    @pytest.mark.asyncio
    async def test_merge_writes_back_legacy_files(self, temp_dir):
        """测试合并后回写旧文件"""
        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()
        await manager.add_to_whitelist("new_group")

        await manager.merge_legacy_files()

        # 回写的旧文件应包含 new_group
        legacy_file = temp_dir / "order_whitelist.json"
        assert legacy_file.exists()
        data = json.loads(legacy_file.read_text(encoding="utf-8"))
        assert "new_group" in data

    @pytest.mark.asyncio
    async def test_merge_no_legacy_files(self, temp_dir):
        """测试无旧文件时合并不报错"""
        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()

        # 无旧文件，不应报错
        await manager.merge_legacy_files()

        assert len(manager.state.order_whitelist) == 0
        assert len(manager.state.bot_blacklist) == 0

    @pytest.mark.asyncio
    async def test_merge_corrupted_legacy_file(self, temp_dir):
        """测试旧文件损坏时合并不报错"""
        legacy_file = temp_dir / "order_whitelist.json"
        legacy_file.write_text("NOT VALID JSON{{{", encoding="utf-8")

        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()

        # 损坏的旧文件，应被安全忽略
        await manager.merge_legacy_files()

        assert len(manager.state.order_whitelist) == 0

    @pytest.mark.asyncio
    async def test_merge_log_blacklist(self, temp_dir):
        """测试合并 log_blacklist 旧文件"""
        legacy_file = temp_dir / "log_blacklist.json"
        legacy_file.write_text(
            json.dumps(["log_group_1"]), encoding="utf-8"
        )

        state_file = temp_dir / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()
        await manager.merge_legacy_files()

        assert "log_group_1" in manager.state.log_blacklist

    @pytest.mark.asyncio
    async def test_full_restart_persistence(self, temp_dir):
        """完整重启持久化场景: 写入 -> 合并 -> 新管理器验证"""
        state_file = temp_dir / "test_state.json"

        # 第一轮: 创建状态并写入旧文件
        manager1 = StateManager(state_file=state_file)
        await manager1.initialize()
        await manager1.add_to_whitelist("group_1")
        await manager1.set_active_agent("session_1", "agent_A")
        await manager1.set_saved_model("session_1", "gpt-4")

        # 模拟旧项目遗留文件
        (temp_dir / "bot_blacklist.json").write_text(
            json.dumps(["old_blocked"]), encoding="utf-8"
        )

        await manager1.merge_legacy_files()

        # 第二轮: 新管理器加载
        manager2 = StateManager(state_file=state_file)
        await manager2.initialize()
        await manager2.merge_legacy_files()

        # 验证所有持久化数据
        assert "group_1" in manager2.state.order_whitelist
        assert await manager2.get_active_agent("session_1") == "agent_A"
        assert await manager2.get_saved_model("session_1") == "gpt-4"
        assert "old_blocked" in manager2.state.bot_blacklist


class TestLegacyFileSync:
    """
    测试 add/remove 操作后遗留文件的实时同步。

    防止 remove_from_blacklist() 只更新 shared_state.json 而不更新
    bot_blacklist.json，导致重启时 merge_legacy_files() 从遗留文件
    读回已删除的数据 (僵尸复活 bug)。
    """

    @pytest.mark.asyncio
    async def test_remove_blacklist_syncs_legacy_file(self, state_manager):
        """remove_from_blacklist 应同步更新 bot_blacklist.json"""
        await state_manager.add_to_blacklist("group_A")
        await state_manager.add_to_blacklist("group_B")

        # 验证遗留文件已创建
        legacy_path = state_manager.state_file.parent / "bot_blacklist.json"
        assert legacy_path.exists()
        legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        assert set(legacy_data) == {"group_A", "group_B"}

        # 移除一个
        await state_manager.remove_from_blacklist("group_A")

        # 验证遗留文件已同步
        legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        assert set(legacy_data) == {"group_B"}
        assert "group_A" not in legacy_data

    @pytest.mark.asyncio
    async def test_remove_whitelist_syncs_legacy_file(self, state_manager):
        """remove_from_whitelist 应同步更新 order_whitelist.json"""
        await state_manager.add_to_whitelist("group_X")
        await state_manager.add_to_whitelist("group_Y")

        legacy_path = state_manager.state_file.parent / "order_whitelist.json"
        assert legacy_path.exists()

        await state_manager.remove_from_whitelist("group_X")

        legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        assert set(legacy_data) == {"group_Y"}

    @pytest.mark.asyncio
    async def test_bot_off_then_on_no_zombie_after_restart(self, temp_dir):
        """
        核心场景: .bot off → .bot on → 重启 → 群不应在黑名单中。

        修复前的 bug:
        1. .bot off → shared_state.json 和 bot_blacklist.json 都有该群
        2. .bot on → shared_state.json 移除该群, 但 bot_blacklist.json 仍有
        3. 重启 → merge_legacy_files() 从 bot_blacklist.json 读回 → 僵尸复活
        """
        state_file = temp_dir / "shared_state.json"

        # 第一轮: 模拟 .bot off 然后 .bot on
        manager1 = StateManager(state_file=state_file)
        await manager1.initialize()
        await manager1.merge_legacy_files()

        # 模拟 .bot off
        await manager1.add_to_blacklist("252578123")
        assert manager1.is_in_blacklist("252578123")

        # 模拟 .bot on
        await manager1.remove_from_blacklist("252578123")
        assert not manager1.is_in_blacklist("252578123")

        # 第二轮: 模拟重启
        manager2 = StateManager(state_file=state_file)
        await manager2.initialize()
        await manager2.merge_legacy_files()

        # 关键断言: 重启后不应在黑名单中 (不应僵尸复活)
        assert not manager2.is_in_blacklist("252578123"), (
            "bug 复现: .bot on 后重启，群又回到黑名单 (僵尸数据)"
        )

    @pytest.mark.asyncio
    async def test_remove_all_blacklist_clears_legacy_file(self, state_manager):
        """所有群都移除黑名单后，遗留文件应变为空列表"""
        await state_manager.add_to_blacklist("group_only")

        legacy_path = state_manager.state_file.parent / "bot_blacklist.json"
        assert legacy_path.exists()

        await state_manager.remove_from_blacklist("group_only")

        legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        assert legacy_data == []

    @pytest.mark.asyncio
    async def test_corrupted_state_file_creates_backup(self, temp_dir):
        """损坏的状态文件应被备份而非静默丢弃"""
        state_file = temp_dir / "shared_state.json"
        # 写入损坏的 JSON
        state_file.write_text("{ broken json content !!!", encoding="utf-8")

        manager = StateManager(state_file=state_file)
        await manager.initialize()

        # 应使用默认空状态
        assert len(manager.state.bot_blacklist) == 0

        # 损坏文件应被备份
        backup_path = state_file.with_suffix(".json.corrupted")
        assert backup_path.exists()
        assert "broken json" in backup_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
