"""
Server 模块

职责:
1. MCP Server 注册与生命周期管理
2. 系统初始化 (配置加载、模块启动)
3. 单例进程锁防止重复启动
4. 优雅关闭处理
"""

from mcp.server.fastmcp import FastMCP
from modules.cherrystudio_module import CherryStudioModule
from modules.command_module import CommandModule
from modules.message_bus import MessageBus
from modules.napcat_bridge import NapCatBridge
from modules.conversation_store import ConversationStore
from modules.hooks import HookManager
from state.manager import StateManager
from protocols.error_codes import ErrorCode, BridgeError
from protocols.messages import (
    ParsedMessage,
    ModuleResponse,
    MessageSource,
    MessageType,
)
import asyncio
import json
import logging
import sys
import os
import time
import ctypes
import tempfile
from pathlib import Path
from typing import Any

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


# 配置日志
# 关键: MCP stdio 协议使用 stdout 传输 JSON-RPC 消息，
# 因此所有日志必须输出到 stderr，绝不能污染 stdout。
# Root logger 设为 DEBUG 以确保 handler 级别过滤生效，
# 实际输出级别由 main() 中根据 debug_mode 配置动态调整。
_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logging.getLogger().setLevel(logging.DEBUG)

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_log_formatter)
logging.getLogger().addHandler(_stderr_handler)

try:
    _file_handler = logging.FileHandler(
        project_root / "PlayerLog" / "bridge.log", encoding="utf-8"
    )
    _file_handler.setFormatter(_log_formatter)
    logging.getLogger().addHandler(_file_handler)
except Exception:
    _file_handler = None

logger = logging.getLogger("server")


