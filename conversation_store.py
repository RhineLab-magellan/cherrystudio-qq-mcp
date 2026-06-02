"""
会话持久化存储
- 每个 Agent 独立文件夹: QQConversationRecord/{agent_name}/
- 目录结构:
    QQConversationRecord/
      mapping.json                      # 会话 → Agent 映射
      麦哲伦/
        {msg_type}_{target_id}/
          session.json                  # 当前会话消息日志
          memory.json                   # 历史摘要记忆
          meta.json                     # 元数据 (最后活跃时间等)
"""

import json
import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("conv-store")

BASE_DIR = Path(__file__).parent / "QQConversationRecord"
MAPPING_FILE = BASE_DIR / "mapping.json"
INACTIVE_DAYS = 3


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------

def _conv_dir(agent_name: str, msg_type: str, target_id: str) -> Path:
    return BASE_DIR / agent_name / f"{msg_type}_{target_id}"


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Agent 映射 (会话 → Agent 名称)
# ---------------------------------------------------------------------------

def _load_mapping() -> dict:
    if MAPPING_FILE.exists():
        return json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    return {}


def _save_mapping(mapping: dict):
    _ensure_dir(BASE_DIR)
    MAPPING_FILE.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")


def get_conversation_agent(msg_type: str, target_id: str) -> str:
    """查询某个会话当前绑定的 Agent 名称"""
    mapping = _load_mapping()
    return mapping.get(f"{msg_type}_{target_id}", "")


def set_conversation_agent(msg_type: str, target_id: str, agent_name: str):
    """设置/更新会话的 Agent 绑定"""
    mapping = _load_mapping()
    mapping[f"{msg_type}_{target_id}"] = agent_name
    _save_mapping(mapping)
    logger.info(f"Agent 绑定: {msg_type}_{target_id} → {agent_name}")


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------


def load_meta(agent_name: str, msg_type: str, target_id: str) -> dict:
    path = _conv_dir(agent_name, msg_type, target_id) / "meta.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"created_at": now_iso(), "last_active": now_iso(), "agent_session_id": "", "message_count": 0}


def save_meta(agent_name: str, msg_type: str, target_id: str, updates: dict):
    d = _conv_dir(agent_name, msg_type, target_id)
    _ensure_dir(d)
    meta = load_meta(agent_name, msg_type, target_id)
    meta.update(updates)
    meta["last_active"] = now_iso()
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def touch_active(agent_name: str, msg_type: str, target_id: str):
    save_meta(agent_name, msg_type, target_id, {})


# ---------------------------------------------------------------------------
# 会话日志
# ---------------------------------------------------------------------------


def load_session_log(agent_name: str, msg_type: str, target_id: str) -> list[dict]:
    path = _conv_dir(agent_name, msg_type, target_id) / "session.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_session_log(agent_name: str, msg_type: str, target_id: str, messages: list[dict]):
    d = _conv_dir(agent_name, msg_type, target_id)
    _ensure_dir(d)
    (d / "session.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")


def append_to_log(agent_name: str, msg_type: str, target_id: str, entry: dict):
    messages = load_session_log(agent_name, msg_type, target_id)
    messages.append(entry)
    save_session_log(agent_name, msg_type, target_id, messages)
    meta = load_meta(agent_name, msg_type, target_id)
    meta["message_count"] = len(messages)
    save_meta(agent_name, msg_type, target_id, meta)


# ---------------------------------------------------------------------------
# 摘要记忆
# ---------------------------------------------------------------------------


def load_memory(agent_name: str, msg_type: str, target_id: str) -> str:
    path = _conv_dir(agent_name, msg_type, target_id) / "memory.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("summary", "")
    return ""


def save_memory(agent_name: str, msg_type: str, target_id: str, summary: str):
    d = _conv_dir(agent_name, msg_type, target_id)
    _ensure_dir(d)
    (d / "memory.json").write_text(
        json.dumps({"summary": summary, "compressed_at": now_iso()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 不活跃检查
# ---------------------------------------------------------------------------


def is_stale(agent_name: str, msg_type: str, target_id: str) -> bool:
    meta = load_meta(agent_name, msg_type, target_id)
    last = meta.get("last_active", "")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
        delta = datetime.now(timezone.utc) - last_dt
        return delta.days >= INACTIVE_DAYS
    except ValueError:
        return False


def get_agent_session_id(agent_name: str, msg_type: str, target_id: str) -> str:
    return load_meta(agent_name, msg_type, target_id).get("agent_session_id", "")


def set_agent_session_id(agent_name: str, msg_type: str, target_id: str, sid: str):
    meta = load_meta(agent_name, msg_type, target_id)
    meta["agent_session_id"] = sid
    save_meta(agent_name, msg_type, target_id, meta)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_session_name(msg_type: str, target_id: str) -> str:
    return f"{msg_type}:{target_id}"


def delete_session(agent_name: str, msg_type: str, target_id: str):
    conv_dir = _conv_dir(agent_name, msg_type, target_id)
    if conv_dir.exists():
        shutil.rmtree(str(conv_dir))
        logger.info(f"会话目录已删除: {conv_dir}")


def force_stale(agent_name: str, msg_type: str, target_id: str):
    meta = load_meta(agent_name, msg_type, target_id)
    stale_time = datetime.now(timezone.utc) - timedelta(days=INACTIVE_DAYS + 1)
    meta["last_active"] = stale_time.isoformat()
    save_meta(agent_name, msg_type, target_id, meta)
    logger.info(f"会话已标记为过期: {agent_name}/{msg_type}_{target_id}")
