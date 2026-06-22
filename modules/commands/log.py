"""
日志命令 (.log new/on/off/end/list/get/del)

移植自旧项目 Plugins/log.py
适配新架构 Command / CommandContext / ParsedMessage 接口。

旧项目中 .log 命令的 log_new/log_resume/log_pause 等方法委托给 auto_reply，
新架构中这些功能需要在 StateManager 或独立模块中实现。
Phase 1 先实现命令框架和基础的文件存储逻辑。
"""

import json
import logging
import time
from pathlib import Path

from modules.command_module import Command, CommandContext
from protocols.messages import ParsedMessage, MessageSource

from modules.commands.utils import load_bot_setting, format_msg

logger = logging.getLogger(__name__)

# 日志文件存储目录
LOG_DIR = Path(__file__).parent.parent.parent / "data" / "logs"


# =================================================================
# 日志存储 — 群聊日志记录的基础设施
# =================================================================

def _group_log_dir(group_id: str) -> Path:
    """获取群日志目录"""
    return LOG_DIR / group_id


def _log_state_path(group_id: str) -> Path:
    """群日志状态文件"""
    return LOG_DIR / group_id / "_state.json"


def _load_log_state(group_id: str) -> dict:
    """加载群日志状态"""
    path = _log_state_path(group_id)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Load log state failed {group_id}: {e}")
    return {"active": None, "paused": set(), "logs": {}}


def _save_log_state(group_id: str, state: dict):
    """保存群日志状态"""
    path = _log_state_path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # set 不可序列化，转为 list
    serializable = {
        "active": state.get("active"),
        "paused": list(state.get("paused", set())),
        "logs": state.get("logs", {}),
    }
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")


def _log_file_path(group_id: str, log_name: str) -> Path:
    """获取日志文件路径"""
    return _group_log_dir(group_id) / f"{log_name}.log"


# =================================================================
# 日志操作函数
# =================================================================

