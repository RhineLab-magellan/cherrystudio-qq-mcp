"""
会话持久化存储模块 (ConversationStore)

职责:
1. 按 Agent 分目录存储会话数据
2. 会话日志、元数据、记忆摘要的持久化
3. 不活跃会话检测与自动摘要压缩
4. 启动时会话校验与恢复
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class SessionMeta:
    """会话元数据"""

    def __init__(
        self,
        session_key: str,
        agent_name: str,
        created_at: str | None = None,
        last_active: str | None = None,
        message_count: int = 0,
        remote_session_id: str | None = None,
    ):
        self.session_key = session_key
        self.agent_name = agent_name
        self.created_at = created_at or datetime.now().isoformat()
        self.last_active = last_active or datetime.now().isoformat()
        self.message_count = message_count
        self.remote_session_id = remote_session_id  # CherryStudio 远程会话 ID (B1 修复)
        self.force_stale = False  # 强制标记为过期

    def to_dict(self) -> dict:
        return {
            "session_key": self.session_key,
            "agent_name": self.agent_name,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "message_count": self.message_count,
            "remote_session_id": self.remote_session_id,
            "force_stale": self.force_stale,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMeta":
        return cls(
            session_key=data["session_key"],
            agent_name=data["agent_name"],
            created_at=data.get("created_at"),
            last_active=data.get("last_active"),
            message_count=data.get("message_count", 0),
            remote_session_id=data.get("remote_session_id"),  # 兼容旧数据
        )


class ConversationStore:
    """
    会话持久化存储

    目录结构:
    QQConversationRecord/
    ├── mapping.json              # 会话 -> Agent 映射
    ├── {agent_name}/
    │   ├── {session_key}/
    │   │   ├── session.json      # 消息日志
    │   │   ├── meta.json         # 元数据
    │   │   └── memory.json       # 记忆摘要
    """

    def __init__(self, base_dir: str | None = None):
        if base_dir is None:
            # 默认路径: 相对于项目根目录 (与 server.py 同级)
            # 不能用相对路径，因为 CherryStudio 启动子进程时 CWD 不是项目目录
            base_dir = str(Path(__file__).parent.parent / "QQConversationRecord")
        self.base_dir = Path(base_dir)
        self.mapping_file = self.base_dir / "mapping.json"
        self.sessions: dict[str, deque] = {}  # session_key -> messages
        self.metas: dict[str, SessionMeta] = {}  # session_key -> meta
        self.memories: dict[str, str] = {}  # session_key -> memory summary
        self.mapping: dict[str, str] = {}  # session_key -> agent_name
        self._lock = asyncio.Lock()

        # 创建基础目录
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 加载现有数据
        self._load_mapping()

    def _load_mapping(self):
        """加载会话映射表"""
        if self.mapping_file.exists():
            try:
                data = json.loads(
                    self.mapping_file.read_text(encoding="utf-8"))
                self.mapping = data.get("mapping", {})
                logger.info(f"Loaded {len(self.mapping)} session mappings")
            except Exception as e:
                logger.error(f"Load session mapping failed: {e}")
                self.mapping = {}
        else:
            self.mapping = {}

    def _save_mapping(self):
        """保存会话映射表"""
        try:
            data = {"mapping": self.mapping}
            self.mapping_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Save session mapping failed: {e}")

    async def load_session(
        self, session_key: str, agent_name: str
    ) -> tuple[deque, str]:
        """
        加载或创建会话

        Args:
            session_key: 会话键 (group_123456 或 private_789)
            agent_name: Agent 名称

        Returns:
            (messages deque, memory summary)
        """
        async with self._lock:
            return await self._load_session_unlocked(session_key, agent_name)

    async def _load_session_unlocked(
        self, session_key: str, agent_name: str
    ) -> tuple[deque, str]:
        """
        加载或创建会话 (内部方法，调用方需已持有 _lock)

        Args:
            session_key: 会话键
            agent_name: Agent 名称

        Returns:
            (messages deque, memory summary)
        """
        # 检查内存缓存
        if session_key in self.sessions:
            return self.sessions[session_key], self.memories.get(session_key, "")

        # 从文件加载
        session_dir = self.base_dir / agent_name / session_key
        session_file = session_dir / "session.json"
        meta_file = session_dir / "meta.json"
        memory_file = session_dir / "memory.json"

        messages = deque(maxlen=40)
        memory = ""

        if session_file.exists():
            try:
                # 加载消息日志
                data = json.loads(session_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    messages = deque(data, maxlen=40)

                # 加载元数据
                if meta_file.exists():
                    meta_data = json.loads(
                        meta_file.read_text(encoding="utf-8"))
                    meta = SessionMeta.from_dict(meta_data)
                    self.metas[session_key] = meta

                # 加载记忆摘要
                if memory_file.exists():
                    memory = memory_file.read_text(encoding="utf-8")

                logger.info(f"Session loaded: {session_key} ({len(messages)} messages)")
            except Exception as e:
                logger.error(f"Load session failed [{session_key}]: {e}", exc_info=True)
        else:
            # 创建新会话目录
            session_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created new session: {session_key}")

        # 更新映射
        self.mapping[session_key] = agent_name
        self._save_mapping()

        # 缓存到内存
        self.sessions[session_key] = messages
        self.memories[session_key] = memory

        # 创建元数据（如果不存在）
        if session_key not in self.metas:
            self.metas[session_key] = SessionMeta(
                session_key=session_key, agent_name=agent_name
            )

        return messages, memory

    async def save_session(self, session_key: str, agent_name: str):
        """
        保存会话到文件

        Args:
            session_key: 会话键
            agent_name: Agent 名称
        """
        async with self._lock:
            await self._save_session_unlocked(session_key, agent_name)

    async def _save_session_unlocked(self, session_key: str, agent_name: str):
        """
        保存会话到文件 (内部方法，调用方需已持有 _lock)

        Args:
            session_key: 会话键
            agent_name: Agent 名称
        """
        if session_key not in self.sessions:
            return

        session_dir = self.base_dir / agent_name / session_key
        session_dir.mkdir(parents=True, exist_ok=True)

        messages = self.sessions[session_key]
        meta = self.metas.get(session_key)
        memory = self.memories.get(session_key, "")

        try:
            # 保存消息日志
            session_file = session_dir / "session.json"
            session_file.write_text(
                json.dumps(list(messages), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 保存元数据
            if meta:
                meta.last_active = datetime.now().isoformat()
                meta.message_count = len(messages)
                meta_file = session_dir / "meta.json"
                meta_file.write_text(
                    json.dumps(meta.to_dict(),
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            # 保存记忆摘要
            if memory:
                memory_file = session_dir / "memory.json"
                memory_file.write_text(memory, encoding="utf-8")

            logger.debug(f"Session saved: {session_key}")
        except Exception as e:
            logger.error(f"Save session failed [{session_key}]: {e}", exc_info=True)

    async def add_message(
        self, session_key: str, agent_name: str, message: dict
    ):
        """
        添加消息到会话

        Args:
            session_key: 会话键
            agent_name: Agent 名称
            message: 消息字典
        """
        async with self._lock:
            # 确保会话已加载
            if session_key not in self.sessions:
                await self._load_session_unlocked(session_key, agent_name)

            # 添加消息
            self.sessions[session_key].append(message)

            # 更新元数据
            if session_key in self.metas:
                self.metas[session_key].last_active = datetime.now().isoformat()
                self.metas[session_key].message_count = len(
                    self.sessions[session_key]
                )

    async def check_stale_sessions(self, days_threshold: int = 3) -> list[str]:
        """
        检查不活跃会话

        Args:
            days_threshold: 不活跃天数阈值

        Returns:
            过期会话键列表
        """
        stale_sessions = []
        now = datetime.now()

        for session_key, meta in self.metas.items():
            if meta.force_stale:
                stale_sessions.append(session_key)
                continue

            try:
                last_active = datetime.fromisoformat(meta.last_active)
                if (now - last_active).days >= days_threshold:
                    stale_sessions.append(session_key)
            except Exception as e:
                logger.warning(f"Parse last active time failed [{session_key}]: {e}")

        return stale_sessions

    async def summarize_and_archive(
        self, session_key: str, agent_name: str, summary: str
    ):
        """
        对过期会话进行摘要压缩并归档

        Args:
            session_key: 会话键
            agent_name: Agent 名称
            summary: AI 生成的摘要
        """
        async with self._lock:
            # 保存摘要
            self.memories[session_key] = summary

            # 清空消息队列，保留摘要
            if session_key in self.sessions:
                self.sessions[session_key].clear()

            # 更新元数据
            if session_key in self.metas:
                self.metas[session_key].force_stale = True

            # 保存到文件
            await self._save_session_unlocked(session_key, agent_name)

            logger.info(f"Session archived: {session_key}")

    async def delete_session(self, session_key: str, agent_name: str):
        """
        删除会话

        Args:
            session_key: 会话键
            agent_name: Agent 名称
        """
        async with self._lock:
            # 从内存中移除
            self.sessions.pop(session_key, None)
            self.metas.pop(session_key, None)
            self.memories.pop(session_key, None)
            self.mapping.pop(session_key, None)

            # 删除文件目录
            session_dir = self.base_dir / agent_name / session_key
            if session_dir.exists():
                try:
                    import shutil

                    shutil.rmtree(session_dir)
                    logger.info(f"Session directory deleted: {session_dir}")
                except Exception as e:
                    logger.error(f"Delete session directory failed [{session_key}]: {e}")

            # 更新映射文件
            self._save_mapping()

    async def get_session_messages(self, session_key: str) -> list[dict]:
        """获取会话消息列表"""
        async with self._lock:
            if session_key in self.sessions:
                return list(self.sessions[session_key])
            return []

    async def get_session_memory(self, session_key: str) -> str:
        """获取会话记忆摘要"""
        async with self._lock:
            return self.memories.get(session_key, "")

    def is_session_stale(self, session_key: str, days_threshold: int = 3) -> bool:
        """
        检查会话是否过期 (基于 meta.last_active)

        Args:
            session_key: 会话键
            days_threshold: 不活跃天数阈值 (默认 3 天)

        Returns:
            True 表示已过期
        """
        meta = self.metas.get(session_key)
        if not meta:
            return False

        if meta.force_stale:
            return True

        try:
            last_active = datetime.fromisoformat(meta.last_active)
            delta = datetime.now() - last_active
            return delta.days >= days_threshold
        except (ValueError, TypeError):
            return False

    def get_stale_session_keys(self, days_threshold: int = 3) -> list[str]:
        """
        获取所有过期会话的键列表

        Args:
            days_threshold: 不活跃天数阈值

        Returns:
            过期会话键列表
        """
        stale = []
        for key, meta in self.metas.items():
            if meta.force_stale:
                stale.append(key)
                continue
            try:
                last_active = datetime.fromisoformat(meta.last_active)
                if (datetime.now() - last_active).days >= days_threshold:
                    stale.append(key)
            except (ValueError, TypeError):
                continue
        return stale

    def get_session_messages_sync(self, session_key: str) -> list[dict]:
        """同步获取会话消息列表 (用于摘要生成)"""
        if session_key in self.sessions:
            return list(self.sessions[session_key])
        return []

    # ------------------------------------------------------------------
    # B1 修复: 远程 session_id 持久化 / 恢复
    # ------------------------------------------------------------------

    async def set_remote_session_id(
        self, session_key: str, agent_name: str, session_id: str | None
    ):
        """
        持久化远程 CherryStudio session_id 到 meta.json

        在 handler 创建远程会话后立即调用，确保 session_id 不随 handler
        超时退出而丢失。仅写 meta.json，不触发完整 session 保存（轻量化）。

        Args:
            session_key: 会话键
            agent_name: Agent 名称
            session_id: CherryStudio 远程会话 ID，None 表示清除
        """
        async with self._lock:
            # 确保会话已加载到内存
            if session_key not in self.sessions:
                await self._load_session_unlocked(session_key, agent_name)

            meta = self.metas.get(session_key)
            if not meta:
                meta = SessionMeta(session_key=session_key, agent_name=agent_name)
                self.metas[session_key] = meta

            meta.remote_session_id = session_id

            # 只写 meta.json (轻量化，不触发完整 session 保存)
            session_dir = self.base_dir / agent_name / session_key
            session_dir.mkdir(parents=True, exist_ok=True)
            meta_file = session_dir / "meta.json"
            meta.last_active = datetime.now().isoformat()
            try:
                meta_file.write_text(
                    json.dumps(meta.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.error(
                    f"Persist remote_session_id failed [{session_key}]: {e}",
                    exc_info=True,
                )

    async def get_remote_session_id(
        self, session_key: str, agent_name: str
    ) -> str | None:
        """
        从 meta.json 恢复远程 CherryStudio session_id

        handler 重建时调用，用于复用已有的远程会话而非创建新的孤立会话。

        Args:
            session_key: 会话键
            agent_name: Agent 名称

        Returns:
            远程 session_id，未找到或已清除时返回 None
        """
        async with self._lock:
            # 先查内存缓存
            meta = self.metas.get(session_key)
            if meta:
                return meta.remote_session_id

            # 内存未命中，从磁盘加载 meta.json
            meta_file = self.base_dir / agent_name / session_key / "meta.json"
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    sid = data.get("remote_session_id")
                    # 回填到内存 (完整加载 meta 对象)
                    self.metas[session_key] = SessionMeta.from_dict(data)
                    return sid
                except Exception as e:
                    logger.warning(
                        f"Read remote_session_id failed [{session_key}]: {e}"
                    )
            return None

    # ------------------------------------------------------------------
    # B3 修复: Agent 切换时缓存失效
    # ------------------------------------------------------------------

    def invalidate_session(self, session_key: str):
        """
        从内存缓存中移除指定会话的缓存数据 (不同步删除磁盘文件)

        在 .order 切换 Agent 时调用，强制下次 load_session 从磁盘重新加载
        新 Agent 目录下的数据，避免返回旧 Agent 的缓存内容。

        Args:
            session_key: 会话键
        """
        self.sessions.pop(session_key, None)
        self.metas.pop(session_key, None)
        self.memories.pop(session_key, None)
        logger.debug(f"Session cache invalidated: {session_key}")

    async def reconcile_sessions(self, server_sessions: list[str]):
        """
        校验本地会话与 CherryStudio 服务端的一致性

        Args:
            server_sessions: 服务端的会话 ID 列表
        """
        logger.info("Starting session consistency check...")

        # 找出本地存在但服务端不存在的会话
        local_keys = set(self.mapping.keys())
        server_keys = set(server_sessions)

        orphaned = local_keys - server_keys
        if orphaned:
            logger.warning(f"Found {len(orphaned)} orphaned sessions: {orphaned}")
            # TODO: 可以选择删除或归档这些会话

        logger.info("Session consistency check complete")

    async def validate_sessions(self) -> dict[str, Any]:
        """
        启动时会话完整性校验

        扫描所有会话目录，验证:
        1. session.json 的 JSON 格式完整性
        2. meta.json 的必要字段 (session_key, agent_name)
        3. memory.json 的可读性

        损坏的文件自动备份到 .corrupted/ 子目录。

        Returns:
            校验结果摘要 {"total": int, "valid": int, "corrupted": int, "details": list}
        """
        logger.info("Starting session integrity check...")

        result = {"total": 0, "valid": 0, "corrupted": 0, "details": []}
        corrupted_dir = self.base_dir / ".corrupted"

        # 遍历所有 agent 目录
        if not self.base_dir.exists():
            logger.info("Session directory not found, skipping check")
            return result

        for agent_dir in self.base_dir.iterdir():
            if not agent_dir.is_dir() or agent_dir.name.startswith("."):
                continue

            # 遍历每个会话目录
            for session_dir in agent_dir.iterdir():
                if not session_dir.is_dir():
                    continue

                session_key = session_dir.name
                result["total"] += 1
                issues = []

                # 1. 校验 session.json
                session_file = session_dir / "session.json"
                if session_file.exists():
                    try:
                        data = json.loads(session_file.read_text(encoding="utf-8"))
                        if not isinstance(data, list):
                            issues.append("session.json: 期望列表格式")
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        issues.append(f"session.json: JSON 损坏 ({e})")

                # 2. 校验 meta.json
                meta_file = session_dir / "meta.json"
                if meta_file.exists():
                    try:
                        meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                        required = ("session_key", "agent_name")
                        for field_name in required:
                            if field_name not in meta_data:
                                issues.append(f"meta.json: 缺少必要字段 '{field_name}'")
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        issues.append(f"meta.json: JSON 损坏 ({e})")

                # 3. 校验 memory.json (可选文件，仅检查可读性)
                memory_file = session_dir / "memory.json"
                if memory_file.exists():
                    try:
                        memory_file.read_text(encoding="utf-8")
                    except UnicodeDecodeError as e:
                        issues.append(f"memory.json: 编码错误 ({e})")

                if issues:
                    result["corrupted"] += 1
                    result["details"].append({
                        "session_key": session_key,
                        "agent": agent_dir.name,
                        "issues": issues,
                    })
                    logger.warning(
                        f"Session validation failed [{session_key}]: {'; '.join(issues)}"
                    )

                    # 备份损坏的会话目录
                    backup_dir = corrupted_dir / agent_dir.name / session_key
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    for f in session_dir.iterdir():
                        if f.is_file():
                            try:
                                import shutil
                                shutil.copy2(str(f), str(backup_dir / f.name))
                            except Exception as e:
                                logger.error(f"Backup file failed {f.name}: {e}")

                    logger.info(f"Corrupted session backed up to .corrupted/{agent_dir.name}/{session_key}")
                else:
                    result["valid"] += 1

        logger.info(
            f"Session validation complete: {result['total']} total, "
            f"{result['valid']} valid, "
            f"{result['corrupted']} corrupted"
        )
        return result
