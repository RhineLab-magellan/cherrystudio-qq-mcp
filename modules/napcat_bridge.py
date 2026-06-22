"""
NapCat 互联桥模块 (NapCatBridge)

职责:
1. 建立 WebSocket 连接到 NapCatQQ
2. 接收原始消息并发送到 MessageBus
3. 从 MessageBus 接收待发送消息并转发到 NapCat
4. 管理连接状态和重连逻辑
"""

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Callable, Any

from collections import deque

import websockets
import aiohttp

from protocols.messages import (
    RawMessage,
    OutgoingMessage,
    MessageType,
    MessageSource,
)
from protocols.error_codes import ErrorCode, BridgeError

logger = logging.getLogger(__name__)


class MessageBuffer:
    """
    消息缓冲区

    缓存最近的消息，用于 qq_get_recent_messages 工具。
    支持全局缓冲和按目标 (群/私聊) 的独立缓冲。
    """

    def __init__(self, max_size: int = 200):
        self.max_size = max_size
        self._global: deque[RawMessage] = deque(maxlen=max_size)
        self._buffers: dict[str, deque[RawMessage]] = {}
        self.lock = asyncio.Lock()

    async def add_message(self, msg: RawMessage):
        """添加消息到缓冲区 (全局 + 按目标)"""
        async with self.lock:
            self._global.append(msg)
            # 按目标键分桶: "group:群号" 或 "private:QQ号"
            key = self._target_key(msg)
            if key not in self._buffers:
                self._buffers[key] = deque(maxlen=self.max_size)
            self._buffers[key].append(msg)

    async def get_recent_messages(
        self, target: str = "", count: int = 10
    ) -> list[RawMessage]:
        """
        获取最近消息

        Args:
            target: 目标 ID (空则返回全局消息，指定则返回该目标的消息)
            count: 消息数量
        """
        async with self.lock:
            if target:
                # 尝试按目标键查找
                # 需要先确定是群还是私聊 — 两种都试
                group_key = f"group:{target}"
                private_key = f"private:{target}"
                if group_key in self._buffers:
                    return list(self._buffers[group_key])[-count:]
                elif private_key in self._buffers:
                    return list(self._buffers[private_key])[-count:]
                else:
                    # 回退: 从全局缓冲中过滤
                    return [m for m in self._global if m.target_id == target][-count:]
            else:
                return list(self._global)[-count:]

    def has_target(self, target_id: str) -> bool:
        """
        检查目标是否有消息记录 (同步方法，用于快速活跃验证)

        Args:
            target_id: 目标 ID (群号或QQ号)

        Returns:
            True 表示该目标在缓冲区中有消息
        """
        group_key = f"group:{target_id}"
        private_key = f"private:{target_id}"
        return group_key in self._buffers or private_key in self._buffers

    def get_all_targets(self) -> list[str]:
        """
        获取所有有消息记录的目标列表

        Returns:
            目标键列表，格式如 ["group:123456", "private:789012"]
        """
        return list(self._buffers.keys())

    async def clear(self):
        """清空缓冲区"""
        async with self.lock:
            self._global.clear()
            self._buffers.clear()

    @staticmethod
    def _target_key(msg: RawMessage) -> str:
        """生成消息的目标键"""
        source = msg.source.value  # "group" 或 "private"
        return f"{source}:{msg.target_id}"


