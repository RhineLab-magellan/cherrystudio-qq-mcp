"""
NapCatQQ 客户端 — WebSocket 双向通信 (事件接收 + API 调用)
"""

import asyncio
import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import websockets

logger = logging.getLogger("napcat")

# ---------------------------------------------------------------------------
# 消息数据模型
# ---------------------------------------------------------------------------


@dataclass
class QQMessage:
    message_id: str
    message_type: str  # "private" | "group"
    sender_id: str
    sender_name: str = ""
    group_id: str = ""
    group_name: str = ""
    text: str = ""
    timestamp: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    image_files: list[str] = field(default_factory=list)  # NapCat 图片 file ID
    file_infos: list[dict] = field(default_factory=list)  # [{url, name, size}]

    def format_for_ai(self) -> str:
        ts = datetime.fromtimestamp(self.timestamp / 1000).strftime("%H:%M:%S") if self.timestamp else ""
        if self.message_type == "group":
            group_label = self.group_name or self.group_id
            return f"[{ts}] 群({group_label}) {self.sender_name}({self.sender_id}): {self.text}"
        else:
            return f"[{ts}] 私聊 {self.sender_name}({self.sender_id}): {self.text}"

    def get_reply_id(self) -> str:
        """从消息段中提取被引用的消息 ID（如果有）"""
        message = self.raw.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "reply":
                    return str(seg.get("data", {}).get("id", ""))
        return ""


# ---------------------------------------------------------------------------
# WebSocket 双向客户端 (事件 + API)
# ---------------------------------------------------------------------------