class Server:
    """
    Server 模块 - 系统核心

    负责：
    1. 加载配置
    2. 初始化所有模块
    3. 注册 MCP 工具
    4. 启动消息循环
    5. 优雅关闭
    """

    def __init__(self, config_path: Path | None = None):
        """
        初始化 Server

        Args:
            config_path: 配置文件路径。如未指定，按以下顺序查找:
                         1. config.json (项目根目录 — 与原项目一致)
                         2. Configuration/config.json
        """
        if config_path is None:
            # 优先使用根目录的 config.json (与原项目一致)
            _root_config = project_root / "config.json"
            _sub_config = project_root / "Configuration" / "config.json"
            if _root_config.exists():
                self.config_path = _root_config
            else:
                self.config_path = _sub_config
        else:
            self.config_path = config_path

        # 配置
        self.config: dict = {}

        # MCP Server
        self.mcp: FastMCP | None = None

        # 核心组件
        self.state_manager: StateManager = StateManager()
        self.napcat_bridge: NapCatBridge | None = None
        self.message_bus: MessageBus | None = None
        self.command_module: CommandModule | None = None
        self.cherrystudio_module: CherryStudioModule | None = None
        self.hook_manager: HookManager | None = None

        # 运行状态
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # MCP 握手完成信号 (InitializedNotification 到达时设置)
        # _deferred_init 等待此事件后再执行 Agent 发现等操作
        self._mcp_handshake_event = asyncio.Event()

        # ---- qq_send_message 防抖 (Debounce) ----
        # 防止 Agent 多次调用 qq_send_message 发送逐步增长的文本
        # key = target_id, value = {"text": str, "message_type": str, "task": asyncio.Task}
        self._pending_sends: dict[str, dict] = {}
        self._send_debounce_seconds: float = 2.0  # 防抖窗口

    async def initialize(self):
        """
        系统初始化

        按顺序初始化所有组件：
        1. 加载配置
        2. 初始化状态管理器
        3. 初始化各模块
        4. 连接模块队列
        """
        logger.info("=" * 60)
        logger.info("QQ-MCP Bridge v3.0 starting...")
        logger.info("=" * 60)

        try:
            # 1. 加载配置
            await self._load_config()

            # 2. 初始化状态管理器 + 双向合并旧持久化文件
            await self.state_manager.initialize()
            await self.state_manager.merge_legacy_files()
            logger.info("StateManager initialized (with legacy file merge)")

            # 3. 初始化 NapCatBridge
            napcat_config = self.config.get("napcat", {})
            self.napcat_bridge = NapCatBridge(
                host=napcat_config.get("ws_host", "127.0.0.1"),
                port=napcat_config.get("ws_port", 3001),
                access_token=napcat_config.get("access_token", ""),
            )
            await self.napcat_bridge.initialize()

            # 设置最大重连次数 (0 = 无限)
            max_reconnect = napcat_config.get("ws_max_reconnect", 0)
            self.napcat_bridge._max_reconnect = max_reconnect
            logger.info(f"NapCat max reconnect attempts: {max_reconnect if max_reconnect > 0 else 'unlimited'}")

            # 设置配置中的 self_qq (NapCat 获取失败时的兜底值)
            config_self_qq = str(napcat_config.get("self_qq", ""))
            self.napcat_bridge.config_self_qq = config_self_qq
            if config_self_qq:
                logger.info(f"NapCat config self_qq: {config_self_qq}")

            # 设置消息缓冲区大小
            buffer_size = self.config.get("bridge", {}).get("message_buffer_size", 200)
            from modules.napcat_bridge import MessageBuffer as _MsgBuf
            self.napcat_bridge.message_buffer = _MsgBuf(max_size=buffer_size)

            # 设置文档阈值 (从 auto_reply.doc_threshold 读取)
            doc_threshold = self.config.get("auto_reply", {}).get("doc_threshold", 1000)
            self.napcat_bridge.set_doc_threshold(doc_threshold)

            logger.info("NapCatBridge initialized")

            # 4. 初始化 MessageBus
            self.message_bus = MessageBus(state_manager=self.state_manager)
            logger.info("MessageBus initialized")

            # 4.5 初始化 HookManager (事件钩子)
            self.hook_manager = HookManager()
            self.message_bus.hook_manager = self.hook_manager
            logger.info("HookManager initialized")

            # 5. 初始化 CommandModule
            self.command_module = CommandModule(
                state_manager=self.state_manager,
                napcat_bridge=self.napcat_bridge,
                config=self.config,
            )
            await self.command_module.initialize()
            logger.info("CommandModule initialized")

            # 6. 初始化 MCP Server (必须在 CherryStudio HTTP API 调用之前完成)
            # MCP Server 注册工具后，CherryStudio 连接时即可使用
            mcp_server_name = self.config.get("cherrystudio", {}).get(
                "mcp_server_name", "QQ Bridge"
            )
            self.mcp = FastMCP(mcp_server_name)
            self._register_mcp_tools()
            logger.info(f"MCP Server initialized: {mcp_server_name}")

            # 7. 初始化 CherryStudioModule (仅轻量级设置，API 调用延迟到 MCP 握手后)
            self.cherrystudio_module = CherryStudioModule(
                state_manager=self.state_manager,
                config=self.config,
            )
            await self.cherrystudio_module.initialize()
            logger.info("CherryStudioModule initialized (lazy API connection)")

            # 8. 连接模块队列
            self._connect_queues()

            # 9. 设置事件处理器 (入群欢迎、好友审批等)
            self._setup_event_handlers()

            # 10. 启动时会话完整性校验
            if self.cherrystudio_module and hasattr(self.cherrystudio_module, 'conversation_store'):
                cs = self.cherrystudio_module.conversation_store
                if cs:
                    await cs.validate_sessions()

            logger.info("=" * 60)
            logger.info("System initialization complete")
            logger.info("=" * 60)

        except BridgeError as e:
            logger.error(f"Initialization failed: {e.user_message}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Initialization exception: {e}", exc_info=True)
            raise BridgeError(
                ErrorCode.SERVER_INIT_FAILED,
                detail=str(e),
            )

    def _register_mcp_tools(self):
        """
        注册 MCP 工具

        注册 12 个标准 QQ Bridge 工具到 MCP Server
        """
        if not self.mcp:
            raise RuntimeError("MCP Server 未初始化")

        # ----------------------------------------------------------------
        # qq_send_message 防抖核心
        # ----------------------------------------------------------------
        async def _flush_pending_send(target_id: str):
            """延迟发送: 等待防抖窗口后实际发送"""
            try:
                await asyncio.sleep(self._send_debounce_seconds)
                pending = self._pending_sends.pop(target_id, None)
                if not pending:
                    return  # 已被新调用替换或取消

                from protocols.messages import (
                    OutgoingMessage, MessageSource, MessageType,
                )
                source = (
                    MessageSource.GROUP
                    if pending["message_type"] == "group"
                    else MessageSource.PRIVATE
                )
                outgoing = OutgoingMessage(
                    target_source=source,
                    target_id=str(target_id),
                    content=pending["text"],
                    message_type=MessageType.TEXT,
                )
                try:
                    # skip_preview=True: 防抖刷新时跳过长文本转文档的预览通知
                    # 避免逐步增长的文本每次都发送一条预览消息
                    msg_id = await self.napcat_bridge.send_message(
                        outgoing, skip_preview=True
                    )
                    logger.info(
                        f"Debounced send complete: target={target_id}, "
                        f"len={len(pending['text'])}, msg_id={msg_id}"
                    )
                except (ConnectionError, TimeoutError):
                    await asyncio.sleep(2)
                    msg_id = await self.napcat_bridge.send_message(
                        outgoing, skip_preview=True
                    )
            except asyncio.CancelledError:
                pass  # 被新调用取消，正常
            except Exception as e:
                logger.error(f"Debounced send failed: {e}", exc_info=True)

        async def _debounced_qq_send(
            message_type: str, target_id: str, message: str
        ) -> str:
            """
            带防抖的消息发送:
            - 如果同一 target_id 在防抖窗口内收到新消息:
              - 新消息以旧消息为前缀 → 替换 (增量文本，只发最终版)
              - 否则 → 立即发送旧消息，缓冲新消息
            """
            key = str(target_id)
            existing = self._pending_sends.get(key)

            if existing:
                old_text = existing["text"]
                old_task: asyncio.Task = existing.get("task")

                # 增量检测: 新文本以旧文本开头 → 逐步增长 → 替换
                if message.startswith(old_text):
                    logger.info(
                        f"Debounced send: incremental text detected "
                        f"(old={len(old_text)}, new={len(message)}), "
                        f"replacing buffer for target={key}"
                    )
                    # 取消旧的延迟发送任务
                    if old_task and not old_task.done():
                        old_task.cancel()
                    # 用新文本替换
                    new_task = asyncio.create_task(
                        _flush_pending_send(key)
                    )
                    existing["text"] = message
                    existing["message_type"] = message_type
                    existing["task"] = new_task
                    return f"消息已缓冲 (增量合并, {len(message)} 字符)"
                else:
                    # 非增量 → 立即发送旧消息
                    logger.info(
                        f"Debounced send: non-incremental text, "
                        f"flushing old ({len(old_text)} chars) and "
                        f"buffering new ({len(message)} chars) for target={key}"
                    )
                    if old_task and not old_task.done():
                        old_task.cancel()
                    # 立即发送旧消息
                    try:
                        from protocols.messages import (
                            OutgoingMessage, MessageSource, MessageType,
                        )
                        source = (
                            MessageSource.GROUP
                            if existing["message_type"] == "group"
                            else MessageSource.PRIVATE
                        )
                        outgoing = OutgoingMessage(
                            target_source=source,
                            target_id=str(target_id),
                            content=old_text,
                            message_type=MessageType.TEXT,
                        )
                        await self.napcat_bridge.send_message(outgoing)
                    except Exception as e:
                        logger.warning(f"Flush old message failed: {e}")
                    self._pending_sends.pop(key, None)

            # 缓冲新消息
            task = asyncio.create_task(_flush_pending_send(key))
            self._pending_sends[key] = {
                "text": message,
                "message_type": message_type,
                "task": task,
            }
            return f"消息已缓冲 ({len(message)} 字符)"

        # ----------------------------------------------------------------
        # MCP 工具注册
        # ----------------------------------------------------------------
        @self.mcp.tool()
        async def qq_send_message(
            message_type: str, target_id: str, message: str
        ) -> str:
            """
            向指定的 QQ 私聊或群聊发送一条文本消息。这是你向用户回复内容的主要方式。
            Bridge 会自动处理消息长度——如果内容过长，会自动转为文档文件发送。
            你不需要关心消息长度限制。不要使用此工具发送图片（请用 qq_send_image）。

            Args:
                message_type: 消息类型 (private/group)
                target_id: 目标 ID (QQ号或群号)
                message: 消息内容

            Returns:
                发送结果描述
            """
            if not self.napcat_bridge:
                return "错误: NapCat 未连接"

            # 活跃目标验证: 检查 target_id 是否在活跃会话中
            if not self.napcat_bridge.is_target_active(str(target_id)):
                return f"目标 {target_id} 不在活跃会话中，请确认目标是否正确"

            # ---- 防抖发送: 合并增量文本，避免多次发送逐步增长的消息 ----
            try:
                return await _debounced_qq_send(
                    message_type, target_id, message
                )
            except Exception as e:
                return f"发送失败: {str(e)}"

        @self.mcp.tool()
        async def qq_send_image(
            message_type: str, target_id: str, image_url: str, summary: str = ""
        ) -> str:
            """
            向指定的 QQ 私聊或群聊发送一张图片。
            image_url 必须是可公开访问的 HTTP/HTTPS 图片链接。
            可选附带 summary 参数作为图片的文字说明。
            如果你需要发送的是文本内容而非图片，请使用 qq_send_message。

            Args:
                message_type: 消息类型 (private/group)
                target_id: 目标 ID (QQ号或群号)
                image_url: 图片 URL
                summary: 图片描述（可选）

            Returns:
                发送结果描述
            """
            if not self.napcat_bridge:
                return "错误: NapCat 未连接"

            try:
                from protocols.messages import OutgoingMessage, MessageSource, MessageType

                source = MessageSource.GROUP if message_type == "group" else MessageSource.PRIVATE
                outgoing = OutgoingMessage(
                    target_source=source,
                    target_id=str(target_id),
                    content=summary or "[图片]",
                    message_type=MessageType.IMAGE,
                    attachments=[{"url": image_url, "type": "image"}],
                )

                async def _do_send():
                    return await self.napcat_bridge.send_message(outgoing)

                try:
                    msg_id = await _do_send()
                except (ConnectionError, TimeoutError):
                    # 断线自动重试: 等待 2 秒后重试一次
                    await asyncio.sleep(2)
                    msg_id = await _do_send()

                return f"图片已发送 (ID: {msg_id})"
            except Exception as e:
                return f"发送失败: {str(e)}"

        @self.mcp.tool()
        async def qq_upload_file(
            message_type: str,
            target_id: str,
            content: str = "",
            file_path: str = "",
            filename: str = "",
        ) -> str:
            """
            向指定的 QQ 私聊或群聊上传一个文件。
            支持两种方式: (1) 提供 content 文本参数，Bridge 会自动保存为文件并上传;
            (2) 提供 file_path 本地文件路径，Bridge 直接上传该文件。
            filename 可选，用于指定接收方看到的文件名。
            适用于发送长文档、代码文件、压缩包等实体文件。
            如果只是想发送一段文字消息，请使用 qq_send_message。

            注意: 当检测到 Markdown 文件 (.md/.markdown) 时，会自动将其渲染为
            精美长图并以图片形式发送到聊天中，无需额外操作。

            Args:
                message_type: 消息类型 (private/group)
                target_id: 目标 ID (QQ号或群号)
                content: 文本内容 (与 file_path 二选一)
                file_path: 本地文件绝对路径 (与 content 二选一，优先)
                filename: 显示文件名（可选）

            Returns:
                上传/发送结果描述
            """
            if not self.napcat_bridge:
                return "错误: NapCat 未连接"

            # 参数校验: file_path 和 content 二选一
            if not file_path and not content:
                return "错误: 必须提供 content 或 file_path 参数"

            # ── 自动检测 Markdown 文件 → 渲染为图片 ──
            md_text = None
            if file_path:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in (".md", ".markdown"):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            md_text = f.read()
                    except Exception:
                        pass  # 读取失败则走普通上传
            elif content:
                # content 模式下，如果 filename 指定了 .md 扩展名则视为 Markdown
                if filename:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in (".md", ".markdown"):
                        md_text = content

            if md_text:
                temp_png = None
                try:
                    from modules.md_to_image import md_to_image

                    title = filename or (os.path.basename(file_path) if file_path else "markdown")
                    title = os.path.splitext(title)[0]

                    temp_png = await md_to_image(md_text, title=title)

                    async def _do_send_img():
                        return await self.napcat_bridge.send_local_image(
                            message_type, str(target_id), temp_png
                        )

                    try:
                        msg_id = await _do_send_img()
                    except (ConnectionError, TimeoutError):
                        await asyncio.sleep(2)
                        msg_id = await _do_send_img()

                    return f"Markdown 已渲染为图片发送 (ID: {msg_id})"
                except Exception as e:
                    return f"Markdown 转图片失败: {str(e)}"
                finally:
                    if temp_png and os.path.exists(temp_png):
                        try:
                            os.remove(temp_png)
                            # 清理 md2img 会话目录 (PNG 所在的空目录)
                            parent = os.path.dirname(temp_png)
                            if parent and os.path.isdir(parent) and not os.listdir(parent):
                                os.rmdir(parent)
                        except OSError:
                            pass

            # ── 普通文件上传模式 ──
            temp_path = None
            try:
                # 模式 B: 本地文件路径 (优先)
                if file_path:
                    upload_path = file_path
                    display_name = filename or os.path.basename(file_path)
                else:
                    # 模式 A: 文本内容 → 写临时文件
                    if not filename:
                        filename = f"bridge_doc_{int(time.time())}.md"
                    display_name = filename
                    temp_dir = tempfile.gettempdir()
                    temp_path = os.path.join(temp_dir, filename)
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    upload_path = temp_path

                # 调用 napcat_bridge.upload_file 上传
                # upload_file 需要文件路径，对于 file_path 模式直接传入
                # 对于 content 模式，临时文件路径即 upload_path
                if file_path and filename:
                    # file_path 模式下如果指定了 filename，需要拷贝重命名
                    import shutil
                    renamed_path = os.path.join(
                        tempfile.gettempdir(), filename
                    )
                    shutil.copy2(file_path, renamed_path)
                    temp_path = renamed_path  # 标记清理
                    upload_path = renamed_path

                async def _do_upload():
                    return await self.napcat_bridge.upload_file(
                        message_type, str(target_id), upload_path
                    )

                try:
                    await _do_upload()
                except (ConnectionError, TimeoutError):
                    # 断线自动重试: 等待 2 秒后重试一次
                    await asyncio.sleep(2)
                    await _do_upload()

                content_info = f" ({len(content)} 字符)" if content else ""
                return f"文件已上传: {display_name}{content_info}"
            except Exception as e:
                return f"上传失败: {str(e)}"
            finally:
                # 清理临时文件
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

        @self.mcp.tool()
        async def qq_get_recent_messages(
            target: str = "", count: int = 10
        ) -> list[dict]:
            """
            获取本地缓存的最近消息。快速轻量，包含私聊和群聊的消息记录。
            数据来源于 Bridge 运行期间接收到的消息缓存。

            Args:
                target: 目标 ID (可选，空则返回所有，格式如 "123456")
                count: 消息数量

            Returns:
                消息列表
            """
            if not self.napcat_bridge:
                return []

            try:
                messages = await self.napcat_bridge.message_buffer.get_recent_messages(
                    target=target, count=count
                )
                return [
                    {
                        "msg_id": msg.msg_id,
                        "sender_id": msg.sender_id,
                        "sender_name": msg.sender_name,
                        "content": msg.content,
                        "timestamp": msg.timestamp.isoformat(),
                        "source": msg.source.value,
                        "target_id": msg.target_id,
                        "message_type": msg.message_type.value,
                        "image_count": len(msg.image_files),
                        "file_count": len(msg.file_infos),
                        "group_name": msg.group_name or None,
                    }
                    for msg in messages
                ]
            except Exception as e:
                return [{"error": str(e)}]

        @self.mcp.tool()
        async def qq_get_group_msg_history(
            group_id: str, count: int = 20
        ) -> list[dict]:
            """
            从 QQ 服务器拉取群聊的历史消息记录。仅支持群聊，
            包含 Bridge 未运行期间的历史消息。数据比 qq_get_recent_messages 更完整。

            Args:
                group_id: 群号
                count: 消息数量

            Returns:
                消息列表
            """
            if not self.napcat_bridge:
                return []

            try:
                result = await self.napcat_bridge.get_group_msg_history(
                    str(group_id), count
                )
                return result if isinstance(result, list) else []
            except Exception as e:
                return [{"error": str(e)}]

        @self.mcp.tool()
        async def qq_get_group_list() -> list[dict]:
            """
            获取群列表

            Returns:
                群列表
            """
            if not self.napcat_bridge:
                return []

            try:
                result = await self.napcat_bridge.get_group_list()
                return result if isinstance(result, list) else []
            except Exception as e:
                return [{"error": str(e)}]

        @self.mcp.tool()
        async def qq_get_friend_list() -> list[dict]:
            """
            获取好友列表

            Returns:
                好友列表
            """
            if not self.napcat_bridge:
                return []

            try:
                result = await self.napcat_bridge.get_friend_list()
                return result if isinstance(result, list) else []
            except Exception as e:
                return [{"error": str(e)}]

        @self.mcp.tool()
        async def qq_get_group_members(group_id: str) -> list[dict]:
            """
            获取群成员列表

            Args:
                group_id: 群号

            Returns:
                成员列表
            """
            if not self.napcat_bridge:
                return []

            try:
                result = await self.napcat_bridge.get_group_member_list(str(group_id))
                return result if isinstance(result, list) else []
            except Exception as e:
                return [{"error": str(e)}]

        @self.mcp.tool()
        async def qq_get_user_info(user_id: str) -> dict:
            """
            获取用户信息

            Args:
                user_id: QQ号

            Returns:
                用户信息
            """
            if not self.napcat_bridge:
                return {}

            try:
                result = await self.napcat_bridge.get_stranger_info(str(user_id))
                return result if isinstance(result, dict) else {}
            except Exception as e:
                return {"error": str(e)}

        @self.mcp.tool()
        async def qq_get_recent_contacts(count: int = 10) -> list[dict]:
            """
            获取最近有消息往来的会话列表（包含群号和QQ号）。
            返回最近活跃的联系人/群，用于了解当前有哪些活跃会话。

            Args:
                count: 数量

            Returns:
                联系人列表
            """
            if not self.napcat_bridge:
                return []

            try:
                result = await self.napcat_bridge.get_recent_contact(count)
                return result if isinstance(result, list) else []
            except Exception as e:
                return [{"error": str(e)}]

        @self.mcp.tool()
        async def qq_check_status() -> dict:
            """
            检查 NapCat 连接状态和基本信息。
            返回连接状态、机器人 QQ 号、消息缓存数量等。

            Returns:
                状态信息
            """
            if not self.napcat_bridge:
                return {"connected": False}

            status = {
                "connected": self.napcat_bridge.is_connected,
                "host": self.napcat_bridge.host,
                "port": self.napcat_bridge.port,
                "bot_qq": self.napcat_bridge.self_qq or "未知",
                "cached_messages": len(self.napcat_bridge.message_buffer._global),
                "active_targets": self.napcat_bridge.message_buffer.get_all_targets(),
            }
            return status

        @self.mcp.tool()
        async def qq_recall_message(message_id: str) -> bool:
            """
            撤回一条机器人自己发送的消息。仅能撤回机器人发送的消息，不能撤回其他用户的消息。

            Args:
                message_id: 消息 ID

            Returns:
                是否成功
            """
            if not self.napcat_bridge:
                return False

            try:
                await self.napcat_bridge.delete_msg(str(message_id))
                return True
            except Exception as e:
                logger.error(f"Recall message failed: {e}")
                return False


    def _connect_queues(self):
        """连接模块间的消息队列"""
        if not self.message_bus or not self.command_module or not self.cherrystudio_module:
            raise RuntimeError("模块未初始化")

        # NapCatBridge -> MessageBus
        self.napcat_bridge.message_bus = self.message_bus

        # 注入 self_qq getter 到黑名单过滤器 (用于 @机器人 时绕过黑名单)
        napcat = self.napcat_bridge
        self.message_bus._blacklist_filter._self_qq_getter = lambda: napcat.self_qq

        # MessageBus -> CommandModule
        command_queue = asyncio.Queue[ParsedMessage]()
        self.message_bus.set_command_queue(command_queue)
        self.command_module.queue = command_queue

        # MessageBus -> CherryStudioModule
        cherrystudio_queue = asyncio.Queue[ParsedMessage]()
        self.message_bus.set_cherrystudio_queue(cherrystudio_queue)
        self.cherrystudio_module.queue = cherrystudio_queue

        # NapCatBridge -> CherryStudioModule (用于 mark_responding 机制)
        self.cherrystudio_module.napcat_bridge = self.napcat_bridge

        # CherryStudioModule -> CommandModule (供 .order 命令访问 discovered_agents / rebuild_session)
        self.command_module.cherrystudio_module = self.cherrystudio_module
        self.command_module.context.cherrystudio_module = self.cherrystudio_module

        # CommandModule / CherryStudioModule -> send_message_queue (非阻塞)
        # 模块直接将 OutgoingMessage 推送到 send_queue，不再通过 response_queue
        self.command_module.context.send_queue = self.message_bus.send_message_queue
        if hasattr(self.cherrystudio_module, 'send_queue'):
            self.cherrystudio_module.send_queue = self.message_bus.send_message_queue

        # HookManager -> CommandContext (供 pre_command / post_command 钩子使用)
        self.command_module.context.hook_manager = self.hook_manager
        # HookManager -> MessageBus (已在 initialize 中设置, 此处确保一致)
        if self.message_bus:
            self.message_bus.hook_manager = self.hook_manager

        # MCP 握手完成信号 (由 main() 中 MCP 协议层触发)
        self.cherrystudio_module._mcp_handshake_event = self._mcp_handshake_event

        logger.info("Module queues connected (non-blocking concurrent mode)")

    def _setup_event_handlers(self):
        """设置 NapCat 事件处理器 (入群欢迎、好友审批等)"""
        if not self.napcat_bridge:
            return

        # ---- 6A.4: BotSettingConfig 自动重建 ----
        self._ensure_bot_setting_config()

        async def on_notice(data: dict):
            """处理通知事件"""
            notice_type = data.get("notice_type", "")

            if notice_type == "group_increase":
                group_id = str(data.get("group_id", ""))
                user_id = str(data.get("user_id", ""))
                logger.info(f"Detected group join: {group_id}, user: {user_id}")

                # 检查是否是机器人自己
                self_qq = self.napcat_bridge.self_qq
                if self_qq and user_id == self_qq:
                    # 机器人被拉入群 → 发送欢迎信息
                    greeting = self._build_bot_greeting(group_id)
                    try:
                        from protocols.messages import OutgoingMessage, MessageSource, MessageType
                        outgoing = OutgoingMessage(
                            target_source=MessageSource.GROUP,
                            target_id=group_id,
                            content=greeting,
                            message_type=MessageType.TEXT,
                        )
                        await self.napcat_bridge.send_message(outgoing)
                    except Exception as e:
                        logger.warning(f"Send group welcome failed: {e}")
                else:
                    # 其他用户入群 → 发送新成员欢迎
                    welcome = self._build_member_welcome(group_id, user_id)
                    if welcome:
                        try:
                            from protocols.messages import OutgoingMessage, MessageSource, MessageType
                            outgoing = OutgoingMessage(
                                target_source=MessageSource.GROUP,
                                target_id=group_id,
                                content=welcome,
                                message_type=MessageType.TEXT,
                            )
                            await self.napcat_bridge.send_message(outgoing)
                        except Exception as e:
                            logger.warning(f"Send new member welcome failed: {e}")

            elif notice_type == "friend_add":
                user_id = str(data.get("user_id", ""))
                logger.info(f"Detected new friend: {user_id}")
                greeting = self._build_friend_greeting(user_id)
                if greeting:
                    try:
                        from protocols.messages import OutgoingMessage, MessageSource, MessageType
                        outgoing = OutgoingMessage(
                            target_source=MessageSource.PRIVATE,
                            target_id=user_id,
                            content=greeting,
                            message_type=MessageType.TEXT,
                        )
                        await self.napcat_bridge.send_message(outgoing)
                    except Exception as e:
                        logger.warning(f"Send friend welcome failed: {e}")

        async def on_request(data: dict):
            """处理请求事件 (好友/群审批)"""
            req_type = data.get("request_type", "")
            flag = data.get("flag", "")

            if req_type == "friend" and self.config.get("auto_accept_friend", False):
                await self.napcat_bridge.approve_friend_request(flag)
                logger.info(f"Auto-approved friend request: {data.get('user_id')}")
            elif req_type == "group" and self.config.get("auto_accept_group", False):
                await self.napcat_bridge.approve_group_invite(flag)
                logger.info(f"Auto-approved group invite: {data.get('group_id')}")

        # 注册处理器
        self.napcat_bridge.register_notice_handler(on_notice)
        self.napcat_bridge.register_request_handler(on_request)

    def _build_bot_greeting(self, group_id: str) -> str:
        """构建机器人入群欢迎信息"""
        # 尝试读取 BotSettingConfig
        try:
            setting_path = project_root / "Configuration" / "BotSettingConfig.json"
            if setting_path.exists():
                settings = json.loads(setting_path.read_text(encoding="utf-8"))
                custom = settings.get("内置模块", {}).get("custom_greeting", "")
                if custom:
                    return custom
        except Exception:
            pass
        return "大家好！我是 QQ-MCP Bridge 机器人，输入 .help 查看可用命令。"

    def _build_member_welcome(self, group_id: str, user_id: str) -> str | None:
        """构建新成员欢迎信息 (读取 StateManager 中的欢迎配置)"""
        entry = self.state_manager.get_welcome(group_id)
        if not entry or not entry.get("enabled"):
            return None
        message = entry.get("message", "").strip()
        if not message:
            message = "欢迎新人！我是本群助手，发送 .help 查看可用命令～"
        # 替换 {at} 占位符
        at_code = f"[CQ:at,qq={user_id}]"
        return message.replace("{at}", at_code)

    def _build_friend_greeting(self, user_id: str) -> str | None:
        """构建好友欢迎信息"""
        return "你好！我是 QQ-MCP Bridge 机器人，有什么可以帮你的吗？输入 .help 查看可用命令。"

    def _ensure_bot_setting_config(self):
        """
        6A.4: BotSettingConfig.json 自动重建

        当 BotSettingConfig.json 不存在时，生成包含全部默认可定制消息模板的
        默认配置文件。确保用户可以通过编辑此文件自定义机器人行为。
        """
        setting_path = project_root / "Configuration" / "BotSettingConfig.json"
        if setting_path.exists():
            return

        default_config = {
            "内置模块": {
                "custom_greeting": ""
            },
            "help": {
                "help_greeting": ""
            },
            "BuiltInOrder": {
                "bot_on_message": "",
                "bot_off_message": "",
                "bot_orderwhite_message": "",
                "dismiss_message": ""
            },
            "dice_core": {
                "r_message": "",
                "ra_message": "",
                "st_message": "",
                "show_message": "",
                "del_card_message": "",
                "nn_message": ""
            },
            "arktrpg": {
                "rk_message": "",
                "rkb_message": "",
                "rkp_message": "",
                "sck_message": "",
                "ark_message": "",
                "sn_rk_message": ""
            },
            "ob": {
                "ob_join_message": "",
                "ob_list_message": ""
            },
            "log": {
                "log_new_message": "",
                "log_list_message": ""
            },
            "notification": {
                "receipt_message": ""
            }
        }

        try:
            setting_path.parent.mkdir(parents=True, exist_ok=True)
            setting_path.write_text(
                json.dumps(default_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"BotSettingConfig.json auto-rebuilt: {setting_path}")
        except Exception as e:
            logger.warning(f"BotSettingConfig.json rebuild failed: {e}")

    async def start(self):
        """
        创建后台任务

        启动所有模块的主循环作为后台 asyncio.Task：
        1. NapCatBridge (WebSocket 监听)
        2. MessageBus (消息路由)
        3. CommandModule (命令处理)
        4. CherryStudioModule (AI 处理)
        5. 发送消息循环 (MessageBus -> NapCatBridge)

        注意: MCP Server 的 stdio 传输由 main() 单独管理
        """
        if not all([
            self.napcat_bridge,
            self.message_bus,
            self.command_module,
            self.cherrystudio_module,
        ]):
            raise RuntimeError("系统未初始化")

        self._running = True
        logger.info("Background services starting...")

        # 创建所有模块的任务
        self._tasks = [
            asyncio.create_task(self.napcat_bridge.start(),
                                name="NapCatBridge"),
            asyncio.create_task(self.message_bus.start(), name="MessageBus"),
            asyncio.create_task(self.command_module.start(),
                                name="CommandModule"),
            asyncio.create_task(
                self.cherrystudio_module.start(), name="CherryStudioModule"),
            asyncio.create_task(self._send_messages_loop(),
                                name="SendMessageLoop"),
        ]

        # CherryStudio 延迟初始化 (Agent ID 解析、模型解析、Agent 发现等)
        # 作为后台任务运行，不阻塞 MCP stdio 服务器启动
        # MCP 握手和延迟初始化并行进行，首条用户消息到达时若尚未完成会使用兜底路径
        if self.cherrystudio_module:
            self._tasks.append(
                asyncio.create_task(
                    self.cherrystudio_module._deferred_init(),
                    name="CherryStudioDeferredInit",
                )
            )

        # 等待 NapCat 连接就绪后获取机器人 QQ 号
        self._tasks.append(
            asyncio.create_task(self._init_self_qq(), name="InitSelfQQ")
        )

        logger.info("All background services started")

    async def _init_self_qq(self):
        """
        [兜底] 等待 NapCat 连接就绪后获取 self_qq。

        主要路径已移至 NapCatBridge.start() -> _fetch_self_qq()，
        该方法在每次 WebSocket 连接/重连时自动刷新 self_qq。
        此处仅作为启动阶段的兜底，防止首次连接时 _fetch_self_qq 因时序问题失败。
        """
        if not self.napcat_bridge:
            return
        try:
            await self.napcat_bridge.wait_ready(timeout=30)
            logger.info("NapCat WebSocket connected")
        except Exception as e:
            logger.warning(f"NapCat connection pending (retrying in background): {e}")
            # 连接尚未就绪，等待 WebSocket 后续自动重连后重试一次
            try:
                await self.napcat_bridge.wait_ready(timeout=60)
                logger.info("NapCat WebSocket connected (retry success)")
            except Exception as e2:
                logger.warning(f"NapCat connection failed permanently: {e2}")
                return

        # 如果 self_qq 已由 NapCatBridge._fetch_self_qq() 设置，跳过重复获取
        if self.napcat_bridge.self_qq:
            logger.info(
                f"[self_qq] fallback: already set ({self.napcat_bridge.self_qq}), skip"
            )
            return

        # 获取登录信息 (机器人 QQ 号) - 兜底路径
        logger.info("[self_qq] fallback: calling get_login_info...")
        try:
            login_info = await self.napcat_bridge.get_login_info()
            logger.debug(f"[self_qq] fallback get_login_info returned: {login_info}")
            if login_info:
                self_qq = str(login_info.get("user_id", ""))
                if self_qq:
                    self.napcat_bridge.self_qq = self_qq
                    nickname = login_info.get("nickname", "")
                    logger.info(f"[self_qq] fallback Bot QQ: {self_qq} (nick: {nickname})")
                else:
                    logger.warning(
                        f"[self_qq] fallback: user_id empty in response: {login_info}"
                    )
            else:
                logger.warning("[self_qq] fallback: get_login_info returned empty")
        except Exception as e:
            logger.warning(f"[self_qq] fallback: get_login_info failed: {e}")

        # NapCat API 也失败了，尝试使用配置中的兜底值
        if not self.napcat_bridge.self_qq and self.napcat_bridge.config_self_qq:
            self.napcat_bridge.self_qq = self.napcat_bridge.config_self_qq
            logger.info(
                f"[self_qq] fallback: using config value: {self.napcat_bridge.config_self_qq}"
            )

    async def _send_messages_loop(self):
        """
        发送消息循环

        从 MessageBus 的 send_message_queue 获取待发送消息，
        并通过 NapCatBridge 发送到 QQ。
        遇到连接断开错误时，等待重连后自动重试一次。
        """
        if not self.message_bus or not self.napcat_bridge:
            return

        # 连接类错误码，遇到这些时等待重连并重试
        _conn_error_codes = {
            ErrorCode.NAPCAT_CONNECTION_FAILED,
            ErrorCode.NAPCAT_DISCONNECTED,
            ErrorCode.NAPCAT_TIMEOUT,
        }

        while self._running:
            try:
                # 从队列获取待发送消息
                outgoing_msg = await self.message_bus.send_message_queue.get()

                # 发送到 NapCat
                await self.napcat_bridge.send_message(outgoing_msg)

                logger.debug(
                    f"消息已发送: {outgoing_msg.target_source.value}_{outgoing_msg.target_id}")

            except asyncio.CancelledError:
                break
            except BridgeError as e:
                if e.code in _conn_error_codes and self._running:
                    target = f"{outgoing_msg.target_source.value}_{outgoing_msg.target_id}"
                    logger.warning(
                        f"发送失败 (连接断开)，等待重连后重试: {target}")
                    try:
                        await self.napcat_bridge.wait_ready(timeout=60.0)
                        await self.napcat_bridge.send_message(outgoing_msg)
                        logger.info(f"重连后重试发送成功: {target}")
                    except Exception as retry_err:
                        logger.error(
                            f"重连后重试发送仍失败: {target} - {retry_err}")
                else:
                    logger.error(f"Send message failed: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Send message failed: {e}", exc_info=True)

    async def shutdown(self):
        """
        优雅关闭

        按顺序关闭所有组件：
        1. 停止接收新消息
        2. 等待正在处理的消息完成
        3. 关闭所有模块
        """
        logger.info("System shutting down...")
        self._running = False

        # 1. 取消所有任务
        for task in self._tasks:
            task.cancel()

        # 2. 等待任务完成
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # 3. 关闭模块
        if self.command_module:
            await self.command_module.stop()

        if self.cherrystudio_module:
            await self.cherrystudio_module.stop()

        if self.message_bus:
            await self.message_bus.stop()

        if self.napcat_bridge:
            await self.napcat_bridge.stop()

        logger.info("System shutdown complete")

    # ------------------------------------------------------------------
    # 配置管理
    # ------------------------------------------------------------------

    async def _load_config(self):
        """加载配置文件"""
        try:
            if not self.config_path.exists():
                raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

            content = self.config_path.read_text(encoding="utf-8")
            self.config = json.loads(content)

            logger.info(f"Config loaded: {self.config_path}")

            # 适配旧配置格式到新格式
            self._adapt_legacy_config()

            # Pydantic 配置验证 (仅发出警告，不阻止启动)
            try:
                from protocols.config_models import validate_config
                validated = validate_config(self.config)
                logger.info("Config Pydantic validation passed")
            except Exception as e:
                logger.warning(f"Config Pydantic validation failed (non-fatal): {e}")

        except json.JSONDecodeError as e:
            raise BridgeError(
                ErrorCode.CONFIG_LOAD_FAILED,
                detail=f"JSON 格式错误: {e}",
            )
        except Exception as e:
            raise BridgeError(
                ErrorCode.CONFIG_LOAD_FAILED,
                detail=str(e),
            )

    def _adapt_legacy_config(self):
        """
        适配旧配置格式到新格式

        支持两种配置格式：
        1. 新格式: {"cherrystudio": {"mcp_server_path": "...", "http_api_base": "..."}}
        2. 旧格式: {"cherry_api_key": "...", "agent_api_url": "..."} (来自 C:\\CherryStudio\\qq-mcp-bridge\\config.json)

        同时标准化:
        - llm -> llm_providers (键名)
        - api_url -> base_url (每个 provider 内)
        """
        # --- CherryStudio 配置节适配 ---
        if "cherrystudio" not in self.config:
            # 检测是否为旧格式配置
            has_legacy_keys = any(key in self.config for key in [
                "cherry_api_key",
                "agent_api_url",
                "mcp_server_name"
            ])

            if has_legacy_keys:
                logger.info("Legacy config format detected, migrating...")

                cherry_api_key = self.config.get("cherry_api_key", "")
                agent_api_url = self.config.get(
                    "agent_api_url", "http://127.0.0.1:23333")
                mcp_server_name = self.config.get("mcp_server_name", "QQ Bridge")

                self.config["cherrystudio"] = {
                    "mcp_server_path": None,
                    "http_api_base": agent_api_url,
                    "api_key": cherry_api_key,
                    "legacy_mode": True,
                    "mcp_server_name": mcp_server_name,
                }

                logger.info(f"Config migration complete:")
                logger.info(f"  - HTTP API: {agent_api_url}")
                logger.info(f"  - MCP Server Name: {mcp_server_name}")
            else:
                logger.warning("CherryStudio config not found, using defaults")
                self.config["cherrystudio"] = {
                    "mcp_server_path": None,
                    "http_api_base": "http://127.0.0.1:23333",
                    "api_key": "",
                }
        else:
            logger.info("Using new CherryStudio config format")

        # --- LLM Provider 键名标准化 ---
        if "llm" in self.config and "llm_providers" not in self.config:
            self.config["llm_providers"] = self.config["llm"]
            logger.info("Config normalized: llm -> llm_providers")

        # 标准化每个 provider 内的配置
        # 注意: 不再将 api_url 复制到 base_url.
        # api_url 表示完整端点 URL (如 https://api.example.com/v1/chat/completions), 由调用方直接使用.
        # base_url 表示 API 根地址 (如 https://api.example.com/v1), 由代码拼接端点路径.
        # 两者语义不同, 不应混用. _call_provider / _call_vision_provider 已支持 api_url 回退.
        for key in ("llm_providers", "llm", "vision_providers"):
            providers = self.config.get(key, [])
            if isinstance(providers, list):
                for provider in providers:
                    if isinstance(provider, dict):
                        # 标准化 model: 如果只有 models (数组), 取第一个元素作为 model
                        if "model" not in provider and "models" in provider:
                            models_list = provider.get("models", [])
                            if models_list and isinstance(models_list, list):
                                provider["model"] = models_list[0]
                                logger.info(
                                    f"Config normalized: models[0] -> model "
                                    f"for provider [{provider.get('name', '?')}]")

        if "vision_providers" in self.config:
            logger.info(f"Config normalized: vision_providers ({len(self.config['vision_providers'])} provider(s))")

    # ------------------------------------------------------------------
    # 单例锁
    # ------------------------------------------------------------------

    def _check_singleton(self):
        """
        PID 单例锁已禁用。
        CherryStudio 频繁重启 MCP 服务器，PID 锁会导致启动失败。
        """
        pass

    def _cleanup_pid_file(self):
        """PID 文件清理已随单例锁一同禁用。"""
        pass


async def main():
    """
    主入口函数

    启动顺序:
    1. 控制台 / 日志初始化
    2. 系统初始化 (配置、模块、MCP 工具注册)
    3. 后台服务启动 (NapCat WebSocket 异步连接，不阻塞)
    4. MCP stdio 服务器启动 (阻塞，等待 CherryStudio 连接)
    """
    # ---- 加载配置 (用于 show_console / debug_mode 等早期设置) ----
    _early_config: dict = {}
    _config_path = project_root / "Configuration" / "config.json"
    try:
        if _config_path.exists():
            _early_config = json.loads(_config_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    # ---- debug_mode: 日志级别控制 ----
    # 0 = 静默 (不记录任何日志)
    # 1 = ERROR 及以上
    # 2 = WARNING 及以上
    # 3 = INFO 及以上
    # 4+ = DEBUG 及以上 (全部记录)
    _DEBUG_MODE_LEVELS = {
        0: logging.CRITICAL + 10,  # 高于 CRITICAL，静默所有日志
        1: logging.ERROR,
        2: logging.WARNING,
        3: logging.INFO,
    }
    _debug_mode = _early_config.get("debug_mode", 0)
    _handler_level = _DEBUG_MODE_LEVELS.get(_debug_mode, logging.DEBUG)
    for _h in logging.getLogger().handlers:
        _h.setLevel(_handler_level)
    if _debug_mode > 0:
        _log_path = (project_root / "PlayerLog" / "bridge.log") if _file_handler else "stderr only"
        logger.info(f"Logging configured: debug_mode={_debug_mode}, level={logging.getLevelName(_handler_level)}, file={_log_path}")

    # ---- Windows 独立控制台窗口 ----
    _show_console = _early_config.get("show_console", False)
    if sys.platform == "win32" and _show_console:
        try:
            ctypes.windll.kernel32.FreeConsole()
            ctypes.windll.kernel32.AllocConsole()
            ctypes.windll.kernel32.SetConsoleTitleW("QQ-MCP Bridge v3.0")
        except Exception:
            pass

    # ---- Windows 控制台 UTF-8 编码 ----
    # 防止中文 Agent 名称等 Unicode 字符在日志中出现乱码
    if sys.platform == "win32":
        try:
            # 设置控制台代码页为 UTF-8
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
        # 确保 PYTHONIOENCODING 为 utf-8
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        # 重新包装 stdout/stderr 以使用 UTF-8 编码
        # AllocConsole() 后新控制台的 TextIOWrapper 可能使用了系统默认编码 (cp936)
        try:
            import io
            if hasattr(sys.stderr, "buffer"):
                sys.stderr = io.TextIOWrapper(
                    sys.stderr.buffer, encoding="utf-8", errors="replace"
                )
            if hasattr(sys.stdout, "buffer"):
                sys.stdout = io.TextIOWrapper(
                    sys.stdout.buffer, encoding="utf-8", errors="replace"
                )
        except Exception:
            pass

    # ---- Windows stderr 控制台模式 (启用 VT 序列) ----
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-12),  # STD_ERROR_HANDLE
                7,  # ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
        except Exception:
            pass

    # 屏蔽 MCP 协议层心跳日志
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

    # ---- 创建并初始化服务器 ----
    srv = Server()

    try:
        await srv.initialize()

        # 启动后台服务 (NapCatBridge, MessageBus, CommandModule, etc.)
        await srv.start()

        # NapCat WebSocket 由 Server.start() 内的 _init_self_qq 后台任务统一处理
        # (不再在此处额外启动 _wait_napcat_ready，避免竞态)

        # ---- 运行 MCP Server (stdio 传输) ----
        # FastMCP 的 run() 是同步函数 (内部调用 anyio.run)，
        # 无法与已有的 asyncio 事件循环共存。
        # 因此使用底层的 stdio_server + Server.run() 来复用当前事件循环。
        logger.info("MCP server started, waiting for CherryStudio connection...")
        from mcp.server.stdio import stdio_server as _stdio_server
        from mcp.server.session import ServerSession as _ServerSession
        from mcp import types as _mcp_types

        # FastMCP._mcp_server 是底层 mcp.server.Server 实例 (SDK 标准属性)
        _low_level_server = srv.mcp._mcp_server

        # Hook into MCP protocol handshake:
        # ServerSession._received_notification handles InitializedNotification internally
        # (before it reaches any registered handler), so we wrap it to set our event.
        _orig_received_notification = _ServerSession._received_notification

        async def _on_received_notification(self_session, notification):
            await _orig_received_notification(self_session, notification)
            if isinstance(notification.root, _mcp_types.InitializedNotification):
                srv._mcp_handshake_event.set()
                logger.info(
                    "[MCP] Client handshake complete "
                    "(InitializedNotification received)"
                )

        _ServerSession._received_notification = _on_received_notification

        async with _stdio_server() as (read_stream, write_stream):
            await _low_level_server.run(
                read_stream,
                write_stream,
                _low_level_server.create_initialization_options(),
            )

    except KeyboardInterrupt:
        logger.info("Interrupt signal received")
    except BridgeError as e:
        logger.error(f"System startup failed [{e.error_code}]: {e.user_message}", exc_info=True)
    except Exception as e:
        logger.error(f"System startup failed: {e}", exc_info=True)
    finally:
        await srv.shutdown()


def main_sync():
    """同步入口函数，用于 pyproject.toml [project.scripts] (uvx/pip)。"""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
