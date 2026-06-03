"""
QQ 自动回复模块
- 多 Agent 支持：每个 Agent 独立的会话记录目录
- 会话持久化到 QQConversationRecord/{agent_name}/
- 3 天无交互 → AI 自动摘要压缩 → 删除旧 Agent 会话
- 下次交互 → 注入记忆 → 新建 Agent 会话
"""

import asyncio
import json
import logging
import random
import shutil
import string
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from Built_in.napcat_client import NapCatClient, QQMessage
from Built_in.conversation_store import (
    _conv_dir,
    append_to_log,
    delete_session,
    force_stale,
    get_agent_session_id,
    get_conversation_agent,
    is_stale,
    load_memory,
    load_session_log,
    make_session_name,
    save_memory,
    set_agent_session_id,
    set_conversation_agent,
    touch_active,
)
from Temp.store import load_list, save_list

logger = logging.getLogger("auto-reply")

SUMMARY_PROMPT = """请用简洁的要点形式总结以下 QQ 聊天记录（不超过 300 字）。
保留：对话参与者是谁、讨论了什么话题、有什么重要信息或约定。
抛弃：无意义的寒暄、重复内容。

聊天记录：
{log}"""

PLAYER_LOG = Path(__file__).parent.parent / "PlayerLog"


def _log_group_dir(group_id: str) -> Path:
    return PLAYER_LOG / f"group_{group_id}"


def _find_log_dir(group_id: str, log_name: str) -> Path | None:
    gd = _log_group_dir(group_id)
    if not gd.exists():
        return None
    for d in sorted(gd.iterdir()):
        if d.is_dir() and log_name in d.name:
            return d
    return None


