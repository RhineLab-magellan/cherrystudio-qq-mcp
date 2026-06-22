"""
状态管理器 (StateManager)

职责:
1. 管理全局共享状态 (黑名单、白名单、Agent配置等)
2. 持久化状态到 JSON 文件
3. 提供线程安全的读写接口
4. 支持状态变更通知
"""

import json
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class SharedState:
    """
    系统级共享状态

    所有模块共享的状态数据，自动持久化到 JSON 文件。
    """
    # 旁观者列表 (群号 -> 用户ID集合)
    observers: dict[str, set[str]] = field(default_factory=dict)

    # 开启旁观者的群
    ob_groups: set[str] = field(default_factory=set)

    # .bot off 的群 (机器人黑名单)
    bot_blacklist: set[str] = field(default_factory=set)

    # 免 @ 的群 (指令白名单)
    order_whitelist: set[str] = field(default_factory=set)

    # 模型偏好 (会话键 -> 模型名称)
    saved_models: dict[str, str] = field(default_factory=dict)

    # 当前活跃的 Agent (会话键 -> Agent名称)
    active_agents: dict[str, str] = field(default_factory=dict)

    # 模块启用状态
    modules_enabled: dict[str, bool] = field(
        default_factory=lambda: {
            "command": True,
            "cherrystudio": True,
        }
    )

    # 日志黑名单
    log_blacklist: set[str] = field(default_factory=set)

    # 欢迎配置 {group_id: {"enabled": bool, "message": str}}
    welcome_config: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        return {
            "observers": {k: list(v) for k, v in self.observers.items()},
            "ob_groups": list(self.ob_groups),
            "bot_blacklist": list(self.bot_blacklist),
            "order_whitelist": list(self.order_whitelist),
            "saved_models": self.saved_models,
            "active_agents": self.active_agents,
            "modules_enabled": self.modules_enabled,
            "log_blacklist": list(self.log_blacklist),
            "welcome_config": self.welcome_config,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SharedState":
        """从字典创建"""
        return cls(
            observers={k: set(v)
                       for k, v in data.get("observers", {}).items()},
            ob_groups=set(data.get("ob_groups", [])),
            bot_blacklist=set(data.get("bot_blacklist", [])),
            order_whitelist=set(data.get("order_whitelist", [])),
            saved_models=data.get("saved_models", {}),
            active_agents=data.get("active_agents", {}),
            modules_enabled=data.get(
                "modules_enabled", {"command": True, "cherrystudio": True}),
            log_blacklist=set(data.get("log_blacklist", [])),
            welcome_config=data.get("welcome_config", {}),
        )


class StateManager:
    """
    状态管理器

    提供全局状态的读写接口，支持自动持久化和变更通知。

    使用示例:
        state_manager = StateManager()
        await state_manager.initialize()

        # 读取状态
        is_enabled = state_manager.state.modules_enabled["command"]

        # 修改状态 (自动持久化)
        await state_manager.update_module_status("command", False)
    """

    def __init__(self, state_file: Path | None = None):
        """
        初始化状态管理器

        Args:
            state_file: 状态文件路径，默认为 Temp/shared_state.json
        """
        if state_file is None:
            base_dir = Path(__file__).parent.parent
            self.state_file = base_dir / "Temp" / "shared_state.json"
        else:
            self.state_file = state_file

        self.state = SharedState()
        self._lock = asyncio.Lock()
        self._change_callbacks: list[callable] = []

    async def initialize(self):
        """
        初始化状态管理器

        从文件加载状态，如果文件不存在则创建默认状态。
        """
        try:
            if self.state_file.exists():
                await self._load_state()
                logger.info(f"State loaded: {self.state_file}")
            else:
                await self._save_state()
                logger.info(f"Created default state file: {self.state_file}")
        except Exception as e:
            logger.error(f"State initialization failed: {e}", exc_info=True)
            raise

    async def _load_state(self):
        """从文件加载状态

        如果 JSON 解析失败或数据结构异常，会备份损坏文件并使用默认状态，
        同时输出 WARNING 日志通知用户数据已丢失。
        """
        try:
            content = self.state_file.read_text(encoding="utf-8")
            data = json.loads(content)

            # Pydantic 验证 (仅发出警告，不阻止加载)
            try:
                from state.state_models import validate_state
                validate_state(data)
            except Exception as e:
                logger.warning(f"State file Pydantic validation warning (non-fatal): {e}")

            self.state = SharedState.from_dict(data)
        except json.JSONDecodeError as e:
            logger.error(f"State file JSON corrupted: {e}", exc_info=True)
            # 备份损坏的文件以便排查
            try:
                backup_path = self.state_file.with_suffix(".json.corrupted")
                self.state_file.rename(backup_path)
                logger.warning(
                    f"Corrupted state file backed up to {backup_path.name}, "
                    f"blacklist etc. will be reset to defaults"
                )
            except Exception:
                pass
            self.state = SharedState()
        except FileNotFoundError:
            logger.warning(f"State file not found: {self.state_file}, using default state")
            self.state = SharedState()
        except Exception as e:
            logger.error(f"Load state file failed: {e}", exc_info=True)
            self.state = SharedState()

    async def _save_state(self):
        """保存状态到文件"""
        try:
            # 确保目录存在
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            data = self.state.to_dict()
            content = json.dumps(data, ensure_ascii=False, indent=2)
            self.state_file.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.error(f"Save state file failed: {e}", exc_info=True)
            raise

    async def update_state(self, updates: dict[str, Any]):
        """
        更新状态 (批量)

        Args:
            updates: 要更新的字段和值
        """
        async with self._lock:
            for key, value in updates.items():
                if hasattr(self.state, key):
                    setattr(self.state, key, value)

            await self._save_state()
            await self._notify_changes(updates)

    async def update_module_status(self, module_name: str, enabled: bool):
        """
        更新模块启用状态

        Args:
            module_name: 模块名称 ("command" 或 "cherrystudio")
            enabled: 是否启用
        """
        async with self._lock:
            self.state.modules_enabled[module_name] = enabled
            await self._save_state()
            await self._notify_changes({"modules_enabled": self.state.modules_enabled})

    async def add_to_blacklist(self, group_id: str):
        """添加到机器人黑名单"""
        async with self._lock:
            self.state.bot_blacklist.add(group_id)
            await self._save_state()
            self._sync_legacy_file("bot_blacklist")

    async def remove_from_blacklist(self, group_id: str):
        """从机器人黑名单移除"""
        async with self._lock:
            self.state.bot_blacklist.discard(group_id)
            await self._save_state()
            self._sync_legacy_file("bot_blacklist")

    async def add_to_whitelist(self, group_id: str):
        """添加到指令白名单"""
        async with self._lock:
            self.state.order_whitelist.add(group_id)
            await self._save_state()
            self._sync_legacy_file("order_whitelist")

    async def remove_from_whitelist(self, group_id: str):
        """从指令白名单移除"""
        async with self._lock:
            self.state.order_whitelist.discard(group_id)
            await self._save_state()
            self._sync_legacy_file("order_whitelist")

    async def set_active_agent(self, session_key: str, agent_name: str):
        """设置会话的活跃 Agent"""
        async with self._lock:
            self.state.active_agents[session_key] = agent_name
            await self._save_state()

    async def get_active_agent(self, session_key: str) -> str | None:
        """获取会话的活跃 Agent"""
        return self.state.active_agents.get(session_key)

    async def set_saved_model(self, session_key: str, model_name: str):
        """
        设置会话的模型偏好 (持久化)

        Args:
            session_key: 会话键 (如 "group:123456")
            model_name: 模型名称 (如 "gpt-4" 或 "provider:model_id")
        """
        async with self._lock:
            self.state.saved_models[session_key] = model_name
            await self._save_state()

    async def get_saved_model(self, session_key: str) -> str | None:
        """
        获取会话的模型偏好

        Args:
            session_key: 会话键

        Returns:
            模型名称，未设置返回 None
        """
        return self.state.saved_models.get(session_key)

    async def remove_saved_model(self, session_key: str):
        """
        移除会话的模型偏好 (恢复默认模型)

        Args:
            session_key: 会话键
        """
        async with self._lock:
            self.state.saved_models.pop(session_key, None)
            await self._save_state()

    # ---- 欢迎配置 API ----

    async def set_welcome(self, group_id: str, enabled: bool | None = None,
                          message: str | None = None):
        """设置群聊欢迎配置 (持久化)"""
        async with self._lock:
            entry = self.state.welcome_config.get(group_id, {"enabled": False, "message": ""})
            if enabled is not None:
                entry["enabled"] = enabled
            if message is not None:
                entry["message"] = message
            self.state.welcome_config[group_id] = entry
            await self._save_state()

    def get_welcome(self, group_id: str) -> dict:
        """获取群聊欢迎配置 (只读)"""
        return self.state.welcome_config.get(
            group_id, {"enabled": False, "message": ""}
        )

    def is_module_enabled(self, module_name: str) -> bool:
        """检查模块是否启用 (无需锁，只读操作)"""
        return self.state.modules_enabled.get(module_name, False)

    def is_in_blacklist(self, group_id: str) -> bool:
        """检查群是否在黑名单中"""
        return group_id in self.state.bot_blacklist

    def is_in_whitelist(self, group_id: str) -> bool:
        """检查群是否在白名单中"""
        return group_id in self.state.order_whitelist

    def register_change_callback(self, callback: callable):
        """
        注册状态变更回调

        Args:
            callback: 回调函数，接收 (changed_fields: dict) 参数
        """
        self._change_callbacks.append(callback)

    async def _notify_changes(self, changed_fields: dict[str, Any]):
        """通知所有注册的回调"""
        for callback in self._change_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(changed_fields)
                else:
                    callback(changed_fields)
            except Exception as e:
                logger.error(f"State change callback failed: {e}", exc_info=True)

    async def reload(self):
        """重新加载状态 (用于热重载)"""
        async with self._lock:
            await self._load_state()
            logger.info("State reloaded")

    async def merge_legacy_files(self):
        """
        双向合并旧项目的独立持久化文件到 SharedState。

        旧项目中 order_whitelist 和 bot_blacklist 同时存储于:
        1. SharedState (Temp/shared_state.json)
        2. 独立文件 (Temp/order_whitelist.json, Temp/bot_blacklist.json)

        新版以 SharedState 为准，合并独立文件中的增量数据，
        再回写独立文件以保持双向一致。

        应在 initialize() 之后调用。
        """
        base_dir = self.state_file.parent  # Temp/
        legacy_files = {
            "order_whitelist": base_dir / "order_whitelist.json",
            "bot_blacklist": base_dir / "bot_blacklist.json",
            "log_blacklist": base_dir / "log_blacklist.json",
        }

        changed = False

        async with self._lock:
            for field_name, file_path in legacy_files.items():
                legacy_data = self._load_list_file(file_path)
                if not legacy_data:
                    continue

                current: set[str] = getattr(self.state, field_name, set())
                before = len(current)
                current |= legacy_data  # 合并增量
                setattr(self.state, field_name, current)

                if len(current) > before:
                    logger.info(
                        f"Merged from legacy file {field_name}: "
                        f"+{len(current) - before} entries "
                        f"(from {file_path.name})"
                    )
                    changed = True

            if changed:
                await self._save_state()

            # 回写独立文件 (确保双向一致)
            for field_name, file_path in legacy_files.items():
                current: set[str] = getattr(self.state, field_name, set())
                if current:
                    self._save_list_file(file_path, list(current))

    @staticmethod
    def _load_list_file(path: Path) -> set[str]:
        """从 JSON 文件加载字符串列表，失败返回空集合"""
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return set(str(item) for item in data)
        except Exception as e:
            logger.debug(f"Load legacy file {path.name} failed: {e}")
        return set()

    @staticmethod
    def _save_list_file(path: Path, data: list[str]):
        """将字符串列表写入 JSON 文件"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"Rewrite legacy file {path.name} failed: {e}")

    def _sync_legacy_file(self, field_name: str):
        """
        将指定字段的当前状态同步到对应的遗留文件。

        防止 shared_state.json 与遗留文件不一致，导致重启时
        merge_legacy_files() 从旧文件读回已删除的数据 (僵尸复活)。

        在 add_to_blacklist / remove_from_blacklist 等变更后调用。
        """
        legacy_map = {
            "bot_blacklist": "bot_blacklist.json",
            "order_whitelist": "order_whitelist.json",
            "log_blacklist": "log_blacklist.json",
        }
        filename = legacy_map.get(field_name)
        if not filename:
            return
        legacy_path = self.state_file.parent / filename
        current: set[str] = getattr(self.state, field_name, set())
        if current:
            self._save_list_file(legacy_path, list(current))
        elif legacy_path.exists():
            # 集合为空时，写入空列表以清除遗留文件中的旧数据
            self._save_list_file(legacy_path, [])
