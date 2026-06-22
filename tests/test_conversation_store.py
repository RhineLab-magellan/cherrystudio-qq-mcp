"""
ConversationStore 独立测试

覆盖:
- 会话 CRUD (创建/加载/保存/删除)
- 消息管理 (添加/获取/maxlen 截断)
- 元数据管理 (SessionMeta 序列化/反序列化)
- 记忆摘要 (保存/读取)
- 映射持久化 (mapping.json)
- 过期会话检测 (force_stale / days_threshold)
- 摘要归档 (summarize_and_archive)
- 远程 session_id 持久化 (B1 修复)
- Agent 切换缓存失效 (B3 修复)
- 会话协调 (reconcile_sessions)
- 启动时完整性校验 (validate_sessions)
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from modules.conversation_store import ConversationStore, SessionMeta


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conv_store(tmp_path):
    """创建临时目录下的 ConversationStore"""
    base_dir = tmp_path / "QQConversationRecord"
    return ConversationStore(base_dir=str(base_dir))


@pytest.fixture
async def populated_store(conv_store):
    """预填充一个会话的 ConversationStore"""
    sk = "group_123456"
    agent = "assistant"
    messages, memory = await conv_store.load_session(sk, agent)
    messages.append({"role": "user", "content": "你好"})
    messages.append({"role": "assistant", "content": "你好！"})
    await conv_store.save_session(sk, agent)
    return conv_store, sk, agent


# ---------------------------------------------------------------------------
# SessionMeta
# ---------------------------------------------------------------------------


class TestSessionMeta:
    def test_defaults(self):
        meta = SessionMeta(session_key="group_1", agent_name="bot")
        assert meta.session_key == "group_1"
        assert meta.agent_name == "bot"
        assert meta.message_count == 0
        assert meta.remote_session_id is None
        assert meta.force_stale is False
        assert meta.created_at is not None

    def test_to_dict(self):
        meta = SessionMeta(session_key="group_1", agent_name="bot", message_count=5)
        d = meta.to_dict()
        assert d["session_key"] == "group_1"
        assert d["agent_name"] == "bot"
        assert d["message_count"] == 5
        assert "created_at" in d
        assert "last_active" in d

    def test_from_dict(self):
        d = {
            "session_key": "group_1",
            "agent_name": "bot",
            "message_count": 10,
            "remote_session_id": "rs_123",
            "created_at": "2026-01-01T00:00:00",
            "last_active": "2026-06-01T12:00:00",
        }
        meta = SessionMeta.from_dict(d)
        assert meta.session_key == "group_1"
        assert meta.message_count == 10
        assert meta.remote_session_id == "rs_123"

    def test_roundtrip(self):
        meta = SessionMeta(session_key="private_999", agent_name="helper", message_count=3)
        meta.remote_session_id = "abc"
        meta.force_stale = True
        d = meta.to_dict()
        meta2 = SessionMeta.from_dict(d)
        assert meta2.session_key == meta.session_key
        assert meta2.agent_name == meta.agent_name
        assert meta2.remote_session_id == "abc"
        # force_stale 不在 from_dict 中恢复 (运行时设置)


# ---------------------------------------------------------------------------
# 会话 CRUD
# ---------------------------------------------------------------------------


class TestSessionCRUD:
    async def test_load_creates_new_session(self, conv_store):
        messages, memory = await conv_store.load_session("group_new", "bot")
        assert len(messages) == 0
        assert memory == ""
        assert "group_new" in conv_store.sessions
        assert "group_new" in conv_store.metas

    async def test_load_existing_session_from_disk(self, tmp_path):
        base_dir = tmp_path / "QQConv"
        # 手动创建会话文件
        session_dir = base_dir / "bot" / "group_old"
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text(
            json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8"
        )
        meta = SessionMeta(session_key="group_old", agent_name="bot", message_count=1)
        (session_dir / "meta.json").write_text(
            json.dumps(meta.to_dict()), encoding="utf-8"
        )
        (session_dir / "memory.json").write_text("之前的摘要", encoding="utf-8")

        store = ConversationStore(base_dir=str(base_dir))
        messages, memory = await store.load_session("group_old", "bot")
        assert len(messages) == 1
        assert messages[0]["content"] == "hi"
        assert memory == "之前的摘要"

    async def test_load_returns_cached_session(self, conv_store):
        msg1, _ = await conv_store.load_session("group_a", "bot")
        msg1.append({"role": "user", "content": "cached"})
        msg2, _ = await conv_store.load_session("group_a", "bot")
        assert len(msg2) == 1
        assert msg2[0]["content"] == "cached"

    async def test_save_persists_to_disk(self, populated_store):
        conv_store, sk, agent = populated_store
        session_dir = conv_store.base_dir / agent / sk
        assert (session_dir / "session.json").exists()
        assert (session_dir / "meta.json").exists()
        data = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        assert len(data) == 2

    async def test_save_updates_meta(self, populated_store):
        conv_store, sk, agent = populated_store
        meta = conv_store.metas[sk]
        assert meta.message_count == 2

    async def test_delete_session(self, populated_store):
        conv_store, sk, agent = populated_store
        await conv_store.delete_session(sk, agent)
        assert sk not in conv_store.sessions
        assert sk not in conv_store.metas
        assert sk not in conv_store.mapping
        session_dir = conv_store.base_dir / agent / sk
        assert not session_dir.exists()

    async def test_delete_nonexistent_session(self, conv_store):
        # 不应抛异常
        await conv_store.delete_session("nonexistent", "bot")

    async def test_save_nonexistent_session_noop(self, conv_store):
        # 保存未加载的会话不应抛异常
        await conv_store.save_session("ghost", "bot")


# ---------------------------------------------------------------------------
# 消息管理
# ---------------------------------------------------------------------------


class TestMessageManagement:
    async def test_add_message(self, conv_store):
        sk, agent = "group_msg", "bot"
        await conv_store.load_session(sk, agent)
        await conv_store.add_message(sk, agent, {"role": "user", "content": "hello"})
        messages = await conv_store.get_session_messages(sk)
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"

    async def test_add_message_updates_meta(self, conv_store):
        sk, agent = "group_meta", "bot"
        await conv_store.load_session(sk, agent)
        await conv_store.add_message(sk, agent, {"role": "user", "content": "a"})
        meta = conv_store.metas[sk]
        assert meta.message_count == 1

    async def test_add_message_auto_loads_session(self, conv_store):
        # 未预加载，add_message 应自动创建
        sk, agent = "group_autoload", "bot"
        await conv_store.add_message(sk, agent, {"role": "user", "content": "auto"})
        assert sk in conv_store.sessions
        messages = await conv_store.get_session_messages(sk)
        assert len(messages) == 1

    async def test_message_maxlen(self, conv_store):
        sk, agent = "group_maxlen", "bot"
        messages, _ = await conv_store.load_session(sk, agent)
        assert messages.maxlen == 40
        for i in range(50):
            messages.append({"role": "user", "content": f"msg_{i}"})
        assert len(messages) == 40
        assert messages[0]["content"] == "msg_10"  # 前 10 条被截断

    async def test_get_session_messages_empty(self, conv_store):
        result = await conv_store.get_session_messages("nonexistent")
        assert result == []

    async def test_get_session_messages_sync(self, conv_store):
        sk, agent = "group_sync", "bot"
        await conv_store.load_session(sk, agent)
        await conv_store.add_message(sk, agent, {"role": "user", "content": "sync"})
        result = conv_store.get_session_messages_sync(sk)
        assert len(result) == 1
        assert result[0]["content"] == "sync"

    async def test_get_session_messages_sync_empty(self, conv_store):
        result = conv_store.get_session_messages_sync("nope")
        assert result == []


# ---------------------------------------------------------------------------
# 记忆摘要
# ---------------------------------------------------------------------------


class TestMemory:
    async def test_memory_saved_to_disk(self, conv_store):
        sk, agent = "group_mem", "bot"
        await conv_store.load_session(sk, agent)
        conv_store.memories[sk] = "这是一段记忆"
        await conv_store.save_session(sk, agent)
        memory_file = conv_store.base_dir / agent / sk / "memory.json"
        assert memory_file.exists()
        assert memory_file.read_text(encoding="utf-8") == "这是一段记忆"

    async def test_memory_loaded_from_disk(self, tmp_path):
        base_dir = tmp_path / "Conv"
        session_dir = base_dir / "bot" / "group_m"
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text("[]", encoding="utf-8")
        (session_dir / "memory.json").write_text("旧记忆", encoding="utf-8")
        store = ConversationStore(base_dir=str(base_dir))
        _, memory = await store.load_session("group_m", "bot")
        assert memory == "旧记忆"

    async def test_get_session_memory(self, conv_store):
        sk, agent = "group_getmem", "bot"
        await conv_store.load_session(sk, agent)
        conv_store.memories[sk] = "test memory"
        result = await conv_store.get_session_memory(sk)
        assert result == "test memory"

    async def test_get_session_memory_empty(self, conv_store):
        result = await conv_store.get_session_memory("nope")
        assert result == ""


# ---------------------------------------------------------------------------
# 映射持久化
# ---------------------------------------------------------------------------


class TestMapping:
    async def test_mapping_saved_on_load_session(self, conv_store):
        await conv_store.load_session("group_map1", "agent_a")
        assert conv_store.mapping["group_map1"] == "agent_a"
        mapping_file = conv_store.mapping_file
        assert mapping_file.exists()
        data = json.loads(mapping_file.read_text(encoding="utf-8"))
        assert data["mapping"]["group_map1"] == "agent_a"

    async def test_mapping_loaded_on_init(self, tmp_path):
        base_dir = tmp_path / "Conv2"
        base_dir.mkdir(parents=True)
        mapping = {"mapping": {"group_x": "agent_y"}}
        (base_dir / "mapping.json").write_text(
            json.dumps(mapping), encoding="utf-8"
        )
        store = ConversationStore(base_dir=str(base_dir))
        assert store.mapping["group_x"] == "agent_y"

    async def test_mapping_updated_on_delete(self, populated_store):
        conv_store, sk, agent = populated_store
        await conv_store.delete_session(sk, agent)
        assert sk not in conv_store.mapping
        data = json.loads(conv_store.mapping_file.read_text(encoding="utf-8"))
        assert sk not in data["mapping"]

    async def test_mapping_corrupted_file(self, tmp_path):
        base_dir = tmp_path / "Conv3"
        base_dir.mkdir(parents=True)
        (base_dir / "mapping.json").write_text("not json", encoding="utf-8")
        store = ConversationStore(base_dir=str(base_dir))
        assert store.mapping == {}


# ---------------------------------------------------------------------------
# 过期会话检测
# ---------------------------------------------------------------------------


class TestStaleSessionDetection:
    async def test_is_session_stale_force(self, conv_store):
        sk, agent = "group_stale_f", "bot"
        await conv_store.load_session(sk, agent)
        conv_store.metas[sk].force_stale = True
        assert conv_store.is_session_stale(sk) is True

    async def test_is_session_stale_by_days(self, conv_store):
        sk, agent = "group_stale_d", "bot"
        await conv_store.load_session(sk, agent)
        old_time = (datetime.now() - timedelta(days=5)).isoformat()
        conv_store.metas[sk].last_active = old_time
        assert conv_store.is_session_stale(sk, days_threshold=3) is True

    async def test_is_session_not_stale(self, conv_store):
        sk, agent = "group_fresh", "bot"
        await conv_store.load_session(sk, agent)
        # 刚创建，last_active 是当前时间
        assert conv_store.is_session_stale(sk, days_threshold=3) is False

    async def test_is_session_stale_no_meta(self, conv_store):
        assert conv_store.is_session_stale("nonexistent") is False

    async def test_get_stale_session_keys(self, conv_store):
        for i in range(3):
            sk = f"group_s{i}"
            await conv_store.load_session(sk, "bot")
        # 让 s0 和 s1 过期
        conv_store.metas["group_s0"].last_active = (
            datetime.now() - timedelta(days=10)
        ).isoformat()
        conv_store.metas["group_s1"].force_stale = True
        stale = conv_store.get_stale_session_keys(days_threshold=3)
        assert "group_s0" in stale
        assert "group_s1" in stale
        assert "group_s2" not in stale

    async def test_check_stale_sessions(self, conv_store):
        await conv_store.load_session("group_c1", "bot")
        await conv_store.load_session("group_c2", "bot")
        conv_store.metas["group_c1"].last_active = (
            datetime.now() - timedelta(days=7)
        ).isoformat()
        stale = await conv_store.check_stale_sessions(days_threshold=3)
        assert "group_c1" in stale
        assert "group_c2" not in stale


# ---------------------------------------------------------------------------
# 摘要归档
# ---------------------------------------------------------------------------


class TestSummarizeAndArchive:
    async def test_summarize_clears_messages(self, populated_store):
        conv_store, sk, agent = populated_store
        messages = await conv_store.get_session_messages(sk)
        assert len(messages) == 2
        await conv_store.summarize_and_archive(sk, agent, "这是摘要")
        messages = await conv_store.get_session_messages(sk)
        assert len(messages) == 0

    async def test_summarize_saves_memory(self, populated_store):
        conv_store, sk, agent = populated_store
        await conv_store.summarize_and_archive(sk, agent, "归档摘要内容")
        assert conv_store.memories[sk] == "归档摘要内容"
        memory_file = conv_store.base_dir / agent / sk / "memory.json"
        assert memory_file.read_text(encoding="utf-8") == "归档摘要内容"

    async def test_summarize_sets_force_stale(self, populated_store):
        conv_store, sk, agent = populated_store
        await conv_store.summarize_and_archive(sk, agent, "摘要")
        assert conv_store.metas[sk].force_stale is True
        assert conv_store.is_session_stale(sk) is True


# ---------------------------------------------------------------------------
# 远程 session_id 持久化 (B1)
# ---------------------------------------------------------------------------


class TestRemoteSessionId:
    async def test_set_and_get_remote_session_id(self, conv_store):
        sk, agent = "group_rs", "bot"
        await conv_store.load_session(sk, agent)
        await conv_store.set_remote_session_id(sk, agent, "remote_abc")
        result = await conv_store.get_remote_session_id(sk, agent)
        assert result == "remote_abc"

    async def test_set_remote_id_persists_to_meta_json(self, conv_store):
        sk, agent = "group_rs_p", "bot"
        await conv_store.load_session(sk, agent)
        await conv_store.set_remote_session_id(sk, agent, "rs_persist")
        meta_file = conv_store.base_dir / agent / sk / "meta.json"
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        assert data["remote_session_id"] == "rs_persist"

    async def test_get_remote_id_from_disk(self, tmp_path):
        base_dir = tmp_path / "Conv_rs"
        session_dir = base_dir / "bot" / "group_rs_d"
        session_dir.mkdir(parents=True)
        meta = SessionMeta(
            session_key="group_rs_d",
            agent_name="bot",
            remote_session_id="disk_rs",
        )
        (session_dir / "meta.json").write_text(
            json.dumps(meta.to_dict()), encoding="utf-8"
        )
        store = ConversationStore(base_dir=str(base_dir))
        result = await store.get_remote_session_id("group_rs_d", "bot")
        assert result == "disk_rs"

    async def test_clear_remote_session_id(self, conv_store):
        sk, agent = "group_rs_clr", "bot"
        await conv_store.load_session(sk, agent)
        await conv_store.set_remote_session_id(sk, agent, "temp_id")
        await conv_store.set_remote_session_id(sk, agent, None)
        result = await conv_store.get_remote_session_id(sk, agent)
        assert result is None

    async def test_get_remote_id_nonexistent(self, conv_store):
        result = await conv_store.get_remote_session_id("nope", "bot")
        assert result is None


# ---------------------------------------------------------------------------
# Agent 切换缓存失效 (B3)
# ---------------------------------------------------------------------------


class TestInvalidateSession:
    async def test_invalidate_removes_from_cache(self, conv_store):
        sk, agent = "group_inv", "bot"
        await conv_store.load_session(sk, agent)
        assert sk in conv_store.sessions
        assert sk in conv_store.metas
        conv_store.invalidate_session(sk)
        assert sk not in conv_store.sessions
        assert sk not in conv_store.metas
        assert sk not in conv_store.memories

    async def test_invalidate_allows_reload_from_disk(self, conv_store):
        sk, agent = "group_inv_r", "bot"
        await conv_store.load_session(sk, agent)
        await conv_store.add_message(sk, agent, {"role": "user", "content": "before"})
        await conv_store.save_session(sk, agent)
        conv_store.invalidate_session(sk)
        messages, _ = await conv_store.load_session(sk, agent)
        assert len(messages) == 1
        assert messages[0]["content"] == "before"

    async def test_invalidate_nonexistent(self, conv_store):
        # 不应抛异常
        conv_store.invalidate_session("ghost_session")


# ---------------------------------------------------------------------------
# 会话协调
# ---------------------------------------------------------------------------


class TestReconcileSessions:
    async def test_reconcile_detects_orphaned(self, conv_store):
        await conv_store.load_session("group_r1", "bot")
        await conv_store.load_session("group_r2", "bot")
        # 模拟服务端只有 r1
        await conv_store.reconcile_sessions(["group_r1"])
        # reconcile 目前只记录日志，不自动删除
        # 但映射表应仍然存在两个
        assert "group_r1" in conv_store.mapping
        assert "group_r2" in conv_store.mapping

    async def test_reconcile_empty_server(self, conv_store):
        await conv_store.load_session("group_re", "bot")
        await conv_store.reconcile_sessions([])
        # 不自动删除
        assert "group_re" in conv_store.mapping


# ---------------------------------------------------------------------------
# 启动时完整性校验 (validate_sessions)
# ---------------------------------------------------------------------------


class TestValidateSessions:
    async def test_validate_empty_directory(self, conv_store):
        result = await conv_store.validate_sessions()
        assert result["total"] == 0
        assert result["valid"] == 0
        assert result["corrupted"] == 0

    async def test_validate_valid_session(self, populated_store):
        conv_store, sk, agent = populated_store
        result = await conv_store.validate_sessions()
        assert result["total"] == 1
        assert result["valid"] == 1
        assert result["corrupted"] == 0

    async def test_validate_corrupted_session_json(self, conv_store):
        # 创建损坏的 session.json
        session_dir = conv_store.base_dir / "bot" / "group_bad"
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text("not valid json{{{", encoding="utf-8")
        meta = SessionMeta(session_key="group_bad", agent_name="bot")
        (session_dir / "meta.json").write_text(
            json.dumps(meta.to_dict()), encoding="utf-8"
        )
        result = await conv_store.validate_sessions()
        assert result["total"] == 1
        assert result["corrupted"] == 1
        assert "session.json" in result["details"][0]["issues"][0]

    async def test_validate_missing_required_meta_fields(self, conv_store):
        session_dir = conv_store.base_dir / "bot" / "group_meta_bad"
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text("[]", encoding="utf-8")
        (session_dir / "meta.json").write_text(
            json.dumps({"some_field": "value"}), encoding="utf-8"
        )
        result = await conv_store.validate_sessions()
        assert result["corrupted"] == 1
        issues_text = " ".join(result["details"][0]["issues"])
        assert "session_key" in issues_text or "agent_name" in issues_text

    async def test_validate_backs_up_corrupted(self, conv_store):
        session_dir = conv_store.base_dir / "bot" / "group_bak"
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text("broken!!!", encoding="utf-8")
        result = await conv_store.validate_sessions()
        assert result["corrupted"] == 1
        backup_dir = conv_store.base_dir / ".corrupted" / "bot" / "group_bak"
        assert backup_dir.exists()
        assert (backup_dir / "session.json").exists()

    async def test_validate_multiple_sessions(self, conv_store):
        # 1 个有效 + 1 个损坏
        good_dir = conv_store.base_dir / "bot" / "group_good"
        good_dir.mkdir(parents=True)
        (good_dir / "session.json").write_text("[]", encoding="utf-8")
        meta_g = SessionMeta(session_key="group_good", agent_name="bot")
        (good_dir / "meta.json").write_text(
            json.dumps(meta_g.to_dict()), encoding="utf-8"
        )

        bad_dir = conv_store.base_dir / "bot" / "group_bad2"
        bad_dir.mkdir(parents=True)
        (bad_dir / "session.json").write_text("{invalid", encoding="utf-8")

        result = await conv_store.validate_sessions()
        assert result["total"] == 2
        assert result["valid"] == 1
        assert result["corrupted"] == 1

    async def test_validate_ignores_dot_directories(self, conv_store):
        # .corrupted 等 .开头的目录应被忽略
        dot_dir = conv_store.base_dir / ".corrupted"
        dot_dir.mkdir(parents=True)
        (dot_dir / "dummy.json").write_text("{}", encoding="utf-8")
        result = await conv_store.validate_sessions()
        assert result["total"] == 0

    async def test_validate_nonexistent_base_dir(self, tmp_path):
        store = ConversationStore(base_dir=str(tmp_path / "nonexistent"))
        result = await store.validate_sessions()
        assert result["total"] == 0