def log_new(group_id: str, log_name: str) -> str:
    """新建日志并开始记录"""
    if not log_name:
        return "用法: .log new <日志名>"

    state = _load_log_state(group_id)

    if log_name in state.get("logs", {}):
        return f"❌ 日志「{log_name}」已存在"

    state["logs"][log_name] = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "lines": 0,
    }
    state["active"] = log_name
    state.setdefault("paused", set())
    if isinstance(state["paused"], list):
        state["paused"] = set(state["paused"])
    state["paused"].discard(group_id)
    _save_log_state(group_id, state)

    # 创建空日志文件
    log_path = _log_file_path(group_id, log_name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")

    template = load_bot_setting("log", "log_new_message", "{}")
    result = f"日志「{log_name}」已创建并开始记录"
    return format_msg(template, result)


def log_resume(group_id: str) -> str:
    """继续记录"""
    state = _load_log_state(group_id)
    if isinstance(state.get("paused"), list):
        state["paused"] = set(state["paused"])

    if not state.get("active"):
        return "❌ 没有活跃的日志"

    state.setdefault("paused", set()).discard(group_id)
    _save_log_state(group_id, state)
    return f"✅ 日志「{state['active']}」已继续记录"


def log_pause(group_id: str) -> str:
    """暂停记录"""
    state = _load_log_state(group_id)
    if isinstance(state.get("paused"), list):
        state["paused"] = set(state["paused"])

    if not state.get("active"):
        return "❌ 没有活跃的日志"

    state.setdefault("paused", set()).add(group_id)
    _save_log_state(group_id, state)
    return f"⏸️ 日志「{state['active']}」已暂停记录"


def log_end(group_id: str) -> str:
    """完成记录"""
    state = _load_log_state(group_id)
    if isinstance(state.get("paused"), list):
        state["paused"] = set(state["paused"])

    active = state.get("active")
    if not active:
        return "❌ 没有活跃的日志"

    state["active"] = None
    state.setdefault("paused", set()).discard(group_id)
    _save_log_state(group_id, state)

    log_path = _log_file_path(group_id, active)
    lines = 0
    if log_path.exists():
        lines = len(log_path.read_text(encoding="utf-8").strip().split("\n"))

    return f"✅ 日志「{active}」已完成记录 ({lines} 条消息)\n使用 .log get {active} 获取日志文件"


def log_list(group_id: str) -> str:
    """查看本群日志列表"""
    state = _load_log_state(group_id)
    logs = state.get("logs", {})

    if not logs:
        return "📋 本群暂无日志"

    active = state.get("active")
    if isinstance(state.get("paused"), list):
        state["paused"] = set(state["paused"])
    paused = state.get("paused", set())

    lines = ["📋 本群日志:"]
    for name, info in logs.items():
        status = ""
        if name == active:
            status = " 🔴 记录中" if group_id not in paused else " ⏸️ 已暂停"
        created = info.get("created", "")
        lines.append(f"  - {name}{status}  ({created})")

    template = load_bot_setting("log", "log_list_message", "{}")
    return format_msg(template, "\n".join(lines))


def log_get(group_id: str, log_name: str) -> str:
    """获取日志内容"""
    if not log_name:
        return "用法: .log get <日志名>"

    log_path = _log_file_path(group_id, log_name)
    if not log_path.exists():
        return f"❌ 日志「{log_name}」不存在"

    content = log_path.read_text(encoding="utf-8").strip()
    if not content:
        return f"📋 日志「{log_name}」为空"

    lines = content.split("\n")
    if len(lines) > 50:
        preview = "\n".join(lines[:50])
        return f"📋 日志「{log_name}」({len(lines)} 条，显示前 50 条):\n{preview}\n..."
    return f"📋 日志「{log_name}」({len(lines)} 条):\n{content}"


def log_delete(group_id: str, log_name: str) -> str:
    """删除日志"""
    if not log_name:
        return "用法: .log del <日志名>"

    state = _load_log_state(group_id)
    if isinstance(state.get("paused"), list):
        state["paused"] = set(state["paused"])

    if log_name not in state.get("logs", {}):
        return f"❌ 日志「{log_name}」不存在"

    if state.get("active") == log_name:
        state["active"] = None
        state["paused"].discard(group_id)

    del state["logs"][log_name]
    _save_log_state(group_id, state)

    # 删除日志文件
    log_path = _log_file_path(group_id, log_name)
    if log_path.exists():
        log_path.unlink()

    return f"✅ 日志「{log_name}」已删除（不可逆）"


# =================================================================
# 日志消息写入 (供 HookManager 回调使用)
# =================================================================

def log_write(group_id: str, sender_name: str, content: str, timestamp: str = ""):
    """将一条消息写入活跃日志"""
    state = _load_log_state(group_id)
    if isinstance(state.get("paused"), list):
        state["paused"] = set(state["paused"])

    active = state.get("active")
    if not active:
        return
    if group_id in state.get("paused", set()):
        return

    log_path = _log_file_path(group_id, active)
    if not log_path.exists():
        return

    ts = timestamp or time.strftime("%H:%M:%S")
    line = f"[{ts}] {sender_name}: {content}\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)

    # 更新行数计数
    if active in state.get("logs", {}):
        state["logs"][active]["lines"] = state["logs"][active].get("lines", 0) + 1
        _save_log_state(group_id, state)


# =================================================================
# 日志钩子回调 (供 HookManager 注册)
# =================================================================

async def _log_on_message(msg: ParsedMessage, context: dict):
    """每条群聊消息到达时：判断是否需要写入活跃日志"""
    if msg.raw.source != MessageSource.GROUP:
        return

    group_id = msg.raw.target_id
    sender_name = msg.raw.sender_name or msg.raw.sender_id
    content = msg.raw.content or "(非文本消息)"
    timestamp = msg.raw.timestamp.strftime("%H:%M:%S") if msg.raw.timestamp else ""

    log_write(group_id, sender_name, content, timestamp)


# =================================================================
# .log 命令
# =================================================================

class LogCommand(Command):
    name = "log"
    description = "群聊日志记录"
    group = "日志"
    usage = ".log <new/on/off/end/list/get/del> [参数]"
    reminder = "使用 .log new <名称> 开始记录；.log end 结束并导出"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        if msg.raw.source != MessageSource.GROUP:
            return ".log 仅在群聊中有效。"

        group_id = msg.raw.target_id
        parts = args.split(None, 1)
        action = parts[0].lower() if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if action == "new":
            return log_new(group_id, sub_args.strip())
        elif action == "on":
            return log_resume(group_id)
        elif action == "off":
            return log_pause(group_id)
        elif action == "end":
            return log_end(group_id)
        elif action == "list":
            return log_list(group_id)
        elif action == "get":
            return log_get(group_id, sub_args.strip())
        elif action == "del":
            return log_delete(group_id, sub_args.strip())
        elif action in ("help", "?"):
            return self._sub_help()
        else:
            return f"未知子命令: .log {args}\n\n{self._sub_help()}"

    def _sub_help(self) -> str:
        return (
            ".log 子命令:\n"
            "  .log new <日志名>   - 新建日志并开始记录\n"
            "  .log on             - 继续记录\n"
            "  .log off            - 暂停记录\n"
            "  .log end            - 完成记录\n"
            "  .log list           - 查看本群日志列表\n"
            "  .log get <日志名>    - 查看日志内容\n"
            "  .log del <日志名>    - 删除日志（不可逆）"
        )
