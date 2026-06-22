"""
共享测试 fixtures — 所有测试文件可用

提供:
- state_manager: 临时 StateManager 实例
- make_raw_message / make_parsed_message: 消息工厂
- make_command_context: CommandContext 工厂
- temp_data_dir: character_store 临时数据目录隔离
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from state.manager import StateManager
from protocols.messages import (
    RawMessage,
    ParsedMessage,
    MessageSource,
    MessageType,
)
from modules.command_module import CommandContext, CommandRegistry
from modules.dice_core import character_store


# ---------------------------------------------------------------------------
# StateManager fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def state_manager():
    """创建临时目录下的 StateManager"""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file=state_file)
        await manager.initialize()
        yield manager


# ---------------------------------------------------------------------------
# 消息工厂
# ---------------------------------------------------------------------------

@pytest.fixture
def make_raw_message():
    """RawMessage 工厂函数"""

    def _factory(
        content: str = "hello",
        sender_id: str = "12345",
        sender_name: str = "TestUser",
        target_id: str = "67890",
        source: MessageSource = MessageSource.GROUP,
        msg_type: MessageType = MessageType.TEXT,
        msg_id: str = "msg_001",
    ) -> RawMessage:
        return RawMessage(
            msg_id=msg_id,
            source=source,
            target_id=target_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            message_type=msg_type,
        )

    return _factory


@pytest.fixture
def make_parsed_message(make_raw_message):
    """ParsedMessage 工厂函数 (自动从 RawMessage 构建)"""

    def _factory(
        content: str = ".help",
        is_command: bool = True,
        command_name: str | None = "help",
        command_args: str | None = "",
        sender_id: str = "12345",
        target_id: str = "67890",
        source: MessageSource = MessageSource.GROUP,
    ) -> ParsedMessage:
        raw = make_raw_message(
            content=content,
            sender_id=sender_id,
            target_id=target_id,
            source=source,
        )
        return ParsedMessage(
            raw=raw,
            is_command=is_command,
            command_name=command_name,
            command_args=command_args,
        )

    return _factory


# ---------------------------------------------------------------------------
# CommandContext 工厂
# ---------------------------------------------------------------------------

@pytest.fixture
def make_command_context():
    """CommandContext 工厂函数"""

    def _factory(
        state_manager=None,
        napcat_bridge=None,
        config=None,
        send_queue=None,
        cherrystudio_module=None,
    ) -> CommandContext:
        if state_manager is None:
            state_manager = MagicMock()
            state_manager.state = MagicMock()
            state_manager.state.observers = {}
        if napcat_bridge is None:
            napcat_bridge = AsyncMock()
        if send_queue is None:
            send_queue = asyncio.Queue()
        registry = CommandRegistry()
        registry.discover_builtin()
        return CommandContext(
            state_manager=state_manager,
            napcat_bridge=napcat_bridge,
            config=config or {},
            send_queue=send_queue,
            command_registry=registry,
            cherrystudio_module=cherrystudio_module,
        )

    return _factory


# ---------------------------------------------------------------------------
# character_store DATA_DIR 隔离
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_data_dir(tmp_path):
    """临时替换 character_store.DATA_DIR，测试结束后恢复"""
    original = character_store.DATA_DIR
    character_store.DATA_DIR = tmp_path / "data"
    yield tmp_path / "data"
    character_store.DATA_DIR = original
