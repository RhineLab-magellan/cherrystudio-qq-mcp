"""
CherryStudio 模块 (CherryStudioModule)

职责:
1. 通过 MCP STDIO 接收消息
2. 通过 HTTP API 发送消息/管理会话
3. 会话管理 (按群/私聊独立任务)
4. Agent 管理
5. LLM 回退链
6. Vision/File 处理
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from datetime import datetime
import uuid
import subprocess
import re

import aiohttp

from protocols.messages import (
    ParsedMessage,
    ModuleResponse,
    MessageSource,
    MessageType,
    OutgoingMessage,
)
from protocols.error_codes import ErrorCode, BridgeError
from state.manager import StateManager
from modules.conversation_store import ConversationStore
from modules.sse_parser import SSEParser, SSEResult, CherryDebugLogger
from modules.commands.utils import load_bot_setting, format_msg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DIAG 日志过滤器 — 受 cherry_debug 配置控制
# 当 cherry_debug=false 时，静默所有包含 [DIAG] 标记的日志输出
# ---------------------------------------------------------------------------
class _DiagFilter(logging.Filter):
    """当 debug_enabled=False 时，过滤掉所有包含 [DIAG] 标记的日志记录"""

    def __init__(self, debug_enabled: bool = True):
        super().__init__()
        self._enabled = debug_enabled

    def filter(self, record: logging.LogRecord) -> bool:
        if self._enabled:
            return True
        return "[DIAG]" not in record.getMessage()


# 过期会话摘要 Prompt (移植自旧项目 auto_reply.py)
SUMMARY_PROMPT = """请用简洁的要点形式总结以下 QQ 聊天记录（不超过 300 字）。
保留：对话参与者是谁、讨论了什么话题、有什么重要信息或约定。
抛弃：无意义的寒暄、重复内容。

聊天记录：
{log}"""

class MCPClient:
    """
    MCP (Model Context Protocol) 客户端

    通过 STDIO 与 CherryStudio MCP Server 通信，使用 JSON-RPC 2.0 协议。
    """

    def __init__(self, server_path: str | None = None):
        self.server_path = server_path
        self._process: asyncio.subprocess.Process | None = None
        self._connected = False
        self._request_id = 0
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None

    async def connect(self):
        """连接到 MCP Server"""
        if not self.server_path:
            logger.warning("MCP Server path not configured, skipping connection")
            return

        try:
            # 启动 MCP Server 子进程
            logger.info(f"Starting MCP Server: {self.server_path}")
            self._process = await asyncio.create_subprocess_exec(
                self.server_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # 启动后台读取任务
            self._reader_task = asyncio.create_task(self._read_loop())

            # 等待初始化完成（发送 initialize 请求）
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "qq-mcp-bridge",
                    "version": "1.0.0"
                }
            }, timeout=10.0)

            if init_result:
                self._connected = True
                logger.info(f"MCP Client connected: {self.server_path}")
                # 发送 initialized 通知
                await self._send_notification("initialized", {})
            else:
                raise BridgeError(ErrorCode.NAPCAT_CONNECTION_FAILED)

        except FileNotFoundError:
            logger.error(f"MCP Server executable not found: {self.server_path}")
            self._connected = False
            raise BridgeError(ErrorCode.NAPCAT_CONNECTION_FAILED)
        except Exception as e:
            logger.error(f"MCP connection failed: {e}", exc_info=True)
            self._connected = False
            if self._process:
                self._process.terminate()
                self._process = None
            raise

    async def _read_loop(self):
        """后台读取 MCP Server 的响应"""
        if not self._process or not self._process.stdout:
            return

        try:
            while self._connected:
                line = await self._process.stdout.readline()
                if not line:
                    # EOF，进程退出
                    logger.warning("MCP Server process exited")
                    break

                try:
                    response = json.loads(line.decode('utf-8'))
                    await self._handle_response(response)
                except json.JSONDecodeError as e:
                    logger.warning(f"MCP response parse failed: {e}")
                except Exception as e:
                    logger.error(f"MCP response handling error: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("MCP read task cancelled")
        except Exception as e:
            logger.error(f"MCP read loop error: {e}", exc_info=True)

    async def _handle_response(self, response: dict):
        """处理 MCP 响应"""
        # JSON-RPC 响应格式: {"jsonrpc": "2.0", "id": <id>, "result": ...}
        # 或者通知: {"jsonrpc": "2.0", "method": "...", "params": ...}

        request_id = response.get("id")
        if request_id is not None and request_id in self._pending_requests:
            future = self._pending_requests.pop(request_id)
            if "error" in response:
                error = response["error"]
                future.set_exception(
                    Exception(f"MCP Error: {error.get('message', 'Unknown')}")
                )
            else:
                future.set_result(response.get("result"))
        elif "method" in response:
            # 这是来自服务器的通知
            method = response["method"]
            logger.debug(f"Received MCP notification: {method}")
            # TODO: 处理服务器通知（如工具调用等）

    async def _send_request(self, method: str, params: dict, timeout: float = 30.0) -> dict | None:
        """发送 JSON-RPC 请求"""
        if not self._process or not self._process.stdin:
            return None

        self._request_id += 1
        request_id = str(self._request_id)

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }

        # 创建 Future 等待响应
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = future

        try:
            # 发送请求
            request_json = json.dumps(request) + "\n"
            self._process.stdin.write(request_json.encode('utf-8'))
            await self._process.stdin.drain()

            # 等待响应
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"MCP request timeout: {method}")
            self._pending_requests.pop(request_id, None)
            return None
        except Exception as e:
            logger.error(f"MCP request failed: {e}", exc_info=True)
            self._pending_requests.pop(request_id, None)
            return None

    async def _send_notification(self, method: str, params: dict):
        """发送 JSON-RPC 通知（不需要响应）"""
        if not self._process or not self._process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }

        try:
            notification_json = json.dumps(notification) + "\n"
            self._process.stdin.write(notification_json.encode('utf-8'))
            await self._process.stdin.drain()
        except Exception as e:
            logger.error(f"MCP notification send failed: {e}", exc_info=True)

    async def disconnect(self):
        """断开连接"""
        self._connected = False

        # 取消读取任务
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # 终止子进程
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            finally:
                self._process = None

        # 清理待处理请求
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

    async def send_message(self, message: dict) -> dict | None:
        """
        发送消息到 MCP Server

        Args:
            message: 消息字典，应包含 session_id, content 等字段

        Returns:
            响应字典，超时或失败返回 None
        """
        if not self._connected:
            return None

        try:
            # 使用 tools/call 或直接发送消息（取决于 MCP Server 的实现）
            # 这里假设有一个 chat.sendMessage 方法
            result = await self._send_request("chat.sendMessage", {
                "sessionId": message.get("session_id"),
                "content": message.get("content"),
                "metadata": message.get("metadata", {})
            }, timeout=60.0)

            return result
        except Exception as e:
            logger.error(f"MCP message send failed: {e}", exc_info=True)
            return None

    @property
    def is_connected(self) -> bool:
        return self._connected


class LLMProviderChain:
    """
    LLM Provider 回退链

    支持多个 LLM Provider，失败时自动切换到下一个
    """

    def __init__(self, providers: list[dict], default_index: int = 0):
        """
        初始化回退链

        Args:
            providers: Provider 配置列表
            default_index: 默认使用的 Provider 索引
        """
        self.providers = providers
        self.current_index = default_index if providers else 0
        self.failure_count = 0
        self.max_failures_before_switch = 3
        self._http_session: aiohttp.ClientSession | None = None

        # ---- 3D.4: 管理员通知 (Provider 切换时私信通知, 1小时冷却) ----
        self._on_switch_callback: Callable[[str, str], Any] | None = None
        self._last_switch_notify_time: float = 0.0
        self._switch_cooldown_seconds: float = 3600.0  # 1 小时

    async def initialize(self):
        """初始化 HTTP 会话"""
        self._http_session = aiohttp.ClientSession()

    async def close(self):
        """关闭 HTTP 会话"""
        if self._http_session:
            await self._http_session.close()

    def _switch_to_next_provider(self):
        """切换到下一个 Provider，并触发管理员通知回调 (1小时冷却)"""
        if len(self.providers) <= 1:
            return  # 只有一个 Provider，无法切换

        old_index = self.current_index
        self.current_index = (self.current_index + 1) % len(self.providers)
        self.failure_count = 0
        old_name = self.providers[old_index].get('name', 'unknown')
        new_name = self.providers[self.current_index].get('name', 'unknown')
        logger.info(f"LLM Provider switched: {old_name} -> {new_name}")

        # ---- 3D.4: 管理员通知 (1小时冷却) ----
        now = time.monotonic()
        if (
            self._on_switch_callback
            and (now - self._last_switch_notify_time) >= self._switch_cooldown_seconds
        ):
            self._last_switch_notify_time = now
            try:
                self._on_switch_callback(old_name, new_name)
            except Exception as e:
                logger.warning(f"Provider switch notification callback failed: {e}")

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        api_format: str = "openai",
        timeout: int = 60,
    ) -> str | None:
        """
        调用 LLM API（带自动回退）

        Args:
            messages: 消息列表
            model: 模型名称
            api_format: API 格式 (openai/anthropic)
            timeout: 超时时间（秒）

        Returns:
            AI 回复文本，失败返回 None
        """
        if not self.providers:
            logger.error("No LLM Provider configured")
            return None

        max_retries = len(self.providers) * 2  # 每个 Provider 最多重试 2 次

        for attempt in range(max_retries):
            provider = self.providers[self.current_index]
            try:
                response = await self._call_provider(provider, messages, model, api_format, timeout)
                if response:
                    # 成功，重置失败计数
                    self.failure_count = 0
                    return response

            except QuotaError:
                # 配额错误，立即切换
                logger.warning(
                    f"Provider [{provider.get('name')}] quota exceeded, switching to next")
                self._switch_to_next_provider()

            except APIError as e:
                self.failure_count += 1
                logger.warning(
                    f"Provider [{provider.get('name')}] call failed ({self.failure_count}/{self.max_failures_before_switch}): {e}"
                )

                if self.failure_count >= self.max_failures_before_switch:
                    logger.warning(f"Max failures reached, switching to next Provider")
                    self._switch_to_next_provider()

            except Exception as e:
                logger.error(f"LLM call unknown error: {e}", exc_info=True)
                break

        logger.error("All LLM Provider calls failed")
        return None

    async def _call_provider(
        self,
        provider: dict,
        messages: list[dict],
        model: str,
        api_format: str,
        timeout: int,
    ) -> str | None:
        """
        调用单个 Provider

        Raises:
            QuotaError: 配额不足
            APIError: API 调用错误
        """
        if not self._http_session:
            raise RuntimeError("LLMProviderChain 未初始化")

        # 兼容两种配置格式:
        #   - 标准格式: base_url (API 根地址, 代码拼接端点路径)
        #   - 旧配置格式: api_url (完整端点 URL, 直接使用)
        base_url = provider.get("base_url", "").rstrip("/")
        _url_is_full_endpoint = False
        if not base_url:
            api_url = provider.get("api_url", "").rstrip("/")
            if api_url:
                base_url = api_url
                _url_is_full_endpoint = True
        api_key = provider.get("api_key", "")
        provider_name = provider.get("name", "unknown")

        if not base_url or not api_key:
            raise APIError(f"Provider [{provider_name}] 配置不完整")

        # 构建请求
        if api_format == "anthropic":
            url = base_url if _url_is_full_endpoint else f"{base_url}/v1/messages"
            headers = {
                "x-api-key": api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": 4096,
            }
        else:  # openai 格式
            url = base_url if _url_is_full_endpoint else f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "messages": messages,
                "temperature": 0.7,
            }

        try:
            async with self._http_session.post(
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # ---- 3D.2 增强: 在 200 响应中检测配额关键词 ----
                    #    (部分 API 返回 200 但 body 中包含错误信息)
                    resp_text = json.dumps(data, ensure_ascii=False).lower()
                    if self._is_quota_exceeded_text(resp_text):
                        raise QuotaError(f"响应体中包含配额关键词: {resp_text[:200]}")
                    return self._extract_text(data, api_format)
                elif resp.status in (429, 402):
                    # 配额错误 (429 Too Many Requests / 402 Payment Required)
                    try:
                        error_data = await resp.json()
                    except Exception:
                        error_data = await resp.text()
                    raise QuotaError(f"配额不足 (HTTP {resp.status}): {error_data}")
                else:
                    error_text = await resp.text()
                    # ---- 3D.2 增强: 非 200/429/402 响应中检测配额关键词 ----
                    if self._is_quota_exceeded_text(error_text.lower()):
                        raise QuotaError(
                            f"响应体中包含配额关键词 (HTTP {resp.status}): {error_text[:200]}"
                        )
                    raise APIError(f"HTTP {resp.status}: {error_text[:200]}")

        except asyncio.TimeoutError:
            raise APIError(f"API 调用超时 ({timeout}s)")
        except aiohttp.ClientError as e:
            raise APIError(f"网络错误: {e}")

    @staticmethod
    def _extract_text(response: dict, api_format: str) -> str | None:
        """从 API 响应中提取文本"""
        if api_format == "anthropic":
            # Anthropic 格式
            content = response.get("content", [])
            if isinstance(content, list) and content:
                return content[0].get("text", "")
            return ""
        else:
            # OpenAI 格式
            choices = response.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""

    @staticmethod
    def _is_quota_exceeded_text(text: str) -> bool:
        """
        检测响应文本中是否包含配额耗尽关键词。

        用于在 HTTP 200 或非标准状态码的响应体中识别配额问题。
        """
        if not text:
            return False
        text_lower = text.lower()
        return any(kw in text_lower for kw in _QUOTA_KEYWORDS)


class QuotaError(Exception):
    """配额不足错误"""
    pass


class APIError(Exception):
    """API 调用错误"""
    pass


# 配额耗尽关键词 (用于在响应体中识别配额问题，即使 HTTP 状态码不是 429/402)
_QUOTA_KEYWORDS = (
    "rate limit", "rate_limit", "ratelimit",
    "quota", "quota exceeded", "quota_exceeded",
    "insufficient", "balance", "credits exhausted",
    "billing", "payment required", "limit reached",
    "too many requests", "exceeded your quota",
    "usage limit", "daily limit", "monthly limit",
)


class VisionProviderChain:
    """
    Vision Provider 回退链

    支持多个视觉模型 Provider，用于图片识别
    """

    def __init__(self, providers: list[dict], default_index: int = 0):
        """
        初始化 Vision 回退链

        Args:
            providers: Vision Provider 配置列表
            default_index: 默认使用的 Provider 索引
        """
        self.providers = providers
        self.current_index = default_index if providers else 0
        self.failure_count = 0
        self.max_failures_before_switch = 3
        self._http_session: aiohttp.ClientSession | None = None

    async def initialize(self):
        """初始化 HTTP 会话"""
        self._http_session = aiohttp.ClientSession()

    async def close(self):
        """关闭 HTTP 会话"""
        if self._http_session:
            await self._http_session.close()

    def _switch_to_next_provider(self):
        """切换到下一个 Provider"""
        if len(self.providers) <= 1:
            return

        self.current_index = (self.current_index + 1) % len(self.providers)
        self.failure_count = 0
        logger.info(
            f"Vision Provider switched to: {self.providers[self.current_index].get('name', 'unknown')}")

    async def recognize_image(
        self,
        image_urls: list[str],
        user_question: str = "",
        timeout: int = 60,
    ) -> str | None:
        """
        识别图片内容（带自动回退）

        Args:
            image_urls: 图片 URL 列表
            user_question: 用户对图片的提问（可选）
            timeout: 超时时间（秒）

        Returns:
            图片描述文本，失败返回 None
        """
        if not self.providers:
            logger.error("No Vision Provider configured")
            return None

        if not image_urls:
            return ""

        max_retries = len(self.providers) * 2

        for attempt in range(max_retries):
            provider = self.providers[self.current_index]
            try:
                response = await self._call_vision_provider(provider, image_urls, user_question, timeout)
                if response:
                    self.failure_count = 0
                    return response

            except QuotaError:
                logger.warning(
                    f"Vision Provider [{provider.get('name')}] quota exceeded, switching to next")
                self._switch_to_next_provider()

            except APIError as e:
                self.failure_count += 1
                logger.warning(
                    f"Vision Provider [{provider.get('name')}] call failed ({self.failure_count}/{self.max_failures_before_switch}): {e}"
                )

                if self.failure_count >= self.max_failures_before_switch:
                    logger.warning("Max failures reached, switching to next Vision Provider")
                    self._switch_to_next_provider()

            except Exception as e:
                logger.error(f"Vision call unknown error: {e}", exc_info=True)
                break

        logger.error("All Vision Provider calls failed")
        return None

    async def _call_vision_provider(
        self,
        provider: dict,
        image_urls: list[str],
        user_question: str,
        timeout: int,
    ) -> str | None:
        """
        调用单个 Vision Provider

        Raises:
            QuotaError: 配额不足
            APIError: API 调用错误
        """
        if not self._http_session:
            raise RuntimeError("VisionProviderChain 未初始化")

        # 兼容两种配置格式:
        #   - 标准格式: base_url (API 根地址, 代码拼接端点路径)
        #   - 旧配置格式: api_url (完整端点 URL, 直接使用)
        base_url = provider.get("base_url", "").rstrip("/")
        _url_is_full_endpoint = False
        if not base_url:
            api_url = provider.get("api_url", "").rstrip("/")
            if api_url:
                base_url = api_url
                _url_is_full_endpoint = True
        api_key = provider.get("api_key", "")
        # 兼容两种模型配置格式:
        #   - model: "gpt-4" (字符串, 直接使用)
        #   - models: ["gpt-4", ...] (数组, 取第一个元素)
        model = provider.get("model", "")
        if not model:
            models_list = provider.get("models", [])
            if models_list and isinstance(models_list, list):
                model = models_list[0]
        api_format = provider.get("api_format", "openai")
        provider_name = provider.get("name", "unknown")

        if not base_url or not api_key or not model:
            raise APIError(f"Vision Provider [{provider_name}] 配置不完整")

        # 下载并编码图片
        images_data = []
        for url in image_urls:
            try:
                image_bytes = await self._download_image(url)
                if image_bytes:
                    base64_data = base64.b64encode(image_bytes).decode("utf-8")
                    mime_type = self._detect_mime_type(url)
                    images_data.append({
                        "base64": base64_data,
                        "mime_type": mime_type,
                    })
            except Exception as e:
                logger.warning(f"Image download failed {url}: {e}")

        if not images_data:
            raise APIError("无法下载任何图片")

        # 构建消息
        prompt = user_question or "请详细描述这张图片的内容"
        messages = self._build_vision_messages(prompt, images_data, api_format)

        # 调用 API — 如果 base_url 来自 api_url (完整端点), 直接使用, 不再拼接路径
        if api_format == "anthropic":
            url = base_url if _url_is_full_endpoint else f"{base_url}/v1/messages"
            headers = {
                "x-api-key": api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": 4096,
            }
        else:  # openai 格式
            url = base_url if _url_is_full_endpoint else f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": model,
                "messages": messages,
                "temperature": 0.7,
            }

        # 诊断日志: 记录 Vision API 实际调用参数
        logger.info(
            f"Vision API call: provider={provider_name} | model={model} | "
            f"url={url} | full_endpoint={_url_is_full_endpoint} | "
            f"images={len(images_data)} | format={api_format}"
        )

        try:
            async with self._http_session.post(
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # 在 200 响应中检测配额关键词
                    resp_text = json.dumps(data, ensure_ascii=False).lower()
                    if LLMProviderChain._is_quota_exceeded_text(resp_text):
                        raise QuotaError(f"响应体中包含配额关键词: {resp_text[:200]}")
                    return self._extract_text(data, api_format)
                elif resp.status in (429, 402):
                    try:
                        error_data = await resp.json()
                    except Exception:
                        error_data = await resp.text()
                    raise QuotaError(f"配额不足 (HTTP {resp.status}): {error_data}")
                else:
                    error_text = await resp.text()
                    if LLMProviderChain._is_quota_exceeded_text(error_text.lower()):
                        raise QuotaError(
                            f"响应体中包含配额关键词 (HTTP {resp.status}): {error_text[:200]}"
                        )
                    raise APIError(f"HTTP {resp.status}: {error_text[:200]}")

        except asyncio.TimeoutError:
            raise APIError(f"Vision API 调用超时 ({timeout}s)")
        except aiohttp.ClientError as e:
            raise APIError(f"网络错误: {e}")

    async def _download_image(self, url: str) -> bytes | None:
        """下载图片（支持 HTTP URL 和 file:// 本地路径）"""
        # ---- 本地文件路径 (file://...) ----
        if url.startswith("file://"):
            local_path = url[7:]  # 去掉 file:// 前缀
            # Windows 路径形如 file:///C:/xxx -> /C:/xxx -> C:/xxx
            if len(local_path) >= 3 and local_path[0] == "/" and local_path[2] == ":":
                local_path = local_path[1:]
            try:
                with open(local_path, "rb") as f:
                    return f.read()
            except Exception as e:
                logger.warning(f"Local image read failed ({local_path}): {e}")
                return None

        # ---- HTTP / HTTPS URL ----
        try:
            async with self._http_session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                return None
        except Exception as e:
            logger.warning(f"Image download failed: {e}")
            return None

    @staticmethod
    def _detect_mime_type(url: str) -> str:
        """根据 URL 推断 MIME 类型"""
        url_lower = url.lower()
        if url_lower.endswith(".png"):
            return "image/png"
        elif url_lower.endswith(".jpg") or url_lower.endswith(".jpeg"):
            return "image/jpeg"
        elif url_lower.endswith(".gif"):
            return "image/gif"
        elif url_lower.endswith(".webp"):
            return "image/webp"
        else:
            return "image/jpeg"  # 默认

    def _build_vision_messages(
        self,
        prompt: str,
        images_data: list[dict],
        api_format: str,
    ) -> list[dict]:
        """构建 Vision API 消息"""
        if api_format == "anthropic":
            # Anthropic 格式
            content = [{"type": "text", "text": prompt}]
            for img in images_data:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["mime_type"],
                        "data": img["base64"],
                    },
                })
            return [{"role": "user", "content": content}]
        else:
            # OpenAI 格式
            content = [{"type": "text", "text": prompt}]
            for img in images_data:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img['mime_type']};base64,{img['base64']}",
                    },
                })
            return [{"role": "user", "content": content}]

    @staticmethod
    def _extract_text(response: dict, api_format: str) -> str | None:
        """从 Vision API 响应中提取文本"""
        if api_format == "anthropic":
            content = response.get("content", [])
            if isinstance(content, list) and content:
                return content[0].get("text", "")
            return ""
        else:
            choices = response.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""