def _log_read_state(log_dir: Path) -> dict | None:
    path = log_dir / "state.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _log_mark_over(log_dir: Path):
    state = _log_read_state(log_dir)
    if state:
        state["over"] = True
        (log_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


@dataclass
class Conversation:
    target_key: str  # "private:QQ号" / "group:群号"
    messages: deque[dict] = field(default_factory=lambda: deque(maxlen=40))
    agent_session_id: str = ""
    agent_name: str = ""
    model: str = ""       # 当前会话使用的模型，空则用默认
    last_reply_time: float = 0


class AutoReply:
    def __init__(
        self,
        napcat: NapCatClient,
        # LLM providers (数组, 索引 0 为默认, 索引递增 fallback)
        llm_providers: list[dict] | None = None,
        vision_providers: list[dict] | None = None,
        default_llm_provider: int = 0,
        default_llm_model: str = "",
        default_vision_provider: int = 0,
        default_vision_model: str = "",
        system_prompt: str = "",
        # Agent API (多 Agent)
        agent_enabled: bool = False,
        agents: dict[str, dict] | None = None,
        default_agent: str = "",
        agent_api_url: str = "http://127.0.0.1:23333",
        agent_timeout: float = 60.0,
        agent_api_key: str = "",
        agent_model: str = "",
        # Vision
        vision_enabled: bool = False,
        vision_prompt: str = "",
        # Settings
        reply_to_groups: list[str] | None = None,
        reply_to_friends: list[str] | None = None,
        reply_mode: str = "mention",
        cooldown_seconds: float = 5.0,
        max_context_messages: int = 20,
        message_split_threshold: float = 5.0,
        reply_chain_depth: int = 4,
        doc_threshold: int = 2000,
        global_context: str = "",
        admin_qq: str = "",
        # File processing (MinerU)
        file_processing_enabled: bool = False,
        mineru_command: str = "mineru-open-api",
        mineru_max_file_size_mb: int = 10,
        mineru_summary_max_chars: int = 2000,
    ):
        self._nc = napcat
        self._system_prompt = system_prompt
        self._reply_to_groups = set(reply_to_groups or [])
        self._reply_to_friends = set(reply_to_friends or [])
        self._reply_mode = reply_mode
        self._cooldown = cooldown_seconds
        self._max_context = max_context_messages
        self._split_threshold = message_split_threshold
        self._reply_chain_depth = max(0, min(reply_chain_depth, 10))
        self._doc_threshold = doc_threshold
        self._global_context = global_context
        self._admin_qq = admin_qq
        self._self_qq = ""  # 机器人自己的 QQ，登录后设置

        # LLM / Vision providers (扁平数组, 索引递增 fallback)
        self._llm_providers: list[dict] = llm_providers or []
        self._vision_providers: list[dict] = vision_providers or []
        self._active_llm_idx: int = max(0, min(default_llm_provider, len(self._llm_providers) - 1)) if self._llm_providers else 0
        self._active_vision_idx: int = max(0, min(default_vision_provider, len(self._vision_providers) - 1)) if self._vision_providers else 0
        self._default_llm_model: str = default_llm_model
        self._default_vision_model: str = default_vision_model
        self._failover_notified: bool = False

        # File processing
        self._file_processing = file_processing_enabled
        self._mineru_cmd = mineru_command
        self._mineru_max_mb = mineru_max_file_size_mb
        self._mineru_summary_chars = mineru_summary_max_chars

        self._agent_enabled = agent_enabled
        self._agents = agents or {}
        self._default_agent = default_agent
        self._agent_base = agent_api_url.rstrip("/")
        self._agent_timeout = agent_timeout
        self._agent_key = agent_api_key or (self._llm_providers[0]["api_key"] if self._llm_providers else "")
        self._agent_model = agent_model
        self._cherry_model_map: dict[str, str] = {}

        self._vision_enabled = vision_enabled
        self._vision_prompt = vision_prompt

        self._conversations: dict[str, Conversation] = {}
        self._saved_models: dict[str, str] = {}
        self._order_whitelist: set[str] = set(load_list("order_whitelist.json"))
        self._bot_blacklist: set[str] = set(load_list("bot_blacklist.json"))
        # 日志系统
        self._active_logs: dict[str, str] = {}             # {group_id: folder_name}
        self._active_log_display: dict[str, str] = {}      # {group_id: display_name}
        self._log_paused: set[str] = set()                 # 暂停记录的群组
        self._ob_enabled: set[str] = set()                 # 已开启旁观模式的群组

        # 每会话消息队列：保证同一会话内消息顺序处理
        self._queues: dict[str, asyncio.Queue[QQMessage]] = {}
        self._workers: dict[str, asyncio.Task] = {}

    def set_self_qq(self, qq: str):
        """设置机器人自己的 QQ 号（登录后调用）"""
        self._self_qq = str(qq)

    # ------------------------------------------------------------------
    # Agent 配置查找
    # ------------------------------------------------------------------

    def _get_agent_config(self, conv_key: str) -> tuple[str, dict]:
        """返回 (agent_name, agent_cfg)。agent_cfg: {agent_id, model, work_dirs}"""
        # 1. 内存中的 Conversation
        conv = self._conversations.get(conv_key)
        if conv and conv.agent_name and conv.agent_name in self._agents:
            return conv.agent_name, self._agents[conv.agent_name]

        # 2. 持久化的 mapping (从 conv_key 反推 msg_type, target_id)
        parts = conv_key.split(":", 1)
        if len(parts) == 2:
            name = get_conversation_agent(parts[0], parts[1])
            if name and name in self._agents:
                if conv:
                    conv.agent_name = name
                return name, self._agents[name]

        # 3. 回退到默认
        if self._default_agent in self._agents:
            if conv:
                conv.agent_name = self._default_agent
            return self._default_agent, self._agents[self._default_agent]

        # 4. 最后的回退：取第一个 agent
        if self._agents:
            first = next(iter(self._agents.items()))
            if conv:
                conv.agent_name = first[0]
            return first

        return "", {}

    @staticmethod
    def _normalize_work_dirs(agent_cfg: dict) -> list[str]:
        """兼容 work_dirs (list) 和 work_dir (string) 两种配置格式"""
        if "work_dirs" in agent_cfg:
            return agent_cfg["work_dirs"]
        if "work_dir" in agent_cfg:
            return [agent_cfg["work_dir"]]
        return []

    # ------------------------------------------------------------------
    # 消息入口（入队）
    # ------------------------------------------------------------------

    async def handle_message(self, msg: QQMessage):
        """将消息放入对应会话的队列，由 Worker 顺序处理"""
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        conv_key = f"{msg.message_type}:{target}"

        # 确保队列存在
        if conv_key not in self._queues:
            self._queues[conv_key] = asyncio.Queue()

        await self._queues[conv_key].put(msg)
        self._ensure_worker(conv_key)

    def _ensure_worker(self, conv_key: str):
        """确保指定会话有 Worker 在运行"""
        task = self._workers.get(conv_key)
        if task is None or task.done():
            self._workers[conv_key] = asyncio.create_task(self._worker(conv_key))

    async def _worker(self, conv_key: str):
        """顺序处理队列中的消息，空闲 5 分钟后自动退出"""
        queue = self._queues[conv_key]
        IDLE_TIMEOUT = 300  # 5 分钟无消息则退出
        logger.info(f"Worker 启动: {conv_key}")
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.info(f"Worker 空闲退出: {conv_key}")
                    # 清理队列和 worker 引用
                    self._queues.pop(conv_key, None)
                    self._workers.pop(conv_key, None)
                    return
                try:
                    await self._run_message(msg)
                except Exception as e:
                    logger.error(f"Worker [{conv_key}] 处理消息异常: {e}", exc_info=True)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"Worker 被取消: {conv_key}")
            self._queues.pop(conv_key, None)
            self._workers.pop(conv_key, None)
            raise

    async def _run_message(self, msg: QQMessage):
        """实际处理单条消息（原 handle_message 逻辑）"""
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        msg_type = msg.message_type

        # 日志记录：群聊消息写入活跃日志
        if msg_type == "group" and target in self._active_logs and target not in self._log_paused:
            if not self._is_log_blacklisted(target, msg.sender_id):
                self.log_write(target, msg)

        # --- 指令拦截：检测消息中的 .xxx 指令 ---
        # 扫描文本中 . 后跟字母的部分，若为合法命令则按指令处理
        text = msg.text.strip()
        cmd_match = self._find_command(text)
        if cmd_match:
            cmd_name, cmd_full = cmd_match
            # 检查是否 @ 了非机器人用户 → 无视指令
            if self._has_at_others(msg):
                return

            # 剔除指令后附带的 @xxx 及其后续内容
            import re
            cmd_full = re.sub(r'\s*@\d+\s*.*$', '', cmd_full)

            # 群聊中所有指令默认需要 @；已在 order 白名单的群或 .bot orderwhite 本身除外
            is_orderwhite = (cmd_name == "bot" and cmd_full.split(None, 2)[1:2] == ["orderwhite"])
            can_cmd = (
                msg.message_type != "group"
                or (target in self._order_whitelist and target not in self._bot_blacklist)
                or self._is_at_me(msg)
                or is_orderwhite
            )
            if can_cmd:
                from OrderSystem import dispatch, get_command as _get_cmd
                from OrderSystem.base import CommandContext
                ctx = CommandContext(nc=self._nc, auto_reply=self)
                reply = await dispatch(cmd_full, msg, ctx)
                if reply:
                    await self._nc.send_msg(msg_type, target, reply)
            return

        if not self._should_reply(msg):
            return

        logger.info(f"处理消息 [{msg_type}:{target}]: {msg.text[:60]}")
        conv = self._get_conversation(msg)
        now = time.monotonic()
        if now - conv.last_reply_time < self._cooldown:
            return

        conv.last_reply_time = now

        # 引用链：递归获取被引用消息的内容和图片
        reply_chain_text = ""
        reply_chain_images: list[str] = []
        if self._reply_chain_depth > 0:
            reply_chain_text, reply_chain_images = await self._fetch_reply_chain(msg, self._reply_chain_depth)
            if reply_chain_images:
                logger.info(f"引用链图片: {len(reply_chain_images)} 张, files: {[f[:20] for f in reply_chain_images]}")

        # 图片识别：合并当前消息 + 引用链中的所有图片
        all_image_files: list[str] = list(msg.image_files) + reply_chain_images
        image_text = ""
        if self._vision_enabled and all_image_files:
            logger.info(f"Vision: 共 {len(all_image_files)} 张图片待识别 (当前消息 {len(msg.image_files)} + 引用链 {len(reply_chain_images)})")
            image_text = await self._recognize_images(all_image_files, msg.text)

        # 文件处理：下载 → MinerU 摘要
        file_text = ""
        if msg.file_infos:
            file_text = await self._process_files(msg.file_infos)

        formatted = self._format_incoming(msg)
        if reply_chain_text:
            formatted = reply_chain_text + "\n\n---\n" + formatted
        if file_text:
            formatted = file_text + "\n\n---\n" + formatted
        if image_text:
            formatted += f"\n\n[图片内容描述]\n{image_text}"
        conv.messages.append({"role": "user", "content": formatted})

        # 确定当前会话使用的 Agent
        agent_name, _ = self._get_agent_config(conv.target_key)

        # 记录到本地日志
        append_to_log(agent_name, msg_type, target, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "role": "user",
            "sender": msg.sender_name,
            "sender_id": msg.sender_id,
            "content": msg.text,
        })

        try:
            replies = None
            if self._agent_enabled and self._agents:
                replies = await self._call_agent_api(conv, msg_type, target, formatted)
            elif self._agent_enabled:
                logger.warning(f"Agent 已启用但 agents 配置为空 [{msg_type}:{target}]")
            else:
                logger.warning(f"Agent 未启用 [{msg_type}:{target}]")

            if replies:
                combined = "\n\n".join(replies)
                # 超长文本 → 以文件形式发送
                if self._doc_threshold > 0 and len(combined) > self._doc_threshold:
                    sent_as_doc = await self._send_as_doc(msg_type, target, combined)
                    if sent_as_doc:
                        summary = combined[:200] + ("…" if len(combined) > 200 else "")
                        await self._nc.send_msg(msg_type, target, f"📄 已生成长文档（{len(combined)}字符），请查收文件。\n前200字预览：{summary}")
                    else:
                        for reply in replies:
                            await self._nc.send_msg(msg_type, target, reply)
                else:
                    for reply in replies:
                        md_images = self._extract_md_images(reply)
                        text_only = self._strip_md_images(reply)
                        if text_only:
                            await self._nc.send_msg(msg_type, target, text_only)
                        for alt, url in md_images:
                            await self._nc.send_image(msg_type, target, url, alt)
                            logger.info(f"Agent 发送图片: {url[:60]}...")
                conv.messages.append({"role": "assistant", "content": combined})
                append_to_log(agent_name, msg_type, target, {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "role": "assistant",
                    "content": combined,
                })
                touch_active(agent_name, msg_type, target)
                logger.info(f"Agent 回复 [{agent_name}][{msg_type}:{target}]: {len(replies)} 条 ({len(combined)} 字符)")
            else:
                logger.warning(f"Agent 无回复 [{msg_type}:{target}]")
                await self._nc.send_msg(msg_type, target, "小企鹅看不懂拉，您发的太深奥了拉")
        except TimeoutError:
            logger.warning(f"发送消息超时 [{msg_type}:{target}]，WebSocket 可能已断开")
        except Exception as e:
            logger.error(f"handle_message 异常: {e}", exc_info=True)
            try:
                await self._nc.send_msg(msg_type, target, "小企鹅看不懂拉，您发的太深奥了拉")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Agent API
    # ------------------------------------------------------------------

    async def _call_agent_api(self, conv: Conversation, msg_type: str, target_id: str, user_text: str) -> list[str] | None:
        try:
            agent_name, agent_cfg = self._get_agent_config(conv.target_key)
            if not agent_cfg:
                logger.warning("没有可用的 Agent 配置")
                return None

            agent_id = agent_cfg["agent_id"]
            work_dirs = self._normalize_work_dirs(agent_cfg)

            # 检测是否为新会话
            is_new_session = not conv.agent_session_id and not get_agent_session_id(agent_name, msg_type, target_id)

            sid = await self._get_or_create_session(conv, msg_type, target_id, agent_name, agent_cfg)
            if not sid:
                return None

            # 新会话：注入工作区上下文
            if is_new_session:
                context = self._load_workspace_context(work_dirs)
                memory = load_memory(agent_name, msg_type, target_id)
                parts = []
                if context:
                    parts.append(context)
                if memory:
                    parts.append(f"<历史对话摘要>\n{memory}\n</历史对话摘要>")
                if self._global_context:
                    parts.append(f"<全局规则>\n{self._global_context}\n</全局规则>")
                if parts:
                    user_text = "\n\n".join(parts) + f"\n\n---\n当前消息：{user_text}"
                    logger.info(f"Agent [{agent_name}]: 新会话注入上下文 ({len(context)} 字符 + {'有' if memory else '无'}记忆 + {'有' if self._global_context else '无'}全局规则)")

            body = {"content": user_text}
            url = f"{self._agent_base}/v1/agents/{agent_id}/sessions/{sid}/messages"
            logger.info(f"Agent [{agent_name}]: 发送到 {sid[:20]}... {user_text[:60]}")

            STALL_TIMEOUT = 30       # 30s 无输出判定停滞
            TOTAL_TIMEOUT = 300      # 5 分钟总超时
            NOTIFY_INTERVAL = 25     # 每隔 25s 可发一次"烧烤中"通知

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._agent_key}",
                    },
                    timeout=aiohttp.ClientTimeout(total=TOTAL_TIMEOUT),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"Agent API {resp.status}: {text[:150]}")
                        # 仅会话不存在时清除本地记录，避免因网络波动丢弃有效会话
                        if resp.status in (404, 410):
                            conv.agent_session_id = ""
                            set_agent_session_id(agent_name, msg_type, target_id, "")
                            logger.info(f"Agent 会话已失效 ({resp.status})，下次将重建")
                        # 检测额度错误 → 切换备用
                        if self._is_quota_error(resp.status, text):
                            await self._do_failover("llm")
                        return None

                    # --- 流式读取 SSE，带停滞检测 ---
                    blocks: list[tuple[float, str]] = []
                    current_deltas: list[str] = []
                    in_text = False
                    last_chunk_time = time.monotonic()
                    start_time = time.monotonic()
                    last_notify_time = 0.0
                    has_any_output = False

                    while True:
                        # 每次 readline 最多等 STALL_TIMEOUT 秒
                        try:
                            line_bytes = await asyncio.wait_for(
                                resp.content.readline(), timeout=STALL_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            # 30s 内没有任何数据到达
                            elapsed = time.monotonic() - start_time
                            if has_any_output:
                                # 有输出但卡住了 → 可能是最后一段在生成
                                if time.monotonic() - last_notify_time > NOTIFY_INTERVAL:
                                    await self._nc.send_msg(msg_type, target_id, "小企鹅正在烧烤中呜……")
                                    last_notify_time = time.monotonic()
                                    logger.info(f"Agent [{agent_name}]: 已等待 {elapsed:.0f}s，仍在生成中")
                                continue
                            else:
                                # 完全没有输出 → 真正超时
                                logger.warning(f"Agent [{agent_name}]: SSE 流 {elapsed:.0f}s 无任何输出")
                                await self._nc.send_msg(msg_type, target_id, "小企鹅看不懂拉，您发的太深奥了拉")
                                conv.agent_session_id = ""
                                set_agent_session_id(agent_name, msg_type, target_id, "")
                                return None

                        if not line_bytes:
                            break  # EOF

                        line = line_bytes.decode("utf-8").strip()
                        last_chunk_time = time.monotonic()

                        if line.startswith("data: ") and line[6:] != "[DONE]":
                            try:
                                obj = json.loads(line[6:])
                                t = obj.get("type", "")
                                if t == "text-start":
                                    current_deltas = []
                                    in_text = True
                                elif t == "text-end":
                                    if current_deltas:
                                        blocks.append((last_chunk_time, max(current_deltas, key=len).strip()))
                                        has_any_output = True
                                    current_deltas = []
                                    in_text = False
                                elif t == "text-delta":
                                    if in_text:
                                        current_deltas.append(str(obj.get("text", "")))
                                        has_any_output = True  # 有流式输出即标记
                            except (json.JSONDecodeError, KeyError):
                                pass
                        elif line == "data: [DONE]":
                            break

                    # 处理未闭合的 text 块
                    if current_deltas:
                        blocks.append((last_chunk_time, max(current_deltas, key=len).strip()))
                        has_any_output = True

                    if not blocks:
                        logger.warning("Agent: 空回复 — 会话可能已失效，清除后下次将重建")
                        conv.agent_session_id = ""
                        set_agent_session_id(agent_name, msg_type, target_id, "")
                        return None

                    # 按时间阈值分组
                    threshold = self._split_threshold
                    messages = []
                    current_parts = []
                    last_ts = 0.0
                    for ts, txt in blocks:
                        if threshold > 0 and current_parts and (ts - last_ts > threshold):
                            messages.append("\n\n".join(current_parts))
                            current_parts = []
                        current_parts.append(txt)
                        last_ts = ts
                    if current_parts:
                        messages.append("\n\n".join(current_parts))

                    logger.info(f"Agent [{agent_name}]: 回复 {sum(len(m) for m in messages)} 字符 ({len(messages)} 条消息)")
                    return messages

        except asyncio.TimeoutError:
            logger.warning("Agent: 总超时 (300s)")
            try:
                await self._nc.send_msg(msg_type, target_id, "小企鹅看不懂拉，您发的太深奥了拉")
            except Exception:
                pass
            return None
        except Exception as e:
            logger.warning(f"Agent: API 异常 {e}")
            return None

    async def _get_or_create_session(self, conv: Conversation, msg_type: str, target_id: str,
                                     agent_name: str, agent_cfg: dict) -> str:
        """获取或创建 Agent 会话"""
        if conv.agent_session_id:
            return conv.agent_session_id

        session_name = make_session_name(msg_type, target_id)

        # 持久化的 session
        stored_sid = get_agent_session_id(agent_name, msg_type, target_id)
        if stored_sid:
            if is_stale(agent_name, msg_type, target_id):
                logger.info(f"Agent: 会话 {session_name} 过期 (>3天)，触发摘要...")
                await self._summarize_and_cleanup(agent_name, msg_type, target_id, stored_sid)
                stored_sid = ""
            else:
                logger.info(f"Agent: 复用持久化会话 {stored_sid[:20]}... ({session_name})")
                conv.agent_session_id = stored_sid
                return stored_sid

        # 创建新会话
        logger.info(f"Agent: 为 {session_name} 创建新会话...")
        new_sid = await self._create_session(session_name, agent_cfg, conv)
        if new_sid:
            conv.agent_session_id = new_sid
            set_agent_session_id(agent_name, msg_type, target_id, new_sid)
        return new_sid

    async def _resolve_cherry_model(self, model: str) -> str:
        """将短模型名解析为 CherryStudio 的 provider:model_id 格式"""
        if not model:
            return model
        # 已经是 provider:model_id 格式的直接返回
        if ":" in model:
            return model
        # 从缓存查找
        if model in self._cherry_model_map:
            return self._cherry_model_map[model]
        # 查询 CherryStudio /v1/models 建立映射
        try:
            url = f"{self._agent_base}/v1/models"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._agent_key}",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        raw_text = await resp.text()
                        data = json.loads(raw_text)
                        logger.info(f"CherryStudio 模型 API: 获取到 {len(raw_text)} bytes")
                        items = data.get("data", [])
                        for m in items:
                            short = m.get("provider_model_id", "")
                            full = m.get("provider", "") + ":" + short
                            if short and short not in self._cherry_model_map:
                                self._cherry_model_map[short] = full
                        if not items:
                            logger.warning(f"模型列表为空: {raw_text[:300]}")
                    else:
                        logger.warning(f"模型列表 HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"无法获取 CherryStudio 模型列表: {e}")

        if model in self._cherry_model_map:
            return self._cherry_model_map[model]
        # 兜底: 用 deepseek provider
        logger.warning(f"模型 '{model}' 未在 CherryStudio 中找到，fallback 到 deepseek:{model}")
        return f"deepseek:{model}"

    async def _fetch_agents_from_cherrystudio(self, whitelist: list[str] | None = None) -> dict[str, dict]:
        """从 CherryStudio /v1/agents 自动拉取 Agent 列表，按白名单过滤。
        返回 {name: {agent_id, work_dirs}} 字典。"""
        url = f"{self._agent_base}/v1/agents"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {self._agent_key}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"拉取 Agent 列表失败 ({resp.status}): {text[:150]}")
                        return {}
                    data = await resp.json()
        except Exception as e:
            logger.warning(f"拉取 Agent 列表异常: {e}")
            return {}

        items = data if isinstance(data, list) else data.get("data", [])
        if not items:
            logger.warning("Agent 列表为空")
            return {}

        whitelist_set = set(whitelist or [])
        agents = {}
        all_names: list[tuple[str, str]] = []
        for item in items:
            agent_id = item.get("id", "")
            name = item.get("name", agent_id)
            all_names.append((name, agent_id))
            if whitelist_set and agent_id not in whitelist_set:
                logger.debug(f"Agent {name} ({agent_id}) 不在白名单，跳过")
                continue
            agents[name] = {
                "agent_id": agent_id,
                "work_dirs": item.get("accessible_paths", []),
            }

        # 自动模式：检查每个 Agent 是否挂载了桥接 MCP
        if not whitelist_set and agents:
            agents = await self._filter_mcp_agents(agents)

        if not whitelist_set:
            lines = ["未设置白名单：从 CherryStudio 获取到全部 Agent："]
            for i, (name, aid) in enumerate(all_names, 1):
                lines.append(f"  {i}: {name} - {aid}")
            logger.info("\n".join(lines))
            # 输出实际通过 MCP 验证的 Agent 名单
            if agents:
                active_lines = ["当前开启的 Agent 名单："]
                for i, (name, cfg) in enumerate(agents.items(), 1):
                    active_lines.append(f"  {i}: {name} - {cfg['agent_id']}")
                logger.info("\n".join(active_lines))

        if agents:
            if whitelist_set:
                agent_names = "、".join(agents.keys())
                logger.info(f"从 CherryStudio 自动拉取 {len(agents)} 个 Agent: {agent_names}")
        else:
            logger.warning("白名单内无匹配 Agent")
        return agents

    async def _filter_mcp_agents(self, agents: dict) -> dict:
        """通过 /v1/agents/{id} 的 mcps 字段验证 Agent 是否挂载了桥接 MCP"""
        # 自动获取桥接 MCP ID
        bridge_mcp_id = await self._get_bridge_mcp_id()
        if not bridge_mcp_id:
            logger.warning("无法获取桥接 MCP ID，全部加载")
            return agents

        verified = {}
        for name, cfg in agents.items():
            aid = cfg["agent_id"]
            try:
                url = f"{self._agent_base}/v1/agents/{aid}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers={"Authorization": f"Bearer {self._agent_key}"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if bridge_mcp_id in data.get("mcps", []):
                                verified[name] = cfg
                            else:
                                logger.info(f"Agent {name} ({aid}) 未挂载桥接 MCP，跳过")
                        else:
                            logger.info(f"Agent {name} ({aid}) 详情获取失败 (HTTP {resp.status})，跳过")
            except Exception as e:
                logger.info(f"Agent {name} ({aid}) MCP 验证失败: {e}，跳过")
        return verified

    async def _get_bridge_mcp_id(self) -> str:
        """从 /v1/mcps 获取 QQ Bridge 的 MCP ID"""
        try:
            url = f"{self._agent_base}/v1/mcps"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {self._agent_key}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        servers = data.get("data", {}).get("servers", {})
                        target_name = self._cfg_str("mcp_server_name", "QQ Bridge")
                        for sid, info in servers.items():
                            if info.get("name") == target_name:
                                logger.info(f"检测到桥接 MCP: {sid} ({target_name})")
                                return sid
        except Exception as e:
            logger.warning(f"获取 MCP 列表失败: {e}")
        return ""

    async def _create_session(self, name: str, agent_cfg: dict, conv: Conversation | None = None) -> str:
        agent_id = agent_cfg["agent_id"]
        work_dirs = self._normalize_work_dirs(agent_cfg)
        model = self._agent_model or self._get_model(conv) or self._default_llm_model
        # 通过 CherryStudio API 查找正确的 provider:model_id 格式
        model = await self._resolve_cherry_model(model)

        # 日志：显示模型名和所属 provider
        llm = self._get_llm_config()
        provider_label = f"#{self._active_llm_idx} {llm.get('name', '?')}"
        logger.info(
            f"Agent: 创建会话 model={model} ({provider_label}), "
            f"agent={agent_id[:20]}..."
        )

        body = {
            "name": name,
            "accessible_paths": work_dirs,
            "model": model,
        }
        url = f"{self._agent_base}/v1/agents/{agent_id}/sessions"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._agent_key}",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    return data.get("id", "")
                text = await resp.text()
                logger.warning(f"创建会话失败: {resp.status} - {text[:200]}")
                return ""

    async def _summarize_and_cleanup(self, agent_name: str, msg_type: str, target_id: str, old_sid: str):
        """用 AI 摘要旧日志，保存 memory，归档旧会话"""
        messages = load_session_log(agent_name, msg_type, target_id)
        if not messages:
            return

        log_text = "\n".join(
            f"[{m.get('time', '?')}] {m.get('sender', m.get('role', '?'))}: {m.get('content', '')[:200]}"
            for m in messages[-100:]
        )

        summary = await self._summarize(log_text)
        if summary:
            save_memory(agent_name, msg_type, target_id, summary)
            logger.info(f"摘要已保存: {agent_name}/{msg_type}_{target_id} ({len(summary)} 字符)")

        conv_dir = _conv_dir(agent_name, msg_type, target_id)
        log_path = conv_dir / "session.json"
        archive_path = conv_dir / f"session_archive_{int(time.time())}.json"
        if log_path.exists():
            shutil.move(str(log_path), str(archive_path))

        set_agent_session_id(agent_name, msg_type, target_id, "")

    async def _summarize(self, log_text: str) -> str:
        llm = self._get_llm_config()
        if not llm.get("url") or not llm.get("key"):
            return ""

        prompt = SUMMARY_PROMPT.format(log=log_text)
        body = {
            "model": llm.get("models", [""])[0],
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                llm["url"],
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {llm['api_key']}",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    if self._is_quota_error(resp.status, text):
                        await self._do_failover("llm")
                    return ""
                data = await resp.json()

        choices = data.get("choices", [])
        if not choices:
            return ""
        msg = choices[0].get("message", {})
        return (msg.get("content", "") or msg.get("reasoning_content", "")).strip()

    @staticmethod
    def _extract_md_images(text: str) -> list[tuple[str, str]]:
        """提取 Markdown 图片语法 ![](url) 和 ![alt](url)，返回 [(url, alt)]"""
        import re
        return re.findall(r'!\[([^\]]*)\]\(([^)]+)\)', text)

    @staticmethod
    def _strip_md_images(text: str) -> str:
        """移除 Markdown 图片语法，保留纯文本"""
        import re
        return re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text).strip()

    # ------------------------------------------------------------------
    # SSE 解析
    # ------------------------------------------------------------------
    # SSE 解析
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sse_blocks(raw: str) -> list[tuple[float, str]]:
        """解析 SSE 响应，返回 [(timestamp, text)] 列表。非流式读取，时间戳为解析时刻。"""
        now = time.monotonic()
        blocks = []
        current_deltas = []
        in_text = False

        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("data: ") and line[6:] != "[DONE]":
                try:
                    obj = json.loads(line[6:])
                    t = obj.get("type", "")
                    if t == "text-start":
                        current_deltas = []
                        in_text = True
                    elif t == "text-end":
                        if current_deltas:
                            blocks.append((now, max(current_deltas, key=len).strip()))
                        current_deltas = []
                        in_text = False
                    elif t == "text-delta":
                        txt = str(obj.get("text", ""))
                        if in_text:
                            current_deltas.append(txt)
                except (json.JSONDecodeError, KeyError):
                    pass

        if current_deltas:
            blocks.append((now, max(current_deltas, key=len).strip()))
        return blocks

    # ------------------------------------------------------------------
    # Chat API (备用)
    # ------------------------------------------------------------------

    async def _call_chat_api(self, conv: Conversation, cfg: dict) -> str | None:
        if not cfg["url"] or not cfg["key"]:
            return None

        recent = list(conv.messages)[-self._max_context :]
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(recent)

        body = {"messages": messages, "stream": False}
        if cfg["model"]:
            body["model"] = cfg["model"]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                cfg["url"],
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg['key']}",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        choices = data.get("choices", [])
        if not choices:
            return None
        msg = choices[0].get("message", {})
        return (msg.get("content", "") or msg.get("reasoning_content", "")).strip()

    # ------------------------------------------------------------------
    # 判断 / 上下文
    # ------------------------------------------------------------------

    def _should_reply(self, msg: QQMessage) -> bool:
        target = msg.group_id if msg.message_type == "group" else msg.sender_id
        if not msg.text.strip():
            return False
        # 过滤机器人自己的消息，防止自激循环
        if self._self_qq and msg.sender_id == self._self_qq:
            return False
        # 群组指令模式：仅响应命令，不参与聊天
        if msg.message_type == "group" and target in self._bot_blacklist:
            return False
        if msg.message_type == "group":
            if self._reply_to_groups and target not in self._reply_to_groups:
                logger.info(f"群 {target} 不在回复白名单，跳过")
                return False
            if self._reply_mode == "mention":
                if not self._is_at_me(msg):
                    logger.info(f"群 {target} 未 @机器人，跳过")
                    return False
        else:
            if self._reply_to_friends and target not in self._reply_to_friends:
                return False
        return True

    def _find_command(self, text: str) -> tuple[str, str] | None:
        """在文本中查找 .xxx 指令。返回 (cmd_name, full_text) 或 None。
        例如: "@bot .log new test" → ("log", ".log new test")"""
        import re
        from OrderSystem import get_command as _get_cmd
        # 匹配：. 后跟字母开头的词（不与前一个非空白字符粘连，排除 URL 等）
        for m in re.finditer(r'(?:^|\s)\.([a-zA-Z]\w*)', text):
            cmd_name = m.group(1).lower()
            if _get_cmd(cmd_name):
                # 提取从 . 开始到行尾的完整指令文本
                full = text[m.start():].strip()
                if full.startswith("." + cmd_name):
                    return (cmd_name, full)
        return None

    def _is_at_me(self, msg: QQMessage) -> bool:
        """检查消息是否 @ 了机器人自己"""
        if not self._self_qq:
            logger.warning("_is_at_me: self_qq 未设置，拒绝所有 @ 触发")
            return False
        message = msg.raw.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq == self._self_qq:
                        return True
                    logger.info(f"群 {msg.group_id} @了 {qq}，不是机器人({self._self_qq})，跳过")
        return False

    def _has_at_others(self, msg: QQMessage) -> bool:
        """检查消息是否 @ 了非机器人的用户"""
        if not self._self_qq:
            return False
        message = msg.raw.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq and qq != self._self_qq:
                        return True
        return False

    def _get_conversation(self, msg: QQMessage) -> Conversation:
        key = f"{msg.message_type}:{msg.group_id if msg.message_type == 'group' else msg.sender_id}"
        if key not in self._conversations:
            conv = Conversation(target_key=key)
            # 恢复重建会话时保留的模型选择
            if key in self._saved_models:
                conv.model = self._saved_models.pop(key)
                logger.info(f"恢复模型选择: {key} → {conv.model}")
            self._conversations[key] = conv
        return self._conversations[key]

    async def _fetch_reply_chain(self, msg: QQMessage, max_depth: int) -> tuple[str, list[str]]:
        """递归获取引用链内容，最多 max_depth 层。返回 (格式化文本, 图片URL列表)。"""
        reply_id = msg.get_reply_id()
        if not reply_id or max_depth <= 0:
            return "", []

        chain_parts = []
        all_image_urls: list[str] = []
        seen_ids = {msg.message_id}  # 防止循环引用
        current_id = reply_id

        for depth in range(max_depth):
            if current_id in seen_ids:
                break
            seen_ids.add(current_id)

            try:
                data = await self._nc.get_msg(current_id)
            except Exception as e:
                logger.warning(f"获取引用消息失败 [{current_id}]: {e}")
                chain_parts.append(f"[引用第{depth + 1}层] (消息无法获取, ID: {current_id})")
                break

            sender = data.get("sender", {})
            sender_name = sender.get("nickname", sender.get("card", ""))
            raw_msg = data.get("raw_message", data.get("message", ""))
            message_segs = data.get("message", [])

            # 提取纯文本（去除 CQ 码）
            text = self._extract_plain_text(raw_msg)

            # 空数据视为获取失败
            if not sender_name and not text:
                logger.warning(f"获取引用消息为空 [{current_id}]")
                chain_parts.append(f"[引用第{depth + 1}层] (消息内容为空, ID: {current_id})")
                break

            # 提取图片 file ID（通过 NapCat get_image 获取）
            img_files = self._extract_image_files(message_segs)
            if img_files:
                logger.info(f"引用链 [{current_id[:12]}...]: 发现 {len(img_files)} 张图片")
            all_image_urls.extend(img_files)

            chain_parts.append(
                f"[引用第{depth + 1}层] {sender_name}: {text}"
            )

            # 检查被引用消息是否也引用了其他消息
            next_reply_id = ""
            if isinstance(message_segs, list):
                for seg in message_segs:
                    if isinstance(seg, dict) and seg.get("type") == "reply":
                        next_reply_id = str(seg.get("data", {}).get("id", ""))
                        break
            current_id = next_reply_id
            if not current_id:
                break

        if not chain_parts:
            return "", []

        return "[引用消息上下文]\n" + "\n".join(reversed(chain_parts)), all_image_urls

    @staticmethod
    def _extract_plain_text(message) -> str:
        """从消息段中提取纯文本"""
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts = []
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                t = seg.get("type", "")
                d = seg.get("data", {})
                if t == "text":
                    parts.append(d.get("text", ""))
                elif t == "image":
                    parts.append("[图片]")
                elif t == "reply":
                    parts.append("[引用]")
                elif t == "at":
                    parts.append(f"@{d.get('qq', '')}")
                elif t == "face":
                    parts.append("[表情]")
                elif t == "file":
                    parts.append(f"[文件: {d.get('name', '')}]")
            return "".join(parts)
        return str(message)

    @staticmethod
    def _extract_image_files(message) -> list[str]:
        """从消息段中提取所有图片的 NapCat file ID"""
        if isinstance(message, str):
            return []
        if isinstance(message, list):
            files = []
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "image":
                    fid = seg.get("data", {}).get("file", "")
                    if fid:
                        files.append(fid)
            return files
        return []

    async def _send_as_doc(self, msg_type: str, target: str, content: str) -> bool:
        """将长文本保存为 .md 文件并通过 QQ 上传"""
        import tempfile, os
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"reply_{ts}.md"
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix="qqreply_", delete=False, encoding="utf-8"
            ) as f:
                f.write(content)
                tmp_path = f.name

            try:
                await self._nc.upload_file(msg_type, target, tmp_path, filename)
                logger.info(f"文档已发送: {filename} ({len(content)} 字符)")
                return True
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"文档发送失败: {e}")
            return False

    async def _process_files(self, file_infos: list[dict]) -> str:
        """下载文件 → MinerU flash-extract → 返回摘要文本"""
        import os
        import tempfile
        import subprocess

        if not self._file_processing or not file_infos:
            return ""

        summaries = []
        for fi in file_infos:
            name = fi.get("name", "未知文件")
            url = fi.get("url", "")
            if not url:
                continue

            # 检查文件大小（如果有的话）
            size_str = fi.get("size", "0")
            try:
                size_mb = int(size_str) / (1024 * 1024)
                if size_mb > self._mineru_max_mb:
                    summaries.append(f"[文件: {name}] (超过大小限制 {size_mb:.1f}MB > {self._mineru_max_mb}MB，未处理)")
                    continue
            except (ValueError, TypeError):
                pass

            logger.info(f"MinerU: 处理文件 {name} ({url[:60]}...)")
            try:
                # 下载文件到临时目录
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            logger.warning(f"MinerU: 下载失败 {resp.status}")
                            summaries.append(f"[文件: {name}] (下载失败)")
                            continue
                        content = await resp.read()

                # 写入临时文件（保留扩展名以便 MinerU 识别）
                suffix = os.path.splitext(name)[1] or ".tmp"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    # 调用 MinerU flash-extract
                    proc = await asyncio.create_subprocess_exec(
                        self._mineru_cmd, "flash-extract", tmp_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=120
                    )

                    if proc.returncode != 0:
                        err = stderr.decode("utf-8", errors="replace")[:200]
                        logger.warning(f"MinerU: 提取失败 rc={proc.returncode}: {err}")
                        summaries.append(f"[文件: {name}] (提取失败)")
                    else:
                        result = stdout.decode("utf-8", errors="replace")
                        max_chars = self._mineru_summary_chars
                        summary = result[:max_chars]
                        if len(result) > max_chars:
                            summary += f"\n\n... (内容截断，共 {len(result)} 字符，显示前 {max_chars})"
                        summaries.append(
                            f"[文件摘要: {name}]\n{summary}\n[文件摘要结束]"
                        )
                        logger.info(f"MinerU: {name} 提取完成 ({len(result)} 字符)")
                finally:
                    os.unlink(tmp_path)

            except asyncio.TimeoutError:
                logger.warning(f"MinerU: 超时 {name}")
                summaries.append(f"[文件: {name}] (处理超时)")
            except Exception as e:
                logger.warning(f"MinerU: 异常 {name}: {e}")
                summaries.append(f"[文件: {name}] (处理异常: {e})")

        if not summaries:
            return ""

        ctx = (
            "[文件处理说明]\n"
            "上述消息中包含文件，已通过 MinerU 自动提取内容摘要。\n"
            "摘要已包含在上下文中。如需要全文详细内容，请使用 MinerU Document Extractor "
            "对原文进行完整提取。\n\n"
        )
        return ctx + "\n\n".join(summaries)

    def _format_incoming(self, msg: QQMessage) -> str:
        if msg.message_type == "group":
            return (
                f"【QQ群 · 群号: {msg.group_id} · 群名: {msg.group_name}】\n"
                f"发送者: {msg.sender_name} (QQ: {msg.sender_id})\n"
                f"回复请用 qq_send_message(message_type='group', target_id='{msg.group_id}', ...)\n"
                f"---\n{msg.text}"
            )
        else:
            return (
                f"【QQ私聊 · {msg.sender_name} (QQ: {msg.sender_id})】\n"
                f"回复请用 qq_send_message(message_type='private', target_id='{msg.sender_id}', ...)\n"
                f"---\n{msg.text}"
            )

    # ------------------------------------------------------------------
    # 指令系统（已迁移至 OrderSystem/ 目录，此处保留内部实现供公开接口调用）
    # ------------------------------------------------------------------

    async def _cmd_rebuild_session(self, msg_type: str, target: str) -> str:
        conv_key = f"{msg_type}:{target}"
        agent_name, agent_cfg = self._get_agent_config(conv_key)
        old_sid = get_agent_session_id(agent_name, msg_type, target)

        await self._summarize_and_cleanup(agent_name, msg_type, target, old_sid or "unknown")

        if old_sid and agent_cfg:
            await self._delete_agent_session(old_sid, agent_cfg)

        # 保存当前模型选择，重建时保留
        conv = self._conversations.get(conv_key)
        if conv and conv.model:
            self._saved_models[conv_key] = conv.model

        self._conversations.pop(conv_key, None)
        delete_session(agent_name, msg_type, target)

        logger.info(f"会话已重建: {agent_name}/{msg_type}_{target}")
        return f"✅ 会话已重建 (当前 Agent: {agent_name})。下次消息将开启全新会话。"

    async def _cmd_switch_agent(self, msg_type: str, target: str, name: str) -> str:
        name = name.strip()
        if not name:
            available = "、".join(self._agents.keys())
            return f"当前可用的 Agent: {available}\n\n用法: .order 切换 <名称>"

        if name not in self._agents:
            available = "、".join(self._agents.keys())
            return f"未找到 Agent '{name}'。可用: {available}"

        # 1. 清理当前会话
        conv_key = f"{msg_type}:{target}"
        old_name, old_cfg = self._get_agent_config(conv_key)
        old_sid = get_agent_session_id(old_name, msg_type, target)
        if old_sid:
            await self._summarize_and_cleanup(old_name, msg_type, target, old_sid)
            if old_cfg:
                await self._delete_agent_session(old_sid, old_cfg)
            delete_session(old_name, msg_type, target)

        # 2. 切换到新 Agent
        conv = self._conversations.get(conv_key)
        if conv and conv.model:
            self._saved_models[conv_key] = conv.model
        self._conversations.pop(conv_key, None)
        set_conversation_agent(msg_type, target, name)

        logger.info(f"Agent 切换: {msg_type}_{target} → {name}")
        return f"✅ 已切换到 Agent「{name}」。下次消息将使用新 Agent。"

    def _cmd_list_agents(self) -> str:
        lines = ["可用 Agent:"]
        for i, (name, cfg) in enumerate(self._agents.items(), 1):
            marker = " ← 当前默认" if name == self._default_agent else ""
            lines.append(f"  {i}. {name}{marker}")
        lines.append("\n切换指令: .order 切换 <名称>")
        return "\n".join(lines)

    async def _cmd_status(self, msg_type: str, target: str) -> str:
        conv_key = f"{msg_type}:{target}"
        agent_name, _ = self._get_agent_config(conv_key)
        conv = self._conversations.get(conv_key)
        session_name = make_session_name(msg_type, target)
        sid = get_agent_session_id(agent_name, msg_type, target)
        stale = is_stale(agent_name, msg_type, target)
        messages = load_session_log(agent_name, msg_type, target)
        memory = load_memory(agent_name, msg_type, target)

        lines = [
            f"📊 会话状态: {session_name}",
            f"当前 Agent: {agent_name or '无'}",
            f"当前模型: {conv.model or self._default_llm_model or '默认'}",
            f"消息数: {len(messages)}",
            f"状态: {'已过期 (>3天)' if stale else '活跃'}",
            f"记忆摘要: {'有 (' + str(len(memory)) + '字)' if memory else '无'}",
            f"Agent会话: {sid[:24] + '...' if sid else '无'}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Master 命令：模型管理
    # ------------------------------------------------------------------

    def _get_model(self, conv: Conversation | None = None) -> str:
        """获取当前会话使用的模型 ID（自动转换格式）"""
        model = conv.model if (conv and conv.model) else self._default_llm_model
        llm = self._get_llm_config()
        # CherryStudio 本地端点需要 provider:model_id 格式
        if "127.0.0.1" in llm.get("url", "") and ":" not in model:
            return f"deepseek:{model}"
        return model

    # ------------------------------------------------------------------
    # LLM / Vision 配置获取 & 主备切换
    # ------------------------------------------------------------------

    QUOTA_KEYWORDS = [
        "quota", "insufficient", "exceeded", "limit", "billing",
        "余额不足", "额度不足", "配额", "欠费", "超出限制",
        "rate limit", "too many requests",
    ]

    @staticmethod
    def _is_quota_error(status: int, body_text: str) -> bool:
        """检测是否为额度不足错误"""
        if status in (429, 402):
            return True
        if status == 403:
            text_lower = body_text.lower()
            return any(kw in text_lower for kw in ["quota", "billing", "insufficient", "exceeded", "limit", "余额", "额度", "配额", "欠费"])
        text_lower = body_text.lower()
        return any(kw in text_lower for kw in AutoReply.QUOTA_KEYWORDS)

    async def _do_failover(self, source: str):
        """执行主备切换：索引递增到下一个 provider"""
        if source == "vision":
            if self._active_vision_idx + 1 < len(self._vision_providers):
                self._active_vision_idx += 1
                name = self._vision_providers[self._active_vision_idx].get("name", "?")
                logger.warning(f"Vision 已切换到 #{self._active_vision_idx} ({name})")
        elif source == "llm":
            if self._active_llm_idx + 1 < len(self._llm_providers):
                self._active_llm_idx += 1
                name = self._llm_providers[self._active_llm_idx].get("name", "?")
                logger.warning(f"LLM 已切换到 #{self._active_llm_idx} ({name})")

        if not self._failover_notified and self._admin_qq:
            self._failover_notified = True
            try:
                await self._nc.send_msg("private", self._admin_qq, "⚠️ 主KEY已经离线，已自动切换到备用KEY。")
            except Exception:
                pass

    def _get_vision_config(self) -> dict:
        """获取当前 Vision provider 配置"""
        if self._vision_providers:
            p = self._vision_providers[self._active_vision_idx]
            return {
                "url": p.get("api_url", ""),
                "key": p.get("api_key", ""),
                "model": p.get("models", [""])[0] if p.get("models") else "",
                "api_format": p.get("api_format", "openai"),
                "name": p.get("name", ""),
                "prompt": self._vision_prompt,
            }
        return {"url": "", "key": "", "model": "", "prompt": self._vision_prompt, "api_format": "openai", "name": ""}

    def _get_llm_config(self) -> dict:
        """获取当前 LLM provider 配置"""
        if self._llm_providers:
            p = self._llm_providers[self._active_llm_idx]
            return {
                "url": p.get("api_url", ""),
                "key": p.get("api_key", ""),
                "model": p.get("models", [""])[0] if p.get("models") else "",
                "api_format": p.get("api_format", "openai"),
                "name": p.get("name", ""),
                "models": p.get("models", []),
            }
        return {"url": "", "key": "", "model": "", "api_format": "openai", "name": "", "models": []}

    def _cmd_master_llm_list(self) -> str:
        if not self._llm_providers:
            return "未配置 LLM provider。"
        lines = ["可用 LLM 模型 (按 provider 分组):"]
        for pi, p in enumerate(self._llm_providers):
            marker = " ← 活跃" if pi == self._active_llm_idx else ""
            models = p.get("models", [])
            lines.append(f"\n  [{pi}] {p.get('name', '?')}{marker}")
            for mi, m in enumerate(models, 1):
                dm = " ← 默认" if m == self._default_llm_model else ""
                lines.append(f"      {mi}. {m}{dm}")
        lines.append("\n切换指令: .model change <模型名>")
        return "\n".join(lines)

    async def _cmd_master_llm_change(self, msg_type: str, target: str, name: str) -> str:
        name = name.strip()
        if not name:
            return "用法: .model change <模型名>\n用 .model list 查看可用模型"

        # 在所有 provider 中查找模型
        all_names = []
        for p in self._llm_providers:
            for m in p.get("models", []):
                all_names.append(m)
                if m == name:
                    conv_key = f"{msg_type}:{target}"
                    self._saved_models[conv_key] = name
                    rebuild_msg = await self._cmd_rebuild_session(msg_type, target)
                    logger.info(f"模型切换并重建: {conv_key} → {name}")
                    return f"✅ 已切换为「{name}」并自动重建会话。\n{rebuild_msg}"

        available = "、".join(all_names)
        return f"未找到模型 '{name}'。可用: {available}"

    def _cmd_master_llm_status(self) -> str:
        llm = self._get_llm_config()
        vis = self._get_vision_config()
        lines = [
            "📊 状态:",
            f"  LLM:   #{self._active_llm_idx} {llm.get('name', '?')} ({llm.get('url', '')[:40]}...)",
            f"  Vision:#{self._active_vision_idx} {vis.get('name', '?')} ({vis.get('url', '')[:40]}...)",
            f"  Vision模型: {vis.get('model', '')}",
            f"  通知状态: {'已通知' if self._failover_notified else '未通知'}",
        ]
        return "\n".join(lines)

    def _cmd_master_llm_reset(self) -> str:
        old_llm = self._active_llm_idx
        old_vis = self._active_vision_idx
        self._active_llm_idx = 0
        self._active_vision_idx = 0
        self._failover_notified = False
        parts = []
        if old_llm != 0:
            parts.append(f"LLM 已从 #{old_llm} 切回 #0")
        if old_vis != 0:
            parts.append(f"Vision 已从 #{old_vis} 切回 #0")
        if parts:
            return "✅ " + "，".join(parts) + "。"
        return "当前已在主端点 (#0)，无需重置。"

    # ------------------------------------------------------------------
    # Admin 命令
    # ------------------------------------------------------------------

    def _check_admin(self, sender_id: str) -> bool:
        return self._admin_qq and sender_id == self._admin_qq

    async def _cmd_admin(self, sender_id: str, args: str) -> str:
        if not self._check_admin(sender_id):
            return "⛔ 权限不足。Admin 命令仅限管理员使用。"

        if not args:
            return (
                "Admin 子命令:\n"
                "  AllResetAgent   - 删除 API 会话 + 清空本地记录\n"
                "  OnlyResetAgent  - 仅删除 API 会话，保留本地记录"
            )

        sub_parts = args.split(None, 1)
        sub_action = sub_parts[0]

        if sub_action == "AllResetAgent":
            return await self._cmd_admin_all_reset_agent()
        elif sub_action == "OnlyResetAgent":
            return await self._cmd_admin_only_reset_agent()

        return f"未知 Admin 子命令: {sub_action}"

    async def _cmd_admin_all_reset_agent(self) -> str:
        """删除所有通过 API 创建的 CherryStudio Agent 会话，保留本地记录"""
        import shutil
        from conversation_store import BASE_DIR, _load_mapping, _save_mapping

        mapping = _load_mapping()
        if not mapping:
            return "没有找到任何 API 会话记录。"

        deleted = 0
        failed = 0
        agent_sessions: dict[str, set[str]] = {}  # agent_name -> {session_ids}

        # 1. 收集所有要删除的会话
        for conv_key, agent_name in mapping.items():
            parts = conv_key.split("_", 1)
            if len(parts) != 2:
                continue
            msg_type, target_id = parts[0], parts[1]
            sid = get_agent_session_id(agent_name, msg_type, target_id)
            if sid:
                agent_sessions.setdefault(agent_name, set()).add(sid)

        # 2. 删除 CherryStudio Agent 会话
        for agent_name, sids in agent_sessions.items():
            agent_cfg = self._agents.get(agent_name, {})
            for sid in sids:
                try:
                    await self._delete_agent_session(sid, agent_cfg)
                    deleted += 1
                except Exception:
                    failed += 1

        # 3. 删除本地 QQConversationRecord 下所有 Agent 目录
        if BASE_DIR.exists():
            for d in BASE_DIR.iterdir():
                if d.is_dir() and d.name != "mapping.json":
                    try:
                        shutil.rmtree(str(d))
                        logger.info(f"已删除本地目录: {d}")
                    except Exception as e:
                        logger.warning(f"删除目录失败: {d} - {e}")

        # 4. 清空 mapping
        _save_mapping({})

        # 5. 清空内存中的 conversation
        self._conversations.clear()

        return f"✅ AllResetAgent 完成\n  删除 Agent 会话: {deleted} 个\n  失败: {failed} 个\n  本地记录已清空，mapping 已重置。"

    async def _cmd_admin_only_reset_agent(self) -> str:
        """仅删除 API 创建的 CherryStudio Agent 会话，保留本地记录和 mapping"""
        from conversation_store import _load_mapping

        mapping = _load_mapping()
        if not mapping:
            return "没有找到任何 API 会话记录。"

        deleted = 0
        failed = 0

        for conv_key, agent_name in mapping.items():
            parts = conv_key.split("_", 1)
            if len(parts) != 2:
                continue
            msg_type, target_id = parts[0], parts[1]
            sid = get_agent_session_id(agent_name, msg_type, target_id)
            if not sid:
                continue

            agent_cfg = self._agents.get(agent_name, {})
            try:
                await self._delete_agent_session(sid, agent_cfg)
                set_agent_session_id(agent_name, msg_type, target_id, "")
                deleted += 1
            except Exception:
                failed += 1

        # 清空内存中的 agent_session_id
        for conv in self._conversations.values():
            conv.agent_session_id = ""

        return f"✅ OnlyResetAgent 完成\n  删除 API 会话: {deleted} 个\n  失败: {failed} 个\n  本地记录和 mapping 已保留。"

    def _cmd_order_white(self, msg_type: str, target: str) -> str:
        """切换当前群组的 order 白名单状态。仅群聊有效。"""
        if msg_type != "group":
            return "orderwhite 仅在群聊中有效。私聊中 .order 指令无需 @。"
        if target in self._order_whitelist:
            self._order_whitelist.discard(target)
            msg = "已关闭本群的 order 免@模式。之后 .order 指令需要 @机器人。"
        else:
            self._order_whitelist.add(target)
            msg = "已开启本群的 order 免@模式。之后 .order 指令无需 @机器人即可生效。"
        save_list("order_whitelist.json", list(self._order_whitelist))
        return msg

    # ------------------------------------------------------------------
    # 公开命令接口（供 OrderSystem 调用）
    # ------------------------------------------------------------------

    async def order_switch_agent(self, msg_type: str, target: str, name: str) -> str:
        return await self._cmd_switch_agent(msg_type, target, name)

    def order_list_agents(self) -> str:
        return self._cmd_list_agents()

    async def order_rebuild_session(self, msg_type: str, target: str) -> str:
        return await self._cmd_rebuild_session(msg_type, target)

    async def order_status(self, msg_type: str, target: str) -> str:
        return await self._cmd_status(msg_type, target)

    def order_orderwhite(self, msg_type: str, target: str) -> str:
        return self._cmd_order_white(msg_type, target)

    def model_list(self) -> str:
        return self._cmd_master_llm_list()

    async def model_change(self, msg_type: str, target: str, name: str) -> str:
        return await self._cmd_master_llm_change(msg_type, target, name)

    def model_status(self) -> str:
        return self._cmd_master_llm_status()

    def master_llm_reset(self) -> str:
        return self._cmd_master_llm_reset()

    async def master_all_reset_agent(self, sender_id: str) -> str:
        if not self._check_admin(sender_id):
            return "⛔ 权限不足。此命令仅限管理员使用。"
        return await self._cmd_admin_all_reset_agent()

    async def master_only_reset_agent(self, sender_id: str) -> str:
        if not self._check_admin(sender_id):
            return "⛔ 权限不足。此命令仅限管理员使用。"
        return await self._cmd_admin_only_reset_agent()

    def build_greeting(self) -> str:
        """构建欢迎消息：自定义问候 + 版本 + 特殊提醒 + 命令列表"""
        import json
        from pathlib import Path
        from OrderSystem import list_commands as _list_cmds

        custom = self._load_bot_setting("custom_greeting", "").strip()

        VERSION = "Cherry Agent Bot！by ARK-Magellan Ver 1.0.0"
        parts = []
        if custom:
            parts.append(custom)
        parts.append(VERSION)

        # 收集各模块的 reminder
        reminders = [c.reminder for c in _list_cmds() if c.reminder]
        if reminders:
            parts.append("---")
            parts.extend(reminders)

        # 一级命令列表
        cmds = _list_cmds()
        parts.append("---")
        parts.append("可用命令:")
        for cmd in cmds:
            parts.append(f"  .{cmd.name:<10} - {cmd.description}")

        return "\n".join(parts)

    def bot_set(self, msg_type: str, target: str, enabled: bool) -> str:
        """开关群组的指令模式。enabled=True=恢复正常, False=仅响应指令"""
        if msg_type != "group":
            return ".bot 仅在群聊中有效。"
        if enabled:
            self._bot_blacklist.discard(target)
            msg = self._load_bot_setting("bot_on_message", "已恢复正常回复。")
        else:
            self._bot_blacklist.add(target)
            msg = self._load_bot_setting("bot_off_message", "已开启指令模式。之后仅响应 .xxx 指令，不参与聊天。")
        save_list("bot_blacklist.json", list(self._bot_blacklist))
        return msg

    @staticmethod
    def _load_bot_setting(key: str, default: str = "") -> str:
        """读取 BotSettingConfig.json，不存在则回退到 config.json"""
        base = Path(__file__).parent.parent  # Built_in/ → 项目根目录
        setting_path = base / "Configuration" / "BotSettingConfig.json"
        config_path = base / "Configuration" / "config.json"
        try:
            if setting_path.exists():
                sc = json.loads(setting_path.read_text(encoding="utf-8"))
                for category in sc.values():
                    if isinstance(category, dict) and key in category:
                        return category[key]
        except Exception:
            pass
        try:
            if config_path.exists():
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                if key in cfg:
                    return cfg[key]
        except Exception:
            pass
        return default

    @staticmethod
    def _cfg_str(key: str, default: str = "") -> str:
        """读取 config.json 中的字符串配置"""
        base = Path(__file__).parent.parent
        config_path = base / "Configuration" / "config.json"
        try:
            if config_path.exists():
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                return cfg.get(key, default)
        except Exception:
            pass
        return default

    # ------------------------------------------------------------------
    # 日志系统 (.log / .ob)
    # ------------------------------------------------------------------

    def log_new(self, group_id: str, name: str) -> str:
        if not name or not name.strip():
            return "不能空日志名哦，请正确设置~~~"
        name = name.strip()
        # 清理文件名中的非法字符
        safe_name = name.translate(str.maketrans({
            '<': '(', '>': ')', ':': '-', '"': "'", '/': '-',
            '\\': '-', '|': '-', '?': '', '*': '', '@': 'at',
        }))
        if group_id in self._active_logs:
            return f"本群已有活跃日志「{self._active_log_display.get(group_id, '')}」，请先 .log end。"
        gd = _log_group_dir(group_id)
        gd.mkdir(parents=True, exist_ok=True)
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        folder_name = f"{group_id}_{safe_name}_{suffix}"
        log_dir = gd / folder_name
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "Photo").mkdir(exist_ok=True)
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        (log_dir / "log.txt").write_text(f"{today}\n---\n", encoding="utf-8")
        (log_dir / "state.json").write_text(
            json.dumps({"over": False, "name": name, "created_at": datetime.now().isoformat()}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._active_logs[group_id] = folder_name
        self._active_log_display[group_id] = name
        self._log_paused.discard(group_id)
        return f"已创建日志「{name}」并开始记录。"

    def log_resume(self, group_id: str) -> str:
        if group_id in self._active_logs:
            self._log_paused.discard(group_id)
            return "已继续记录。"
        # 检查是否有未 over 的日志
        gd = _log_group_dir(group_id)
        if gd.exists():
            for d in sorted(gd.iterdir(), reverse=True):
                if d.is_dir():
                    state = _log_read_state(d)
                    if state and not state.get("over", True):
                        self._active_logs[group_id] = d.name
                        self._active_log_display[group_id] = state.get("name", d.name)
                        self._log_paused.discard(group_id)
                        return f"已恢复日志「{state.get('name', d.name)}」。"
        return "本群没有活跃日志，请先 .log new <日志名>。"

    def log_pause(self, group_id: str) -> str:
        if group_id not in self._active_logs:
            return "本群没有活跃日志。"
        self._log_paused.add(group_id)
        return "已暂停记录。"

    async def log_end(self, group_id: str, nc) -> str:
        if group_id not in self._active_logs:
            return "本群没有活跃日志。"
        folder_name = self._active_logs.pop(group_id)
        self._log_paused.discard(group_id)
        self._active_log_display.pop(group_id, None)
        log_dir = _log_group_dir(group_id) / folder_name
        zip_path = log_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in log_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(log_dir))
        await nc.upload_file("group", group_id, str(zip_path), f"{folder_name}.zip")
        zip_path.unlink(missing_ok=True)
        _log_mark_over(log_dir)
        return f"日志「{folder_name}」已完成并发送。"

    def log_list(self, group_id: str) -> str:
        gd = _log_group_dir(group_id)
        if not gd.exists():
            return "本群暂无日志。"
        lines = ["本群日志:"]
        for d in sorted(gd.iterdir()):
            if d.is_dir() and d.name != "config.json":
                n = d.name[len(group_id) + 1:].rsplit("_", 1)[0]
                state = _log_read_state(d)
                marker = " [Over]" if (state and state.get("over")) else " [活跃]"
                lines.append(f"  - {n}{marker}")
        return "\n".join(lines)

    async def log_get(self, group_id: str, name: str, nc) -> str:
        if not name.strip():
            return "用法: .log get <日志名>"
        log_dir = _find_log_dir(group_id, name.strip())
        if not log_dir:
            return f"未找到日志「{name.strip()}」。"
        zip_path = log_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in log_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(log_dir))
        await nc.upload_file("group", group_id, str(zip_path), f"{log_dir.name}.zip")
        zip_path.unlink(missing_ok=True)
        _log_mark_over(log_dir)
        return f"日志「{name.strip()}」已发送。"

    def log_delete(self, group_id: str, name: str) -> str:
        if not name.strip():
            return "用法: .log del <日志名>"
        log_dir = _find_log_dir(group_id, name.strip())
        if not log_dir:
            return f"未找到日志「{name.strip()}」。"
        shutil.rmtree(str(log_dir))
        return f"日志「{name.strip()}」已删除。"

    def log_write(self, group_id: str, msg: QQMessage):
        """记录一条消息到活跃日志（内部调用）"""
        folder = self._active_logs.get(group_id)
        if not folder:
            return
        log_dir = _log_group_dir(group_id) / folder

        # 处理文本：替换图片占位符
        text = msg.text
        if msg.image_files:
            photo_dir = log_dir / "Photo"
            existing = len(list(photo_dir.glob("*"))) if photo_dir.exists() else 0
            for i in range(len(msg.image_files)):
                text = text.replace("[图片]", f"[图片{existing + i + 1}]", 1)

        line = f"{msg.sender_name}: {text}\n"
        log_path = log_dir / "log.txt"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)

    def ob_join(self, group_id: str, user_id: str) -> str:
        bl = self._log_blacklist_load()
        bl.setdefault(group_id, [])
        if user_id in bl[group_id]:
            return "已经加入旁观了哦。"
        bl[group_id].append(user_id)
        self._log_blacklist_save(bl)
        return "已经加入旁观了哦。"

    def ob_exit(self, group_id: str, user_id: str) -> str:
        bl = self._log_blacklist_load()
        if group_id in bl and user_id in bl[group_id]:
            bl[group_id].remove(user_id)
            self._log_blacklist_save(bl)
            return "已退出旁观模式。"
        return "你不在旁观列表中。"

    def ob_list(self, group_id: str) -> str:
        bl = self._log_blacklist_load()
        obs = bl.get(group_id, [])
        if not obs:
            return "本群暂无旁观者。"
        return "本群旁观者:\n" + "\n".join(f"  - {uid}" for uid in obs)

    def ob_clear(self, group_id: str) -> str:
        bl = self._log_blacklist_load()
        count = len(bl.pop(group_id, []))
        self._log_blacklist_save(bl)
        return f"已清除 {count} 位旁观者。"

    def ob_toggle(self, group_id: str, enabled: bool) -> str:
        if enabled:
            self._ob_enabled.add(group_id)
            return "已开启本群旁观模式。旁观者的发言不计入日志。"
        else:
            self._ob_enabled.discard(group_id)
            return "已关闭本群旁观模式。"

    def extract_at_targets(self, msg: QQMessage) -> list[str]:
        """提取消息中 @ 的 QQ 号列表"""
        targets = []
        message = msg.raw.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq:
                        targets.append(qq)
        return targets

    def check_admin(self, sender_id: str) -> bool:
        return self._check_admin(sender_id)

    @property
    def admin_qq(self) -> str:
        return self._admin_qq

    async def dismiss_leave(self, nc, group_id: str, input_id: str) -> str:
        """退群。先发告别消息，后退群，清理本地数据"""
        if not input_id.strip():
            return "用法: .dismiss <群号后四位>"
        gid = str(group_id)
        if input_id.strip() == gid or (len(gid) >= 4 and gid[-4:] == input_id.strip()):
            farewell = self._load_bot_setting("dismiss_message", "").strip()
            if farewell:
                await nc.send_msg("group", gid, farewell)
            await nc.leave_group(gid)
            self._dismiss_cleanup(gid)
            return None
        return "群号不匹配，操作取消。"

    def _dismiss_cleanup(self, group_id: str):
        """清理退群群组的所有本地数据"""
        import json, shutil
        from pathlib import Path
        base = Path(__file__).parent.parent
        gid = str(group_id)
        key = f"group_{gid}"

        # 1. QQConversationRecord: mapping + 会话目录
        qqcr = base / "QQConversationRecord"
        mapping_path = qqcr / "mapping.json"
        if mapping_path.exists():
            try:
                mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
                if key in mapping:
                    del mapping[key]
                    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        old_dir = qqcr / key
        if old_dir.exists():
            shutil.rmtree(str(old_dir), ignore_errors=True)
        for agent_dir in qqcr.iterdir():
            if agent_dir.is_dir() and agent_dir.name != "__pycache__":
                gdir = agent_dir / key
                if gdir.exists():
                    shutil.rmtree(str(gdir), ignore_errors=True)

        # 2. Temp 持久化文件
        temp_dir = base / "Temp"
        for fname in ("bot_blacklist.json", "order_whitelist.json", "log_blacklist.json"):
            path = temp_dir / fname
            try:
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        data.pop(gid, None)
                    elif isinstance(data, list) and gid in data:
                        data.remove(gid)
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        # 3. PlayerLog
        pl_dir = base / "PlayerLog" / key
        if pl_dir.exists():
            shutil.rmtree(str(pl_dir), ignore_errors=True)

        # 4. 内存状态
        self._bot_blacklist.discard(gid)
        self._order_whitelist.discard(gid)
        self._ob_enabled.discard(gid)
        self._active_logs.pop(gid, None)
        self._active_log_display.pop(gid, None)
        self._log_paused.discard(gid)
        for conv_key in list(self._conversations.keys()):
            if conv_key.endswith(f":{gid}"):
                del self._conversations[conv_key]

        logger.info(f"已清理群 {gid} 的所有本地数据")

    # ------------------------------------------------------------------
    # 日志内部辅助
    # ------------------------------------------------------------------

    def _log_blacklist_load(self) -> dict:
        from Temp.store import STORE_DIR
        path = STORE_DIR / "log_blacklist.json"
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _log_blacklist_save(self, data: dict):
        from Temp.store import STORE_DIR
        path = STORE_DIR / "log_blacklist.json"
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _is_log_blacklisted(self, group_id: str, user_id: str) -> bool:
        if group_id not in self._ob_enabled:
            return False
        bl = self._log_blacklist_load()
        return user_id in bl.get(group_id, [])

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _delete_agent_session(self, sid: str, agent_cfg: dict):
        agent_id = agent_cfg["agent_id"]
        try:
            url = f"{self._agent_base}/v1/agents/{agent_id}/sessions/{sid}"
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    url,
                    headers={"Authorization": f"Bearer {self._agent_key}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 204):
                        logger.info(f"Agent 会话已删除: {sid[:24]}...")
                    else:
                        logger.warning(f"删除 Agent 会话失败 {resp.status}: {sid[:24]}...")
        except Exception as e:
            logger.warning(f"删除 Agent 会话异常: {e}")

    @staticmethod
    def _parse_vision_reply(data: dict, api_format: str) -> str:
        """解析 Vision API 响应（OpenAI / Anthropic）"""
        if api_format == "anthropic":
            content = data.get("content", [])
            if isinstance(content, list) and content:
                return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            return ""
        else:
            choice = data.get("choices", [{}])[0]
            return choice.get("message", {}).get("content", "").strip()

    def _load_workspace_context(self, work_dirs: list[str]) -> str:
        """模拟 UI 启动会话时的上下文注入：SOUL.md + USER.md + FACT.md。
        work_dirs[0] 是 Agent 独立路径（存放 SOUL/USER/memory），其余是共享数据库。"""
        parts = []
        if not work_dirs:
            return ""

        wd = Path(work_dirs[0])
        for filename in ["SOUL.md", "USER.md"]:
            path = wd / filename
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                    parts.append(f"<{filename}>\n{content}\n</{filename}>")
                except Exception:
                    pass

        fact_path = wd / "memory" / "FACT.md"
        if fact_path.exists():
            try:
                content = fact_path.read_text(encoding="utf-8").strip()
                parts.append(f"<FACT.md>\n{content}\n</FACT.md>")
            except Exception:
                pass

        return "\n\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # 图片识别 (多模态模型)
    # ------------------------------------------------------------------

    async def _recognize_images(self, image_files: list[str], user_text: str = "") -> str:
        """通过 NapCat get_image 读取图片 → 发送多模态模型识别"""
        import base64

        vis = self._get_vision_config()
        if not vis["url"] or not vis["key"]:
            logger.warning("Vision 模型未配置")
            return ""

        base_prompt = self._vision_prompt or "Describe this image."
        if user_text.strip():
            full_prompt = f"{base_prompt}\n\n用户对这张图片的提问或说明：{user_text.strip()}"
        else:
            full_prompt = base_prompt

        descriptions = []
        for idx, file_id in enumerate(image_files):
            try:
                # 1. 通过 NapCat get_image 获取本地路径
                logger.info(f"Vision: 获取图片 {idx + 1}/{len(image_files)} (file={file_id[:30]}...)")
                local_path = await self._nc.get_image_path(file_id)
                if not local_path or not Path(local_path).exists():
                    logger.warning(f"Vision: get_image 失败或文件不存在: {local_path}")
                    continue

                img_data = Path(local_path).read_bytes()
                # 根据扩展名推断 content_type
                ext = Path(local_path).suffix.lower()
                mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
                content_type = mime_map.get(ext, "image/jpeg")
                b64 = base64.b64encode(img_data).decode("ascii")
                logger.info(f"Vision: 读取图片 {idx + 1} ({len(img_data)} bytes, {content_type})")

                # 2. 构建请求（OpenAI / Anthropic 格式）
                async with aiohttp.ClientSession() as session:
                    api_format = vis.get("api_format", "openai")
                    if api_format == "anthropic":
                        body = {
                            "model": vis["model"],
                            "max_tokens": 500,
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": b64}},
                                    {"type": "text", "text": full_prompt},
                                ],
                            }],
                        }
                        headers = {
                            "Content-Type": "application/json",
                            "x-api-key": vis["key"],
                            "anthropic-version": "2023-06-01",
                        }
                    else:
                        body = {
                            "model": vis["model"],
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": full_prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}},
                                ],
                            }],
                            "max_tokens": 500,
                        }
                        headers = {
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {vis['key']}",
                        }

                    async with session.post(
                        vis["url"],
                        json=body,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            logger.warning(f"Vision: 识别失败 {resp.status}: {text[:150]}")
                            if self._is_quota_error(resp.status, text):
                                old_idx = self._active_vision_idx
                                await self._do_failover("vision")
                                if self._active_vision_idx != old_idx:
                                    vis = self._get_vision_config()
                                    logger.info("Vision: 已切换到备用，重试...")
                                    # 重建 body（备用可能是不同格式）
                                    api_format2 = vis.get("api_format", "openai")
                                    if api_format2 == "anthropic":
                                        body2 = {
                                            "model": vis["model"],
                                            "max_tokens": 500,
                                            "messages": [{
                                                "role": "user",
                                                "content": [
                                                    {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": b64}},
                                                    {"type": "text", "text": full_prompt},
                                                ],
                                            }],
                                        }
                                        headers2 = {
                                            "Content-Type": "application/json",
                                            "x-api-key": vis["key"],
                                            "anthropic-version": "2023-06-01",
                                        }
                                    else:
                                        body2 = body
                                        headers2 = {"Content-Type": "application/json", "Authorization": f"Bearer {vis['key']}"}
                                    async with session.post(
                                        vis["url"],
                                        json=body2,
                                        headers=headers2,
                                        timeout=aiohttp.ClientTimeout(total=30),
                                    ) as retry_resp:
                                        if retry_resp.status == 200:
                                            data = await retry_resp.json()
                                            desc = self._parse_vision_reply(data, api_format2)
                                            if desc:
                                                descriptions.append(f"[图片{idx + 1}]: {desc}")
                                                logger.info(f"Vision: 备用识别完成 ({len(desc)} 字符)")
                                            continue
                            continue
                        data = await resp.json()
                        desc = self._parse_vision_reply(data, api_format)
                        if desc:
                            descriptions.append(f"[图片{idx + 1}]: {desc}")
                            logger.info(f"Vision: 识别完成 ({len(desc)} 字符)")

            except asyncio.TimeoutError:
                logger.warning(f"Vision: 超时 file={file_id[:30]}...")
                await self._do_failover("vision")
            except Exception as e:
                logger.warning(f"Vision: 异常 {e}")
                if self._active_vision_idx == 0:
                    await self._do_failover("vision")

        return "\n".join(descriptions) if descriptions else ""