class NapCatClient:
    """通过单一 WebSocket 连接实现事件接收和 API 调用"""

    def __init__(self, host: str = "127.0.0.1", port: int = 3001, access_token: str = ""):
        self.ws_url = f"ws://{host}:{port}"
        self._access_token = access_token
        if access_token:
            self.ws_url += f"?access_token={access_token}"
        self._ws = None
        self._running = False
        self._pending: dict[str, asyncio.Future] = {}  # echo -> future
        self._on_message: Callable | None = None
        self._on_notice: Callable | None = None
        self._ready = asyncio.Event()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    def set_message_handler(self, handler: Callable):
        self._on_message = handler

    def set_notice_handler(self, handler: Callable):
        self._on_notice = handler

    async def start(self):
        self._running = True
        retry_delay = 1
        while self._running:
            try:
                logger.info(f"连接 NapCat WebSocket: {self.ws_url}")
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
                    retry_delay = 1
                    logger.info("NapCat WebSocket 已连接")
                    await self._listen(ws)
            except (OSError, websockets.WebSocketException) as e:
                self._connected = False
                self._ready.clear()
                logger.warning(f"WebSocket 断开: {e} — {retry_delay}s 后重连")
                # 失败所有等待中的请求
                for echo, fut in self._pending.items():
                    if not fut.done():
                        fut.set_exception(ConnectionError("WebSocket 已断开"))
                self._pending.clear()
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    async def stop(self):
        self._running = False
        self._connected = False
        if self._ws:
            await self._ws.close()

    async def wait_ready(self, timeout: float = 30.0):
        """等待连接就绪"""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            raise ConnectionError(f"NapCat WebSocket 连接超时 ({timeout}s)")

    # ------------------------------------------------------------------
    # API 调用 (通过 WebSocket)
    # ------------------------------------------------------------------

    async def _call(self, action: str, params: dict | None = None, timeout: float = 60.0) -> Any:
        """通过 WebSocket 发送 OneBot API 请求并等待响应"""
        if not self._ws:
            raise ConnectionError("WebSocket 未连接")

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
            result = await asyncio.wait_for(fut, timeout)
            if result.get("status") == "failed":
                raise RuntimeError(f"API 调用失败 [{action}]: {result.get('message', result)}")
            return result.get("data", result)
        except asyncio.TimeoutError:
            raise TimeoutError(f"API 调用超时 [{action}] ({timeout}s)")
        finally:
            self._pending.pop(echo, None)

    # --- 消息发送 ---
    async def send_msg(self, message_type: str, target_id: str, message: str) -> str:
        """统一发送接口，使用通用 send_msg API"""
        params = {"message_type": message_type, "message": message}
        if message_type == "private":
            params["user_id"] = str(target_id)
        else:
            params["group_id"] = str(target_id)
        data = await self._call("send_msg", params)
        return str(data.get("message_id", ""))

    async def send_image(self, message_type: str, target_id: str, image_url: str, summary: str = "") -> str:
        """发送图片：下载 URL → 本地临时文件 → [CQ:image,file=...]"""
        import tempfile, os
        try:
            img_data = await self.download_image(image_url)
            if not img_data:
                raise RuntimeError("下载图片失败")

            suffix = os.path.splitext(image_url.split("?")[0])[1] or ".png"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(img_data)
                tmp_path = f.name

            cq = f"[CQ:image,file=file://{tmp_path}]"
            msg = cq if not summary else f"{summary}\n{cq}"
            msg_id = await self.send_msg(message_type, target_id, msg)
            return msg_id
        except Exception as e:
            logger.warning(f"发送图片失败: {e}")
            if summary:
                return await self.send_msg(message_type, target_id, f"{summary}\n[图片发送失败]")
            raise

    # --- 图片下载 ---
    async def get_image_path(self, file_id: str) -> str:
        """通过 NapCat get_image API 获取图片本地缓存路径"""
        data = await self._call("get_image", {"file": file_id})
        return str(data.get("file", "")) if isinstance(data, dict) else str(data)

    async def download_image(self, url: str) -> bytes | None:
        """通过 NapCat 会话下载 QQ 图片（解决 CDN 鉴权问题）"""
        import aiohttp
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://multimedia.nt.qq.com.cn/",
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    logger.warning(f"下载图片失败: HTTP {resp.status} ({url[:60]}...)")
                    return None
        except Exception as e:
            logger.warning(f"下载图片异常: {e}")
            return None

    async def upload_file(self, message_type: str, target_id: str, file_path: str, file_name: str) -> bool:
        """上传文件到群聊或私聊"""
        if message_type == "private":
            return await self._call("upload_private_file", {
                "user_id": str(target_id),
                "file": file_path,
                "name": file_name,
            })
        else:
            return await self._call("upload_group_file", {
                "group_id": str(target_id),
                "file": file_path,
                "name": file_name,
            })

    # --- 消息获取 ---
    async def get_msg(self, message_id: str) -> dict:
        return await self._call("get_msg", {"message_id": str(message_id)})

    # --- 群/好友列表 ---
    async def get_group_list(self) -> list[dict]:
        return await self._call("get_group_list")

    async def get_group_member_list(self, group_id: str) -> list[dict]:
        return await self._call("get_group_member_list", {"group_id": str(group_id)})

    async def get_friend_list(self) -> list[dict]:
        return await self._call("get_friend_list")

    # --- 群信息 ---
    async def get_group_info(self, group_id: str) -> dict:
        return await self._call("_get_group_info", {"group_id": str(group_id)})

    # --- 用户信息 ---
    async def get_stranger_info(self, user_id: str) -> dict:
        return await self._call("get_stranger_info", {"user_id": str(user_id)})

    # --- 撤回消息 ---
    async def delete_msg(self, message_id: str):
        await self._call("delete_msg", {"message_id": str(message_id)})

    # --- 状态 ---
    async def get_login_info(self) -> dict:
        return await self._call("get_login_info")

    # --- 最近联系人 ---
    async def get_recent_contact(self, count: int = 20) -> list[dict]:
        return await self._call("get_recent_contact", {"count": count})

    # --- 聊天记录 ---
    async def get_friend_msg_history(self, user_id: str, count: int = 20) -> dict:
        return await self._call("get_friend_msg_history", {"user_id": str(user_id), "count": count})

    async def get_group_msg_history(self, group_id: str, count: int = 20) -> dict:
        return await self._call("get_group_msg_history", {"group_id": str(group_id), "count": count})

    # ------------------------------------------------------------------
    # 消息监听 & 分发
    # ------------------------------------------------------------------

    async def _listen(self, ws):
        async for raw in ws:
            try:
                data = json.loads(raw)
                # API 响应 (有 echo 字段)
                if "echo" in data:
                    echo = data["echo"]
                    if echo in self._pending:
                        self._pending[echo].set_result(data)
                else:
                    await self._dispatch(data)
            except json.JSONDecodeError:
                logger.warning(f"无法解析 WS 消息: {raw[:200]}")

    async def _dispatch(self, data: dict):
        post_type = data.get("post_type", "")
        if post_type == "message":
            msg_type = data.get("message_type", "")
            sender = data.get("sender", {})
            message = data.get("message", [])
            text = self._extract_text(message)
            raw_text = data.get("raw_message", text)
            image_files = self._extract_image_files(message)
            file_infos = self._extract_file_infos(message)

            msg = QQMessage(
                message_id=str(data.get("message_id", "")),
                message_type=msg_type,
                sender_id=str(sender.get("user_id", "")),
                sender_name=sender.get("nickname", sender.get("card", "")),
                group_id=str(data.get("group_id", "")) if msg_type == "group" else "",
                text=raw_text,
                timestamp=data.get("time", 0) * 1000,
                raw=data,
                image_files=image_files,
                file_infos=file_infos,
            )
            logger.info(msg.format_for_ai())
            if self._on_message:
                await self._on_message(msg)
        elif post_type == "notice":
            if self._on_notice:
                await self._on_notice(data)

    @staticmethod
    def _extract_text(message: list | str) -> str:
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
        return "".join(parts)

    @staticmethod
    def _extract_image_files(message: list | str) -> list[str]:
        """从消息段中提取所有图片的 NapCat file ID"""
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
        """从消息段中提取所有文件信息 [{url, name, size}]"""
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
                        "size": d.get("size", ""),
                    })
        return infos


# ---------------------------------------------------------------------------
# 消息缓冲区
# ---------------------------------------------------------------------------


class MessageBuffer:
    def __init__(self, max_size: int = 200):
        self.max_size = max_size
        self._buffers: dict[str, deque] = {}
        self._global: deque[QQMessage] = deque(maxlen=max_size)

    def add(self, msg: QQMessage):
        key = f"{msg.message_type}:{msg.group_id if msg.message_type == 'group' else msg.sender_id}"
        self._buffers.setdefault(key, deque(maxlen=self.max_size)).append(msg)
        self._global.append(msg)

    def get_recent(self, target: str = "", count: int = 20) -> list[QQMessage]:
        if target and target in self._buffers:
            return list(self._buffers[target])[-count:]
        return list(self._global)[-count:]

    def get_all_targets(self) -> list[str]:
        return list(self._buffers.keys())