class FileProcessor:
    """
    文件处理器 (MinerU)

    用于通过 MinerU 解析 PDF/Word 等文件，提取文本内容并生成摘要
    """

    def __init__(self, config: dict):
        """
        初始化文件处理器

        Args:
            config: 文件处理配置
        """
        self.mineru_command = config.get("mineru_command", "mineru-open-api")
        self.max_file_size_mb = config.get("max_file_size_mb", 10)
        self.summary_max_chars = config.get("summary_max_chars", 2000)
        self._http_session: aiohttp.ClientSession | None = None

    async def initialize(self):
        """初始化 HTTP 会话"""
        self._http_session = aiohttp.ClientSession()

    async def close(self):
        """关闭 HTTP 会话"""
        if self._http_session:
            await self._http_session.close()

    async def process_file(self, file_url: str) -> str | None:
        """
        下载文件并通过 MinerU 提取内容摘要

        Args:
            file_url: 文件 URL

        Returns:
            提取的文本摘要，失败返回 None
        """
        if not self._http_session:
            raise RuntimeError("FileProcessor 未初始化")

        temp_path = None
        try:
            # 1. 检查文件大小
            file_size = await self._get_file_size(file_url)
            if file_size > self.max_file_size_mb * 1024 * 1024:
                logger.warning(
                    f"File size exceeds limit: {file_size / (1024*1024):.2f}MB > {self.max_file_size_mb}MB")
                return None

            # 2. 下载文件到临时目录
            temp_path = await self._download_file(file_url)
            if not temp_path:
                logger.error("File download failed")
                return None

            # 3. 调用 MinerU 提取内容
            extracted_text = await self._call_mineru(temp_path)
            if not extracted_text:
                logger.error("MinerU extraction failed")
                return None

            # 4. 生成摘要（截断到最大字符数）
            summary = extracted_text[:self.summary_max_chars]
            if len(extracted_text) > self.summary_max_chars:
                summary += "\n...(内容已截断)"

            logger.info(
                f"File processed successfully, extracted {len(extracted_text)} chars, summary {len(summary)} chars")
            return summary

        except Exception as e:
            logger.error(f"File processing error: {e}", exc_info=True)
            return None
        finally:
            # 5. 清理临时文件
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as e:
                    logger.warning(f"Temp file cleanup failed: {e}")

    async def _get_file_size(self, url: str) -> int:
        """获取文件大小（字节）"""
        try:
            async with self._http_session.head(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    return int(content_length)
                return 0
        except Exception as e:
            logger.warning(f"Failed to get file size: {e}")
            return 0

    async def _download_file(self, url: str) -> str | None:
        """下载文件到临时目录"""
        try:
            # 创建临时文件
            suffix = self._get_file_suffix(url)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_path = tmp.name

            # 下载文件
            async with self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    logger.error(f"File download failed: HTTP {resp.status}")
                    return None

                with open(temp_path, "wb") as f:
                    while True:
                        chunk = await resp.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)

            logger.info(f"File downloaded successfully: {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"File download failed: {e}", exc_info=True)
            return None

    async def _call_mineru(self, file_path: str) -> str | None:
        """
        调用 MinerU 命令行工具提取文本

        Args:
            file_path: 文件路径

        Returns:
            提取的文本内容
        """
        try:
            # 构建命令
            cmd = [
                self.mineru_command,
                "flash-extract",
                file_path,
            ]

            logger.info(f"Executing MinerU command: {' '.join(cmd)}")

            # 执行命令
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(
                    f"MinerU execution failed (returncode={process.returncode}): {stderr.decode('utf-8', errors='ignore')[:500]}")
                return None

            # 解析输出
            text = stdout.decode("utf-8", errors="ignore")
            return text if text.strip() else None

        except FileNotFoundError:
            logger.error(f"MinerU command not found: {self.mineru_command}, please ensure it is installed")
            return None
        except Exception as e:
            logger.error(f"MinerU call exception: {e}", exc_info=True)
            return None

    @staticmethod
    def _get_file_suffix(url: str) -> str:
        """根据 URL 推断文件后缀"""
        url_lower = url.lower().split("?")[0]  # 去除查询参数
        if url_lower.endswith(".pdf"):
            return ".pdf"
        elif url_lower.endswith(".docx"):
            return ".docx"
        elif url_lower.endswith(".doc"):
            return ".doc"
        elif url_lower.endswith(".pptx"):
            return ".pptx"
        elif url_lower.endswith(".xlsx"):
            return ".xlsx"
        else:
            return ""  # 让系统自动判断


class HTTPClient:
    """
    HTTP API 客户端

    用于与 CherryStudio HTTP API 交互。
    支持两种模式：
    1. 标准模式: /sessions, /chat 等端点
    2. Agent API 模式（兼容旧项目）: /v1/agents/{id}/sessions/{sid}/messages
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8080", api_key: str = "", legacy_mode: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.legacy_mode = legacy_mode  # 是否使用旧项目的 Agent API 格式
        self._session: aiohttp.ClientSession | None = None

        # Agent API 相关配置
        self.agent_id: str | None = None  # 从配置中获取或使用默认值
        self.default_model: str = ""  # 默认模型 (provider:model_id 格式)
        self._model_map: dict[str, str] = {}  # 短名 -> provider:model_id 缓存

    async def initialize(self):
        """初始化 HTTP 会话并验证连通性"""
        has_auth = bool(self.api_key)
        logger.info(
            f"[DIAG] HTTPClient initializing: "
            f"base_url={self.base_url} | "
            f"legacy_mode={self.legacy_mode} | "
            f"has_api_key={has_auth}"
        )
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                "Content-Type": "application/json",
            }
        )

        # 验证 API 连通性
        try:
            health_url = f"{self.base_url}/health"
            logger.info(f"[DIAG] Health check >>> GET {health_url}")
            async with self._session.get(
                health_url,
                timeout=aiohttp.ClientTimeout(total=5.0)
            ) as resp:
                body = await resp.text()
                resp_headers = dict(resp.headers) if resp.headers else {}
                logger.info(
                    f"[DIAG] Health check <<< "
                    f"HTTP {resp.status} | "
                    f"body={body[:200]} | "
                    f"server={resp_headers.get('Server', 'N/A')}"
                )
                if resp.status != 200:
                    logger.warning(
                        f"HTTP API health check returned {resp.status}, "
                        f"will continue. Body: {body[:300]}"
                    )
        except asyncio.TimeoutError:
            logger.warning(f"HTTP API health check timeout: {self.base_url}")
        except aiohttp.ClientError as e:
            logger.warning(f"HTTP API connection failed: {e}, will retry on first use")
        except Exception as e:
            logger.warning(f"HTTP API health check error: {e}")

    async def fetch_agent_id(self, agent_name: str) -> str | None:
        """
        从 CherryStudio Agent API 获取 Agent 列表，通过显示名查找内部 ID。

        内部复用 fetch_all_agents() (60s 超时)，避免重复维护相同的列表获取逻辑。

        Args:
            agent_name: Agent 显示名称 (如 "麦哲伦QQ")

        Returns:
            Agent 内部 ID (如 "agent_1780254091652_boosaiyfg")，失败则返回 None
        """
        items = await self.fetch_all_agents()
        if not items:
            logger.warning(
                f"Agent list empty or fetch failed, cannot resolve '{agent_name}'")
            return None

        # 按显示名匹配，同时打印所有可用 Agent 便于排查
        all_names: list[tuple[str, str]] = []
        for item in items:
            item_id = item.get("id", "")
            item_name = item.get("name", item_id)
            all_names.append((item_name, item_id))

        for item in items:
            item_id = item.get("id", "")
            item_name = item.get("name", item_id)
            if item_name == agent_name:
                logger.info(
                    f"Agent name resolved: '{agent_name}' -> {item_id}")
                return item_id

        # 未找到匹配项，列出所有可用 Agent
        names_str = ", ".join(f"'{n}'({i})" for n, i in all_names)
        logger.warning(
            f"Agent '{agent_name}' not found, available: {names_str}")
        return None

    async def fetch_all_agents(self) -> list[dict]:
        """
        从 CherryStudio 获取所有 Agent 列表。

        Note: CherryStudio 返回完整 Agent 列表 (含指令文本) 可能非常大
        (实测 9 个 Agent 约 82KB)，因此超时设为 120 秒。

        Returns:
            Agent 列表，每项包含 id, name, accessible_paths 等字段。
            失败返回空列表。
        """
        if not self._session:
            return []

        try:
            url = f"{self.base_url}/v1/agents"
            t0 = time.monotonic()
            async with self._session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                elapsed = time.monotonic() - t0
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(
                        f"Failed to fetch Agent list (HTTP {resp.status}): {text[:150]}")
                    return []
                data = await resp.json()
                logger.debug(f"Agent list fetched in {elapsed:.1f}s")
        except asyncio.TimeoutError:
            logger.warning("Agent list fetch timeout")
            return []
        except aiohttp.ClientError as e:
            logger.warning(f"Agent list fetch connection failed: {e}")
            return []
        except Exception as e:
            logger.warning(f"Agent list fetch error: {e}")
            return []

        items = data if isinstance(data, list) else data.get("data", [])
        return items

    async def fetch_agent_detail(self, agent_id: str) -> dict | None:
        """
        获取单个 Agent 的详细信息 (包含 mcps, accessible_paths 字段)。

        Args:
            agent_id: Agent 内部 ID

        Returns:
            Agent 详情字典，失败返回 None
        """
        if not self._session:
            return None

        try:
            url = f"{self.base_url}/v1/agents/{agent_id}"
            async with self._session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    body = await resp.text()
                    logger.debug(
                        f"Agent {agent_id} detail fetch failed (HTTP {resp.status}): "
                        f"{body[:300]}")
                    return None
        except Exception as e:
            logger.debug(f"Agent {agent_id} detail query error: {e}")
            return None

    async def fetch_mcp_servers(self) -> dict[str, dict]:
        """
        从 /v1/mcps 获取 CherryStudio 中注册的所有 MCP Server。

        Returns:
            {server_id: {name: ...}} 字典，失败返回空字典。
        """
        if not self._session:
            return {}

        try:
            url = f"{self.base_url}/v1/mcps"
            async with self._session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # 兼容多种响应格式:
                    # 1) {"data": {"servers": {...}}}  (标准格式)
                    # 2) {"data": [...]}               (列表格式)
                    # 3) [...]                          (顶层列表)
                    # 4) {"servers": {...}}             (直接 servers 键)
                    if isinstance(data, list):
                        return self._normalize_mcp_list(data)
                    inner = data.get("data", data)
                    if isinstance(inner, list):
                        return self._normalize_mcp_list(inner)
                    if isinstance(inner, dict):
                        servers = inner.get("servers", inner)
                        if isinstance(servers, dict):
                            return servers
                    # 兜底: 如果 data 本身就是 {id: {...}} 字典
                    if isinstance(data, dict) and all(
                        isinstance(v, dict) for v in data.values()
                        if isinstance(data, dict)
                    ):
                        return data
                    logger.warning(
                        f"[DIAG] MCP response format unexpected: "
                        f"type={type(data).__name__}, "
                        f"keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}, "
                        f"snippet={str(data)[:300]}"
                    )
                    return {}
                else:
                    body = await resp.text()
                    logger.warning(
                        f"[DIAG] MCP server list fetch failed "
                        f"(HTTP {resp.status}): {body[:300]}"
                    )
                    return {}
        except asyncio.TimeoutError:
            logger.warning(
                f"[DIAG] MCP server list fetch timeout: "
                f"{self.base_url}/v1/mcps"
            )
            return {}
        except Exception as e:
            logger.warning(f"[DIAG] MCP list query error: {e}")
            return {}

    @staticmethod
    def _normalize_mcp_list(items: list) -> dict[str, dict]:
        """
        将 MCP 列表格式 [{id, name, ...}, ...] 标准化为 {id: {name: ...}} 字典。
        """
        result: dict[str, dict] = {}
        for item in items:
            if isinstance(item, dict):
                sid = item.get("id", item.get("server_id", ""))
                if sid:
                    result[sid] = item
        return result

    async def resolve_model(self, model: str) -> str:
        """
        将短模型名解析为 CherryStudio 的 provider:model_id 格式。

        例如 "minimax-m2.5" -> "OpenCode:minimax-m2.5"

        通过查询 GET /v1/models 建立映射缓存。
        """
        if not model:
            return model
        # 已经是 provider:model_id 格式
        if ":" in model:
            return model
        # 从缓存查找
        if model in self._model_map:
            return self._model_map[model]

        # 查询 CherryStudio /v1/models 建立映射
        if self._session:
            try:
                url = f"{self.base_url}/v1/models"
                async with self._session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("data", [])
                        for m in items:
                            short = m.get("provider_model_id", "")
                            provider = m.get("provider", "")
                            full = f"{provider}:{short}" if provider else short
                            if short and short not in self._model_map:
                                self._model_map[short] = full
                        logger.info(
                            f"CherryStudio model list: {len(self._model_map)} models cached")
                    else:
                        body = await resp.text()
                        logger.warning(f"Model list fetch failed: HTTP {resp.status}, body: {body[:300]}")
            except Exception as e:
                logger.warning(f"Model list fetch error: {e}")

        if model in self._model_map:
            return self._model_map[model]

        # 兜底: 使用 deepseek provider
        logger.warning(f"Model '{model}' not found in CherryStudio, falling back to deepseek:{model}")
        return f"deepseek:{model}"

    async def close(self):
        """关闭 HTTP 会话"""
        if self._session:
            await self._session.close()

    async def create_session(self, agent_name: str, agent_id: str | None = None, model: str | None = None, accessible_paths: list[str] | None = None) -> str | None:
        """
        创建会话

        Args:
            agent_name: Agent 名称（用作会话名称）
            agent_id: Agent ID（可选，legacy_mode下需要）
            model: 模型名称（可选，优先使用传入值，其次 default_model）
            accessible_paths: Agent 工作区目录列表（必需，CherryStudio 要求非空）

        Returns:
            会话 ID，失败返回 None
        """
        if not self._session:
            return None

        try:
            if self.legacy_mode:
                # Agent API 模式: POST /v1/agents/{agent_id}/sessions
                actual_agent_id = agent_id or "default"
                url = f"{self.base_url}/v1/agents/{actual_agent_id}/sessions"

                actual_model = model or self.default_model
                paths = accessible_paths or []
                body = {
                    "name": agent_name,
                    "accessible_paths": paths,
                    "model": actual_model,
                }

                logger.info(
                    f"[DIAG] Session creation >>> "
                    f"POST {url} | "
                    f"agent_name={agent_name} | "
                    f"agent_id={actual_agent_id} | "
                    f"model={actual_model} | "
                    f"accessible_paths={paths}"
                )

                async with self._session.post(
                    url,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        session_id = data.get("id")
                        logger.info(
                            f"[DIAG] Session created <<< "
                            f"HTTP {resp.status} | "
                            f"session_id={session_id} | "
                            f"response_keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}"
                        )
                        return session_id
                    else:
                        text = await resp.text()
                        logger.error(
                            f"[DIAG] Session creation FAILED <<< "
                            f"HTTP {resp.status} | body: {text[:300]}")
                        return None
            else:
                # 标准模式: POST /sessions
                async with self._session.post(
                    f"{self.base_url}/sessions",
                    json={"agent": agent_name},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("session_id")
                    else:
                        body = await resp.text()
                        logger.error(f"Session creation failed: HTTP {resp.status}, body: {body[:300]}")
                        return None
        except Exception as e:
            logger.error(f"Session creation error: {e}", exc_info=True)
            return None

    async def send_chat_message(
        self,
        session_id: str,
        message: str,
        agent_id: str | None = None
    ) -> str | None:
        """
        发送聊天消息

        Args:
            session_id: 会话 ID
            message: 消息内容
            agent_id: Agent ID（legacy_mode下需要）

        Returns:
            AI 回复，失败返回 None
        """
        if not self._session:
            return None

        try:
            if self.legacy_mode:
                # Agent API 模式: POST /v1/agents/{agent_id}/sessions/{sid}/messages
                actual_agent_id = agent_id or "default"
                url = f"{self.base_url}/v1/agents/{actual_agent_id}/sessions/{session_id}/messages"

                body = {"content": message}

                async with self._session.post(
                    url,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Agent API 返回格式: {"text": "...", "content": "...", ...}
                        reply = _extract_response_text(data)
                        return reply if reply else None
                    else:
                        text = await resp.text()
                        logger.error(
                            f"Agent API message send failed: HTTP {resp.status} - {text[:200]}")
                        return None
            else:
                # 标准模式: POST /chat
                async with self._session.post(
                    f"{self.base_url}/chat",
                    json={
                        "session_id": session_id,
                        "message": message,
                    },
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("reply")
                    else:
                        body = await resp.text()
                        logger.error(f"Chat message send failed: HTTP {resp.status}, body: {body[:300]}")
                        return None
        except asyncio.TimeoutError:
            logger.warning("HTTP API call timeout")
            return None
        except Exception as e:
            logger.error(f"Message send error: {e}", exc_info=True)
            return None

    async def delete_session(self, session_id: str, agent_id: str | None = None) -> bool:
        """删除会话"""
        if not self._session:
            return False

        try:
            if self.legacy_mode:
                # Agent API 模式: DELETE /v1/agents/{agent_id}/sessions/{sid}
                actual_agent_id = agent_id or "default"
                url = f"{self.base_url}/v1/agents/{actual_agent_id}/sessions/{session_id}"

                async with self._session.delete(url) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        logger.warning(
                            f"Session deletion returned {resp.status} (legacy): {body[:300]}")
                    return resp.status in (200, 204)
            else:
                # 标准模式: DELETE /sessions/{sid}
                async with self._session.delete(
                    f"{self.base_url}/sessions/{session_id}",
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(
                            f"Session deletion returned {resp.status}: {body[:300]}")
                    return resp.status == 200
        except Exception as e:
            logger.error(f"Session deletion error: {e}", exc_info=True)
            return False

    def get_sse_request_context(
        self,
        session_id: str,
        message: str,
        agent_id: str | None = None,
        total_timeout: int = 600,
    ):
        """
        返回一个 aiohttp 请求的上下文管理器，用于 SSE 流式响应。

        调用方在 async with 中使用此方法，在上下文内通过 SSEParser 解析流。

        Returns:
            aiohttp.ClientResponse 的上下文管理器

        Raises:
            aiohttp.ClientError: HTTP session 未初始化 (_session 为 None)
        """
        if not self._session:
            raise aiohttp.ClientError(
                "HTTPClient._session is None (HTTP session not initialized or already closed) "
                "[BRG-4012]. Please check: 1) CherryStudioModule.initialize() completed "
                "2) HTTPClient was not closed"
            )

        if self.legacy_mode:
            actual_agent_id = agent_id or "default"
            url = f"{self.base_url}/v1/agents/{actual_agent_id}/sessions/{session_id}/messages"
            body = {"content": message}
        else:
            url = f"{self.base_url}/chat"
            body = {"session_id": session_id, "message": message}

        # ---- 诊断日志: 完整请求信息 ----
        msg_preview = message[:150].replace("\n", "\\n") if message else "<empty>"
        session_closed = self._session.closed if self._session else True
        logger.info(
            f"[DIAG] Agent request >>> "
            f"POST {url} | "
            f"legacy={self.legacy_mode} | "
            f"agent_id={agent_id} | "
            f"session_id={session_id} | "
            f"msg_len={len(message)} chars | "
            f"msg_preview='{msg_preview}' | "
            f"timeout={total_timeout}s | "
            f"http_session_closed={session_closed} | "
            f"base_url={self.base_url}"
        )

        timeout = aiohttp.ClientTimeout(total=total_timeout)
        return self._session.post(
            url,
            json=body,
            timeout=timeout,
            headers={"Accept": "text/event-stream"},
        )

    def _with_header_timeout(self, ctx, header_timeout: float):
        """
        为 SSE 请求的 HTTP 响应头部读取阶段添加独立的超时保护。

        正常使用 ``async with session.post(...) as resp:`` 时，aiohttp 的
        ``ClientTimeout(total=...)`` 覆盖整个请求生命周期，但无法单独约束
        头部等待时间。如果 Agent 服务接受了 TCP 连接却迟迟不返回 HTTP 响应头，
        调用方会阻塞数十秒甚至数分钟，期间 SSEParser 尚未启动，所有存活/停滞
        检测机制均无法工作。

        本方法返回一个异步上下文管理器，将 ``__aenter__()`` (发送请求 + 等待头部)
        包裹在 ``asyncio.wait_for(timeout=header_timeout)`` 中：

        - 头部在 ``header_timeout`` 秒内到达 → 正常进入 ``async with`` 块
        - 头部超时 → 抛出 ``asyncio.TimeoutError``，调用方可立即报错

        用法::

            ctx = http_client.get_sse_request_context(...)
            try:
                async with http_client._with_header_timeout(ctx, 90) as resp:
                    sse_result = await parser.parse(resp)
            except asyncio.TimeoutError:
                # 头部等待超时
        """
        return _HeaderTimeoutContext(ctx, header_timeout)


class _HeaderTimeoutContext:
    """
    异步上下文管理器：为 aiohttp 请求的头部等待阶段添加超时保护。

    将原始上下文管理器的 __aenter__() 包裹在 asyncio.wait_for() 中，
    确保头部等待不会无限阻塞。__aexit__() 阶段的清理操作不受超时约束。
    """

    __slots__ = ("_ctx", "_header_timeout", "_resp")

    def __init__(self, ctx, header_timeout: float):
        self._ctx = ctx
        self._header_timeout = header_timeout
        self._resp = None

    async def __aenter__(self):
        self._resp = await asyncio.wait_for(
            self._ctx.__aenter__(),
            timeout=self._header_timeout,
        )
        return self._resp

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._resp is not None:
            return await self._ctx.__aexit__(exc_type, exc_val, exc_tb)
        return False


def _extract_response_text(response: dict) -> str:
    """从 CherryStudio finish-step response 中提取文本"""
    if not response:
        return ""
    # response 可能包含 content/text/output 字段
    if isinstance(response, str):
        return response
    for key in ("text", "content", "output", "message"):
        val = response.get(key, "")
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict):
                    parts.append(item.get("text", "")
                                 or item.get("content", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
    return ""


class SessionData:
    """
    会话数据

    存储单个会话的状态和历史。
    """

    def __init__(self, session_key: str, agent_name: str = "default"):
        self.session_key = session_key
        self.agent_name = agent_name
        self.session_id: str | None = None
        self.created_at = datetime.now()
        self.last_active = datetime.now()
        self.message_count = 0

    def update_activity(self):
        """更新活跃时间"""
        self.last_active = datetime.now()
        self.message_count += 1

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """检查会话是否过期"""
        elapsed = (datetime.now() - self.last_active).total_seconds()
        return elapsed > (timeout_minutes * 60)


class CherryStudioSessionHandler:
    """
    CherryStudio 会话处理器

    为每个会话 (群/私聊) 创建独立的异步任务。
    """

    def __init__(
        self,
        session_key: str,
        mcp_client: MCPClient,
        http_client: HTTPClient,
        state_manager: StateManager,
        response_queue: asyncio.Queue[ModuleResponse] | None = None,
        on_cleanup: Callable | None = None,  # 清理回调
        agent_id: str | None = None,  # Agent ID（legacy_mode下需要）
        send_queue: asyncio.Queue | None = None,  # 非阻塞模式: 直接推送 OutgoingMessage
        napcat_bridge: Any = None,  # NapCatBridge 引用 (用于 mark_responding)
        config: dict | None = None,  # 配置字典 (读取 SSE/自动回复配置)
        conversation_store: ConversationStore | None = None,  # 会话持久化存储
    ):
        self.session_key = session_key
        self.mcp_client = mcp_client
        self.http_client = http_client
        self.state_manager = state_manager
        self.response_queue = response_queue
        self.on_cleanup = on_cleanup  # 会话结束时的回调
        self.agent_id = agent_id  # Agent ID
        self.parent_module = None  # 将在 CherryStudioModule 中设置
        self.send_queue = send_queue  # 非阻塞模式: 直接推送到 send_message_queue
        self.napcat_bridge = napcat_bridge  # NapCatBridge 引用
        self.config = config or {}
        self.conversation_store = conversation_store  # 会话持久化存储

        # 会话是否刚创建 (用于注入历史记忆)
        self._session_just_created = False

        # 当前会话使用的 Agent ID (可能与 self.agent_id 不同，.order 切换后)
        self._session_agent_id: str | None = None

        # SSE 配置
        bridge_config = self.config.get("bridge", {})
        self._sse_stall_max_retries = bridge_config.get("sse_stall_max_retries", 4)
        self._pre_tool_text_policy = bridge_config.get("pre_tool_text_policy", "keep")
        auto_reply_config = self.config.get("auto_reply", {})
        # agent_timeout_seconds: 停滞检测间隔 (每次 readline 等待的最大秒数)
        self._agent_timeout = self.config.get("agent_timeout_seconds", 60)
        # SSE 总超时: 基于停滞检测参数自动计算, 而非直接使用 agent_timeout
        # 公式: (停滞间隔 + 30s缓冲) × (最大重试 + 1)
        # 例: (60+30) × (4+1) = 450s, 确保停滞检测机制能完整运行
        self._sse_total_timeout = (
            (self._agent_timeout + 30) * (self._sse_stall_max_retries + 1)
        )
        self._reply_chain_depth: int = max(
            0, min(int(auto_reply_config.get("reply_chain_depth", 4)), 10)
        )
        self._receipt_notification: bool = auto_reply_config.get("receipt_notification", False)
        logger.info(
            f"SSE timeout config: stall detection interval={self._agent_timeout}s, "
            f"max stall retries={self._sse_stall_max_retries}, "
            f"calculated total timeout={self._sse_total_timeout}s"
        )

        # 消息队列
        self.message_queue: asyncio.Queue[ParsedMessage] = asyncio.Queue()

        # 会话数据
        self.session_data: SessionData | None = None

        # 运行状态
        self._running = False
        self._task: asyncio.Task | None = None

        # 超时配置 (10分钟，与旧系统一致)
        self.timeout = 600

        # 2-strike 停滞处理: 连续停滞 2 次销毁会话并重建
        self._stall_count: int = 0

    async def _header_alive_monitor(
        self, msg: ParsedMessage, target_id: str, start_time: float
    ):
        """
        SSE 头部等待阶段的存活通知 (用于 Fallback 重试)。

        在 Agent 处理期间定期向用户发送 "正在思考中" 提示，
        防止长时间无反馈导致用户困惑。
        """
        try:
            while True:
                await asyncio.sleep(self._agent_timeout)
                elapsed_s = int(time.monotonic() - start_time)
                if self.send_queue:
                    await self.send_queue.put(OutgoingMessage(
                        target_source=msg.raw.source,
                        target_id=str(target_id),
                        content=f"Agent 正在思考中……已等待 {elapsed_s}s",
                        message_type=MessageType.TEXT,
                    ))
        except asyncio.CancelledError:
            pass

    async def _rebuild_session_fallback(self) -> bool:
        """
        会话失效 Fallback: 自动删除旧索引并重建新会话。

        当 SSE 返回 session_not_found 或 HTTP 404/410 时调用。
        删除远端失效会话 → 创建新会话 → 更新 session_data + 持久化。

        Returns:
            True 表示重建成功，False 表示失败
        """
        old_sid = self.session_data.session_id if self.session_data else None
        logger.warning(
            f"[Fallback] Session expired, attempting auto-rebuild: "
            f"{self.session_key} (old_sid={old_sid})"
        )

        # 1. 清除旧 session_id
        if self.session_data:
            self.session_data.session_id = None

        # 2. 尝试删除远端失效会话 (不阻塞, 失败不影响重建)
        if old_sid and self.http_client:
            try:
                await self.http_client.delete_session(
                    old_sid, agent_id=self._session_agent_id or self.agent_id
                )
                logger.info(f"[Fallback] Stale remote session deleted: {old_sid}")
            except Exception as e:
                logger.debug(f"[Fallback] Delete stale session failed (non-blocking): {e}")

        # 3. 获取 agent_name
        agent_name = self.session_data.agent_name if self.session_data else "default"
        if not agent_name or agent_name == "default":
            agent_name = await self.state_manager.get_active_agent(self.session_key) or "default"

        # 4. 解析 agent_id
        _session_agent_id = self.agent_id
        if agent_name != "default" and self.parent_module:
            _discovered = getattr(self.parent_module, 'discovered_agents', {})
            if agent_name in _discovered:
                _resolved_id = _discovered[agent_name].get("agent_id", "")
                if _resolved_id:
                    _session_agent_id = _resolved_id

        # 5. 获取 accessible_paths
        _agent_paths: list[str] = []
        if self.parent_module and hasattr(self.parent_module, '_agent_paths_by_id'):
            _agent_paths = self.parent_module._agent_paths_by_id.get(
                _session_agent_id or "", []
            )
        if not _agent_paths and _session_agent_id:
            try:
                detail = await self.http_client.fetch_agent_detail(_session_agent_id)
                if detail:
                    _agent_paths = detail.get("accessible_paths", [])
                    if _agent_paths and self.parent_module and hasattr(self.parent_module, '_agent_paths_by_id'):
                        self.parent_module._agent_paths_by_id[_session_agent_id] = _agent_paths
            except Exception as e:
                logger.warning(f"[Fallback] Path fetch failed: {e}")
        if not _agent_paths and self.parent_module:
            _default_paths = getattr(self.parent_module, '_default_accessible_paths', [])
            if _default_paths:
                _agent_paths = _default_paths

        # 6. 构建显示名
        _src, _tid = self.session_key.split("_", 1)
        _display_name = f"Private:{_tid}" if _src == "private" else f"group:{_tid}"

        # 7. 读取持久化的模型偏好
        saved_model = await self.state_manager.get_saved_model(self.session_key)

        # 8. 创建新会话
        new_sid = await self.http_client.create_session(
            _display_name,
            agent_id=_session_agent_id,
            model=saved_model,
            accessible_paths=_agent_paths,
        )

        if new_sid:
            if not self.session_data:
                self.session_data = SessionData(
                    session_key=self.session_key,
                    agent_name=agent_name,
                )
            self.session_data.session_id = new_sid
            self._session_just_created = True
            self._session_agent_id = _session_agent_id

            # 持久化新 session_id
            if self.conversation_store:
                try:
                    await self.conversation_store.set_remote_session_id(
                        self.session_key, agent_name, new_sid
                    )
                except Exception as e:
                    logger.warning(f"[Fallback] Failed to persist new session_id: {e}")

            logger.info(
                f"[Fallback] Session rebuilt: {self.session_key} "
                f"(old={old_sid} -> new={new_sid})"
            )
            return True

        logger.error(f"[Fallback] Session rebuild FAILED: {self.session_key}")
        return False

    async def start(self):
        """启动会话处理器"""
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.debug(f"CherryStudio session handler started: {self.session_key}")

    async def stop(self):
        """停止会话处理器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # 保留远程会话以支持复用 (不再删除)
        if self.session_data and self.session_data.session_id:
            logger.info(
                f"[DIAG] Handler stopped, remote session preserved: "
                f"{self.session_data.session_id} ({self.session_key})")

        logger.info(f"[DIAG] CherryStudio session handler stopped: {self.session_key}")

    async def add_message(self, msg: ParsedMessage):
        """添加消息到队列"""
        await self.message_queue.put(msg)

    async def _run(self):
        """主循环"""
        try:
            while self._running:
                try:
                    # 等待消息 (带超时)
                    msg = await asyncio.wait_for(
                        self.message_queue.get(),
                        timeout=self.timeout
                    )

                    # ---- 诊断日志: 消息到达 ----
                    _msg_recv_time = time.monotonic()
                    logger.info(
                        f"[DIAG] Message received in _run(): "
                        f"session_key={self.session_key} | "
                        f"has_session={'yes' if (self.session_data and self.session_data.session_id) else 'no'} | "
                        f"sender={msg.raw.sender_name or msg.raw.sender_id} | "
                        f"content_preview='{msg.raw.strip_at_mentions()[:80]}'"
                    )

                    # ---- B2 修复: 等待延迟初始化完成 ----
                    # discovered_agents 由 _deferred_init() 异步填充，
                    # 若尚未完成则 agent_id 解析会使用错误的默认值。
                    # 仅在需要创建/恢复会话时等待（后续消息不受影响）。
                    if (not self.session_data or not self.session_data.session_id) \
                            and self.parent_module \
                            and not getattr(self.parent_module, '_deferred_init_done', True):
                        logger.info(
                            f"[{self.session_key}] Deferred init not complete, waiting...")
                        _wait_start = time.monotonic()
                        while not getattr(self.parent_module, '_deferred_init_done', True):
                            if time.monotonic() - _wait_start > 90:
                                logger.warning(
                                    f"[{self.session_key}] Deferred init wait timeout (90s), "
                                    f"proceeding with available data")
                                break
                            await asyncio.sleep(1)
                        _wait_elapsed = int(time.monotonic() - _wait_start)
                        if _wait_elapsed > 0:
                            logger.info(
                                f"[{self.session_key}] Deferred init wait done ({_wait_elapsed}s)")

                    # 获取或创建会话
                    # 当 session_data 不存在或 session_id 被清除 (HTTP 404/410, session_not_found)
                    # 时，均需要重新创建会话
                    if not self.session_data or not self.session_data.session_id:
                        agent_name = await self.state_manager.get_active_agent(self.session_key)
                        if not agent_name:
                            agent_name = "default"

                        # ---- B1 修复: 尝试从 ConversationStore 恢复远程 session_id ----
                        _recovered_sid = None
                        if self.conversation_store:
                            _recovered_sid = await self.conversation_store.get_remote_session_id(
                                self.session_key, agent_name
                            )
                            if _recovered_sid:
                                logger.info(
                                    f"[B1] Remote session_id recovered from store: "
                                    f"{_recovered_sid} ({self.session_key})")

                        if _recovered_sid:
                            # 复用已有远程会话，无需重新创建
                            self.session_data = SessionData(
                                session_key=self.session_key,
                                agent_name=agent_name,
                            )
                            self.session_data.session_id = _recovered_sid
                            self._session_just_created = False  # 非新会话，不注入上下文

                            # 解析 agent_id (与新建会话相同的逻辑)
                            _session_agent_id = self.agent_id
                            if agent_name != "default" and self.parent_module:
                                _discovered = getattr(self.parent_module, 'discovered_agents', {})
                                if agent_name in _discovered:
                                    _resolved_id = _discovered[agent_name].get("agent_id", "")
                                    if _resolved_id:
                                        _session_agent_id = _resolved_id
                            self._session_agent_id = _session_agent_id

                            # 加载历史记忆
                            if self.conversation_store:
                                try:
                                    _, memory = await self.conversation_store.load_session(
                                        self.session_key, agent_name
                                    )
                                    if memory:
                                        logger.info(
                                            f"[B1] Recovered session memory loaded "
                                            f"({len(memory)} chars): {self.session_key}")
                                except Exception as e:
                                    logger.warning(
                                        f"[B1] Failed to load recovered session memory: {e}")

                            logger.info(
                                f"[B1] Session recovered (not created): {self.session_key} "
                                f"(ID: {_recovered_sid}, agent: {agent_name})")

                            # 跳过后续的创建逻辑，直接进入消息处理
                            _setup_elapsed = int(time.monotonic() - _msg_recv_time)
                            logger.info(
                                f"[DIAG] About to call _process_message: "
                                f"setup_elapsed={_setup_elapsed}s | "
                                f"session_id={self.session_data.session_id if self.session_data else 'None'}"
                            )
                            response = await self._process_message(msg)
                            _total_elapsed = int(time.monotonic() - _msg_recv_time)
                            logger.info(
                                f"[DIAG] _process_message returned: "
                                f"total_elapsed={_total_elapsed}s | "
                                f"success={response.success if response else 'None'} | "
                                f"reply_len={len(response.content) if (response and response.content) else 0}"
                            )

                            if response and response.success and response.content:
                                if self.send_queue:
                                    outgoing = OutgoingMessage(
                                        target_source=msg.raw.source,
                                        target_id=msg.raw.target_id,
                                        content=response.content,
                                        message_type=MessageType.TEXT,
                                    )
                                    await self.send_queue.put(outgoing)
                                elif self.response_queue:
                                    await self.response_queue.put(response)
                            elif response and not response.success:
                                if self.send_queue:
                                    outgoing = OutgoingMessage(
                                        target_source=msg.raw.source,
                                        target_id=msg.raw.target_id,
                                        content=response.user_message,
                                        message_type=MessageType.TEXT,
                                    )
                                    await self.send_queue.put(outgoing)
                                elif self.response_queue:
                                    await self.response_queue.put(response)

                            _roundtrip_elapsed = int(time.monotonic() - _msg_recv_time)
                            logger.info(
                                f"[DIAG] Response dispatched: "
                                f"roundtrip={_roundtrip_elapsed}s | "
                                f"success={response.success if response else 'None'} | "
                                f"session_id={self.session_data.session_id if self.session_data else 'None'}"
                            )
                            self.session_data.update_activity()
                            continue  # 跳过后续的创建逻辑，回到 while 循环

                        # ---- 解析活跃 Agent 的内部 ID ----
                        # .order 切换 后 agent_name 可能是非默认 Agent (如 "VRChatAgent")
                        # 需要从 discovered_agents 获取其内部 ID，否则会话会创建在错误的 Agent 上
                        _session_agent_id = self.agent_id  # 默认使用模块级 Agent ID
                        if agent_name != "default" and self.parent_module:
                            _discovered = getattr(self.parent_module, 'discovered_agents', {})
                            if agent_name in _discovered:
                                _resolved_id = _discovered[agent_name].get("agent_id", "")
                                if _resolved_id:
                                    _session_agent_id = _resolved_id
                                    logger.info(
                                        f"[DIAG] Session agent resolved via discovered_agents: "
                                        f"'{agent_name}' -> {_session_agent_id}")
                                else:
                                    logger.warning(
                                        f"[DIAG] Agent '{agent_name}' found in discovered_agents "
                                        f"but has no agent_id, falling back to default")
                            else:
                                logger.warning(
                                    f"[DIAG] Agent '{agent_name}' not in discovered_agents "
                                    f"(available: {list(_discovered.keys())}), "
                                    f"falling back to default agent_id={self.agent_id}")

                        # 读取持久化的模型偏好 (优先于全局默认模型)
                        saved_model = await self.state_manager.get_saved_model(self.session_key)

                        # ---- BRG-4011: Agent ID 未解析时尝试重新解析 ----
                        if (self.parent_module
                                and getattr(self.parent_module, '_agent_id_unresolved', False)):
                            logger.warning(
                                "Agent ID unresolved [BRG-4011], attempting to re-resolve...")
                            display_name = self.parent_module.agent_id
                            new_id = await self.http_client.fetch_agent_id(display_name)
                            if new_id:
                                logger.info(
                                    f"Agent ID re-resolved: '{display_name}' -> {new_id}")
                                self.agent_id = new_id
                                self.parent_module.agent_id = new_id
                                self.parent_module._agent_id_unresolved = False
                            else:
                                logger.error(
                                    f"Agent ID re-resolution failed [BRG-4011]: '{display_name}'")
                                response = ModuleResponse.error_response(
                                    ErrorCode.AGENT_ID_UNRESOLVED.code,
                                    error_detail=f"Agent 名称 '{display_name}' 无法映射到内部 ID",
                                    custom_text="Agent配置异常，请检查 CherryStudio",
                                )
                                if self.send_queue:
                                    await self.send_queue.put(OutgoingMessage(
                                        target_source=msg.raw.source,
                                        target_id=msg.raw.target_id,
                                        content=response.user_message,
                                        message_type=MessageType.TEXT,
                                    ))
                                break  # 停止处理器，等下次消息时重试

                        # 创建远程会话
                        # 从初始化时构建的映射表获取 accessible_paths (CherryStudio 要求非空)
                        _agent_paths: list[str] = []
                        if self.parent_module and hasattr(self.parent_module, '_agent_paths_by_id'):
                            _agent_paths = self.parent_module._agent_paths_by_id.get(
                                _session_agent_id or "", [])

                        # ---- 最后手段: 运行时获取 Agent 详情以填充路径 ----
                        if not _agent_paths and _session_agent_id:
                            logger.warning(
                                f"[DIAG] accessible_paths empty at session creation, "
                                f"attempting last-resort fetch for agent_id={_session_agent_id}")
                            try:
                                detail = await self.http_client.fetch_agent_detail(_session_agent_id)
                                if detail:
                                    _agent_paths = detail.get("accessible_paths", [])
                                    if _agent_paths:
                                        logger.info(
                                            f"[DIAG] Last-resort path fetch succeeded: "
                                            f"agent_id={_session_agent_id} | paths={_agent_paths}")
                                        if self.parent_module and hasattr(self.parent_module, '_agent_paths_by_id'):
                                            self.parent_module._agent_paths_by_id[_session_agent_id] = _agent_paths
                            except Exception as e:
                                logger.warning(f"[DIAG] Last-resort path fetch failed: {e}")

                        # ---- 最终兜底: 使用 config 中的 default_accessible_paths ----
                        if not _agent_paths and self.parent_module:
                            _default_paths = getattr(self.parent_module, '_default_accessible_paths', [])
                            if _default_paths:
                                _agent_paths = _default_paths
                                logger.info(
                                    f"[DIAG] Using default_accessible_paths fallback: {_agent_paths}")

                        if not _agent_paths:
                            logger.error(
                                f"[DIAG] CRITICAL: accessible_paths is EMPTY for "
                                f"agent_name='{agent_name}' | "
                                f"agent_id={_session_agent_id}. "
                                f"Available IDs: {list(self.parent_module._agent_paths_by_id.keys()) if self.parent_module else 'N/A'}. "
                                f"CherryStudio requires non-empty paths for claude-code agents!"
                            )

                        # 构造会话显示名: Private:qqID 或 group:qqID
                        _src, _tid = self.session_key.split("_", 1)
                        if _src == "private":
                            _session_display_name = f"Private:{_tid}"
                        else:
                            _session_display_name = f"group:{_tid}"

                        session_id = await self.http_client.create_session(
                            _session_display_name,
                            agent_id=_session_agent_id,
                            model=saved_model,  # None 时回退到 default_model
                            accessible_paths=_agent_paths,
                        )
                        if session_id:
                            # 只有成功创建后才初始化 session_data
                            self.session_data = SessionData(
                                session_key=self.session_key,
                                agent_name=agent_name,
                            )
                            self.session_data.session_id = session_id
                            self._session_just_created = True
                            # 记住此会话使用的 Agent ID (后续 API 调用需要)
                            self._session_agent_id = _session_agent_id
                            logger.info(
                                f"Session created: {self.session_key} "
                                f"(ID: {session_id}, agent: {agent_name}, "
                                f"agent_id: {_session_agent_id})")

                            # ---- B1 修复: 立即持久化远程 session_id ----
                            if self.conversation_store:
                                try:
                                    await self.conversation_store.set_remote_session_id(
                                        self.session_key,
                                        agent_name,
                                        session_id,
                                    )
                                    logger.info(
                                        f"[B1] Remote session_id persisted: "
                                        f"{session_id} ({self.session_key})")
                                except Exception as e:
                                    logger.warning(
                                        f"[B1] Failed to persist remote session_id: {e}")

                            # ---- ConversationStore: 加载历史记忆 ----
                            if self.conversation_store:
                                try:
                                    _, memory = await self.conversation_store.load_session(
                                        self.session_key, agent_name
                                    )

                                    # ---- Phase 2C.3: 过期会话检测 + AI 摘要 ----
                                    if self.conversation_store.is_session_stale(self.session_key):
                                        if self.parent_module:
                                            archived = await self.parent_module._check_and_archive_stale(
                                                self.session_key, agent_name
                                            )
                                            if archived:
                                                # 归档后重新加载 memory (现在是摘要)
                                                memory = await self.conversation_store.get_session_memory(
                                                    self.session_key
                                                )
                                                logger.info(
                                                    f"Stale session archived, summary loaded: {self.session_key}"
                                                )

                                    if memory:
                                        logger.info(
                                            f"Loaded conversation history ({len(memory)} chars): {self.session_key}"
                                        )
                                except Exception as e:
                                    logger.warning(f"Failed to load conversation memory: {e}")
                        else:
                            # 会话创建失败 — 发送错误后停止处理器
                            # 避免每条消息都重复尝试创建并刷屏
                            logger.error(
                                f"Session creation failed, stopping handler [BRG-4005]: {self.session_key}")
                            response = ModuleResponse.error_response(
                                ErrorCode.SESSION_CREATE_FAILED.code,
                                error_detail="无法创建 CherryStudio 会话",
                                custom_text="AI服务初始化失败",
                            )
                            if self.send_queue:
                                await self.send_queue.put(OutgoingMessage(
                                    target_source=msg.raw.source,
                                    target_id=msg.raw.target_id,
                                    content=response.user_message,
                                    message_type=MessageType.TEXT,
                                ))
                            elif self.response_queue:
                                await self.response_queue.put(response)
                            # break 退出 while 循环，触发 _cleanup()
                            # 下一条消息到达时会创建全新的处理器并重试
                            break

                    # 处理消息
                    _setup_elapsed = int(time.monotonic() - _msg_recv_time)
                    logger.info(
                        f"[DIAG] About to call _process_message: "
                        f"setup_elapsed={_setup_elapsed}s | "
                        f"session_id={self.session_data.session_id if self.session_data else 'None'}"
                    )
                    response = await self._process_message(msg)
                    _total_elapsed = int(time.monotonic() - _msg_recv_time)
                    logger.info(
                        f"[DIAG] _process_message returned: "
                        f"total_elapsed={_total_elapsed}s | "
                        f"success={response.success if response else 'None'} | "
                        f"reply_len={len(response.content) if (response and response.content) else 0}"
                    )

                    # 发送响应
                    if response and response.success and response.content:
                        # 有实际回复内容才发送
                        if self.send_queue:
                            outgoing = OutgoingMessage(
                                target_source=msg.raw.source,
                                target_id=msg.raw.target_id,
                                content=response.content,
                                message_type=MessageType.TEXT,
                            )
                            await self.send_queue.put(outgoing)
                        elif self.response_queue:
                            await self.response_queue.put(response)
                    elif response and not response.success:
                        # 错误响应 — 发送用户可见的错误消息
                        if self.send_queue:
                            outgoing = OutgoingMessage(
                                target_source=msg.raw.source,
                                target_id=msg.raw.target_id,
                                content=response.user_message,
                                message_type=MessageType.TEXT,
                            )
                            await self.send_queue.put(outgoing)
                        elif self.response_queue:
                            await self.response_queue.put(response)
                    # else: 空成功响应 (工具已发送消息)，不发送任何东西

                    # ---- 诊断日志: 完整回合耗时 ----
                    _roundtrip_elapsed = int(time.monotonic() - _msg_recv_time)
                    logger.info(
                        f"[DIAG] Response dispatched: "
                        f"roundtrip={_roundtrip_elapsed}s | "
                        f"success={response.success if response else 'None'} | "
                        f"session_id={self.session_data.session_id if self.session_data else 'None'}"
                    )

                    # 更新活跃时间
                    self.session_data.update_activity()

                except asyncio.TimeoutError:
                    # 超时，清理会话
                    logger.info(f"CherryStudio session timeout, cleaning up: {self.session_key}")
                    break
                except asyncio.CancelledError:
                    logger.info(f"CherryStudio session cancelled: {self.session_key}")
                    break
                except Exception as e:
                    logger.error(
                        f"CherryStudio session handler exception [{self.session_key}]: {e}", exc_info=True)
        finally:
            # 无论何种原因退出，都执行清理
            await self._cleanup()

    async def _cleanup(self):
        """清理会话资源"""
        self._running = False

        # ---- ConversationStore: 保存会话数据 ----
        if self.conversation_store and self.session_data:
            try:
                await self.conversation_store.save_session(
                    self.session_key,
                    self.session_data.agent_name,
                )
                logger.info(f"ConversationStore session saved: {self.session_key}")
            except Exception as e:
                logger.warning(f"Failed to save ConversationStore session: {e}")

        # 远程会话保留在 CherryStudio 中以支持复用 (不删除)
        # session_id 已通过 ConversationStore.set_remote_session_id() 持久化到 meta.json
        # handler 重建时通过 get_remote_session_id() 恢复，无需重新创建
        if self.session_data and self.session_data.session_id:
            logger.info(
                f"Remote session preserved for reuse: {self.session_data.session_id}")

        # 调用父模块的清理回调
        if self.on_cleanup:
            try:
                await self.on_cleanup(self.session_key)
            except Exception as e:
                logger.error(f"Session cleanup callback exception: {e}", exc_info=True)

        logger.info(f"Session handler cleaned up: {self.session_key}")

    # ------------------------------------------------------------------
    # 回复链解析 (Phase 3A)
    # ------------------------------------------------------------------

    async def _fetch_reply_chain(
        self, msg: ParsedMessage, max_depth: int
    ) -> tuple[str, list[str]]:
        """
        递归获取引用链内容，最多 max_depth 层。

        移植自旧项目 auto_reply._fetch_reply_chain():
        通过 NapCatBridge.get_msg() 逐层获取被引用消息，
        提取文本和图片，构建引用上下文。

        Args:
            msg: 当前消息
            max_depth: 最大遍历深度

        Returns:
            (格式化引用文本, 引用链中的图片 file ID 列表)
        """
        if not self.napcat_bridge or max_depth <= 0:
            return "", []

        reply_id = msg.raw.get_reply_id()
        if not reply_id:
            return "", []

        chain_parts: list[str] = []
        all_image_files: list[str] = []
        seen_ids: set[str] = {msg.raw.msg_id}  # 防止循环引用
        current_id = reply_id

        for depth in range(max_depth):
            if current_id in seen_ids:
                break
            seen_ids.add(current_id)

            try:
                data = await self.napcat_bridge.get_msg(current_id)
            except Exception as e:
                logger.warning(f"Failed to fetch quoted message [{current_id}]: {e}")
                chain_parts.append(
                    f"[引用第{depth + 1}层] (消息无法获取, ID: {current_id})"
                )
                break

            if not data:
                chain_parts.append(
                    f"[引用第{depth + 1}层] (消息无法获取, ID: {current_id})"
                )
                break

            # 提取发送者
            sender = data.get("sender", {})
            sender_name = sender.get("nickname", sender.get("card", ""))

            # 提取消息段
            message_segs = data.get("message", [])
            raw_msg = data.get("raw_message", "")

            # 提取纯文本
            text = self._extract_plain_text(message_segs, raw_msg)

            if not sender_name and not text:
                chain_parts.append(
                    f"[引用第{depth + 1}层] (消息内容为空, ID: {current_id})"
                )
                break

            # 提取图片 file ID
            img_files = self._extract_image_file_ids(message_segs)
            if img_files:
                logger.info(
                    f"Reply chain [{current_id[:12]}...]: found {len(img_files)} image(s)"
                )
            all_image_files.extend(img_files)

            chain_parts.append(f"[引用第{depth + 1}层] {sender_name}: {text}")

            # 查找下一层引用
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

        return (
            "[引用消息上下文]\n" + "\n".join(reversed(chain_parts)),
            all_image_files,
        )

    @staticmethod
    def _extract_plain_text(message_segs: list | str, raw_msg: str = "") -> str:
        """
        从消息段提取纯文本 (用于回复链显示)。

        复用 NapCatBridge._extract_text 的逻辑，
        但在 reply 段显示 [引用] 而非 [引用回复]。
        """
        if isinstance(message_segs, str):
            return message_segs if message_segs else raw_msg

        parts: list[str] = []
        for seg in message_segs:
            if not isinstance(seg, dict):
                continue
            t = seg.get("type", "")
            d = seg.get("data", {})

            if t == "text":
                parts.append(d.get("text", ""))
            elif t == "image":
                parts.append("[图片]")
            elif t == "at":
                parts.append(f"@{d.get('qq', '')}")
            elif t == "reply":
                pass  # 引用标记已在链中体现，不再重复
            elif t == "face":
                parts.append("[表情]")
            elif t == "file":
                parts.append(f"[文件: {d.get('name', '')}]")
            elif t == "record":
                parts.append("[语音]")
            elif t == "video":
                parts.append("[视频]")
            else:
                # 其他类型忽略
                pass

        return "".join(parts)

    @staticmethod
    def _extract_image_file_ids(message_segs: list | str) -> list[str]:
        """从消息段中提取所有图片段的 file 字段列表"""
        if isinstance(message_segs, str):
            return []
        files: list[str] = []
        for seg in message_segs:
            if isinstance(seg, dict) and seg.get("type") == "image":
                fid = seg.get("data", {}).get("file", "")
                if fid:
                    files.append(fid)
        return files

    # ------------------------------------------------------------------
    # 输出后处理 (Phase 3C)
    # ------------------------------------------------------------------

    _MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')

    @staticmethod
    def _extract_md_images(text: str) -> list[tuple[str, str]]:
        """
        提取 Markdown 图片语法 ![alt](url)，返回 [(alt, url)] 列表。

        移植自旧项目 auto_reply._extract_md_images()
        """
        return CherryStudioSessionHandler._MD_IMAGE_RE.findall(text)

    @staticmethod
    def _strip_md_images(text: str) -> str:
        """
        移除 Markdown 图片语法，保留纯文本。

        移植自旧项目 auto_reply._strip_md_images()
        """
        return CherryStudioSessionHandler._MD_IMAGE_RE.sub('', text).strip()

    @staticmethod
    def _replace_name_placeholders(text: str, sender_name: str, sender_id: str) -> str:
        """
        替换回复中的名称占位符。

        移植自旧项目 auto_reply._replace_name_placeholder():
        - {name} / {sender} → 发送者昵称
        - {at} → CQ @ 语法
        """
        text = text.replace("{name}", sender_name)
        text = text.replace("{sender}", sender_name)
        text = text.replace("{at}", f"[CQ:at,qq={sender_id}]")
        return text

    async def _process_message(self, msg: ParsedMessage) -> ModuleResponse:
        """
        处理单条消息 — 使用 SSE 流式解析引擎。

        流程:
        1. 前置处理 (图片识别、文件处理)
        2. 构建发送给 Agent 的完整消息
        3. 通过 SSE 流式调用 CherryStudio Agent API
        4. 使用 SSEParser 解析流式响应
        5. 根据解析结果决定发送行为
        """
        # 剥离 @提及后获取纯文本 (群消息中 @机器人 的 @QQ号 对 AI 无意义)
        content = msg.raw.strip_at_mentions()
        target_id = msg.raw.target_id
        _process_start = time.monotonic()

        # ---- 路由元数据注入: 让 Agent 知道消息来源和目标 ----
        # Agent 通过 qq_send_message(message_type, target_id, message) 回复,
        # 但消息文本本身不含路由信息, Agent 只能从会话历史猜测目标。
        # 当历史记忆来自其他会话 (如管理员私聊), Agent 会发错目标。
        # 解决: 在消息头部注入 source/target/sender, 确保 Agent 路由正确。
        _sender_label = msg.raw.sender_name or msg.raw.sender_id or ""
        if msg.raw.source == MessageSource.GROUP:
            _route_meta = f"[来源: 群聊 {target_id} | 发送者: {_sender_label}]"
        else:
            _route_meta = f"[来源: 私聊 | 发送者: {_sender_label}]"
        content = f"{_route_meta}\n{content}" if content else _route_meta

        # ---- CherryDebug: 创建本次调用的调试日志器 ----
        _dbg: CherryDebugLogger | None = None
        if self.config.get("cherry_debug", False):
            try:
                _dbg = CherryDebugLogger(session_key=self.session_key)
                _dbg.log("SessionHandler", "_process_message START",
                         f"session_key={self.session_key}, "
                         f"session_id={self.session_data.session_id if self.session_data else 'None'}, "
                         f"agent_id={self.agent_id}",
                         content_after=content)
            except Exception as e:
                logger.warning(f"CherryDebugLogger init failed: {e}")
                _dbg = None

        # ---- 诊断日志: 消息处理开始 ----
        logger.info(
            f"[DIAG] _process_message START: "
            f"session_key={self.session_key} | "
            f"session_id={self.session_data.session_id if self.session_data else 'None'} | "
            f"agent_id={self.agent_id} | "
            f"content_len={len(content)} chars | "
            f"attachments={len(msg.raw.attachments) if msg.raw.attachments else 0} | "
            f"sender={msg.raw.sender_name or msg.raw.sender_id}"
        )

        # ---- 前置处理: 图片识别 ----
        images = []
        if msg.raw.attachments:
            for attachment in msg.raw.attachments:
                if attachment.get("type") == "image" and attachment.get("url"):
                    images.append(attachment["url"])

        if images and self.parent_module and self.parent_module.vision_chain:
            try:
                logger.info(f"Detected {len(images)} image(s), starting recognition...")
                vision_description = await self.parent_module.vision_chain.recognize_image(
                    image_urls=images,
                    user_question=content if content else "",
                )
                if vision_description:
                    _before = content
                    if content:
                        content = f"[图片识别结果]\n{vision_description}\n\n[用户消息]\n{content}"
                    else:
                        content = f"[图片识别结果]\n{vision_description}"
                    logger.info("Image recognition succeeded")
                    if _dbg:
                        _dbg.log("VisionChain", "图片识别结果拼接",
                                 f"vision_len={len(vision_description)}, "
                                 f"content_len_before={len(_before)}, "
                                 f"content_len_after={len(content)}",
                                 content_before=_before,
                                 content_after=content)
            except Exception as e:
                logger.error(f"Image recognition exception: {e}", exc_info=True)

        # ---- 前置处理: 文件处理 ----
        files = []
        if msg.raw.attachments:
            for attachment in msg.raw.attachments:
                if attachment.get("type") == "file" and attachment.get("url"):
                    files.append(attachment["url"])

        if files and self.parent_module and self.parent_module.file_processor:
            try:
                logger.info(f"Detected {len(files)} file(s), starting processing...")
                file_summaries = []
                for file_url in files:
                    summary = await self.parent_module.file_processor.process_file(file_url)
                    if summary:
                        file_summaries.append(summary)
                if file_summaries:
                    file_content = "\n\n".join([
                        f"[文件提取内容 #{i+1}]\n{s}"
                        for i, s in enumerate(file_summaries)
                    ])
                    _before = content
                    if content:
                        content = f"{file_content}\n\n[用户消息]\n{content}"
                    else:
                        content = file_content
                    logger.info(f"File processing succeeded, {len(file_summaries)} file(s) processed")
                    if _dbg:
                        _dbg.log("FileProcessor", "文件提取内容拼接",
                                 f"files={len(file_summaries)}, "
                                 f"file_content_len={len(file_content)}, "
                                 f"content_len_before={len(_before)}, "
                                 f"content_len_after={len(content)}",
                                 content_before=_before,
                                 content_after=content)
            except Exception as e:
                logger.error(f"File processing exception: {e}", exc_info=True)

        # ---- Phase 3A: 回复链解析 ----
        if self._reply_chain_depth > 0:
            try:
                reply_text, reply_images = await self._fetch_reply_chain(
                    msg, self._reply_chain_depth
                )
                if reply_text:
                    _before = content
                    content = f"{reply_text}\n\n---\n{content}" if content else reply_text
                    logger.info(
                        f"Reply chain parsed: {len(reply_text)} chars, "
                        f"{len(reply_images)} image(s)"
                    )
                    if _dbg:
                        _dbg.log("ReplyChain", "回复链拼接",
                                 f"reply_text_len={len(reply_text)}, "
                                 f"reply_images={len(reply_images)}, "
                                 f"content_len_before={len(_before)}, "
                                 f"content_len_after={len(content)}",
                                 content_before=_before,
                                 content_after=content)

                # ---- 回复链图片识别 ----
                # 将引用链中的图片 file ID 通过 NapCat get_image 解析为本地路径，
                # 再送入视觉模型识别，将描述拼接到 content 中。
                # (移植自旧项目 auto_reply._run_message 中合并 all_image_files 的逻辑)
                if reply_images and self.parent_module and self.parent_module.vision_chain:
                    reply_image_urls: list[str] = []
                    for fid in reply_images:
                        try:
                            local_path = await self.napcat_bridge.get_image_path(fid)
                            if local_path and os.path.isfile(local_path):
                                reply_image_urls.append(f"file://{local_path}")
                                logger.info(
                                    f"Reply chain image resolved: {fid[:20]}... -> {local_path}"
                                )
                            else:
                                logger.warning(
                                    f"Reply chain image path invalid: {fid[:20]}... -> {local_path}"
                                )
                        except Exception as e:
                            logger.warning(f"Reply chain image resolve failed: {fid[:20]}... -> {e}")

                    if reply_image_urls:
                        try:
                            user_question = msg.raw.strip_at_mentions() or ""
                            logger.info(
                                f"Vision: recognizing {len(reply_image_urls)} reply chain image(s)..."
                            )
                            vision_desc = await self.parent_module.vision_chain.recognize_image(
                                image_urls=reply_image_urls,
                                user_question=user_question,
                            )
                            if vision_desc:
                                content += f"\n\n[引用图片识别结果]\n{vision_desc}"
                                logger.info(
                                    f"Reply chain image recognition succeeded ({len(vision_desc)} chars)"
                                )
                                if _dbg:
                                    _dbg.log("ReplyChainVision", "引用图片识别结果",
                                             f"image_count={len(reply_image_urls)}, "
                                             f"desc_len={len(vision_desc)}",
                                             content_before=content,
                                             content_after=content)
                            else:
                                logger.warning("Reply chain image recognition returned empty")
                        except Exception as e:
                            logger.error(f"Reply chain image recognition exception: {e}", exc_info=True)
            except Exception as e:
                logger.warning(f"Reply chain parsing failed: {e}")

        # ---- 回执通知: Agent 开始处理时发送自定义提示 ----
        if self._receipt_notification and self.send_queue:
            try:
                receipt_tpl = load_bot_setting("notification", "receipt_message")
                if receipt_tpl:
                    receipt_text = format_msg(
                        receipt_tpl,
                        player_name=msg.raw.sender_name or "",
                    )
                    await self.send_queue.put(OutgoingMessage(
                        target_source=msg.raw.source,
                        target_id=str(target_id),
                        content=receipt_text,
                        message_type=MessageType.TEXT,
                    ))
                    logger.info(f"Receipt notification sent: {self.session_key}")
            except Exception as e:
                logger.warning(f"Failed to send receipt notification: {e}")

        # ---- SSE 流式调用 ----
        if not self.session_data or not self.session_data.session_id:
            return ModuleResponse.error_response(
                ErrorCode.SESSION_CREATE_FAILED.code,
                error_detail="会话未创建",
                custom_text="AI服务初始化失败",
            )

        # ---- ConversationStore: 记录用户消息 ----
        agent_name = self.session_data.agent_name if self.session_data else "default"
        if self.conversation_store:
            try:
                await self.conversation_store.add_message(
                    self.session_key,
                    agent_name,
                    {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "role": "user",
                        "sender": msg.raw.sender_name or msg.raw.sender_id,
                        "sender_id": msg.raw.sender_id,
                        "content": msg.raw.strip_at_mentions(),
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to record user message to ConversationStore: {e}")

        # ---- Phase 2C.4: 新会话首条消息注入工作区上下文 + 历史记忆 + 全局规则 ----
        if self._session_just_created:
            self._session_just_created = False
            if self.parent_module:
                try:
                    injection = self.parent_module._build_injection_context(
                        agent_name, self.session_key
                    )
                    if injection:
                        _before = content
                        content = f"{injection}\n\n---\n当前消息：{content}"
                        logger.info(
                            f"New session context injected: {self.session_key} "
                            f"({len(injection)} chars)"
                        )
                        if _dbg:
                            _dbg.log("Injection", "新会话上下文注入",
                                     f"injection_len={len(injection)}, "
                                     f"content_len_before={len(_before)}, "
                                     f"content_len_after={len(content)}",
                                     content_before=_before,
                                     content_after=content)
                except Exception as e:
                    logger.warning(f"Failed to inject workspace context: {e}")

        # 标记目标正在被响应 (使 qq_send_message 的活跃验证通过)
        if self.napcat_bridge:
            self.napcat_bridge.mark_responding(str(target_id))

        try:
            # 构建 SSE 通知回调
            async def _notify(text: str):
                """停滞时向用户发送通知"""
                if self.send_queue:
                    await self.send_queue.put(OutgoingMessage(
                        target_source=msg.raw.source,
                        target_id=str(target_id),
                        content=text,
                        message_type=MessageType.TEXT,
                    ))

            # 构建 SSE 存活回调 — Agent 在检测间隔内有内容产出时发送存活提示
            async def _alive(text: str):
                """Agent 存活时向用户发送自定义存活提示"""
                if self.send_queue:
                    await self.send_queue.put(OutgoingMessage(
                        target_source=msg.raw.source,
                        target_id=str(target_id),
                        content=text,
                        message_type=MessageType.TEXT,
                    ))

            # 创建 SSE 解析器
            parser = SSEParser(
                stall_timeout=self._agent_timeout,          # 停滞检测间隔 = agent_timeout_seconds
                total_timeout=self._sse_total_timeout,      # 总超时 = 基于停滞参数自动计算
                max_stall_retries=self._sse_stall_max_retries,
                pre_tool_text_policy=self._pre_tool_text_policy,
                notify_callback=_notify,
                alive_callback=_alive,
                debug_logger=_dbg,
                debug_logging=self.config.get("cherry_debug", False),
            )

            if _dbg:
                _dbg.log_separator("Agent Request")
                _dbg.log("SessionHandler", "FINAL CONTENT → Agent",
                         f"content_len={len(content)} chars, "
                         f"session_id={self.session_data.session_id}, "
                         f"agent_id={self._session_agent_id or self.agent_id}",
                         content_after=content)

            # 发起 Agent 请求并解析 (含 1 次重试，仅对连接错误重试，超时不重试)
            # 注意: CherryStudio Agent API 是同步阻塞式端点 —
            # POST /v1/agents/{id}/sessions/{sid}/messages 在 Agent 完全处理完毕前
            # 不会发送 HTTP 响应头部。头部等待时间 = Agent 处理时间 (可能数分钟)。
            sse_result: SSEResult | None = None
            _direct_reply: str | None = None  # 非 SSE 模式时的直接回复文本

            # ---- 诊断日志: 预处理耗时 ----
            _preprocess_elapsed = int(time.monotonic() - _process_start)
            logger.info(
                f"[DIAG] Pre-processing done: "
                f"elapsed={_preprocess_elapsed}s | "
                f"final_content_len={len(content)} chars | "
                f"session_id={self.session_data.session_id} | "
                f"about to send Agent request..."
            )

            for _sse_attempt in range(2):
                ctx = self.http_client.get_sse_request_context(
                    session_id=self.session_data.session_id,
                    message=content,
                    agent_id=self._session_agent_id or self.agent_id,
                    total_timeout=self._sse_total_timeout,
                )

                # ---- 头部等待阶段的存活监控 ----
                # CherryStudio Agent API 在 Agent 处理期间不会发送 HTTP 头部,
                # 因此需要独立的监控任务定期向 QQ 发送存活提示
                _request_start = time.monotonic()

                async def _header_monitor():
                    """Agent 处理阶段的存活通知监控任务"""
                    try:
                        while True:
                            await asyncio.sleep(self._agent_timeout)
                            elapsed_s = int(time.monotonic() - _request_start)
                            if self.send_queue:
                                await self.send_queue.put(OutgoingMessage(
                                    target_source=msg.raw.source,
                                    target_id=str(target_id),
                                    content=f"Agent 正在思考中……已等待 {elapsed_s}s",
                                    message_type=MessageType.TEXT,
                                ))
                            logger.debug(
                                f"Agent processing keepalive: {elapsed_s}s"
                            )
                    except asyncio.CancelledError:
                        pass

                _monitor_task = asyncio.create_task(_header_monitor())

                try:
                    async with ctx as resp:
                        # 头部已到达，取消监控任务
                        _monitor_task.cancel()
                        elapsed_s = int(time.monotonic() - _request_start)

                        # ---- 诊断日志: 完整响应头部 ----
                        resp_headers = getattr(resp, 'headers', {}) or {}
                        all_headers = dict(resp_headers) if hasattr(resp_headers, 'items') else {}
                        logger.info(
                            f"[DIAG] Agent response <<< "
                            f"HTTP {resp.status} | "
                            f"elapsed={elapsed_s}s | "
                            f"headers={all_headers}"
                        )

                        if resp.status in (404, 410):
                            # 会话失效 → 尝试自动重建
                            logger.warning(
                                f"CherryStudio returned HTTP {resp.status}, session expired. "
                                f"Attempting auto-rebuild...")
                            _rebuilt = await self._rebuild_session_fallback()
                            if _rebuilt:
                                logger.info(
                                    "[Fallback] Session rebuilt after HTTP "
                                    f"{resp.status}, retrying SSE request...")
                                continue  # 重试循环下一次
                            # 重建失败，回退到手动重试提示
                            return ModuleResponse.error_response(
                                ErrorCode.CHERRY_SESSION_EXPIRED.code,
                                error_detail="会话已失效，自动重建失败",
                                custom_text="会话已过期，请重新发送消息",
                            )

                        if resp.status != 200:
                            error_text = await resp.text()
                            logger.error(
                                f"Agent API returned HTTP {resp.status}: {error_text[:300]}")
                            return ModuleResponse.error_response(
                                ErrorCode.LLM_PROVIDER_FAILED.code,
                                error_detail=f"HTTP {resp.status}: {error_text[:200]}",
                                custom_text="AI服务异常",
                            )

                        # ---- 自适应响应格式检测 ----
                        content_type = resp_headers.get("Content-Type", "") if hasattr(resp_headers, 'get') else ""
                        logger.info(f"[DIAG] Response Content-Type: '{content_type}'")

                        if "text/event-stream" in content_type:
                            # ---- SSE 流式响应 → 使用 SSEParser ----
                            logger.info("[DIAG] Response mode: SSE streaming -> SSEParser")
                            sse_result = await parser.parse(resp)
                        else:
                            # ---- 非 SSE 响应 (JSON) → 同步阻塞模式 ----
                            logger.info("[DIAG] Response mode: non-SSE (blocking JSON)")
                            body_text = await resp.text()
                            logger.info(
                                f"[DIAG] Agent JSON response: "
                                f"length={len(body_text)} chars | "
                                f"first 500 chars: {body_text[:500]}"
                            )
                            try:
                                data = json.loads(body_text)
                            except (json.JSONDecodeError, ValueError) as e:
                                logger.error(
                                    f"Agent response is not valid JSON: {e}, "
                                    f"body[:300]: {body_text[:300]}"
                                )
                                return ModuleResponse.error_response(
                                    ErrorCode.LLM_PROVIDER_FAILED.code,
                                    error_detail=f"Non-JSON response: {body_text[:200]}",
                                    custom_text="AI返回格式异常",
                                )

                            # 从 JSON 响应中提取回复文本
                            _direct_reply = _extract_response_text(data)

                            # ---- 诊断日志: JSON 结构分析 ----
                            if isinstance(data, dict):
                                logger.info(
                                    f"[DIAG] JSON top-level keys: {list(data.keys())} | "
                                    f"role={data.get('role', 'N/A')} | "
                                    f"has_content={'content' in data} | "
                                    f"content_type={type(data.get('content')).__name__}"
                                )
                            else:
                                logger.info(
                                    f"[DIAG] JSON root type: {type(data).__name__}")

                            if not _direct_reply:
                                # 尝试从嵌套字段提取
                                if isinstance(data, dict):
                                    content_field = data.get("content")
                                    if isinstance(content_field, list):
                                        parts = []
                                        for part in content_field:
                                            if isinstance(part, dict):
                                                t = part.get("text", "")
                                                if t:
                                                    parts.append(t)
                                        _direct_reply = "\n".join(parts)
                                        logger.info(
                                            f"[DIAG] Extracted from content[].text: "
                                            f"{len(parts)} parts, "
                                            f"total={len(_direct_reply)} chars"
                                        )
                                    elif isinstance(content_field, str):
                                        _direct_reply = content_field

                            if _direct_reply:
                                logger.info(
                                    f"[DIAG] Agent reply extracted: "
                                    f"{len(_direct_reply)} chars | "
                                    f"preview='{_direct_reply[:200]}'"
                                )
                            else:
                                logger.warning(
                                    f"[DIAG] REPLY EXTRACTION FAILED. "
                                    f"Keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'} | "
                                    f"body[:500]: {body_text[:500]}"
                                )
                            break  # 成功，跳出重试循环

                except asyncio.TimeoutError:
                    _monitor_task.cancel()
                    elapsed_s = int(time.monotonic() - _request_start)
                    logger.error(
                        f"Agent request total timeout [BRG-4016]: "
                        f"waited {elapsed_s}s (limit={self._sse_total_timeout}s), "
                        f"stall interval={self._agent_timeout}s, "
                        f"max retries={self._sse_stall_max_retries} "
                        f"(attempt {_sse_attempt + 1}/2)"
                    )
                    return ModuleResponse.error_response(
                        ErrorCode.SSE_TOTAL_TIMEOUT.code,
                        error_detail=(
                            f"Agent did not complete within {self._sse_total_timeout}s "
                            f"(waited {elapsed_s}s). "
                            f"Consider increasing agent_timeout_seconds or sse_stall_max_retries"
                        ),
                        custom_text="AI响应超时",
                    )
                except aiohttp.ClientError as e:
                    _monitor_task.cancel()
                    if _sse_attempt == 0:
                        logger.warning(
                            f"Agent API connection failed, retrying in 1s: "
                            f"{type(e).__name__}: {e}")
                        await asyncio.sleep(1)
                        continue
                    logger.error(
                        f"Agent API connection failed (retry exhausted) [BRG-4010]: "
                        f"{type(e).__name__}: {e}")
                    return ModuleResponse.error_response(
                        ErrorCode.SSE_RETRY_EXHAUSTED.code,
                        error_detail=f"{type(e).__name__}: {e}",
                        custom_text="AI服务连接失败",
                    )
                finally:
                    _monitor_task.cancel()

            # ---- 根据响应类型决定行为 ----

            # ---- 非 SSE 模式: 直接使用 Agent 返回的 JSON 文本 ----
            if _direct_reply is not None:
                if _dbg:
                    _dbg.log_separator("Non-SSE Response")
                    _dbg.log("SessionHandler", "NON-SSE JSON reply",
                             f"reply_len={len(_direct_reply)}",
                             content_after=_direct_reply)
                if _direct_reply:
                    logger.info(
                        f"Agent non-SSE reply: {len(_direct_reply)} chars")
                    return ModuleResponse.success_response(_direct_reply)
                else:
                    logger.warning("Agent returned empty reply (non-SSE mode)")
                    return ModuleResponse.error_response(
                        ErrorCode.LLM_PROVIDER_FAILED.code,
                        error_detail="Agent returned empty response",
                        custom_text="AI未生成回复",
                    )

            # ---- SSE 模式: 根据 SSEResult 决定行为 ----
            if sse_result is None:
                return ModuleResponse.error_response(
                    ErrorCode.LLM_PROVIDER_FAILED.code,
                    error_detail="No response from Agent (neither SSE nor JSON)",
                    custom_text="AI处理失败",
                )

            # session_not_found → 自动重建会话并重试 SSE
            if sse_result.session_not_found:
                logger.warning("session_not_found, attempting auto-rebuild")
                _rebuilt = await self._rebuild_session_fallback()
                if _rebuilt:
                    logger.info("[Fallback] Session rebuilt, retrying SSE request...")
                    # 重新发送 SSE 请求 (使用新 session)
                    sse_result = None
                    try:
                        ctx = self.http_client.get_sse_request_context(
                            session_id=self.session_data.session_id,
                            message=content,
                            agent_id=self._session_agent_id or self.agent_id,
                            total_timeout=self._sse_total_timeout,
                        )
                        _fb_start = time.monotonic()
                        _fb_monitor = asyncio.create_task(
                            self._header_alive_monitor(msg, target_id, _fb_start)
                        )
                        try:
                            async with ctx as resp:
                                _fb_monitor.cancel()
                                if resp.status == 200:
                                    sse_result = await parser.parse(resp)
                                    logger.info(
                                        f"[Fallback] SSE retry completed: "
                                        f"session_not_found={sse_result.session_not_found}, "
                                        f"blocks={len(sse_result.reply_blocks)}"
                                    )
                                else:
                                    logger.error(
                                        f"[Fallback] SSE retry HTTP {resp.status}")
                        except asyncio.CancelledError:
                            _fb_monitor.cancel()
                            raise
                        finally:
                            _fb_monitor.cancel()
                    except Exception as e:
                        logger.error(f"[Fallback] SSE retry request failed: {e}")

                    # 重试后再次 session_not_found 或失败，不再重试
                    if sse_result is None or sse_result.session_not_found:
                        return ModuleResponse.error_response(
                            ErrorCode.CHERRY_SESSION_EXPIRED.code,
                            error_detail="session_not_found (fallback retry failed)",
                            custom_text="会话重建后仍失败，请重新发送消息",
                        )
                    # sse_result 已更新为重试结果，继续后续处理
                else:
                    return ModuleResponse.error_response(
                        ErrorCode.CHERRY_SESSION_EXPIRED.code,
                        error_detail="session_not_found (rebuild failed)",
                        custom_text="会话已失效，自动重建失败，请重新发送消息",
                    )

            # 获取回复文本
            reply_text = sse_result.get_reply_text(
                pre_tool_text_policy=self._pre_tool_text_policy
            )

            # ---- 防重复发送: 当 Agent 已通过工具发送消息时，丢弃 pre-tool 文本 ----
            # 当 had_output_tool=True 时，Agent 已经通过 qq_send_message 工具
            # 自行发送了消息。pre-tool 文本（如 "让我帮你查一下"）通常是
            # 工具消息的前缀，再发送一次会导致用户看到两条消息。
            if sse_result.had_output_tool and reply_text:
                if _dbg:
                    _dbg.log("SessionHandler", "PRE-TOOL TEXT DISCARDED",
                             f"had_output_tool=True, "
                             f"discarded_len={len(reply_text)}, "
                             f"discarded_preview='{reply_text[:100]}'")
                logger.info(
                    f"Pre-tool text discarded ({len(reply_text)} chars): "
                    f"Agent already sent message via output tool")
                reply_text = ""

            if _dbg:
                _dbg.log_separator("Post-Processing")
                _dbg.log("SessionHandler", "get_reply_text()",
                         f"policy={self._pre_tool_text_policy}, "
                         f"had_output_tool={sse_result.had_output_tool}, "
                         f"reply_blocks={len(sse_result.reply_blocks)}, "
                         f"pre_tool_blocks={len(sse_result.pre_tool_reply_blocks)}, "
                         f"reply_text_len={len(reply_text) if reply_text else 0}",
                         content_after=reply_text or "(empty)")

            if reply_text:
                # ---- Phase 3C: Markdown 图片提取 + 单独发送 ----
                md_images = self._extract_md_images(reply_text)
                if md_images:
                    text_only = self._strip_md_images(reply_text)
                    if _dbg:
                        _dbg.log("SessionHandler", "Markdown 图片提取",
                                 f"images_found={len(md_images)}, "
                                 f"reply_text_len={len(reply_text)}, "
                                 f"text_only_len={len(text_only)}",
                                 content_before=reply_text,
                                 content_after=text_only)
                    logger.info(
                        f"Extracted {len(md_images)} Markdown image(s), sending separately"
                    )
                    # 用去除图片的文本作为回复
                    reply_text = text_only if text_only else ""

                # ---- ConversationStore: 记录 AI 回复 ----
                if self.conversation_store and reply_text:
                    try:
                        await self.conversation_store.add_message(
                            self.session_key,
                            agent_name,
                            {
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "role": "assistant",
                                "content": reply_text,
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Failed to record AI reply to ConversationStore: {e}")

                # ---- Phase 3C: 发送提取的 Markdown 图片 ----
                if md_images and self.send_queue:
                    for alt, url in md_images:
                        try:
                            img_outgoing = OutgoingMessage(
                                target_source=msg.raw.source,
                                target_id=msg.raw.target_id,
                                content=f"[CQ:image,file={url}]" + (f"\n{alt}" if alt else ""),
                                message_type=MessageType.TEXT,
                            )
                            await self.send_queue.put(img_outgoing)
                        except Exception as e:
                            logger.warning(f"Failed to send Markdown image: {e}")

                # 成功回复，重置停滞计数
                self._stall_count = 0
                return ModuleResponse.success_response(reply_text)

            # 无回复文本
            if sse_result.had_output_tool:
                # 工具已发送消息，无需额外回复; 视为成功，重置停滞计数
                self._stall_count = 0
                return ModuleResponse.success_response("")
            elif sse_result.stalled:
                # ---- 2-strike 停滞处理 ----
                self._stall_count += 1
                if self._stall_count >= 2:
                    logger.warning(
                        f"[BRG-3001] {self._stall_count} consecutive stalls, "
                        f"destroying and rebuilding session: {self.session_key}"
                    )
                    # 删除远程会话
                    if self.session_data and self.session_data.session_id:
                        try:
                            await self.http_client.delete_session(
                                self.session_data.session_id,
                                agent_id=self.agent_id,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to delete stalled session: {e}")
                    # 清除 SID，下次消息到达时将自动重建
                    if self.session_data:
                        self.session_data.session_id = None
                    self._stall_count = 0
                else:
                    logger.warning(
                        f"[BRG-3002] Stall {self._stall_count}/2, "
                        f"keeping session for reuse: {self.session_key}"
                    )
                # 停滞时已在 notify_callback 中发送了通知
                return ModuleResponse.success_response("")
            elif sse_result.error:
                return ModuleResponse.error_response(
                    ErrorCode.LLM_PROVIDER_FAILED.code,
                    error_detail=sse_result.error,
                    custom_text="小企鹅看不懂拉，您发的太深奥了拉",
                )
            else:
                # 模型没有输出任何内容
                return ModuleResponse.error_response(
                    ErrorCode.LLM_PROVIDER_FAILED.code,
                    error_detail="模型未产生任何输出",
                    custom_text="小企鹅看不懂拉，您发的太深奥了拉",
                )

        finally:
            # ---- CherryDebug: 关闭调试日志器 ----
            if _dbg:
                try:
                    _dbg.log_separator("_process_message END")
                    _total = int(time.monotonic() - _process_start)
                    _dbg.log("SessionHandler", "COMPLETE",
                             f"total_elapsed={_total}s, "
                             f"had_output_tool={sse_result.had_output_tool if sse_result else 'N/A'}, "
                             f"final_reply_len={len(reply_text) if reply_text else 0}")
                    _dbg.close()
                except Exception:
                    pass
            # 无论成功失败，取消响应中标记
            if self.napcat_bridge:
                self.napcat_bridge.unmark_responding(str(target_id))


class CherryStudioModule:
    """
    CherryStudio 模块

    核心功能:
    1. 接收普通消息
    2. 为每个会话创建独立的 CherryStudioSessionHandler
    3. 路由消息到对应的会话处理器
    4. 收集响应并返回
    5. 支持会话重建 (配置变更时)
    """

    def __init__(
        self,
        state_manager: StateManager,
        config: dict | None = None,
    ):
        """
        初始化 CherryStudio 模块

        Args:
            state_manager: 状态管理器
            config: 配置字典
        """
        self.state_manager = state_manager
        self.config = config or {}

        # ---- DIAG 日志控制 ----
        # cherry_debug=false 时静默所有 [DIAG] 标记的日志
        _cherry_debug = self.config.get("cherry_debug", False)
        logger.addFilter(_DiagFilter(debug_enabled=_cherry_debug))

        # MCP Client
        mcp_config = self.config.get("cherrystudio", {})
        self.mcp_client = MCPClient(
            server_path=mcp_config.get("mcp_server_path"),
        )

        # HTTP Client
        legacy_mode = mcp_config.get("legacy_mode", False)
        self.http_client = HTTPClient(
            base_url=mcp_config.get("http_api_base", "http://127.0.0.1:23333"),
            api_key=mcp_config.get("api_key", ""),
            legacy_mode=legacy_mode,
        )

        # LLM Provider Chain
        llm_providers = self.config.get("llm_providers", [])
        default_llm_index = 0
        if llm_providers:
            self.llm_chain = LLMProviderChain(
                providers=llm_providers,
                default_index=default_llm_index,
            )
        else:
            self.llm_chain = None

        # Vision Provider Chain
        vision_providers = self.config.get("vision_providers", [])
        default_vision_index = 0
        if vision_providers:
            self.vision_chain = VisionProviderChain(
                providers=vision_providers,
                default_index=default_vision_index,
            )
        else:
            self.vision_chain = None

        # File Processor (MinerU)
        file_processing_config = self.config.get("file_processing", {})
        if file_processing_config.get("enabled", False):
            self.file_processor = FileProcessor(file_processing_config)
        else:
            self.file_processor = None

        # Conversation Store (会话持久化)
        conversation_store_enabled = self.config.get(
            "conversation_store_enabled", True)
        if conversation_store_enabled:
            self.conversation_store = ConversationStore()
        else:
            self.conversation_store = None

        # Agent ID（legacy_mode下需要）
        # 优先使用 default_agent 配置（CherryStudio 中注册的 Agent 名称）
        # 回退到 mcp_server_name（MCP Server 显示名称，仅用于兼容旧配置）
        self.agent_id = (
            self.config.get("default_agent")
            or mcp_config.get("mcp_server_name", "QQ Bridge")
        )

        # 多 Agent 自动发现结果: {name: {agent_id, work_dirs}}
        # 由 _discover_agents() 填充，供 .order 命令和 Agent 切换使用
        self.discovered_agents: dict[str, dict] = {}

        # agent_id → accessible_paths 映射表
        # 初始化时一次性加载，创建会话时直接复用 (无需运行时查找)
        self._agent_paths_by_id: dict[str, list[str]] = {}

        # 配置兜底: 当 Agent 列表 API 超时时使用的默认工作路径
        # 用户可在 config.json 中添加 "default_accessible_paths": ["C:/Users/.../workspace"]
        self._default_accessible_paths: list[str] = self.config.get(
            "default_accessible_paths", []
        )

        # ---- 自动回复配置 ----
        auto_reply_cfg = self.config.get("auto_reply", {})
        self._auto_reply_enabled: bool = auto_reply_cfg.get("enabled", True)
        self._reply_to_groups: set[str] = set(
            auto_reply_cfg.get("reply_to_groups", [])
        )
        self._reply_to_friends: set[str] = set(
            auto_reply_cfg.get("reply_to_friends", [])
        )
        self._reply_mode: str = auto_reply_cfg.get("reply_mode", "mention")
        self._cooldown_seconds: float = float(
            auto_reply_cfg.get("cooldown_seconds", 3)
        )

        # BRG-4006 节流: self_qq 未设置的警告只 WARNING 一次，后续降级为 DEBUG
        self._self_qq_warned = False

        # ---- 6C.3: 全局上下文长度警告 ----
        _global_ctx = self.config.get("global_context", "")
        if _global_ctx and len(_global_ctx) > 500:
            logger.warning(
                f"[BRG-5001] global_context length {len(_global_ctx)} chars (>500), "
                f"may affect LLM call efficiency and cost. Consider trimming global rules."
            )

        # 冷却时间记录: session_key -> 上次回复时间戳 (time.monotonic)
        self._last_reply_time: dict[str, float] = {}

        # 消息队列 (来自 MessageBus)
        self.queue: asyncio.Queue[ParsedMessage] = asyncio.Queue()

        # 响应队列 (兼容旧模式，非阻塞模式下不被使用)
        self.response_queue: asyncio.Queue[ModuleResponse] | None = asyncio.Queue()

        # 发送队列 (由 server._connect_queues() 设置，非阻塞模式直接推送 OutgoingMessage)
        self.send_queue: asyncio.Queue | None = None

        # 会话处理器
        self.session_handlers: dict[str, CherryStudioSessionHandler] = {}

        # NapCatBridge 引用 (由 server._connect_queues() 设置)
        self.napcat_bridge = None

        # MCP 握手完成信号 (由 server._connect_queues() 注入, main() 中 MCP 协议层触发)
        self._mcp_handshake_event: asyncio.Event | None = None

        # 运行状态
        self._running = False

    async def initialize(self):
        """
        轻量级初始化 (Phase 1)

        仅完成不依赖 CherryStudio HTTP API 的基础设置:
        - 配置验证
        - HTTP Client 创建 (不发起 API 调用)

        重量级初始化 (Agent ID 解析、模型解析、Agent 发现等) 延迟到
        _deferred_init() 中执行，由 Server.start() 在 MCP 握手后触发。
        这确保 MCP Server 先就绪，CherryStudio 能尽快连接。
        """
        # 验证配置
        cherrystudio_config = self.config.get("cherrystudio", {})

        if not cherrystudio_config:
            logger.warning("'cherrystudio' section missing in config, using defaults")

        mcp_server_path = cherrystudio_config.get("mcp_server_path")
        http_api_base = cherrystudio_config.get(
            "http_api_base", "http://127.0.0.1:8080")
        api_key = cherrystudio_config.get("api_key", "")

        # 记录配置状态
        if mcp_server_path:
            logger.info(f"MCP Server path: {mcp_server_path}")
        else:
            logger.warning("MCP Server not configured, using HTTP API only")

        logger.info(f"HTTP API base URL: {http_api_base}")
        if api_key:
            logger.info("API Key configured")
        else:
            logger.warning("API Key not configured, HTTP requests will not include auth header")

        # 初始化 HTTP Client (仅创建 aiohttp session + 健康检查，不发起业务 API 调用)
        await self.http_client.initialize()

        self._mcp_server_path = mcp_server_path  # 保存供 _deferred_init 使用
        self._deferred_init_done = False
        # Agent ID 尚未解析为内部 ID (由 _deferred_init 完成)
        # Session handler 通过此标志判断是否需要等待或重试
        self._agent_id_unresolved = True
        logger.info("CherryStudio module lightweight init complete (API calls deferred)")

    async def _deferred_init(self):
        """
        重量级延迟初始化 (Phase 2)

        在 MCP 握手完成后由 Server.start() 作为后台任务触发。
        执行所有需要 CherryStudio HTTP API 的初始化步骤:
        - Agent ID 解析
        - 模型解析
        - 主 Agent 路径预缓存
        - MCP Client 连接
        - 多 Agent 自动发现
        - LLM / Vision / File Processor / Conversation Store 初始化
        """
        logger.info("[Phase 2] Starting deferred CherryStudio initialization...")
        try:
            # ---- Agent ID 解析 ----
            self._agent_id_unresolved = False
            if self.http_client.legacy_mode and self.agent_id:
                display_name = self.agent_id
                resolved_id = await self.http_client.fetch_agent_id(display_name)
                if resolved_id:
                    logger.info(f"Agent ID resolved: '{display_name}' -> {resolved_id}")
                    self.agent_id = resolved_id
                else:
                    logger.warning(
                        f"Agent ID resolution failed (1st attempt), retrying in 3s: '{display_name}'")
                    await asyncio.sleep(3)
                    resolved_id = await self.http_client.fetch_agent_id(display_name)
                    if resolved_id:
                        logger.info(
                            f"Agent ID resolved (retry): '{display_name}' -> {resolved_id}")
                        self.agent_id = resolved_id
                    else:
                        self._agent_id_unresolved = True
                        logger.error(
                            f"[BRG-4011] Agent ID resolution failed permanently: '{display_name}' cannot be mapped to internal ID. "
                            f"Subsequent API calls (POST /v1/agents/{display_name}/sessions) will return 404. "
                            f"Please check: 1) Agent named '{display_name}' exists in CherryStudio "
                            f"2) CherryStudio Agent API is running "
                            f"3) config.json default_agent matches the Agent name in CherryStudio")

            # ---- 模型解析 ----
            if self.http_client.legacy_mode:
                default_llm = self.config.get("default_llm", {})
                raw_model = default_llm.get("model", "")
                if raw_model:
                    resolved_model = await self.http_client.resolve_model(raw_model)
                    self.http_client.default_model = resolved_model
                    logger.info(f"Default model resolved: '{raw_model}' -> {resolved_model}")
                else:
                    logger.warning("default_llm.model not configured, sessions will not specify a model")

            # ---- 主 Agent 路径预缓存 ----
            if self.http_client.legacy_mode and self.agent_id:
                try:
                    logger.info(
                        f"[DIAG] Pre-caching primary agent paths: "
                        f"fetching detail for agent_id={self.agent_id}")
                    detail = await self.http_client.fetch_agent_detail(self.agent_id)
                    if detail:
                        paths = detail.get("accessible_paths", [])
                        if paths:
                            self._agent_paths_by_id[self.agent_id] = paths
                            logger.info(
                                f"[DIAG] Primary agent paths pre-cached: "
                                f"agent_id={self.agent_id} | "
                                f"accessible_paths={paths}")
                        else:
                            logger.warning(
                                f"[DIAG] Primary agent detail returned empty accessible_paths: "
                                f"agent_id={self.agent_id}")
                    else:
                        logger.warning(
                            f"[DIAG] Primary agent detail fetch returned None: "
                            f"agent_id={self.agent_id}")
                except Exception as e:
                    logger.warning(
                        f"[DIAG] Primary agent path pre-caching failed: {e}")

            # ---- 连接 MCP Client ----
            mcp_server_path = getattr(self, '_mcp_server_path', None)
            if mcp_server_path:
                try:
                    await self.mcp_client.connect()
                except BridgeError as e:
                    logger.error(f"MCP Client connection failed, falling back to HTTP API: {e}")
            else:
                logger.info("Skipping MCP Client connection (path not configured)")

            # ---- 等待 MCP 握手完成 ----
            # _discover_agents 需要查询 /v1/mcps 和 /v1/agents，
            # 这些 API 在 MCP 握手完成前可能返回不完整的结果。
            if self._mcp_handshake_event:
                logger.info(
                    "[Phase 2] Waiting for MCP client handshake to complete...")
                try:
                    await asyncio.wait_for(
                        self._mcp_handshake_event.wait(), timeout=60
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[Phase 2] MCP handshake wait timed out (60s), "
                        "proceeding with initialization anyway"
                    )

            # ---- 多 Agent 自动发现 ----
            if self.http_client.legacy_mode:
                await self._discover_agents()
            else:
                logger.info("Non-legacy mode, skipping Agent auto-discovery")

            # ---- LLM Provider Chain ----
            if self.llm_chain:
                await self.llm_chain.initialize()
                admin_qq = self.config.get("admin_qq", "")
                if admin_qq:
                    self.llm_chain._on_switch_callback = (
                        lambda old, new: self._notify_admin_provider_switch(
                            admin_qq, old, new
                        )
                    )
                logger.info(
                    f"LLM Provider Chain initialized ({len(self.llm_chain.providers)} provider(s))")
            else:
                logger.warning("LLM Providers not configured, using CherryStudio API only")

            # ---- Vision Provider Chain ----
            if self.vision_chain:
                await self.vision_chain.initialize()
                logger.info(
                    f"Vision Provider Chain initialized ({len(self.vision_chain.providers)} provider(s))")
            else:
                logger.info("Vision Providers not configured, image recognition unavailable")

            # ---- File Processor ----
            if self.file_processor:
                await self.file_processor.initialize()
                logger.info("File Processor (MinerU) initialized")
            else:
                logger.info("File processing not enabled")

            # ---- Conversation Store ----
            if self.conversation_store:
                logger.info("Conversation Store initialized")
            else:
                logger.info("Conversation Store not enabled")

            # ---- 启动时过期会话扫描 ----
            await self._startup_stale_check()

            self._deferred_init_done = True
            logger.info("[Phase 2] CherryStudio deferred initialization complete")

            # ---- 通知管理员初始化完成 ----
            self._notify_admin_init_complete()

        except Exception as e:
            logger.error(f"[Phase 2] Deferred initialization failed: {e}", exc_info=True)

    async def start(self):
        """启动 CherryStudio 模块"""
        self._running = True
        logger.info("CherryStudio module started")

        while self._running:
            try:
                # 从队列获取消息
                msg = await self.queue.get()

                # ---- 过滤: 是否转发给 Agent 处理 ----
                if not self._should_reply(msg):
                    continue

                # ---- 冷却控制: 同一会话最小间隔 ----
                session_key = msg.session_key
                now = time.monotonic()
                last = self._last_reply_time.get(session_key, 0.0)
                if self._cooldown_seconds > 0 and (now - last) < self._cooldown_seconds:
                    logger.debug(
                        f"Cooldown active, skipping: {session_key} "
                        f"(elapsed {now - last:.1f}s < {self._cooldown_seconds}s)"
                    )
                    continue
                self._last_reply_time[session_key] = now

                # 获取或创建会话处理器
                if session_key not in self.session_handlers:
                    handler = CherryStudioSessionHandler(
                        session_key=session_key,
                        mcp_client=self.mcp_client,
                        http_client=self.http_client,
                        state_manager=self.state_manager,
                        response_queue=self.response_queue,
                        on_cleanup=self._on_session_cleanup,
                        agent_id=self.agent_id,
                        send_queue=self.send_queue,
                        napcat_bridge=self.napcat_bridge,
                        config=self.config,
                        conversation_store=self.conversation_store,
                    )
                    handler.parent_module = self  # 设置父模块引用
                    self.session_handlers[session_key] = handler
                    await handler.start()

                # 添加消息到会话队列
                await self.session_handlers[session_key].add_message(msg)

            except asyncio.CancelledError:
                logger.info("CherryStudio module main loop cancelled")
                break
            except Exception as e:
                logger.error(f"CherryStudio module message processing failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # 3D.4: 管理员通知 — Provider 切换时私信通知 (1小时冷却)
    # ------------------------------------------------------------------

    def _notify_admin_provider_switch(
        self, admin_qq: str, old_name: str, new_name: str
    ):
        """
        当 LLM Provider 发生切换时，通过 send_queue 向管理员发送私信通知。

        此方法作为回调传给 LLMProviderChain._on_switch_callback，
        冷却控制已在 LLMProviderChain._switch_to_next_provider 中完成。
        """
        if not self.send_queue or not admin_qq:
            return
        try:
            from protocols.messages import (
                OutgoingMessage, MessageSource, MessageType,
            )
            text = (
                f"⚠️ LLM Provider 已自动切换:\n"
                f"  原: {old_name}\n"
                f"  新: {new_name}\n"
                f"(1小时内不再重复通知)"
            )
            outgoing = OutgoingMessage(
                target_source=MessageSource.PRIVATE,
                target_id=admin_qq,
                content=text,
                message_type=MessageType.TEXT,
            )
            self.send_queue.put_nowait(outgoing)
            logger.info(f"Sent Provider switch notification to admin {admin_qq}")
        except Exception as e:
            logger.warning(f"Failed to send Provider switch notification: {e}")

    def _notify_admin_init_complete(self):
        """
        初始化完成后，通过 send_queue 向管理员发送私信通知。

        使用与 Send 指令相同的 send_queue -> MessageBus -> NapCatBridge 路径。
        """
        admin_qq = self.config.get("admin_qq", "")
        if not self.send_queue or not admin_qq:
            return
        try:
            agent_count = len(self.discovered_agents)
            agent_names = "、".join(self.discovered_agents.keys()) if self.discovered_agents else "无"
            text = (
                f"✅ 初始化已完成\n"
                f"  Agent: {agent_count} 个 ({agent_names})\n"
                f"  LLM: {'已配置' if self.llm_chain else '未配置'}\n"
                f"  Vision: {'已配置' if self.vision_chain else '未配置'}\n"
                f"  FileProcessor: {'已配置' if self.file_processor else '未配置'}"
            )
            outgoing = OutgoingMessage(
                target_source=MessageSource.PRIVATE,
                target_id=admin_qq,
                content=text,
                message_type=MessageType.TEXT,
            )
            self.send_queue.put_nowait(outgoing)
            logger.info(
                f"Sent initialization complete notification to admin {admin_qq}")
        except Exception as e:
            logger.warning(
                f"Failed to send init complete notification: {e}")

    # ------------------------------------------------------------------
    # 多 Agent 自动发现 + MCP 绑定验证
    # ------------------------------------------------------------------

    async def _discover_agents(self) -> None:
        """
        从 CherryStudio 自动发现 Agent 列表并验证 MCP 绑定。

        移植自旧项目 auto_reply._fetch_agents_from_cherrystudio():
        1. GET /v1/agents 拉取全部 Agent
        2. 按 agent_whitelist 过滤 (若配置)
        3. 通过 GET /v1/agents/{id} 的 mcps 字段验证 MCP 绑定
        4. 结果存入 self.discovered_agents: {name: {agent_id, work_dirs}}
        """
        whitelist: list[str] = self.config.get("agent_whitelist", [])
        whitelist_set = set(whitelist) if whitelist else set()

        # 1. 拉取全部 Agent
        items = await self.http_client.fetch_all_agents()
        if not items:
            cached_count = len(self._agent_paths_by_id)
            if cached_count > 0:
                logger.warning(
                    f"[BRG-3009] Agent list fetch failed, but {cached_count} agent path(s) "
                    f"already pre-cached from detail endpoint. Session creation will use cached paths.")
            elif self._default_accessible_paths:
                logger.warning(
                    f"[BRG-3009] Agent list fetch failed, using default_accessible_paths "
                    f"from config: {self._default_accessible_paths}")
                # 用 agent_id (可能未解析的显示名) 作为 key 存入映射表
                fallback_key = self.agent_id or "default"
                self._agent_paths_by_id[fallback_key] = self._default_accessible_paths
                logger.info(
                    f"[DIAG] Fallback paths loaded: "
                    f"key='{fallback_key}' | paths={self._default_accessible_paths}")
            else:
                logger.warning(
                    "[BRG-3009] Agent list is empty and no paths pre-cached. "
                    "Session creation may fail with 'accessible_paths must not be empty'.")
            return

        # 2. 构建候选字典 + 白名单过滤
        all_names: list[tuple[str, str]] = []
        agents: dict[str, dict] = {}
        for item in items:
            agent_id = item.get("id", "")
            name = item.get("name", agent_id)
            all_names.append((name, agent_id))
            if whitelist_set and agent_id not in whitelist_set:
                logger.debug(f"Agent {name} ({agent_id}) not in whitelist, skipping")
                continue
            agents[name] = {
                "agent_id": agent_id,
                "work_dirs": item.get("accessible_paths", []),
            }

        # 打印全部 Agent 列表 (便于运维排查)
        lines = ["CherryStudio Agent list:"]
        for i, (name, aid) in enumerate(all_names, 1):
            lines.append(f"  {i}: {name} - {aid}")
        logger.info("\n".join(lines))

        # 3. MCP 绑定验证 (仅自动发现模式，即未配置白名单时)
        if not whitelist_set and agents:
            agents = await self._filter_mcp_agents(agents)

        # 4. 输出结果
        if agents:
            active_lines = ["Active Agent list:"]
            for i, (name, cfg) in enumerate(agents.items(), 1):
                active_lines.append(f"  {i}: {name} - {cfg['agent_id']}")
            logger.info("\n".join(active_lines))
            if whitelist_set:
                agent_names = "、".join(agents.keys())
                logger.info(
                    f"Loaded {len(agents)} Agent(s) from whitelist: {agent_names}")
        else:
            if whitelist_set:
                logger.warning("[BRG-3010] No matching Agent in whitelist")
            else:
                logger.warning(
                    "[BRG-3010] No Agent passed MCP binding verification")

        self.discovered_agents = agents

        # 构建 agent_id → accessible_paths 映射表 (创建会话时直接复用)
        self._agent_paths_by_id.clear()
        for _name, _cfg in agents.items():
            _aid = _cfg.get("agent_id", "")
            _paths = _cfg.get("work_dirs", [])
            if _aid:
                self._agent_paths_by_id[_aid] = _paths
                logger.info(
                    f"[DIAG] Agent paths loaded: "
                    f"name='{_name}' | agent_id={_aid} | "
                    f"accessible_paths={_paths}"
                )

    async def _find_bridge_mcp_id(self) -> str:
        """
        从 /v1/mcps 获取桥接 MCP Server 的 ID。

        注意: /v1/mcps 端点会返回所有已注册的 MCP 服务器（包括 stdio 类型），
        但由于 _deferred_init 与 MCP stdio 启动是并行执行的，bridge MCP 可能
        尚未完成握手注册。因此需要足够的重试次数来等待 bridge 出现在列表中。

        策略: 最多尝试 10 次 (共 ~50s)，给 MCP 握手充足的完成时间。

        Returns:
            MCP Server ID，失败返回空字符串。
        """
        mcp_config = self.config.get("cherrystudio", {})
        target_name = (
            self.config.get("mcp_server_name")
            or mcp_config.get("mcp_server_name", "QQ Bridge")
        )

        for attempt in range(10):
            servers = await self.http_client.fetch_mcp_servers()
            if servers:
                server_names = [
                    info.get("name", "?") if isinstance(info, dict) else str(info)
                    for info in servers.values()
                ]
                logger.info(
                    f"MCP list: {len(servers)} server(s) "
                    f"[{', '.join(server_names)[:200]}]"
                )
                # 精确匹配
                for sid, info in servers.items():
                    name = info.get("name", "") if isinstance(info, dict) else ""
                    if name == target_name:
                        logger.info(
                            f"Bridge MCP detected: {sid} ({target_name})")
                        return sid
                # 宽松匹配 (忽略大小写和空格)
                for sid, info in servers.items():
                    name = info.get("name", "") if isinstance(info, dict) else ""
                    if name.lower().replace(" ", "") == target_name.lower().replace(" ", ""):
                        logger.info(
                            f"Bridge MCP detected (fuzzy): {sid} "
                            f"('{name}' ~= '{target_name}')")
                        return sid
                # 首次未匹配时输出诊断
                if attempt == 0:
                    logger.info(
                        f"[DIAG] MCP servers found but '{target_name}' not in list. "
                        f"Available: {server_names}. "
                        f"(bridge MCP may not have completed handshake yet)"
                    )
            else:
                if attempt == 0:
                    logger.info(
                        f"[DIAG] MCP server list empty. "
                        f"CherryStudio may not have registered MCP servers yet. "
                        f"base_url={self.http_client.base_url}"
                    )

            if attempt < 9:
                await asyncio.sleep(5)

        logger.info(
            f"[BRG-3011] Bridge MCP '{target_name}' not found in /v1/mcps "
            f"(gave up after ~50s — MCP handshake may not have completed). "
            f"All discovered Agents will be loaded without MCP binding verification."
        )
        return ""

    async def _filter_mcp_agents(
        self, agents: dict[str, dict]
    ) -> dict[str, dict]:
        """
        通过 /v1/agents/{id} 的 mcps 字段验证 Agent 是否挂载了桥接 MCP。

        移植自旧项目 auto_reply._filter_mcp_agents():
        仅保留 mcps 列表中包含桥接 MCP ID 的 Agent。

        Args:
            agents: {name: {agent_id, work_dirs}} 候选字典

        Returns:
            过滤后的字典 (仅包含绑定了桥接 MCP 的 Agent)
        """
        bridge_mcp_id = await self._find_bridge_mcp_id()
        if not bridge_mcp_id:
            logger.info(
                f"[BRG-3011] Skipping MCP binding verification, "
                f"loading all {len(agents)} discovered Agent(s). "
                f"(stdio MCP servers are not listed in /v1/mcps)"
            )
            return agents

        verified: dict[str, dict] = {}
        for name, cfg in agents.items():
            aid = cfg["agent_id"]
            detail = await self.http_client.fetch_agent_detail(aid)
            if detail:
                mcps = detail.get("mcps", [])
                if bridge_mcp_id in mcps:
                    verified[name] = cfg
                else:
                    logger.info(
                        f"Agent {name} ({aid}) does not have bridge MCP mounted, skipping")
            else:
                logger.info(
                    f"Agent {name} ({aid}) detail fetch failed, skipping")

        return verified

    # ------------------------------------------------------------------
    # 工作区上下文注入 (Phase 2C.4)
    # ------------------------------------------------------------------

    @staticmethod
    def _load_workspace_context(work_dirs: list[str]) -> str:
        """
        加载 Agent 工作区上下文文件: SOUL.md + USER.md + FACT.md。

        移植自旧项目 auto_reply._load_workspace_context():
        work_dirs[0] 是 Agent 独立路径 (存放 SOUL/USER/memory)，
        其余是共享数据库 (当前版本不使用)。

        Args:
            work_dirs: 工作目录列表 (来自 CherryStudio Agent API 的 accessible_paths)

        Returns:
            XML 标签包裹的上下文文本，无文件时返回空字符串
        """
        if not work_dirs:
            return ""

        parts: list[str] = []
        wd = Path(work_dirs[0])

        # SOUL.md + USER.md (在 Agent 工作区根目录)
        for filename in ("SOUL.md", "USER.md"):
            path = wd / filename
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                    if content:
                        parts.append(f"<{filename}>\n{content}\n</{filename}>")
                except Exception as e:
                    logger.debug(f"Failed to read {filename}: {e}")

        # FACT.md (在 memory/ 子目录)
        fact_path = wd / "memory" / "FACT.md"
        if fact_path.exists():
            try:
                content = fact_path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"<FACT.md>\n{content}\n</FACT.md>")
            except Exception as e:
                logger.debug(f"Failed to read FACT.md: {e}")

        return "\n\n".join(parts) if parts else ""

    def _build_injection_context(
        self, agent_name: str, session_key: str
    ) -> str:
        """
        构建新会话首条消息的注入上下文。

        组合三部分 (移植自旧项目 _call_agent_api_once):
        1. 工作区上下文 (SOUL.md + USER.md + FACT.md)
        2. 历史对话摘要 (memory.json)
        3. 全局规则 (config.global_context)

        Args:
            agent_name: Agent 名称
            session_key: 会话键

        Returns:
            组合后的注入文本，无上下文时返回空字符串
        """
        parts: list[str] = []

        # 1. 工作区上下文
        agent_info = self.discovered_agents.get(agent_name, {})
        work_dirs = agent_info.get("work_dirs", [])
        workspace_ctx = self._load_workspace_context(work_dirs)
        if workspace_ctx:
            parts.append(workspace_ctx)

        # 2. 历史对话摘要
        if self.conversation_store:
            try:
                memory = self.conversation_store.memories.get(session_key, "")
                if memory:
                    parts.append(
                        f"<历史对话摘要>\n{memory}\n</历史对话摘要>"
                    )
            except Exception as e:
                logger.debug(f"Failed to get conversation history: {e}")

        # 3. 全局规则
        global_context = self.config.get("global_context", "")
        if global_context:
            parts.append(f"<全局规则>\n{global_context}\n</全局规则>")

        return "\n\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # 过期会话检测 + AI 摘要归档 (Phase 2C.3)
    # ------------------------------------------------------------------

    async def _check_and_archive_stale(
        self, session_key: str, agent_name: str
    ) -> bool:
        """
        检查会话是否过期，如果过期则执行: AI 摘要 → 归档 → 清除记忆。

        移植自旧项目 auto_reply._summarize_and_cleanup():
        1. 检查 meta.last_active 是否超过 3 天
        2. 调用 LLM 生成摘要
        3. 保存摘要到 memory.json
        4. 归档 session.json → session_archive_{timestamp}.json
        5. 清除远程会话映射

        Args:
            session_key: 会话键
            agent_name: Agent 名称

        Returns:
            True 表示会话已过期并归档，False 表示会话仍活跃
        """
        if not self.conversation_store:
            return False

        if not self.conversation_store.is_session_stale(session_key):
            return False

        logger.info(
            f"Session stale (>3 days inactive), triggering summary: {session_key}"
        )

        # 1. 获取消息日志
        messages = self.conversation_store.get_session_messages_sync(session_key)
        if not messages:
            logger.info(f"Stale session has no messages, cleaning up directly: {session_key}")
            await self.conversation_store.summarize_and_archive(
                session_key, agent_name, "(无消息记录)"
            )
            return True

        # 2. 构建日志文本
        log_text = "\n".join(
            f"[{m.get('time', '?')}] "
            f"{m.get('sender', m.get('role', '?'))}: "
            f"{m.get('content', '')[:200]}"
            for m in messages[-100:]
        )

        # 3. 调用 LLM 生成摘要
        summary = await self._summarize_session(log_text)
        if not summary:
            summary = f"(摘要生成失败，原始消息 {len(messages)} 条)"
            logger.warning(f"Summary generation failed, using placeholder: {session_key}")

        # 4. 保存摘要 + 归档
        await self.conversation_store.summarize_and_archive(
            session_key, agent_name, summary
        )
        logger.info(
            f"Session summary archived: {session_key} ({len(summary)} chars)"
        )

        # 5. 归档 session.json 文件
        session_dir = self.conversation_store.base_dir / agent_name / session_key
        log_path = session_dir / "session.json"
        if log_path.exists():
            try:
                import shutil
                import time as _time
                archive_path = session_dir / f"session_archive_{int(_time.time())}.json"
                shutil.move(str(log_path), str(archive_path))
                logger.info(f"session.json archived: {archive_path.name}")
            except Exception as e:
                logger.warning(f"Failed to archive session.json: {e}")

        return True

    async def _summarize_session(self, log_text: str) -> str:
        """
        使用 LLM 生成会话摘要。

        移植自旧项目 auto_reply._summarize():
        优先使用 LLMProviderChain，回退到直接 HTTP API 调用。

        Args:
            log_text: 格式化的聊天记录文本

        Returns:
            摘要文本，失败返回空字符串
        """
        prompt = SUMMARY_PROMPT.format(log=log_text)

        # 优先使用 LLMProviderChain
        if self.llm_chain:
            try:
                result = await self.llm_chain.chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.http_client.default_model or "gpt-3.5-turbo",
                    timeout=60,
                )
                if result:
                    return result.strip()
            except Exception as e:
                logger.warning(f"LLM Chain summary call failed: {e}")

        # 回退: 使用第一个 LLM Provider 的直接 API
        llm_providers = self.config.get("llm_providers", [])
        if not llm_providers:
            return ""

        provider = llm_providers[0]
        base_url = provider.get("base_url", "").rstrip("/")
        api_key = provider.get("api_key", "")
        model = provider.get("model", "gpt-3.5-turbo")

        if not base_url or not api_key:
            return ""

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                url = f"{base_url}/chat/completions"
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "stream": False,
                }
                async with session.post(
                    url,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Summary API returned HTTP {resp.status}: {body[:300]}")
                        return ""
                    data = await resp.json()

            choices = data.get("choices", [])
            if not choices:
                return ""
            msg = choices[0].get("message", {})
            return (msg.get("content", "") or msg.get("reasoning_content", "")).strip()

        except Exception as e:
            logger.warning(f"Direct API summary call failed: {e}")
            return ""

    async def _startup_stale_check(self):
        """
        启动时扫描所有会话，对过期会话执行摘要归档。

        在 initialize() 末尾调用，遍历 ConversationStore 中所有已知会话。
        """
        if not self.conversation_store:
            return

        stale_keys = self.conversation_store.get_stale_session_keys(days_threshold=3)
        if not stale_keys:
            logger.info("Startup check: no stale sessions")
            return

        logger.info(f"Startup check: found {len(stale_keys)} stale session(s), starting summary archival...")

        for session_key in stale_keys:
            agent_name = self.conversation_store.mapping.get(session_key, "default")
            try:
                await self._check_and_archive_stale(session_key, agent_name)
            except Exception as e:
                logger.error(f"Stale session archival failed [{session_key}]: {e}", exc_info=True)

        logger.info(f"Startup check complete: {len(stale_keys)} stale session(s) processed")

    # ------------------------------------------------------------------
    # 自动回复过滤
    # ------------------------------------------------------------------

    def _get_self_qq(self) -> str:
        """获取机器人自身的 QQ 号 (来自 NapCatBridge)"""
        if self.napcat_bridge:
            return self.napcat_bridge.self_qq or ""
        return ""

    def _should_reply(self, msg: ParsedMessage) -> bool:
        """
        判断是否应该对这条消息进行 AI 自动回复。

        过滤规则 (移植自旧项目 auto_reply._should_reply):
        1. auto_reply.enabled 总开关
        2. 空文本过滤
        3. 命令消息拦截 (.xxx 格式走命令系统，不走 Agent)
        4. 自激循环防护 (机器人自己的消息)
        5. .bot off 黑名单群
        6. 群/好友白名单
        7. mention 模式 @检测
        """
        # 1. 总开关
        if not self._auto_reply_enabled:
            return False

        # 2. 空文本过滤 (剥离 @提及后判断，纯 @机器人 无正文视为空)
        if not msg.raw.strip_at_mentions():
            return False

        # 3. 命令消息拦截: .xxx / 。xxx 格式由 CommandModule 处理，不应转发到 Agent
        #    (防御性编程: MessageBus 已将命令互斥分发，此处作为安全兜底)
        if msg.is_command:
            logger.debug(f"Command message intercepted, not forwarding to Agent: {msg.command_name}")
            return False

        # 4. 自激循环防护: 过滤机器人自己发送的消息
        self_qq = self._get_self_qq()
        if self_qq:
            if msg.raw.sender_id == self_qq:
                return False
        else:
            logger.debug("self_qq not set, cannot filter self-triggered messages")

        target_id = msg.raw.target_id
        is_group = msg.raw.source == MessageSource.GROUP

        # 5. .bot off 黑名单群: 仅响应命令，不触发 AI 回复
        if is_group and self.state_manager.is_in_blacklist(target_id):
            return False

        if is_group:
            # 6. 群白名单过滤
            if self._reply_to_groups and target_id not in self._reply_to_groups:
                logger.debug(f"Group {target_id} not in reply whitelist, skipping")
                return False

            # 7. mention 模式: 群聊需 @bot 才触发
            if self._reply_mode == "mention":
                if not self._is_at_me(msg):
                    logger.debug(f"Group {target_id} did not @bot, skipping")
                    return False
        else:
            # 私聊: 好友白名单
            if self._reply_to_friends and target_id not in self._reply_to_friends:
                logger.debug(f"Friend {target_id} not in reply whitelist, skipping")
                return False

        return True

    def _is_at_me(self, msg: ParsedMessage) -> bool:
        """
        检查消息是否 @ 了机器人自己。

        从 raw_data 的 message 段中查找 type="at" 且 qq == self_qq 的段。
        """
        self_qq = self._get_self_qq()
        if not self_qq:
            # 首次 WARNING，后续降级为 DEBUG 避免刷屏
            if not self._self_qq_warned:
                bridge_state = ""
                if self.napcat_bridge:
                    bridge_state = (
                        f" (bridge: connected={self.napcat_bridge.is_connected}, "
                        f"self_qq='{self.napcat_bridge.self_qq}')"
                    )
                else:
                    bridge_state = " (no napcat_bridge)"
                logger.warning(
                    f"[BRG-4006] _is_at_me: self_qq not set, rejecting all @ triggers"
                    f"{bridge_state}. Subsequent occurrences will be logged at DEBUG level."
                )
                self._self_qq_warned = True
            else:
                logger.debug("[BRG-4006] _is_at_me: self_qq not set (suppressed)")
            return False

        raw_message = msg.raw.raw_data.get("message", [])
        if isinstance(raw_message, list):
            for seg in raw_message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq == self_qq:
                        return True
                    logger.debug(
                        f"Group {msg.raw.target_id} @{qq}, "
                        f"not the bot ({self_qq}), skipping"
                    )
        return False

    def _has_at_others(self, msg: ParsedMessage) -> bool:
        """检查消息是否 @ 了非机器人的用户"""
        self_qq = self._get_self_qq()
        if not self_qq:
            return False

        raw_message = msg.raw.raw_data.get("message", [])
        if isinstance(raw_message, list):
            for seg in raw_message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = str(seg.get("data", {}).get("qq", ""))
                    if qq and qq != self_qq:
                        return True
        return False

    async def _on_session_cleanup(self, session_key: str):
        """
        会话清理回调

        当会话处理器退出时调用，从字典中移除
        """
        if session_key in self.session_handlers:
            del self.session_handlers[session_key]
            logger.debug(f"Session handler removed from dict: {session_key}")

    async def stop(self):
        """停止 CherryStudio 模块"""
        self._running = False

        # 停止所有会话处理器（复制列表以避免遍历时修改）
        handlers = list(self.session_handlers.values())
        for handler in handlers:
            await handler.stop()

        self.session_handlers.clear()

        # 关闭 LLM Provider Chain
        if self.llm_chain:
            await self.llm_chain.close()

        # 关闭 Vision Provider Chain
        if self.vision_chain:
            await self.vision_chain.close()

        # 关闭 File Processor
        if self.file_processor:
            await self.file_processor.close()

        # 关闭客户端
        await self.mcp_client.disconnect()
        await self.http_client.close()

        logger.info("CherryStudio module stopped")

    async def rebuild_session(self, session_key: str):
        """
        重建会话 (配置变更时调用)

        Args:
            session_key: 会话键
        """
        if session_key in self.session_handlers:
            # 停止旧会话（这会自动触发清理回调并从字典中删除）
            await self.session_handlers[session_key].stop()
            # 清理回调可能已经删除了会话，使用pop安全删除
            self.session_handlers.pop(session_key, None)

            logger.info(f"Session rebuilt: {session_key}")

    async def reload_config(self):
        """热重载配置"""
        logger.info("Starting hot-reload of CherryStudio module...")

        # 重新初始化客户端
        await self.mcp_client.disconnect()
        await self.http_client.close()

        await self.initialize()
        await self._deferred_init()

        logger.info("CherryStudio module hot-reload complete")
