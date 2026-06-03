"""
QQ-MCP Bridge 服务器
=====================
CherryStudio (MCP Client, STDIO) <-> 本服务器 <-> NapCatQQ (WebSocket 双向)
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# === PID 文件锁：防止重复启动 ===
PID_FILE = Path(__file__).parent / "Configuration" / "bridge.pid"

def _check_singleton():
    """检查是否已有实例运行，若无则写入 PID 文件"""
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            # Windows: 检查进程是否存在
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x0400, False, old_pid)  # PROCESS_QUERY_INFORMATION
                if handle:
                    kernel32.CloseHandle(handle)
                    print(f"[bridge] 已有实例运行中 (PID: {old_pid})，退出。")
                    return False
        except (ValueError, OSError):
            pass
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    return True

if not _check_singleton():
    sys.exit(0)

# 先读配置
CONFIG_PATH = Path(__file__).parent / "Configuration" / "config.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
DEBUG_MODE = CONFIG.get("debug_mode", 0)
LOG_LEVEL = CONFIG.get("log_level", "INFO").upper()

# 强制分配控制台窗口（Windows）
SHOW_CONSOLE = CONFIG.get("show_console", False)
if sys.platform == "win32" and SHOW_CONSOLE:
    import ctypes
    try:
        ctypes.windll.kernel32.FreeConsole()
        ctypes.windll.kernel32.AllocConsole()
        ctypes.windll.kernel32.SetConsoleTitleW("QQ-MCP Bridge")
    except Exception:
        pass

LOG_FILE = Path(__file__).parent / "bridge.log"
handlers: list[logging.Handler] = []

# 控制台输出
if sys.platform == "win32" and SHOW_CONSOLE:
    try:
        con_handler = logging.StreamHandler(open("CONOUT$", "w", buffering=1))
        handlers.append(con_handler)
    except Exception:
        handlers.append(logging.StreamHandler(sys.stderr))
else:
    handlers.append(logging.StreamHandler(sys.stderr))

if DEBUG_MODE:
    # 每次重启清空旧日志
    try:
        LOG_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=handlers,
)
logger = logging.getLogger("qq-mcp-bridge")

# 屏蔽 MCP 协议层心跳日志
logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

NAPCAT_CFG: dict = CONFIG.get("napcat", {})
BRIDGE_CFG: dict = CONFIG.get("bridge", {})

from Built_in.napcat_client import MessageBuffer, NapCatClient, QQMessage
from Built_in.auto_reply import AutoReply

# 统一客户端 (WebSocket 双向: 事件接收 + API 调用)
client: NapCatClient
buffer: MessageBuffer = MessageBuffer(BRIDGE_CFG.get("message_buffer_size", 200))
auto_reply: AutoReply | None = None


async def init_napcat():
    """后台连接 NapCat (不阻塞 MCP)"""
    global client, auto_reply

    # 检测 BotSettingConfig.json
    setting_path = Path(__file__).parent / "Configuration" / "BotSettingConfig.json"
    if not setting_path.exists():
        setting_path.parent.mkdir(parents=True, exist_ok=True)
        defaults = {
            "内置模块": {
                "custom_greeting": "欢迎消息的自定义前缀，留空则仅显示版本和命令列表"
            },
            "指令模块": {
                "bot_on_message": ".bot on 时发送的消息，留空使用默认文案",
                "bot_off_message": ".bot off 时发送的消息，留空使用默认文案",
                "dismiss_message": "退群时发送的告别消息，留空不发送"
            }
        }
        setting_path.write_text(json.dumps(defaults, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.warning("检测到Bot设定文件丢失-已经重建")
        if SHOW_CONSOLE:
            print("\n[!] 检测到Bot设定文件丢失-已经重建 BotSettingConfig.json")

    ws_host = NAPCAT_CFG.get("ws_host", "127.0.0.1")
    ws_port = NAPCAT_CFG.get("ws_port", 3001)
    access_token = NAPCAT_CFG.get("access_token", "")

    client = NapCatClient(ws_host, ws_port, access_token)

    # 自动回复
    auto_cfg: dict = CONFIG.get("auto_reply", {})
    if auto_cfg.get("enabled", False):
        # 读取 LLM / Vision provider 数组
        llm_providers: list[dict] = CONFIG.get("llm", [])
        vision_providers: list[dict] = CONFIG.get("vision_providers", [])
        default_llm = CONFIG.get("default_llm", {})
        default_vision = CONFIG.get("default_vision", {})
        vision_cfg = CONFIG.get("vision", {})

        # 构建 agents 字典 —— 优先级: 手动配置 > 自动拉取(白名单) > 旧格式
        agents: dict[str, dict] = CONFIG.get("agents", {})
        agent_whitelist: list[str] = CONFIG.get("agent_whitelist", [])

        if not agents:
            old_agent = CONFIG.get("agent", {})
            if old_agent.get("enabled") and old_agent.get("agent_id"):
                agents["默认"] = {
                    "agent_id": old_agent["agent_id"],
                    "work_dir": old_agent.get("work_dir", ""),
                }

        agent_enabled = CONFIG.get("agent_enabled", False)
        agent_timeout = CONFIG.get("agent_timeout_seconds", 60)
        default_agent = CONFIG.get("default_agent", list(agents.keys())[0] if agents else "")

        # Agent API key: 优先用 cherry_api_key，回退到第一个 LLM provider 的 key
        agent_api_key = CONFIG.get("cherry_api_key", "")
        if not agent_api_key and llm_providers:
            agent_api_key = llm_providers[0].get("api_key", "")
            logger.warning("未设置 cherry_api_key，将使用第一个 LLM provider 的 key 作为 Agent API key（可能无效）")
        if not agent_api_key:
            logger.warning("未设置 cherry_api_key 且无 LLM provider，Agent API 将无法工作")

        if llm_providers:
            names = ", ".join(p.get("name", "?") for p in llm_providers)
            logger.info(f"LLM providers: {names} (默认: #{default_llm.get('provider', 0)} {default_llm.get('model', '')})")
        if vision_providers:
            names = ", ".join(p.get("name", "?") for p in vision_providers)
            logger.info(f"Vision providers: {names} (默认: #{default_vision.get('provider', 0)} {default_vision.get('model', '')})")

        # Agent model 留空，由 _get_model 从活跃组取，_resolve_cherry_model 负责查 CherryStudio 正确格式
        agent_model = ""

        auto_reply = AutoReply(
            napcat=client,
            # LLM / Vision providers (扁平数组)
            llm_providers=llm_providers,
            vision_providers=vision_providers,
            default_llm_provider=default_llm.get("provider", 0),
            default_llm_model=default_llm.get("model", ""),
            default_vision_provider=default_vision.get("provider", 0),
            default_vision_model=default_vision.get("model", ""),
            system_prompt=" ",
            # Agent API — 固定用 CherryStudio 兼容 model
            agent_enabled=agent_enabled,
            agent_api_key=agent_api_key,
            agent_model=agent_model,
            agents=agents,
            default_agent=default_agent,
            agent_api_url="http://127.0.0.1:23333",
            agent_timeout=agent_timeout,
            # Vision
            vision_enabled=vision_cfg.get("enabled", False),
            vision_prompt=vision_cfg.get("prompt", ""),
            # Settings
            reply_to_groups=auto_cfg.get("reply_to_groups", []),
            reply_to_friends=auto_cfg.get("reply_to_friends", []),
            reply_mode=auto_cfg.get("reply_mode", "mention"),
            cooldown_seconds=auto_cfg.get("cooldown_seconds", 5),
            max_context_messages=auto_cfg.get("max_context_messages", 20),
            message_split_threshold=auto_cfg.get("message_split_threshold", 5.0),
            reply_chain_depth=auto_cfg.get("reply_chain_depth", 4),
            doc_threshold=auto_cfg.get("doc_threshold", 2000),
            global_context=CONFIG.get("global_context", ""),
            admin_qq=CONFIG.get("admin_qq", ""),
            # File processing (MinerU)
            file_processing_enabled=CONFIG.get("file_processing", {}).get("enabled", False),
            mineru_command=CONFIG.get("file_processing", {}).get("mineru_command", "mineru-open-api"),
            mineru_max_file_size_mb=CONFIG.get("file_processing", {}).get("max_file_size_mb", 10),
            mineru_summary_max_chars=CONFIG.get("file_processing", {}).get("summary_max_chars", 2000),
        )
        # 自动拉取 Agent：手动配置为空时，从 CherryStudio 拉取全部
        if not agents:
            agents = await auto_reply._fetch_agents_from_cherrystudio(agent_whitelist if agent_whitelist else None)

            if agents:
                auto_reply._agents = agents
                # 为所有 Agent 追加 PlayerLog 工作目录
                player_log_path = str(Path(__file__).parent / "PlayerLog")
                for cfg in agents.values():
                    dirs: list[str] = cfg.get("work_dirs", [])
                    if player_log_path not in dirs:
                        dirs.append(player_log_path)
                        cfg["work_dirs"] = dirs
                if default_agent and default_agent not in agents:
                    auto_reply._default_agent = list(agents.keys())[0]
                    logger.info(f"默认 Agent 不在拉取列表中，已改为: {auto_reply._default_agent}")

        if agents:
            agent_names = "、".join(agents.keys())
            logger.info(f"自动回复已启用 (Agent{'s' if len(agents) > 1 else ''}: {agent_names})")
        else:
            logger.info("自动回复已启用 (Chat API 回退)")

    async def on_msg(msg: QQMessage):
        buffer.add(msg)
        logger.info(f"收到QQ消息: {msg.format_for_ai()[:100]}")
        if auto_reply:
            asyncio.create_task(auto_reply.handle_message(msg))
        else:
            logger.warning("auto_reply 未初始化!")

    async def on_notice(data: dict):
        notice_type = data.get("notice_type", "")
        if notice_type == "group_increase":
            group_id = str(data.get("group_id", ""))
            logger.info(f"检测到加入群聊: {group_id}")
            if auto_reply:
                greeting = auto_reply.build_greeting()
                asyncio.create_task(_safe_send(client, "group", group_id, greeting))
        elif notice_type == "friend_add":
            user_id = str(data.get("user_id", ""))
            logger.info(f"检测到新好友: {user_id}")
            if auto_reply:
                greeting = auto_reply.build_greeting()
                asyncio.create_task(_safe_send(client, "private", user_id, greeting))

    async def _safe_send(nc, msg_type, target, text):
        try:
            await nc.send_msg(msg_type, target, text)
        except Exception as e:
            logger.warning(f"发送欢迎消息失败 [{msg_type}:{target}]: {e}")

    client.set_message_handler(on_msg)
    client.set_notice_handler(on_notice)

    async def on_request(data: dict):
        req_type = data.get("request_type", "")
        flag = data.get("flag", "")
        if req_type == "friend" and CONFIG.get("auto_accept_friend", True):
            await client.approve_friend_request(flag)
            logger.info(f"自动同意好友申请: {data.get('user_id')}")
        elif req_type == "group" and CONFIG.get("auto_accept_group", True):
            await client.approve_group_invite(flag)
            logger.info(f"自动同意群邀请: {data.get('group_id')}")

    client.set_request_handler(on_request)

    # 启动 WS (后台)
    asyncio.create_task(client.start())

    # 等待就绪
    try:
        await client.wait_ready(30)
        login = await client.get_login_info()
        logger.info(f"NapCat 已连接: {login.get('nickname')} ({login.get('user_id')})")
        if auto_reply:
            auto_reply.set_self_qq(str(login.get("user_id", "")))
    except Exception as e:
        logger.warning(f"NapCat 连接等待中: {e}")


def _ensure_connected():
    if client is None or not client.connected:
        raise RuntimeError("NapCatQQ 未连接。请确认 NapCatQQ 已启动并登录 QQ。")


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

mcp_server = Server("qq-mcp-bridge")


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="qq_send_message",
            description="向 QQ 好友或群聊发送消息。message_type: 'private'(私聊) 或 'group'(群聊)。target_id: 对方QQ号或群号。message: 要发送的文本。",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_type": {"type": "string", "enum": ["private", "group"], "description": "private=私聊, group=群聊"},
                    "target_id": {"type": "string", "description": "目标QQ号或群号"},
                    "message": {"type": "string", "description": "消息内容"},
                },
                "required": ["message_type", "target_id", "message"],
            },
        ),
        Tool(
            name="qq_get_recent_messages",
            description="获取最近缓存的 QQ 消息。target 可选: 留空=全部, 'group:群号'=指定群, 'private:QQ号'=指定私聊。count 默认20。",
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "消息来源, 如 'group:123456'"},
                    "count": {"type": "integer", "description": "返回数量", "default": 20},
                },
                "required": [],
            },
        ),
        Tool(
            name="qq_get_group_list", description="获取当前 QQ 账号加入的所有群聊列表。",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="qq_get_friend_list", description="获取 QQ 好友列表。",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="qq_get_group_members", description="获取指定群的成员列表。",
            inputSchema={
                "type": "object", "properties": {"group_id": {"type": "string", "description": "群号"}},
                "required": ["group_id"],
            },
        ),
        Tool(
            name="qq_get_user_info", description="获取指定 QQ 用户的昵称等基本信息。",
            inputSchema={
                "type": "object", "properties": {"user_id": {"type": "string", "description": "QQ号"}},
                "required": ["user_id"],
            },
        ),
        Tool(
            name="qq_check_status", description="检查 QQ 机器人当前在线状态。",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="qq_recall_message", description="撤回一条机器人发送的消息。参数: message_id",
            inputSchema={
                "type": "object", "properties": {"message_id": {"type": "string", "description": "消息ID"}},
                "required": ["message_id"],
            },
        ),
        Tool(
            name="qq_get_group_msg_history", description="拉取指定群的历史聊天记录。",
            inputSchema={
                "type": "object", "properties": {
                    "group_id": {"type": "string", "description": "群号"},
                    "count": {"type": "integer", "description": "拉取数量", "default": 20},
                },
                "required": ["group_id"],
            },
        ),
        Tool(
            name="qq_get_recent_contacts", description="获取最近有消息往来的会话列表。",
            inputSchema={
                "type": "object", "properties": {"count": {"type": "integer", "description": "数量", "default": 20}},
                "required": [],
            },
        ),
        Tool(
            name="qq_upload_file",
            description="上传文件到 QQ 私聊或群聊。message_type: 'private' 或 'group'。target_id: QQ号或群号。content: 文件内容文本（会保存为 .md 发送）。filename: 可选文件名。",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_type": {"type": "string", "enum": ["private", "group"], "description": "private=私聊, group=群聊"},
                    "target_id": {"type": "string", "description": "目标QQ号或群号"},
                    "content": {"type": "string", "description": "文件内容"},
                    "filename": {"type": "string", "description": "文件名（可选，默认 reply_时间戳.md）"},
                },
                "required": ["message_type", "target_id", "content"],
            },
        ),
        Tool(
            name="qq_send_image",
            description="发送图片到 QQ 私聊或群聊。image_url: 图片 URL。summary: 可选附带的文字说明。",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_type": {"type": "string", "enum": ["private", "group"], "description": "private=私聊, group=群聊"},
                    "target_id": {"type": "string", "description": "QQ号或群号"},
                    "image_url": {"type": "string", "description": "图片URL"},
                    "summary": {"type": "string", "description": "附带文字（可选）"},
                },
                "required": ["message_type", "target_id", "image_url"],
            },
        ),
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    logger.info(f"MCP 调用: {name}({json.dumps(arguments, ensure_ascii=False, default=str)[:200]})")
    try:
        result = await _dispatch(name, arguments)
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        return [TextContent(type="text", text=text)]
    except Exception as e:
        logger.error(f"失败: {name} - {e}")
        return [TextContent(type="text", text=f"错误: {e}")]


async def _dispatch(name: str, args: dict) -> str:
    if name == "qq_send_message":
        _ensure_connected()
        msg_id = await client.send_msg(args["message_type"], args["target_id"], args["message"])
        return f"已发送\nmessage_id: {msg_id}"

    elif name == "qq_get_recent_messages":
        msgs = buffer.get_recent(args.get("target", ""), args.get("count", 20))
        if not msgs:
            return "暂无缓存的 QQ 消息。新消息到来后会自动缓存。"
        return "\n".join(["最近消息:"] + [m.format_for_ai() for m in msgs])

    elif name == "qq_get_group_list":
        _ensure_connected()
        groups = await client.get_group_list()
        return "\n".join(
            [f"共 {len(groups)} 个群:"] +
            [f"  [{g.get('group_id')}] {g.get('group_name', '?')} ({g.get('member_count', '?')}人)" for g in groups]
        )

    elif name == "qq_get_friend_list":
        _ensure_connected()
        friends = await client.get_friend_list()
        return "\n".join(
            [f"共 {len(friends)} 个好友:"] +
            [f"  [{f.get('user_id')}] {f.get('nickname', '?')}" for f in friends]
        )

    elif name == "qq_get_group_members":
        _ensure_connected()
        members = await client.get_group_member_list(args["group_id"])
        rm = {"owner": "[群主]", "admin": "[管理]", "member": ""}
        return "\n".join(
            [f"群 {args['group_id']} 共 {len(members)} 人:"] +
            [f"  [{m.get('user_id')}] {m.get('card') or m.get('nickname', '?')} {rm.get(m.get('role', ''), '')}" for m in members]
        )

    elif name == "qq_get_user_info":
        _ensure_connected()
        info = await client.get_stranger_info(args["user_id"])
        return f"QQ: {info.get('user_id')}\n昵称: {info.get('nickname', '?')}\n年龄: {info.get('age', '?')}\n性别: {info.get('sex', '?')}"

    elif name == "qq_check_status":
        _ensure_connected()
        info = await client.get_login_info()
        return f"QQ 在线\nQQ号: {info.get('user_id')}\n昵称: {info.get('nickname')}\n缓存消息: {len(buffer.get_recent())} 条"

    elif name == "qq_recall_message":
        _ensure_connected()
        await client.delete_msg(args["message_id"])
        return "已撤回"

    elif name == "qq_get_group_msg_history":
        _ensure_connected()
        history = await client.get_group_msg_history(args["group_id"], args.get("count", 20))
        msgs = history.get("messages", [])
        lines = [f"群 {args['group_id']} 最近 {len(msgs)} 条消息:"]
        for m in msgs:
            s = m.get("sender", {})
            lines.append(f"[{s.get('nickname', s.get('card', '?'))}] {str(m.get('message', ''))[:200]}")
        return "\n".join(lines)

    elif name == "qq_get_recent_contacts":
        _ensure_connected()
        contacts = await client.get_recent_contact(args.get("count", 20))
        return "\n".join(
            [f"最近 {len(contacts)} 个会话:"] +
            [f"  {'[群]' if c.get('type') == 'group' else '[好友]'} [{c.get('id')}] {c.get('name', '?')}" for c in contacts]
        )

    elif name == "qq_upload_file":
        _ensure_connected()
        import tempfile, os as _os, time as _time
        content = args["content"]
        filename = args.get("filename") or f"reply_{_time.strftime('%Y%m%d_%H%M%S')}.md"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="qqfile_", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = f.name
        try:
            await client.upload_file(args["message_type"], args["target_id"], tmp_path, filename)
            return f"文件已发送: {filename} ({len(content)} 字符)"
        except Exception as e:
            logger.warning(f"文件上传失败: {e}，回退为文本消息")
            await client.send_msg(args["message_type"], args["target_id"], content)
            return f"文件上传失败({e})，已转为文本消息发送 ({len(content)} 字符)"
        finally:
            _os.unlink(tmp_path)

    elif name == "qq_send_image":
        _ensure_connected()
        msg_id = await client.send_image(
            args["message_type"], args["target_id"],
            args["image_url"], args.get("summary", "")
        )
        return f"图片已发送\nmessage_id: {msg_id}"

    return f"未知工具: {name}"


async def main():
    asyncio.create_task(init_napcat())
    logger.info("MCP 服务器启动, 等待 CherryStudio 连接...")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
    finally:
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def main_sync():
    """Synchronous entry point for pyproject.toml [project.scripts] (NPX/UVX)."""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