class NapCatBridge:
    """
    NapCat 互联桥

    负责与 NapCatQQ 的双向通信：
    - 接收: WebSocket 事件 -> RawMessage -> MessageBus
    - 发送: OutgoingMessage <- MessageBus -> OneBot API
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3001,
        access_token: str = "",
        message_bus: Any = None,
    ):
        """
        初始化 NapCat 互联桥

        Args:
            host: NapCat WebSocket 主机
            port: NapCat WebSocket 端口
            access_token: 访问令牌
            message_bus: 消息互联桥实例 (用于传递消息)
        """
        self.host = host
        self.port = port
        self.access_token = access_token
        self.message_bus = message_bus

        # 消息缓冲区
        buffer_size = 200  # 默认值，可从配置中读取
        self.message_buffer = MessageBuffer(max_size=buffer_size)

        # WebSocket URL
        self.ws_url = f"ws://{host}:{port}"
        if access_token:
            self.ws_url += f"?access_token={access_token}"

        # 连接状态
        self._ws = None
        self._running = False
        self._connected = False
        self._ready = asyncio.Event()

        # API 调用 pending 队列 (echo -> Future)
        self._pending: dict[str, asyncio.Future] = {}

        # 重连配置
        self._max_reconnect = 10
        self._reconnect_delay = 1

        # 配置中的 self_qq (NapCat get_login_info 失败时的兜底值)
        self.config_self_qq: str = ""

        # 事件回调处理器
        self._on_message_handlers: list[Callable] = []
        self._on_notice_handlers: list[Callable] = []
        self._on_request_handlers: list[Callable] = []

        # 正在响应中的目标集合 (由 CherryStudioModule 在 SSE 请求期间设置)
        self._responding_targets: set[str] = set()

        # 消息去重: NapCat/QQ 协议层可能为同一条消息发送多个重复事件,
        # 使用 msg_id 集合过滤重复消息，防止 Agent 多次回复同一条消息。
        self._seen_msg_ids: set[str] = set()
        self._seen_msg_ids_max: int = 2000  # 最多保留 2000 条消息 ID

        # 长文本自动转文档的阈值 (从 config.json 的 auto_reply.doc_threshold 读取)
        self._doc_threshold: int = 1000

    async def initialize(self):
        """初始化互联桥"""
        logger.info(f"NapCatBridge init: {self.host}:{self.port}")

    # ---- 响应目标管理 (由 CherryStudioModule 在 SSE 请求期间调用) ----

    def mark_responding(self, target_id: str):
        """标记某个目标正在被 SSE 响应中"""
        self._responding_targets.add(str(target_id))

    def unmark_responding(self, target_id: str):
        """取消标记"""
        self._responding_targets.discard(str(target_id))

    def is_target_active(self, target_id: str) -> bool:
        """
        检查目标是否活跃。

        活跃条件 (满足任一即可):
        1. 目标正在被 SSE 响应中 (_responding_targets)
        2. MessageBuffer 中有该目标的最近消息
        """
        tid = str(target_id)
        if tid in self._responding_targets:
            return True
        return self.message_buffer.has_target(tid)

    def set_doc_threshold(self, threshold: int):
        """设置长文本自动转文档的字符阈值"""
        self._doc_threshold = max(100, threshold)  # 最小 100 字符
        logger.info(f"Doc threshold set: {self._doc_threshold} chars")

    async def _fetch_self_qq(self):
        """从 NapCat 获取机器人自身 QQ 号并缓存 (每次连接/重连时调用)

        优先使用 NapCat get_login_info API，失败时回退到 config.json 中的 napcat.self_qq。
        """
        logger.debug("[self_qq] _fetch_self_qq: calling get_login_info...")
        t0 = asyncio.get_event_loop().time()
        try:
            login_info = await self.get_login_info()
            elapsed = asyncio.get_event_loop().time() - t0
            logger.debug(f"[self_qq] get_login_info returned ({elapsed:.2f}s): {login_info}")

            if login_info:
                user_id = login_info.get("user_id", "")
                if user_id:
                    self.self_qq = str(user_id)
                    nickname = login_info.get("nickname", "")
                    logger.info(f"[self_qq] Bot QQ: {self.self_qq} (nick: {nickname})")
                    return
                else:
                    logger.warning(
                        f"[self_qq] get_login_info missing user_id: {login_info}"
                    )
            else:
                logger.warning("[self_qq] get_login_info returned empty")

        except Exception as e:
            elapsed = asyncio.get_event_loop().time() - t0
            logger.warning(f"[self_qq] get_login_info failed after {elapsed:.2f}s: {e}")

        # NapCat API 失败/返回空，回退到配置值
        if self.config_self_qq:
            self.self_qq = self.config_self_qq
            logger.info(f"[self_qq] Fallback to config value: {self.config_self_qq}")
        else:
            logger.warning("[self_qq] No config fallback available, self_qq remains unset")

    async def start(self):
        """启动 WebSocket 连接"""
        self._running = True
        reconnect_count = 0
        listener: asyncio.Task | None = None

        while self._running:
            if self._max_reconnect > 0 and reconnect_count > self._max_reconnect:
                error = BridgeError(
                    ErrorCode.NAPCAT_CONNECTION_FAILED,
                    detail=f"重连已达上限 {self._max_reconnect}",
                )
                logger.error(error.user_message)
                break

            try:
                logger.info(f"Connecting NapCat WebSocket: {self.ws_url}")
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._ready.set()
                    reconnect_count = 0
                    self._reconnect_delay = 1  # 重连成功后重置退避延迟
                    logger.info("NapCat WebSocket connected")

                    # ★ 必须先启动消息监听，否则后续 API 调用的响应无人接收
                    listener = asyncio.create_task(self._listen(ws))
                    logger.debug("[self_qq] _listen task started, proceeding to _fetch_self_qq")

                    # 每次连接/重连时刷新机器人自身 QQ 号
                    # (_listen 已在后台运行，API 响应可正常接收)
                    await self._fetch_self_qq()

                    # 等待监听任务结束 (WebSocket 关闭)
                    logger.debug("[self_qq] _fetch_self_qq done, awaiting _listen task...")
                    await listener

                    # _listen 正常返回 = WebSocket 被对端关闭 (无异常)
                    # 需手动清理状态，否则 _connected/_ws 残留为旧连接的引用
                    logger.info("NapCat WebSocket closed (normal exit)")
                    self._connected = False
                    self._ws = None
                    self._ready.clear()

                    # 失败所有等待中的请求 (旧连接已关闭，不会有回复)
                    for echo, fut in self._pending.items():
                        if not fut.done():
                            fut.set_exception(ConnectionError("WebSocket 已断开 (normal close)"))
                    self._pending.clear()
                    listener = None

            except (OSError, websockets.WebSocketException) as e:
                self._connected = False
                self._ws = None
                self._ready.clear()

                # 确保旧的监听任务已清理
                if listener and not listener.done():
                    listener.cancel()
                    try:
                        await listener
                    except (asyncio.CancelledError, Exception):
                        pass
                listener = None

                error = BridgeError(
                    ErrorCode.NAPCAT_DISCONNECTED,
                    detail=str(e),
                )
                logger.warning(
                    f"{error.user_message} - {self._reconnect_delay}s 后重连")

                # 失败所有等待中的请求
                for echo, fut in self._pending.items():
                    if not fut.done():
                        fut.set_exception(ConnectionError("WebSocket 已断开"))
                self._pending.clear()

                reconnect_count += 1
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def stop(self):
        """停止互联桥"""
        self._running = False
        self._connected = False
        if self._ws:
            await self._ws.close()
        self._ws = None
        logger.info("NapCatBridge stopped")

    async def wait_ready(self, timeout: float = 30.0):
        """等待连接就绪"""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            raise BridgeError(
                ErrorCode.NAPCAT_CONNECTION_FAILED,
                detail=f"连接超时 ({timeout}s)",
            )

    # ------------------------------------------------------------------
    # 消息监听 & 解析
    # ------------------------------------------------------------------

    async def _listen(self, ws):
        """监听 WebSocket 消息"""
        async for raw in ws:
            try:
                data = json.loads(raw)
                # API 响应 (有 echo 字段)
                if "echo" in data:
                    echo = data["echo"]
                    fut = self._pending.pop(echo, None)
                    if fut is not None:
                        if not fut.done():
                            fut.set_result(data)
                            logger.debug(
                                f"[WS] API response matched: echo={echo}, "
                                f"status={data.get('status', '?')}"
                            )
                        else:
                            logger.debug(
                                f"[WS] API response for already-done future: echo={echo} "
                                f"(likely timeout race, response discarded)"
                            )
                    else:
                        logger.debug(
                            f"[WS] API response with unexpected echo: {echo} "
                            f"(no pending future, possibly late response)"
                        )
                else:
                    # 事件消息，分发处理
                    await self._dispatch_event(data)
            except json.JSONDecodeError:
                logger.warning(f"[BRG-1006] Failed to parse WS message: {raw[:200]}")
            except Exception as e:
                # 捕获事件处理中的所有异常，防止 WebSocket 连接断开
                logger.error(
                    f"[BRG-1007] 处理 WS 消息时出错: {type(e).__name__}: {e}",
                    exc_info=True
                )

    async def _dispatch_event(self, data: dict):
        """分发事件消息"""
        post_type = data.get("post_type", "")

        if post_type == "message":
            # 解析为 RawMessage
            raw_msg = self._parse_raw_message(data)

            # ---- 消息去重: 过滤 NapCat/QQ 协议层的重复事件 ----
            # 同一条 QQ 消息可能因 NTQQ 协议同步、多设备同步或 WebSocket 重连
            # 而被多次投递。通过 msg_id 去重，避免 Agent 对同一消息多次回复。
            msg_id = getattr(raw_msg, "msg_id", "")
            if msg_id:
                if msg_id in self._seen_msg_ids:
                    logger.debug(f"Duplicate message filtered: msg_id={msg_id}")
                    return
                self._seen_msg_ids.add(msg_id)
                # 防止集合无限增长: 超过阈值时清空一半 (保留最近的)
                if len(self._seen_msg_ids) > self._seen_msg_ids_max:
                    # 简单策略: 清空后重新添加当前 ID
                    # (set 无序，无法保证保留最近的，但 2000 的容量足够大)
                    self._seen_msg_ids = {msg_id}
                    logger.debug("Seen msg_ids cache cleared (overflow protection)")

            # 添加到消息缓冲区
            await self.message_buffer.add_message(raw_msg)

            # 发送到 MessageBus
            if self.message_bus:
                await self.message_bus.raw_message_queue.put(raw_msg)

            # 调用注册的处理器
            for handler in self._on_message_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(raw_msg)
                    else:
                        handler(raw_msg)
                except Exception as e:
                    logger.error(f"Message handler execution failed: {e}", exc_info=True)

        elif post_type == "notice":
            await self._handle_notice(data)
        elif post_type == "request":
            await self._handle_request(data)

    def _parse_raw_message(self, data: dict) -> RawMessage:
        """
        解析原始消息

        Args:
            data: OneBot 消息数据

        Returns:
            RawMessage 对象 (包含 image_files, file_infos, group_name 等完整信息)
        """
        msg_type = data.get("message_type", "")
        sender = data.get("sender", {})
        message = data.get("message", [])

        # 提取文本内容 (优先从 message 段解析)
        text = self._extract_text(message)

        # raw_message 回退: 如果 message 段解析结果为空，使用 OneBot 的 raw_message 字段
        raw_message_str = data.get("raw_message", "")
        if not text and raw_message_str:
            text = raw_message_str

        # 提取附件
        attachments = self._extract_attachments(message)

        # 提取图片 file ID 和文件信息
        image_files = self._extract_image_files(message)
        file_infos = self._extract_file_infos(message)

        # 确定消息来源
        source = MessageSource.GROUP if msg_type == "group" else MessageSource.PRIVATE
        target_id = str(data.get("group_id", "")) if msg_type == "group" else str(
            sender.get("user_id", ""))

        # 智能检测 MessageType
        detected_type = self._detect_message_type(message, text)

        # 群名称 (OneBot 的 sender 字段中可能包含 group_name)
        group_name = ""
        if msg_type == "group":
            group_name = sender.get("group_name", "")
            # 某些 NapCat 版本在 raw_data 的顶层提供 group_name
            if not group_name:
                group_name = data.get("group_name", "")

        return RawMessage(
            msg_id=str(data.get("message_id", "")),
            source=source,
            target_id=target_id,
            sender_id=str(sender.get("user_id", "")),
            sender_name=sender.get("nickname", sender.get("card", "")),
            content=text,
            message_type=detected_type,
            attachments=attachments,
            timestamp=datetime.fromtimestamp(data.get("time", 0)),
            raw_data=data,
            image_files=image_files,
            file_infos=file_infos,
            group_name=group_name,
        )

    @staticmethod
    def _detect_message_type(message: list | str, text: str) -> MessageType:
        """
        智能检测消息类型

        根据消息段中的类型分布判断最终的 MessageType:
        - 仅包含文本段 → TEXT
        - 包含图片段 → IMAGE
        - 包含文件段 → FILE
        - 包含多种类型 → MIXED
        """
        if isinstance(message, str):
            return MessageType.TEXT

        has_text = False
        has_image = False
        has_file = False
        has_at = False
        has_reply = False

        for seg in message:
            if not isinstance(seg, dict):
                continue
            t = seg.get("type", "")
            if t == "text":
                has_text = True
            elif t == "image":
                has_image = True
            elif t == "file":
                has_file = True
            elif t == "at":
                has_at = True
            elif t == "reply":
                has_reply = True

        # 计算主要类型数量
        type_count = sum([has_text, has_image, has_file])

        if type_count > 1:
            return MessageType.MIXED
        elif has_image:
            return MessageType.IMAGE
        elif has_file:
            return MessageType.FILE
        elif has_at and not has_text:
            return MessageType.AT
        elif has_reply and not has_text:
            return MessageType.REPLY
        else:
            return MessageType.TEXT

    @staticmethod
    def _extract_text(message: list | str) -> str:
        """
        从消息段中提取文本

        支持 17+ 种 OneBot CQ 消息类型:
        text, image, record, video, at, reply, face, file,
        share, location, contact, music, forward, markdown,
        poke, gift, 以及通用回退
        """
        if isinstance(message, str):
            return message

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
            elif t == "record":
                parts.append("[语音]")
            elif t == "video":
                parts.append("[视频]")
            elif t == "at":
                parts.append(f"@{d.get('qq', '')}")
            elif t == "reply":
                parts.append("[引用回复]")
            elif t == "face":
                parts.append("[表情]")
            elif t == "file":
                parts.append(f"[文件: {d.get('name', '')}]")
            elif t == "share":
                parts.append(f"[分享: {d.get('title', d.get('url', ''))}]")
            elif t == "location":
                parts.append(f"[位置: {d.get('title', d.get('content', ''))}]")
            elif t == "contact":
                parts.append("[推荐联系人]")
            elif t == "music":
                parts.append(f"[音乐: {d.get('title', '')}]")
            elif t == "forward":
                parts.append("[合并转发]")
            elif t == "markdown":
                parts.append(f"[Markdown: {str(d.get('content', ''))[:80]}]")
            elif t == "poke":
                parts.append("[戳一戳]")
            elif t == "gift":
                parts.append("[礼物]")
            else:
                # 通用回退：避免静默丢弃未知类型
                parts.append(f"[{t}]")

        return "".join(parts)

    @staticmethod
    def _extract_attachments(message: list) -> list[dict]:
        """从消息段中提取附件信息"""
        if not isinstance(message, list):
            return []

        attachments = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            t = seg.get("type", "")
            d = seg.get("data", {})

            if t == "image":
                attachments.append({
                    "type": "image",
                    "file": d.get("file", ""),
                    "url": d.get("url", ""),
                })
            elif t == "file":
                attachments.append({
                    "type": "file",
                    "name": d.get("name", ""),
                    "url": d.get("url", ""),
                    "size": d.get("size", 0),
                })

        return attachments

    @staticmethod
    def _extract_image_files(message: list | str) -> list[str]:
        """
        从消息段中提取所有图片段的 file 字段列表

        返回 NapCat 图片 file ID 列表，可用于后续 get_image API 获取缓存路径
        """
        if isinstance(message, str):
            return []
        files = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "image":
                fid = seg.get("data", {}).get("file", "")
                if fid:
                    files.append(fid)
        return files

    @staticmethod
    def _extract_file_infos(message: list | str) -> list[dict]:
        """
        从消息段中提取所有文件段的详细信息

        返回 [{url, name, size}] 列表
        """
        if isinstance(message, str):
            return []
        infos = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "file":
                d = seg.get("data", {})
                url = d.get("url", "")
                if url:
                    infos.append({
                        "url": url,
                        "name": d.get("name", ""),
                        "size": int(d.get("size", 0)) if d.get("size") else 0,
                    })
        return infos

    # ------------------------------------------------------------------
    # 通知 & 请求事件处理
    # ------------------------------------------------------------------

    async def _handle_notice(self, data: dict):
        """
        处理通知事件

        支持:
        - group_increase: 群成员增加事件
        - group_decrease: 群成员减少事件 (退群/踢出)
        - friend_add: 好友添加事件
        - group_ban: 群禁言事件
        - group_admin: 群管理员变动事件
        - group_recall: 群消息撤回事件
        - friend_recall: 私聊消息撤回事件
        - notify: 通用通知 (poke, lucky_king 等)
        """
        notice_type = data.get("notice_type", "")
        logger.debug(f"Received notice: {notice_type}")

        # 构建通用事件信息
        event_info: dict = {"type": notice_type, "raw": data}

        if notice_type == "group_increase":
            group_id = str(data.get("group_id", ""))
            user_id = str(data.get("user_id", ""))
            operator_id = str(data.get("operator_id", ""))
            logger.info(f"Group member added: group={group_id}, user={user_id}, operator={operator_id}")
            event_info.update({
                "group_id": group_id,
                "user_id": user_id,
                "operator_id": operator_id,
                "sub_type": data.get("sub_type", ""),
            })

        elif notice_type == "group_decrease":
            group_id = str(data.get("group_id", ""))
            user_id = str(data.get("user_id", ""))
            operator_id = str(data.get("operator_id", ""))
            sub_type = data.get("sub_type", "")  # leave / kick / kick_me
            logger.info(f"Group member removed: group={group_id}, user={user_id}, type={sub_type}")
            event_info.update({
                "group_id": group_id,
                "user_id": user_id,
                "operator_id": operator_id,
                "sub_type": sub_type,
            })

        elif notice_type == "friend_add":
            user_id = str(data.get("user_id", ""))
            logger.info(f"Friend added: user={user_id}")
            event_info.update({"user_id": user_id})

        elif notice_type == "group_ban":
            group_id = str(data.get("group_id", ""))
            user_id = str(data.get("user_id", ""))
            operator_id = str(data.get("operator_id", ""))
            duration = data.get("duration", 0)
            sub_type = data.get("sub_type", "")  # ban / lift_ban
            logger.info(f"Group mute: group={group_id}, user={user_id}, type={sub_type}, duration={duration}s")
            event_info.update({
                "group_id": group_id,
                "user_id": user_id,
                "operator_id": operator_id,
                "sub_type": sub_type,
                "duration": duration,
            })

        elif notice_type == "group_admin":
            group_id = str(data.get("group_id", ""))
            user_id = str(data.get("user_id", ""))
            sub_type = data.get("sub_type", "")  # set / unset
            logger.info(f"Group admin changed: group={group_id}, user={user_id}, type={sub_type}")
            event_info.update({
                "group_id": group_id,
                "user_id": user_id,
                "sub_type": sub_type,
            })

        elif notice_type in ("group_recall", "friend_recall"):
            group_id = str(data.get("group_id", ""))
            user_id = str(data.get("user_id", ""))
            operator_id = str(data.get("operator_id", ""))
            message_id = str(data.get("message_id", ""))
            logger.info(f"Message recalled: type={notice_type}, user={user_id}, messageID={message_id}")
            event_info.update({
                "group_id": group_id,
                "user_id": user_id,
                "operator_id": operator_id,
                "message_id": message_id,
            })

        elif notice_type == "notify":
            sub_type = data.get("sub_type", "")  # poke / lucky_king / honor
            group_id = str(data.get("group_id", ""))
            user_id = str(data.get("user_id", ""))
            target_id = str(data.get("target_id", ""))
            logger.info(f"Notice: subtype={sub_type}, group={group_id}, user={user_id}")
            event_info.update({
                "sub_type": sub_type,
                "group_id": group_id,
                "user_id": user_id,
                "target_id": target_id,
            })

        else:
            # 未知通知类型 — 仍然分发给注册的处理器
            logger.debug(f"Unknown notice type: {notice_type}")

        # 分发给所有注册的处理器
        for handler in self._on_notice_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event_info)
                else:
                    handler(event_info)
            except Exception as e:
                logger.error(f"Notice handler execution failed: {e}", exc_info=True)

    async def _handle_request(self, data: dict):
        """
        处理请求事件

        支持:
        - friend: 好友申请
        - group: 群邀请
        """
        request_type = data.get("request_type", "")
        logger.debug(f"Received request: {request_type}")

        if request_type == "friend":
            user_id = str(data.get("user_id", ""))
            comment = data.get("comment", "")
            flag = data.get("flag", "")
            logger.info(f"Friend request: user={user_id}, comment={comment}")

            event_info = {
                "type": "friend_request",
                "user_id": user_id,
                "comment": comment,
                "flag": flag,
                "raw": data,
            }
            for handler in self._on_request_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event_info)
                    else:
                        handler(event_info)
                except Exception as e:
                    logger.error(f"Request handler execution failed: {e}", exc_info=True)

        elif request_type == "group":
            group_id = str(data.get("group_id", ""))
            user_id = str(data.get("user_id", ""))
            comment = data.get("comment", "")
            flag = data.get("flag", "")
            sub_type = data.get("sub_type", "")
            logger.info(f"Group invite: group={group_id}, user={user_id}, type={sub_type}")

            event_info = {
                "type": "group_request",
                "group_id": group_id,
                "user_id": user_id,
                "comment": comment,
                "flag": flag,
                "sub_type": sub_type,
                "raw": data,
            }
            for handler in self._on_request_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event_info)
                    else:
                        handler(event_info)
                except Exception as e:
                    logger.error(f"Request handler execution failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------------

    async def _call(self, action: str, params: dict | None = None, timeout: float = 60.0) -> Any:
        """
        调用 OneBot API

        Args:
            action: API 动作名称
            params: API 参数
            timeout: 超时时间 (秒)

        Returns:
            API 响应数据

        Raises:
            BridgeError: 调用失败时抛出
        """
        if not self._ws or not self._connected:
            raise BridgeError(
                ErrorCode.NAPCAT_CONNECTION_FAILED, detail="WebSocket 未连接")

        echo = str(uuid.uuid4())[:8]
        request = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[echo] = fut

        try:
            await self._ws.send(json.dumps(request, ensure_ascii=False))
            logger.debug(f"[WS] API request sent: {action}, echo={echo}, timeout={timeout}s")
            result = await asyncio.wait_for(fut, timeout)

            if result.get("status") == "failed":
                raise BridgeError(
                    ErrorCode.NAPCAT_INVALID_RESPONSE,
                    detail=f"API 调用失败 [{action}]: {result.get('message', result)}",
                )

            return result.get("data", result)

        except (asyncio.TimeoutError, TimeoutError):
            raise BridgeError(
                ErrorCode.NAPCAT_TIMEOUT,
                detail=f"API 调用超时 [{action}] ({timeout}s)",
            )
        finally:
            self._pending.pop(echo, None)

    # ------------------------------------------------------------------
    # 发送消息
    # ------------------------------------------------------------------

    async def send_message(self, msg: OutgoingMessage, *, skip_preview: bool = False) -> str:
        """
        发送消息到 NapCat

        Args:
            msg: 待发送消息
            skip_preview: True 时跳过长文本转文档的预览通知
                          (由防抖刷新调用时使用，避免逐步增长的预览消息)

        Returns:
            消息 ID

        Raises:
            BridgeError: 发送失败时抛出
        """
        try:
            # 构建 OneBot 消息格式
            message_type = "group" if msg.target_source == MessageSource.GROUP else "private"

            # 发送文本消息
            if msg.message_type == MessageType.TEXT:
                msg_id = await self._send_text(message_type, msg.target_id, msg.content, skip_preview=skip_preview, skip_doc=msg.skip_doc)

            # 发送图片消息
            elif msg.message_type == MessageType.IMAGE:
                msg_id = await self._send_image(message_type, msg.target_id, msg.attachments)

            # 混合消息
            else:
                msg_id = await self._send_text(message_type, msg.target_id, msg.content, skip_preview=skip_preview, skip_doc=msg.skip_doc)

            logger.info(
                f"消息已发送: {msg.target_source.value}_{msg.target_id}, ID={msg_id}")
            return msg_id

        except BridgeError:
            raise
        except Exception as e:
            raise BridgeError(
                ErrorCode.NAPCAT_SEND_FAILED,
                detail=str(e),
            )

    async def _send_text(self, message_type: str, target_id: str, text: str, *, skip_preview: bool = False, skip_doc: bool = False) -> str:
        """
        发送文本消息。

        智能后处理: 当文本长度超过 doc_threshold 时，自动将文本保存为
        临时 .md 文件并通过 upload_file API 上传，附带前 300 字符的预览通知。

        Args:
            message_type: "private" 或 "group"
            target_id: 目标 ID
            text: 消息文本
            skip_preview: True 时跳过长文本转文档的预览通知
                          (由 server.py 防抖刷新调用时使用，避免重复发送预览)
            skip_doc: True 时完全跳过自动转文档，始终以纯文本发送
                      (命令系统回复等场景使用)
        """
        # 长文本自动转文档 (skip_doc=True 时跳过)
        if not skip_doc and self._doc_threshold > 0 and len(text) > self._doc_threshold:
            return await self._send_long_text_as_doc(message_type, target_id, text, skip_preview=skip_preview)

        # 正常短文本发送
        params = {"message_type": message_type, "message": text}
        if message_type == "private":
            params["user_id"] = str(target_id)
        else:
            params["group_id"] = str(target_id)

        data = await self._call("send_msg", params)
        return str(data.get("message_id", ""))

    async def _send_long_text_as_doc(
        self, message_type: str, target_id: str, text: str, *, skip_preview: bool = False
    ) -> str:
        """
        将超长文本转为图片或文档上传。

        优先路径: 渲染 Markdown → PNG 图片发送 (md_to_image)
        回退路径: 保存为 .md 文件 → upload_file 上传

        流程:
        1. 尝试 md_to_image 渲染为图片并发送
        2. 若失败，写入临时 .md 文件并上传
        3. 发送前 300 字符的预览通知 (skip_preview=True 时跳过)
        4. 清理临时文件
        """
        import tempfile as _tempfile

        # ── 路径 1: 渲染为图片发送 ──
        temp_png = None
        try:
            from modules.md_to_image import md_to_image

            temp_png = await md_to_image(text)
            msg_id = await self.send_local_image(message_type, str(target_id), temp_png)
            logger.info(f"Long text rendered as image: {len(text)} chars, ID={msg_id}")

            # 发送简短通知 (skip_preview 时仅发送简短版本)
            if not skip_preview:
                preview_len = 300
                preview = text[:preview_len]
                if len(text) > preview_len:
                    preview += "……"
                preview_msg = f"📄 已生成长文档图片 ({len(text)} 字符)，预览:\n{preview}"
            else:
                preview_msg = f"📄 已生成长文档图片 ({len(text)} 字符)"

            params = {"message_type": message_type, "message": preview_msg}
            if message_type == "private":
                params["user_id"] = str(target_id)
            else:
                params["group_id"] = str(target_id)
            await self._call("send_msg", params)

            return msg_id

        except Exception as e:
            logger.warning(f"Long text image render failed ({e}), falling back to doc upload")

        finally:
            # 清理图片临时文件
            if temp_png and os.path.exists(temp_png):
                try:
                    os.remove(temp_png)
                except OSError:
                    pass

        # ── 路径 2: 回退为 .md 文档上传 ──
        tmp_path = None
        try:
            ts = int(time.time())
            filename = f"bridge_doc_{ts}.md"

            # 写入临时文件
            tmp_dir = _tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, filename)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(text)

            # 上传文件
            await self.upload_file(message_type, target_id, tmp_path)
            logger.info(f"Long text uploaded as document: {filename} ({len(text)} chars)")

            # 发送预览通知 (skip_preview=True 时仅发送简短通知，防止防抖刷新时重复发送长预览)
            if not skip_preview:
                preview_len = 300
                preview = text[:preview_len]
                if len(text) > preview_len:
                    preview += "……"
                preview_msg = f"📄 已生成长文档 ({len(text)} 字符)，预览:\n{preview}"
            else:
                # skip_preview: 仅发送简短通知，避免重复刷屏
                preview_msg = f"📄 已生成长文档 ({len(text)} 字符)"

            params = {"message_type": message_type, "message": preview_msg}
            if message_type == "private":
                params["user_id"] = str(target_id)
            else:
                params["group_id"] = str(target_id)

            data = await self._call("send_msg", params)
            return str(data.get("message_id", ""))

        except Exception as e:
            logger.warning(f"Long text doc upload failed: {e}, falling back to raw text")
            # 回退: 直接发送原文本
            params = {"message_type": message_type, "message": text}
            if message_type == "private":
                params["user_id"] = str(target_id)
            else:
                params["group_id"] = str(target_id)
            data = await self._call("send_msg", params)
            return str(data.get("message_id", ""))

        finally:
            # 清理临时文件
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def _send_image(self, message_type: str, target_id: str, attachments: list[dict]) -> str:
        """
        发送图片消息 (内部方法，由 send_message 调用)

        流程: 从附件获取图片 URL -> 下载 -> base64 编码 -> 用 OneBot send_msg
        的 message 段格式发送 (type: "image", data: {file: "base64://..."})
        """
        if not attachments:
            raise ValueError("图片附件不能为空")

        # 获取第一张图片的 URL
        image_url = attachments[0].get("url", "")
        if not image_url:
            raise ValueError("图片 URL 不能为空")

        # 下载图片
        img_data = await self._download_image(image_url)
        if not img_data:
            raise BridgeError(ErrorCode.NAPCAT_SEND_FAILED, detail="下载图片失败")

        # base64 编码
        b64_data = base64.b64encode(img_data).decode("utf-8")

        # 构建 OneBot message 段格式
        message_segments = [
            {"type": "image", "data": {"file": f"base64://{b64_data}"}}
        ]

        # 如果有附件中的文本描述，添加到消息前面
        text_content = attachments[0].get("summary", "")
        if text_content:
            message_segments.insert(0, {"type": "text", "data": {"text": f"{text_content}\n"}})

        params = {"message_type": message_type, "message": message_segments}
        if message_type == "private":
            params["user_id"] = str(target_id)
        else:
            params["group_id"] = str(target_id)

        data = await self._call("send_msg", params)
        return str(data.get("message_id", ""))

    async def _download_image(self, url: str) -> bytes | None:
        """下载图片"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://multimedia.nt.qq.com.cn/",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    logger.warning(f"[BRG-1003] Download image failed: HTTP {resp.status}")
                    return None
        except Exception as e:
            logger.warning(f"[BRG-1003] Download image exception: {e}")
            return None

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def register_message_handler(self, handler: Callable):
        """注册消息处理器"""
        self._on_message_handlers.append(handler)

    def register_notice_handler(self, handler: Callable):
        """注册通知事件处理器"""
        self._on_notice_handlers.append(handler)

    def register_request_handler(self, handler: Callable):
        """注册请求事件处理器"""
        self._on_request_handlers.append(handler)

    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected

    # ------------------------------------------------------------------
    # OneBot API 扩展方法
    # ------------------------------------------------------------------

    async def get_login_info(self) -> dict:
        """获取当前登录账号信息"""
        result = await self._call("get_login_info")
        return result if isinstance(result, dict) else {}

    async def get_group_list(self) -> list:
        """获取群列表"""
        result = await self._call("get_group_list")
        return result if isinstance(result, list) else []

    async def get_friend_list(self) -> list:
        """获取好友列表"""
        result = await self._call("get_friend_list")
        return result if isinstance(result, list) else []

    async def get_group_member_list(self, group_id: str) -> list:
        """获取群成员列表"""
        result = await self._call("get_group_member_list", {"group_id": int(group_id)})
        return result if isinstance(result, list) else []

    async def get_stranger_info(self, user_id: str) -> dict:
        """获取用户信息"""
        result = await self._call("get_stranger_info", {"user_id": int(user_id)})
        return result if isinstance(result, dict) else {}

    async def get_group_info(self, group_id: str) -> dict:
        """获取群信息"""
        result = await self._call("get_group_info", {"group_id": int(group_id)})
        return result if isinstance(result, dict) else {}

    async def get_group_msg_history(self, group_id: str, count: int = 20) -> list:
        """获取群历史消息"""
        result = await self._call(
            "get_group_msg_history",
            {"group_id": int(group_id), "count": count},
        )
        if isinstance(result, dict):
            return result.get("messages", [])
        return result if isinstance(result, list) else []

    async def get_msg(self, message_id: str) -> dict:
        """获取单条消息详情"""
        result = await self._call("get_msg", {"message_id": int(message_id)})
        return result if isinstance(result, dict) else {}

    async def get_recent_contact(self, count: int = 20) -> list:
        """获取最近联系人列表"""
        result = await self._call("get_recent_contact", {"count": count})
        return result if isinstance(result, list) else []

    async def get_friend_msg_history(self, user_id: str, count: int = 20) -> list:
        """获取私聊历史消息"""
        result = await self._call(
            "get_friend_msg_history",
            {"user_id": int(user_id), "count": count},
        )
        if isinstance(result, dict):
            return result.get("messages", [])
        return result if isinstance(result, list) else []

    async def delete_msg(self, message_id: str) -> bool:
        """撤回消息"""
        try:
            await self._call("delete_msg", {"message_id": int(message_id)})
            return True
        except Exception:
            return False

    async def set_group_card(self, group_id: str, user_id: str, card: str):
        """设置群名片"""
        await self._call("set_group_card", {
            "group_id": int(group_id),
            "user_id": int(user_id),
            "card": card,
        })

    async def leave_group(self, group_id: str):
        """退出群聊"""
        await self._call("set_group_leave", {"group_id": int(group_id)})

    async def get_image_path(self, file_id: str) -> str:
        """通过 NapCat get_image API 获取图片本地缓存路径"""
        result = await self._call("get_image", {"file": file_id})
        if isinstance(result, dict):
            return str(result.get("file", "") or result.get("url", ""))
        return str(result) if result else ""

    async def approve_friend_request(self, flag: str, approve: bool = True) -> bool:
        """同意好友申请"""
        try:
            await self._call(
                "set_friend_add_request",
                {"flag": flag, "approve": approve},
            )
            return True
        except Exception:
            return False

    async def approve_group_invite(self, flag: str, approve: bool = True) -> bool:
        """同意群邀请"""
        try:
            await self._call(
                "set_group_add_request",
                {"flag": flag, "sub_type": "invite", "approve": approve},
            )
            return True
        except Exception:
            return False

    async def upload_file(
        self, message_type: str, target_id: str, file_path: str
    ) -> bool:
        """
        上传文件到群聊或私聊

        通过 OneBot upload_private_file / upload_group_file API 发送文件。
        file_path 应为本地文件的绝对路径。

        Args:
            message_type: "private" 或 "group"
            target_id: 目标 QQ 号或群号
            file_path: 本地文件绝对路径

        Returns:
            是否成功

        Raises:
            BridgeError: 文件不存在或上传失败时抛出
        """
        if not os.path.isfile(file_path):
            raise BridgeError(
                ErrorCode.NAPCAT_SEND_FAILED,
                detail=f"文件不存在: {file_path}",
            )

        file_name = os.path.basename(file_path)

        if message_type == "private":
            await self._call("upload_private_file", {
                "user_id": int(target_id),
                "file": file_path,
                "name": file_name,
            })
        else:
            await self._call("upload_group_file", {
                "group_id": int(target_id),
                "file": file_path,
                "name": file_name,
            })
        return True

    async def send_image(
        self, message_type: str, target_id: str, image_url: str, summary: str = ""
    ) -> str:
        """
        发送图片 (公开 API 方法)

        流程: 下载 URL -> base64 编码 -> 用 OneBot send_msg 的 message 段格式发送
        (type: "image", data: {file: "base64://..."})

        Args:
            message_type: "private" 或 "group"
            target_id: 目标 QQ 号或群号
            image_url: 图片 URL
            summary: 图片描述 (可选)

        Returns:
            消息 ID

        Raises:
            BridgeError: 下载或发送失败时抛出
        """
        # 下载图片
        img_data = await self._download_image(image_url)
        if not img_data:
            raise BridgeError(ErrorCode.NAPCAT_SEND_FAILED, detail="下载图片失败")

        # base64 编码
        b64_data = base64.b64encode(img_data).decode("utf-8")

        # 构建 OneBot message 段
        message_segments: list[dict] = []
        if summary:
            message_segments.append({"type": "text", "data": {"text": f"{summary}\n"}})
        message_segments.append(
            {"type": "image", "data": {"file": f"base64://{b64_data}"}}
        )

        params = {"message_type": message_type, "message": message_segments}
        if message_type == "private":
            params["user_id"] = str(target_id)
        else:
            params["group_id"] = str(target_id)

        data = await self._call("send_msg", params)
        return str(data.get("message_id", ""))

    async def send_local_image(
        self, message_type: str, target_id: str, image_path: str, summary: str = ""
    ) -> str:
        """
        发送本地图片文件 (不经过 URL 下载)

        直接读取本地文件 → base64 编码 → OneBot send_msg 图片段发送。
        适用于 md_to_image 等本地生成的图片。

        Args:
            message_type: "private" 或 "group"
            target_id:    目标 QQ 号或群号
            image_path:   本地 PNG/JPG 文件绝对路径
            summary:      图片描述 (可选)

        Returns:
            消息 ID

        Raises:
            BridgeError: 文件不存在或发送失败时抛出
        """
        if not os.path.isfile(image_path):
            raise BridgeError(
                ErrorCode.NAPCAT_SEND_FAILED,
                detail=f"图片文件不存在: {image_path}",
            )

        with open(image_path, "rb") as f:
            img_data = f.read()

        b64_data = base64.b64encode(img_data).decode("utf-8")

        message_segments: list[dict] = []
        if summary:
            message_segments.append({"type": "text", "data": {"text": f"{summary}\n"}})
        message_segments.append(
            {"type": "image", "data": {"file": f"base64://{b64_data}"}}
        )

        params = {"message_type": message_type, "message": message_segments}
        if message_type == "private":
            params["user_id"] = str(target_id)
        else:
            params["group_id"] = str(target_id)

        data = await self._call("send_msg", params)
        msg_id = str(data.get("message_id", ""))
        logger.info(f"Local image sent: {image_path} -> {message_type}_{target_id}, ID={msg_id}")
        return msg_id

    # ------------------------------------------------------------------
    # 状态与版本查询
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        """获取 NapCat 在线状态 (online, good 等字段)"""
        result = await self._call("get_status")
        return result if isinstance(result, dict) else {}

    async def get_version_info(self) -> dict:
        """获取 NapCat 版本信息 (app_name, app_version, protocol_version)"""
        result = await self._call("get_version_info")
        return result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------
    # 群管理 API
    # ------------------------------------------------------------------

    async def set_group_kick(
        self, group_id: str, user_id: str, reject_add_request: bool = False
    ) -> bool:
        """群踢人。reject_add_request=True 时拒绝再次加群"""
        try:
            await self._call("set_group_kick", {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "reject_add_request": reject_add_request,
            })
            return True
        except Exception as e:
            logger.warning(f"Group kick failed: group={group_id}, user={user_id}: {e}")
            return False

    async def set_group_ban(
        self, group_id: str, user_id: str, duration: int = 1800
    ) -> bool:
        """群禁言 (单人)。duration 为秒数，0 为解除禁言"""
        try:
            await self._call("set_group_ban", {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "duration": duration,
            })
            return True
        except Exception as e:
            logger.warning(f"Group mute failed: group={group_id}, user={user_id}: {e}")
            return False

    async def set_group_whole_ban(self, group_id: str, enable: bool = True) -> bool:
        """群全员禁言。enable=True 开启，False 关闭"""
        try:
            await self._call("set_group_whole_ban", {
                "group_id": int(group_id),
                "enable": enable,
            })
            return True
        except Exception as e:
            logger.warning(f"Group mute-all failed: group={group_id}: {e}")
            return False

    async def set_group_admin(
        self, group_id: str, user_id: str, enable: bool = True
    ) -> bool:
        """设置/取消群管理员"""
        try:
            await self._call("set_group_admin", {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "enable": enable,
            })
            return True
        except Exception as e:
            logger.warning(f"Set group admin failed: group={group_id}, user={user_id}: {e}")
            return False

    async def set_group_name(self, group_id: str, group_name: str) -> bool:
        """设置群名"""
        try:
            await self._call("set_group_name", {
                "group_id": int(group_id),
                "group_name": group_name,
            })
            return True
        except Exception as e:
            logger.warning(f"Set group name failed: group={group_id}: {e}")
            return False

    async def set_group_special_title(
        self, group_id: str, user_id: str, special_title: str = "", duration: int = -1
    ) -> bool:
        """设置群专属头衔。duration=-1 为永久，空字符串清除"""
        try:
            await self._call("set_group_special_title", {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "special_title": special_title,
                "duration": duration,
            })
            return True
        except Exception as e:
            logger.warning(f"Set group title failed: group={group_id}, user={user_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # 群荣誉
    # ------------------------------------------------------------------

    async def get_group_honor_info(self, group_id: str, honor_type: str = "all") -> dict:
        """获取群荣誉信息 (type: all/talk/performer/legend/strong_newbie/emotion)"""
        result = await self._call("get_group_honor_info", {
            "group_id": int(group_id),
            "type": honor_type,
        })
        return result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------
    # 合并转发消息
    # ------------------------------------------------------------------

    async def send_group_forward_msg(self, group_id: str, messages: list[dict]) -> str:
        """
        发送群合并转发消息

        messages 格式: [{"type": "node", "data": {"name": "xxx", "uin": "123", "content": [...]}}]
        """
        data = await self._call("send_group_forward_msg", {
            "group_id": int(group_id),
            "messages": messages,
        })
        return str(data.get("message_id", "")) if isinstance(data, dict) else ""

    async def send_private_forward_msg(self, user_id: str, messages: list[dict]) -> str:
        """发送私聊合并转发消息"""
        data = await self._call("send_private_forward_msg", {
            "user_id": int(user_id),
            "messages": messages,
        })
        return str(data.get("message_id", "")) if isinstance(data, dict) else ""

    async def get_forward_msg(self, forward_id: str) -> list[dict]:
        """获取合并转发消息内容"""
        result = await self._call("get_forward_msg", {"id": forward_id})
        if isinstance(result, dict):
            return result.get("messages", result.get("message", []))
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # 群公告
    # ------------------------------------------------------------------

    async def send_group_notice(self, group_id: str, content: str, image: str = "") -> bool:
        """发送群公告 (NapCat 内部 API: _send_group_notice)"""
        try:
            params: dict = {"group_id": int(group_id), "content": content}
            if image:
                params["image"] = image
            await self._call("_send_group_notice", params)
            return True
        except Exception as e:
            logger.warning(f"Send group announcement failed: group={group_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # 群文件管理
    # ------------------------------------------------------------------

    async def get_group_root_files(self, group_id: str) -> dict:
        """获取群根目录文件列表，返回 {files: [...], folders: [...]}"""
        try:
            result = await self._call("get_group_root_files", {"group_id": int(group_id)})
            return result if isinstance(result, dict) else {"files": [], "folders": []}
        except Exception:
            return {"files": [], "folders": []}

    async def get_group_files_by_folder(self, group_id: str, folder_id: str) -> dict:
        """获取群子目录文件列表"""
        try:
            result = await self._call("get_group_files_by_folder", {
                "group_id": int(group_id),
                "folder_id": folder_id,
            })
            return result if isinstance(result, dict) else {"files": [], "folders": []}
        except Exception:
            return {"files": [], "folders": []}

    async def get_group_file_url(self, group_id: str, file_id: str, busid: int) -> str:
        """获取群文件下载 URL"""
        try:
            result = await self._call("get_group_file_url", {
                "group_id": int(group_id),
                "file_id": file_id,
                "busid": busid,
            })
            return result.get("url", "") if isinstance(result, dict) else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 戳一戳
    # ------------------------------------------------------------------

    async def send_group_poke(self, group_id: str, user_id: str) -> bool:
        """在群内戳一戳某人"""
        try:
            await self._call("group_poke", {
                "group_id": int(group_id),
                "user_id": int(user_id),
            })
            return True
        except Exception:
            return False

    async def send_private_poke(self, user_id: str) -> bool:
        """私聊戳一戳"""
        try:
            await self._call("friend_poke", {"user_id": int(user_id)})
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 消息标记
    # ------------------------------------------------------------------

    async def mark_msg_as_read(self, message_id: str) -> bool:
        """标记消息为已读 (消除红点)"""
        try:
            await self._call("mark_msg_as_read", {"message_id": int(message_id)})
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # @全体 剩余次数
    # ------------------------------------------------------------------

    async def get_group_at_all_remain(self, group_id: str) -> dict:
        """获取群 @全体成员 的剩余次数"""
        try:
            result = await self._call("get_group_at_all_remain", {"group_id": int(group_id)})
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------

    @property
    def self_qq(self) -> str:
        """获取机器人自身的 QQ 号 (启动时由 get_login_info 设置)"""
        return getattr(self, '_self_qq', '')

    @self_qq.setter
    def self_qq(self, value: str):
        self._self_qq = str(value)
