"""
SSE 流式响应解析器 (SSEParser)

职责:
1. 解析 CherryStudio Agent API 返回的 SSE 流式响应
2. 精确区分思考 (reasoning) 与回复 (text) 内容
3. 跟踪工具调用，判断模型是否已通过 MCP 工具发送消息
4. 处理停滞检测、超时保护、session_not_found 错误
5. 返回结构化的 SSEResult，由调用方决定如何发送

设计原则:
- SSEParser 只负责解析，不负责发送消息或管理会话
- 通过 notify_callback 回调与调用方通信（如停滞通知）
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 输出类 MCP 工具名称集合 — 调用这些工具意味着模型已自行发送了消息
# ---------------------------------------------------------------------------
OUTPUT_TOOL_NAMES: set[str] = {
    "qq_send_message",
    "qq_send_image",
    "qq_upload_file",
}

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SSETextBlock:
    """SSE 流中提取的一个文本块"""
    text: str
    is_reasoning: bool = False      # True = 思考内容, False = 回复内容
    is_tool_result: bool = False    # True = 来自 finish-step 的工具结果文本
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class SSEToolCall:
    """SSE 流中检测到的一次工具调用"""
    tool_name: str                  # 去除 mcp__*__ 前缀后的工具名
    raw_name: str                   # 原始工具名（含前缀）
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class SSEResult:
    """SSE 流的完整解析结果"""
    reply_blocks: list[SSETextBlock] = field(default_factory=list)
    reasoning_blocks: list[SSETextBlock] = field(default_factory=list)
    tool_calls: list[SSEToolCall] = field(default_factory=list)
    had_output_tool: bool = False           # 是否调用了输出类工具
    pre_tool_reply_blocks: list[SSETextBlock] = field(default_factory=list)
    error: str | None = None                # 错误信息
    session_not_found: bool = False         # 是否收到 session_not_found 错误
    stalled: bool = False                   # 是否因停滞而提前终止
    total_duration: float = 0.0             # 总耗时（秒）

    def get_reply_text(self, pre_tool_text_policy: str = "keep") -> str:
        """
        根据策略获取最终回复文本。

        Args:
            pre_tool_text_policy: "keep" 保留工具前文本, "discard" 全部丢弃

        Returns:
            拼接后的回复文本，无内容时返回空字符串
        """
        if self.had_output_tool:
            if pre_tool_text_policy == "keep" and self.pre_tool_reply_blocks:
                return "\n\n".join(
                    b.text for b in self.pre_tool_reply_blocks if b.text
                )
            # discard 策略 或 工具前无文本
            return ""

        # 无输出类工具 → 返回所有回复文本
        return "\n\n".join(b.text for b in self.reply_blocks if b.text)


# ---------------------------------------------------------------------------
# 工具名提取: 去除 mcp__<server>__ 前缀 (支持 server 名含下划线)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CherryDebugLogger — 按群/会话粒度的 SSE 内容拼接追踪日志
# ---------------------------------------------------------------------------

class CherryDebugLogger:
    """
    SSE 内容拼接调试日志器

    当 config.cherry_debug = True 时启用:
    - 每次 Agent 调用在系统 Temp 目录创建独立日志文件
    - 文件名格式: cherry_debug_{group_id}_{timestamp}.log
    - 记录每次内容拼接的: 时间戳、调用模块、拼接前内容、拼接后内容
    """

    def __init__(self, session_key: str = "unknown"):
        """
        Args:
            session_key: 会话标识 (通常为群 ID 或私聊 ID)
        """
        self._session_key = session_key
        self._log_path: str | None = None
        self._log_file = None
        self._start_time = time.monotonic()
        self._entry_count = 0
        self._init_log_file()

    def _init_log_file(self):
        """创建调试日志文件"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 清理 session_key 中的非法文件名字符
            safe_key = "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in self._session_key
            )
            filename = f"cherry_debug_{safe_key}_{timestamp}.log"
            temp_dir = tempfile.gettempdir()
            self._log_path = os.path.join(temp_dir, filename)
            self._log_file = open(self._log_path, "a", encoding="utf-8")
            self._write_header()
        except Exception as e:
            logger.warning(f"CherryDebugLogger: failed to create log file: {e}")
            self._log_file = None

    def _write_header(self):
        """写入日志文件头部"""
        if not self._log_file:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_file.write(
            f"{'=' * 80}\n"
            f"CherryDebug SSE Trace Log\n"
            f"Session Key : {self._session_key}\n"
            f"Created At  : {now}\n"
            f"Log File    : {self._log_path}\n"
            f"{'=' * 80}\n\n"
        )
        self._log_file.flush()

    def log(self, module: str, action: str, detail: str = "",
            content_before: str = "", content_after: str = ""):
        """
        记录一次内容拼接事件

        Args:
            module: 调用模块名称 (如 "SSEParser", "SessionHandler", "VisionChain")
            action: 操作描述 (如 "text-delta append", "reply_blocks join")
            detail: 附加说明
            content_before: 拼接前的内容片段 (截断到 500 字符)
            content_after: 拼接后的内容片段 (截断到 500 字符)
        """
        if not self._log_file:
            return
        self._entry_count += 1
        elapsed = time.monotonic() - self._start_time
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        self._log_file.write(
            f"[#{self._entry_count:04d}] {now} (+{elapsed:.1f}s)\n"
            f"  Module : {module}\n"
            f"  Action : {action}\n"
        )
        if detail:
            self._log_file.write(f"  Detail : {detail}\n")
        if content_before:
            preview = content_before[:500].replace("\n", "\\n")
            self._log_file.write(f"  Before : {preview}\n")
        if content_after:
            preview = content_after[:500].replace("\n", "\\n")
            self._log_file.write(f"  After  : {preview}\n")
        self._log_file.write("\n")
        self._log_file.flush()

    def log_separator(self, title: str):
        """写入分隔标记，便于在日志中快速定位阶段"""
        if not self._log_file:
            return
        elapsed = time.monotonic() - self._start_time
        self._log_file.write(
            f"\n{'─' * 40} {title} {'─' * 40}\n"
            f"  Elapsed: {elapsed:.1f}s\n\n"
        )
        self._log_file.flush()

    def close(self):
        """关闭日志文件并写入摘要"""
        if not self._log_file:
            return
        elapsed = time.monotonic() - self._start_time
        self._log_file.write(
            f"\n{'=' * 80}\n"
            f"Trace Complete: {self._entry_count} entries, {elapsed:.1f}s total\n"
            f"{'=' * 80}\n"
        )
        self._log_file.flush()
        self._log_file.close()
        self._log_file = None
        logger.info(
            f"CherryDebug log saved: {self._log_path} "
            f"({self._entry_count} entries, {elapsed:.1f}s)"
        )

    @property
    def log_path(self) -> str | None:
        return self._log_path

    def __del__(self):
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------


def _extract_tool_name(obj: dict) -> str:
    """从 SSE JSON 对象中提取工具名（去除 mcp__*__ 前缀）"""
    raw = (
        obj.get("toolName", "")
        or obj.get("name", "")
        or obj.get("function", {}).get("name", "")
    )
    if not raw:
        return ""
    # 处理 mcp__<server>__<tool> 格式 (server 名可能含下划线)
    if raw.startswith("mcp__"):
        # 去掉 "mcp__" 前缀 (5 chars)，找到剩余的 "__" 分隔符
        rest = raw[5:]
        sep_idx = rest.find("__")
        if sep_idx != -1:
            return rest[sep_idx + 2:]
        return rest
    return raw


def _extract_response_text(response: dict | str | list | None) -> str:
    """
    从 finish-step 的 response 字段提取纯文本。

    兼容:
    - dict: 依次检查 text / content / output / message
    - str: 直接返回
    - list: 拼接所有文本项
    """
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, list):
        parts = []
        for item in response:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content", "output", "message"):
                    val = item.get(key)
                    if isinstance(val, str):
                        parts.append(val)
                        break
        return "\n".join(parts)
    if isinstance(response, dict):
        for key in ("text", "content", "output", "message"):
            val = response.get(key)
            if isinstance(val, str):
                return val
            if isinstance(val, list):
                return "\n".join(str(v) for v in val)
    return ""


# ---------------------------------------------------------------------------
# 流式去重: 检测后缀-前缀重叠
# ---------------------------------------------------------------------------
_MIN_OVERLAP_LEN = 4  # 最小重叠长度，避免合法重复文本被误判


def _deduplicate_text(prev_text: str, new_text: str) -> str:
    """
    检测 prev_text 尾部与 new_text 头部的重叠并去除。

    仅当重叠长度 >= _MIN_OVERLAP_LEN 时才触发。
    """
    if not prev_text or not new_text:
        return new_text

    min_len = min(len(prev_text), len(new_text))
    overlap = 0
    for i in range(min_len, _MIN_OVERLAP_LEN - 1, -1):
        if prev_text.endswith(new_text[:i]):
            overlap = i
            break

    if overlap > 0:
        return new_text[overlap:]
    return new_text


# ---------------------------------------------------------------------------
# 跳过事件类型白名单 — 这些事件不应被误判为工具调用
# ---------------------------------------------------------------------------
_SKIP_EVENT_TYPES: set[str] = {
    "start",
    "raw",
    "start-step",
    "reasoning-start",
    "reasoning-end",
    "tool-input-start",
    "tool-input-delta",
    "tool-input-end",
    "ping",
}

# ---------------------------------------------------------------------------
# 进度事件类型 — 文档记录，实际重置逻辑在各事件处理器内按条件执行
# reasoning-* 事件虽然代表 AI 在工作，但属于"思考中"而非"产出进度"，
# 不重置停滞计数，确保长时间纯思考仍可被停滞检测捕获
# 重置 stall_count 的路径:
#   - text-delta (非 reasoning 阶段的文本生成)
#   - text-end   (非 reasoning 的文本块完成)
#   - finish-step (步骤完成)
#   - finish      (流结束)
#   - error       (Agent 已处理)
#   - 工具调用     (兜底检测中的动态类型事件)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SSEParser
# ---------------------------------------------------------------------------

class SSEParser:
    """
    独立的 SSE 流式响应解析器。

    用法::

        async with http_session.post(url, json=body, timeout=timeout) as resp:
            parser = SSEParser(...)
            result = await parser.parse(resp)

    解析结果通过 SSEResult 返回，调用方根据 result.get_reply_text() 决定发送行为。
    """

    # 默认配置
    DEFAULT_STALL_TIMEOUT = 30       # 单次 readline 超时（秒）
    DEFAULT_TOTAL_TIMEOUT = 600      # 总超时（秒）
    DEFAULT_MAX_STALL_RETRIES = 4    # 最大停滞重试次数
    NOTIFY_INTERVAL = 25             # 停滞通知最小间隔（秒）

    def __init__(
        self,
        stall_timeout: int = DEFAULT_STALL_TIMEOUT,
        total_timeout: int = DEFAULT_TOTAL_TIMEOUT,
        max_stall_retries: int = DEFAULT_MAX_STALL_RETRIES,
        pre_tool_text_policy: str = "keep",
        notify_callback: Callable[[str], Awaitable[None]] | None = None,
        alive_callback: Callable[[str], Awaitable[None]] | None = None,
        debug_logger: CherryDebugLogger | None = None,
        debug_logging: bool = False,
    ):
        """
        Args:
            stall_timeout: 单次 readline 超时秒数
            total_timeout: 总超时秒数
            max_stall_retries: 最大停滞重试次数
            pre_tool_text_policy: "keep" 或 "discard"
            notify_callback: 停滞通知回调，接收消息字符串
            alive_callback: 存活通知回调，当 Agent 在检测间隔内有内容产出
                            (证明存活) 但尚未完成时调用，接收消息字符串
            debug_logger: CherryDebug 日志器 (cherry_debug=True 时传入)
            debug_logging: 是否输出 SSE 进度/工具检测等 stderr 日志
        """
        self._stall_timeout = stall_timeout
        self._total_timeout = total_timeout
        self._max_stall_retries = max_stall_retries
        self._pre_tool_text_policy = pre_tool_text_policy
        self._notify_callback = notify_callback
        self._alive_callback = alive_callback
        self._dbg = debug_logger
        self._debug_logging = debug_logging

    async def parse(self, response: aiohttp.ClientResponse) -> SSEResult:
        """
        解析 SSE 流式响应。

        Args:
            response: aiohttp POST 请求的响应对象

        Returns:
            SSEResult 结构化解析结果
        """
        start_time = time.monotonic()
        result = SSEResult()

        if self._dbg:
            self._dbg.log_separator("SSE Parse Start")
            self._dbg.log("SSEParser", "parse() begin",
                          f"stall_timeout={self._stall_timeout}s, "
                          f"total_timeout={self._total_timeout}s, "
                          f"max_retries={self._max_stall_retries}")

        # ---- 状态变量 ----
        reply_blocks: list[SSETextBlock] = []
        reasoning_blocks: list[SSETextBlock] = []
        tool_calls: list[SSEToolCall] = []

        current_deltas: list[str] = []          # text-start ~ text-end 之间的增量
        reasoning_deltas: list[str] = []        # reasoning 阶段的增量（仅调试）
        in_text = False                         # 是否在 text 输出阶段
        in_reasoning = False                    # 是否在 reasoning 思考阶段

        had_output_tool = False                 # 是否调用了输出类工具
        pre_tool_blocks: list[SSETextBlock] = []  # 工具调用前的文本快照

        stall_count = 0                         # 连续停滞次数
        has_any_output = False                  # 是否曾有过任何输出
        last_notify_time = 0.0                  # 上次通知时间
        _alive_this_interval = False            # 本轮 stall_timeout 间隔内是否有事件到达

        # ---- 事件计数器 (用于进度摘要和诊断) ----
        _event_count = 0                        # 总事件数
        _event_type_counts: dict[str, int] = {} # 按类型统计
        _last_progress_log = 0.0                # 上次进度日志时间
        _raw_line_count = 0                     # 原始行计数 (含空行/non-data)

        try:
            while True:
                # -- 总超时检查 --
                elapsed = time.monotonic() - start_time
                if elapsed > self._total_timeout:
                    logger.warning(f"SSE total timeout {self._total_timeout}s")
                    result.stalled = True
                    break

                # -- 行级读取（带停滞检测）--
                try:
                    line_bytes = await asyncio.wait_for(
                        response.content.readline(),
                        timeout=self._stall_timeout,
                    )
                    # 注意: 不再在此处重置 stall_count
                    # ping/heartbeat 等保活事件也会触发 readline 成功，
                    # 但它们不代表 AI 有实际内容产出。
                    # stall_count 仅在下方解析到内容事件时才重置。

                except asyncio.TimeoutError:
                    if _alive_this_interval:
                        # ---- Agent 存活: 本轮间隔内有事件到达 ----
                        # Agent 正在工作 (可能在思考/推理) 但尚未完成
                        _alive_this_interval = False  # 重置，开始下一轮检测
                        now = time.monotonic()
                        _elapsed_alive = int(now - start_time)
                        _type_summary = ", ".join(
                            f"{k}:{v}" for k, v in sorted(
                                _event_type_counts.items(), key=lambda x: -x[1]
                            )
                        )
                        if (
                            self._alive_callback
                            and now - last_notify_time > self.NOTIFY_INTERVAL
                        ):
                            msg = f"Agent 正在思考中……已运行 {_elapsed_alive}s"
                            try:
                                await self._alive_callback(msg)
                            except Exception:
                                pass
                            last_notify_time = now
                        if self._debug_logging:
                            logger.info(
                                f"SSE alive check: Agent alive (events={_event_count}, "
                                f"stall={stall_count}, elapsed={_elapsed_alive}s) "
                                f"types=[{_type_summary}]"
                            )
                        if self._dbg:
                            self._dbg.log(
                                "SSEParser", "ALIVE (readline timeout)",
                                f"events={_event_count}, "
                                f"stall_count={stall_count}, "
                                f"elapsed={_elapsed_alive}s, "
                                f"has_any_output={has_any_output}, "
                                f"types=[{_type_summary}]",
                            )
                        continue
                    elif has_any_output:
                        # ---- Agent 停滞: 本轮间隔内无内容产出 ----
                        stall_count += 1
                        if stall_count >= self._max_stall_retries:
                            logger.warning(
                                f"SSE stall limit reached ({stall_count}/{self._max_stall_retries})"
                            )
                            result.stalled = True
                            break
                        # 未达上限 → 发送停滞通知
                        now = time.monotonic()
                        if (
                            self._notify_callback
                            and now - last_notify_time > self.NOTIFY_INTERVAL
                        ):
                            count = stall_count
                            max_r = self._max_stall_retries
                            msg = f"小企鹅正在烧烤中呜……({count}/{max_r})"
                            try:
                                await self._notify_callback(msg)
                            except Exception:
                                pass
                            last_notify_time = now
                        continue
                    else:
                        # 从未有过输出 → 判定为完全无响应
                        logger.warning("SSE received no output, treated as unresponsive")
                        result.stalled = True
                        if self._notify_callback:
                            try:
                                await self._notify_callback(
                                    "小企鹅看不懂拉，您发的太深奥了拉"
                                )
                            except Exception:
                                pass
                        break

                # -- EOF --
                if not line_bytes:
                    if self._dbg:
                        self._dbg.log("SSEParser", "EOF",
                                      f"raw_lines={_raw_line_count}, "
                                      f"events={_event_count}")
                    break

                _raw_line_count += 1
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # -- data: [DONE] --
                if line == "data: [DONE]":
                    if self._dbg:
                        self._dbg.log("SSEParser", "data: [DONE]",
                                      f"raw_lines={_raw_line_count}, "
                                      f"events={_event_count}")
                    break

                # -- 只处理 data: 行 --
                if not line.startswith("data:"):
                    if self._dbg:
                        self._dbg.log("SSEParser", f"NON-DATA LINE #{_raw_line_count}",
                                      f"line='{line[:200]}'")
                    continue

                data_str = line[5:].strip()
                if not data_str:
                    continue

                # -- 解析 JSON --
                try:
                    obj = json.loads(data_str)
                except (json.JSONDecodeError, ValueError):
                    continue

                if not isinstance(obj, dict):
                    continue

                # ==== 事件计数 + 原始事件追踪 ====
                t = obj.get("type", "")
                _event_count += 1
                _event_type_counts[t] = _event_type_counts.get(t, 0) + 1

                # CherryDebug: 记录所有原始 SSE 事件
                if self._dbg:
                    # 截取 obj 的关键字段，避免日志过大
                    _raw_keys = list(obj.keys())
                    _raw_type = t or "(empty)"
                    _raw_text = str(obj.get("text", ""))[:100]
                    _raw_tool = obj.get("toolName", obj.get("name", ""))
                    _detail = f"keys={_raw_keys}"
                    if _raw_text:
                        _detail += f", text='{_raw_text}'"
                    if _raw_tool:
                        _detail += f", tool={_raw_tool}"
                    self._dbg.log(
                        "SSEParser", f"RAW EVENT #{_event_count}",
                        f"type={_raw_type}, {_detail}",
                    )

                # 定期输出进度摘要 (每 30 秒或每 50 个事件)
                _now_progress = time.monotonic()
                if (
                    _event_count % 50 == 0
                    or _now_progress - _last_progress_log > 30
                ):
                    _elapsed_p = int(_now_progress - start_time)
                    _type_summary = ", ".join(
                        f"{k}:{v}" for k, v in sorted(
                            _event_type_counts.items(), key=lambda x: -x[1]
                        )
                    )
                    if self._debug_logging:
                        logger.info(
                            f"SSE progress: {_elapsed_p}s | "
                            f"events={_event_count} | "
                            f"reply_blocks={len(reply_blocks)} | "
                            f"tool_calls={len(tool_calls)} | "
                            f"stall={stall_count} | "
                            f"types=[{_type_summary}]"
                        )
                    _last_progress_log = _now_progress

                    if self._dbg:
                        self._dbg.log(
                            "SSEParser", "PROGRESS SUMMARY",
                            f"elapsed={_elapsed_p}s, events={_event_count}, "
                            f"reply_blocks={len(reply_blocks)}, "
                            f"reasoning_blocks={len(reasoning_blocks)}, "
                            f"tool_calls={len(tool_calls)}, "
                            f"stall_count={stall_count}, "
                            f"types=[{_type_summary}]",
                        )

                # ==== 按事件类型分发 ====
                # (t 已在上方事件计数阶段赋值)

                # -- 存活追踪: 任何已解析事件都证明连接存活 --
                if t:
                    _alive_this_interval = True
                # 注意: stall_count 的重置在各事件处理器内部按条件执行，
                # 仅在实际进度 (非 reasoning 的文本生成、工具调用、步骤完成) 时重置

                # -- 类别 A: 回复文本事件 --
                if t == "text-start":
                    current_deltas = []
                    in_text = True
                    if self._dbg:
                        self._dbg.log("SSEParser", "text-start",
                                      f"in_reasoning={in_reasoning}")

                elif t == "text-delta":
                    if in_text and not in_reasoning:
                        text_fragment = str(obj.get("text", ""))
                        if text_fragment:
                            # ---- Snapshot 自动检测 ----
                            # 如果 text_fragment 以当前已累积文本开头，说明这是
                            # 完整快照 (snapshot) 而非增量 (delta)。
                            # 此时应覆盖而非追加，避免重复。
                            current_accumulated = "".join(current_deltas)
                            if (
                                current_accumulated
                                and len(text_fragment) > len(current_accumulated)
                                and text_fragment.startswith(current_accumulated)
                            ):
                                # Snapshot 模式: 覆盖已累积内容
                                if self._dbg:
                                    self._dbg.log(
                                        "SSEParser", "text-delta SNAPSHOT detected",
                                        f"prev_accumulated={len(current_accumulated)}, "
                                        f"snapshot_len={len(text_fragment)}",
                                        content_before=current_accumulated[:200],
                                        content_after=text_fragment[:200],
                                    )
                                current_deltas = [text_fragment]
                            elif (
                                current_accumulated
                                and current_accumulated
                                and len(text_fragment) <= len(current_accumulated)
                                and current_accumulated.startswith(text_fragment)
                            ):
                                # 新 fragment 是已累积文本的子集 → 重复数据，跳过
                                if self._dbg:
                                    self._dbg.log(
                                        "SSEParser", "text-delta DUPLICATE skipped",
                                        f"fragment_len={len(text_fragment)}, "
                                        f"accumulated_len={len(current_accumulated)}",
                                    )
                            else:
                                # 正常增量模式: 追加
                                current_deltas.append(text_fragment)
                            has_any_output = True
                            stall_count = 0  # 实际文本生成 = 进度
                            if self._dbg and text_fragment not in ("",):
                                self._dbg.log(
                                    "SSEParser", "text-delta (reply)",
                                    f"fragment_len={len(text_fragment)}, "
                                    f"deltas_count={len(current_deltas)}",
                                    content_after=text_fragment,
                                )
                    elif in_reasoning:
                        # 收集 reasoning delta（仅调试，不用于回复）
                        # 注意: reasoning delta 不重置 stall_count
                        rd = str(obj.get("text", ""))
                        if rd:
                            reasoning_deltas.append(rd)
                            if self._dbg:
                                self._dbg.log(
                                    "SSEParser", "text-delta (reasoning)",
                                    f"fragment_len={len(rd)}, "
                                    f"deltas_count={len(reasoning_deltas)}",
                                    content_after=rd,
                                )

                elif t == "text-end":
                    if current_deltas and not in_reasoning:
                        text = "".join(current_deltas).strip()
                        if self._dbg:
                            self._dbg.log(
                                "SSEParser", "text-end JOIN (reply)",
                                f"deltas={len(current_deltas)}, "
                                f"joined_len={len(text)}",
                                content_before="[...]".join(
                                    d[:30] for d in current_deltas[:5]
                                ),
                                content_after=text,
                            )
                        if text:
                            # 流式去重
                            if reply_blocks:
                                prev = reply_blocks[-1].text
                                original_text = text
                                text = _deduplicate_text(prev, text)
                                if self._dbg and text != original_text:
                                    self._dbg.log(
                                        "SSEParser", "DEDUP applied",
                                        f"prev_tail='{prev[-50:]}' | "
                                        f"before_dedup_len={len(original_text)} | "
                                        f"after_dedup_len={len(text)}",
                                        content_before=original_text,
                                        content_after=text,
                                    )
                            if text:
                                block = SSETextBlock(text=text, is_reasoning=False)
                                reply_blocks.append(block)
                                has_any_output = True
                                stall_count = 0  # 文本块完成 = 进度
                                if self._dbg:
                                    self._dbg.log(
                                        "SSEParser", "reply_blocks APPEND",
                                        f"block #{len(reply_blocks)}, "
                                        f"text_len={len(text)}, "
                                        f"total_blocks={len(reply_blocks)}",
                                        content_after=text,
                                    )
                    elif current_deltas and in_reasoning:
                        # reasoning 阶段的 text-end → 归入 reasoning_blocks
                        text = "".join(current_deltas).strip()
                        if self._dbg:
                            self._dbg.log(
                                "SSEParser", "text-end JOIN (reasoning)",
                                f"deltas={len(current_deltas)}, "
                                f"joined_len={len(text)}",
                                content_after=text,
                            )
                        if text:
                            reasoning_blocks.append(
                                SSETextBlock(text=text, is_reasoning=True)
                            )
                            if self._dbg:
                                self._dbg.log(
                                    "SSEParser", "reasoning_blocks APPEND",
                                    f"block #{len(reasoning_blocks)}, "
                                    f"text_len={len(text)}",
                                    content_after=text,
                                )
                    current_deltas = []
                    in_text = False

                # -- 类别 B: 思考文本事件 --
                elif t == "reasoning-start":
                    in_reasoning = True
                    reasoning_deltas = []
                    if self._dbg:
                        self._dbg.log("SSEParser", "reasoning-start")

                elif t == "reasoning-end":
                    in_reasoning = False
                    if self._dbg:
                        self._dbg.log(
                            "SSEParser", "reasoning-end",
                            f"deltas_collected={len(reasoning_deltas)}",
                        )
                    # 保存已收集的 reasoning 内容
                    if reasoning_deltas:
                        text = "".join(reasoning_deltas).strip()
                        if self._dbg:
                            self._dbg.log(
                                "SSEParser", "reasoning-end JOIN",
                                f"deltas={len(reasoning_deltas)}, "
                                f"joined_len={len(text)}",
                                content_after=text,
                            )
                        if text:
                            reasoning_blocks.append(
                                SSETextBlock(text=text, is_reasoning=True)
                            )
                            if self._dbg:
                                self._dbg.log(
                                    "SSEParser", "reasoning_blocks APPEND",
                                    f"block #{len(reasoning_blocks)}, "
                                    f"text_len={len(text)}",
                                    content_after=text,
                                )
                    reasoning_deltas = []

                elif t == "reasoning-delta":
                    # 收集 reasoning 增量内容（CherryStudio 可能用 reasoning-delta 而非 text-delta）
                    rd = str(obj.get("text", ""))
                    if rd:
                        reasoning_deltas.append(rd)
                        has_any_output = True
                        if self._dbg:
                            self._dbg.log(
                                "SSEParser", "reasoning-delta",
                                f"fragment_len={len(rd)}, "
                                f"deltas_count={len(reasoning_deltas)}",
                                content_after=rd,
                            )

                # -- 类别 D: 步骤完成事件 --
                elif t == "finish-step":
                    stall_count = 0  # 步骤完成 = 明确进度
                    raw_response = obj.get("response")
                    if self._dbg:
                        resp_type = type(raw_response).__name__
                        resp_preview = str(raw_response)[:300] if raw_response else "None"
                        self._dbg.log(
                            "SSEParser", "finish-step",
                            f"had_output_tool={had_output_tool}, "
                            f"response_type={resp_type}, "
                            f"response_preview={resp_preview}",
                        )
                    if not had_output_tool:
                        response_text = _extract_response_text(raw_response)
                        if self._dbg:
                            self._dbg.log(
                                "SSEParser", "finish-step EXTRACT",
                                f"extracted_len={len(response_text)}, "
                                f"stripped_len={len(response_text.strip())}",
                                content_after=response_text,
                            )
                        if response_text and response_text.strip():
                            response_text = response_text.strip()

                            # ---- Snapshot 去重: 防止 finish-step 的完整快照与
                            #      text-delta 增量累积重复 ----
                            existing_text = "\n\n".join(
                                b.text for b in reply_blocks if b.text
                            ).strip()

                            _dedup_action = "append"  # 默认: 新内容，追加
                            if existing_text:
                                if response_text == existing_text:
                                    # finish-step 完全等于已累积文本 → 跳过
                                    _dedup_action = "skip_identical"
                                elif response_text in existing_text:
                                    # finish-step 是已累积文本的子集 → 跳过
                                    _dedup_action = "skip_subset"
                                elif existing_text in response_text:
                                    # finish-step 是已累积文本的超集 (更完整) → 替换
                                    _dedup_action = "replace_superset"
                                elif response_text.startswith(existing_text):
                                    # finish-step 以已累积文本为前缀 → snapshot 风格，替换
                                    _dedup_action = "replace_superset"
                                elif existing_text.startswith(response_text):
                                    # 已累积文本以 finish-step 为前缀 → 跳过
                                    _dedup_action = "skip_subset"
                                else:
                                    # 检测后缀-前缀重叠 (existing_text 尾部 == response_text 头部)
                                    _overlap = _deduplicate_text(existing_text, response_text)
                                    if _overlap != response_text:
                                        # 检测到重叠: 仅保留非重叠部分
                                        response_text = _overlap
                                        _dedup_action = "append_deduped"
                                # else: 不同内容 → 正常追加

                            if self._dbg:
                                self._dbg.log(
                                    "SSEParser", "finish-step DEDUP",
                                    f"action={_dedup_action}, "
                                    f"existing_len={len(existing_text)}, "
                                    f"response_len={len(response_text)}",
                                    content_before=existing_text[:200],
                                    content_after=response_text[:200],
                                )

                            if _dedup_action == "skip_identical":
                                if self._debug_logging:
                                    logger.info(
                                        "finish-step text identical to accumulated text, skipped (snapshot dedup)")
                            elif _dedup_action == "skip_subset":
                                if self._debug_logging:
                                    logger.info(
                                        "finish-step text is subset of accumulated text, skipped (snapshot dedup)")
                            elif _dedup_action == "replace_superset":
                                # finish-step 包含更完整的内容，用其替换
                                reply_blocks = [
                                    SSETextBlock(
                                        text=response_text,
                                        is_reasoning=False,
                                        is_tool_result=False,
                                    )
                                ]
                                has_any_output = True
                                if self._debug_logging:
                                    logger.info(
                                        f"finish-step text is superset of accumulated text, "
                                        f"replaced with full snapshot ({len(response_text)} chars)")
                            else:
                                # 不同内容或去重后的追加
                                reply_blocks.append(
                                    SSETextBlock(
                                        text=response_text,
                                        is_reasoning=False,
                                        is_tool_result=False,
                                    )
                                )
                                has_any_output = True
                                if _dedup_action == "append_deduped":
                                    if self._debug_logging:
                                        logger.info(
                                            f"finish-step text overlaps with accumulated text, "
                                            f"appended after dedup ({len(response_text)} chars)")
                                if self._dbg:
                                    self._dbg.log(
                                        "SSEParser", f"finish-step APPEND ({_dedup_action})",
                                        f"block #{len(reply_blocks)}, "
                                        f"text_len={len(response_text)}",
                                        content_after=response_text,
                                    )
                    elif self._dbg:
                        self._dbg.log(
                            "SSEParser", "finish-step DISCARDED",
                            "had_output_tool=True, finish-step text treated as tool result",
                        )
                    # 若 had_output_tool，finish-step 文本视为工具结果，丢弃

                elif t == "finish":
                    # 流结束标记 — 最终进度
                    stall_count = 0
                    break

                # -- 类别 E: 跳过事件 --
                elif t in _SKIP_EVENT_TYPES:
                    continue

                # -- 类别 E: 错误事件 --
                elif t == "error":
                    stall_count = 0  # 错误事件表明 Agent 已处理 = 进度

                    # CherryStudio 可能以两种格式发送 error 事件:
                    # 扁平: {"type": "error", "message": "...", "code": "session_not_found"}
                    # 嵌套: {"type": "error", "error": {"message": "...", "code": "session_not_found"}}
                    _err_obj = obj.get("error", {}) if isinstance(obj.get("error"), dict) else {}

                    err_msg = str(
                        obj.get("message", "")
                        or _err_obj.get("message", "")
                        or obj.get("error", "")
                        or obj.get("detail", "")
                    )
                    result.error = err_msg
                    logger.error(f"SSE error event: {err_msg}")

                    # 检测 session_not_found (兼容扁平 + 嵌套两种格式)
                    code = obj.get("code", "") or _err_obj.get("code", "")
                    if code == "session_not_found":
                        result.session_not_found = True
                        logger.warning("SSE: session_not_found, will trigger session rebuild")
                    continue

                # -- 类别 C: 工具调用事件（兜底检测）--
                else:
                    tool_name = _extract_tool_name(obj)
                    if not tool_name:
                        # 非工具事件，跳过
                        continue

                    # 工具调用是明确的进度 → 重置停滞计数
                    # (工具事件类型名动态，无法通过静态类型名匹配)
                    stall_count = 0

                    # 记录工具调用
                    raw_name = (
                        obj.get("toolName", "")
                        or obj.get("name", "")
                        or obj.get("function", {}).get("name", "")
                    )
                    tc = SSEToolCall(tool_name=tool_name, raw_name=raw_name)
                    tool_calls.append(tc)
                    if self._debug_logging:
                        logger.info(f"SSE tool call detected: {tool_name}")

                    if self._dbg:
                        self._dbg.log(
                            "SSEParser", "TOOL CALL detected",
                            f"tool_name={tool_name}, "
                            f"raw_name={raw_name}, "
                            f"is_output_tool={tool_name in OUTPUT_TOOL_NAMES}, "
                            f"total_tool_calls={len(tool_calls)}",
                        )

                    # 判断是否为输出类工具
                    if tool_name in OUTPUT_TOOL_NAMES:
                        had_output_tool = True
                        # 快照工具调用前的文本块
                        pre_tool_blocks = list(reply_blocks)
                        if self._dbg:
                            snapshot_text = "\n\n".join(
                                b.text for b in pre_tool_blocks if b.text
                            )
                            self._dbg.log(
                                "SSEParser", "OUTPUT TOOL — snapshot",
                                f"pre_tool_blocks={len(pre_tool_blocks)}, "
                                f"snapshot_len={len(snapshot_text)}",
                                content_before=snapshot_text,
                            )
                        # 清空当前回复块（后续文本将被丢弃）
                        reply_blocks = []
                        if self._dbg:
                            self._dbg.log(
                                "SSEParser", "OUTPUT TOOL — reply_blocks CLEARED",
                                "All subsequent text-delta/finish-step text will be discarded",
                            )

        except asyncio.TimeoutError:
            # 外层总超时保护
            logger.warning(f"SSE outer total timeout: {self._total_timeout}s")
            result.stalled = True

        except aiohttp.ClientError as e:
            logger.error(f"SSE connection error: {e}")
            result.error = str(e)

        except Exception as e:
            logger.error(f"SSE parse exception: {e}", exc_info=True)
            result.error = str(e)

        # ---- 组装结果 ----
        result.reply_blocks = reply_blocks
        result.reasoning_blocks = reasoning_blocks
        result.tool_calls = tool_calls
        result.had_output_tool = had_output_tool
        result.pre_tool_reply_blocks = pre_tool_blocks
        result.total_duration = time.monotonic() - start_time

        if self._dbg:
            self._dbg.log_separator("SSE Parse Result Assembly")
            # 记录所有 reply_blocks
            for i, block in enumerate(reply_blocks):
                self._dbg.log(
                    "SSEParser", f"reply_blocks[{i}]",
                    f"text_len={len(block.text)}, "
                    f"is_reasoning={block.is_reasoning}, "
                    f"is_tool_result={block.is_tool_result}",
                    content_after=block.text,
                )
            # 记录所有 reasoning_blocks
            for i, block in enumerate(reasoning_blocks):
                self._dbg.log(
                    "SSEParser", f"reasoning_blocks[{i}]",
                    f"text_len={len(block.text)}",
                    content_after=block.text,
                )
            # 记录所有 tool_calls
            for i, tc in enumerate(tool_calls):
                self._dbg.log(
                    "SSEParser", f"tool_calls[{i}]",
                    f"tool_name={tc.tool_name}, raw_name={tc.raw_name}",
                )
            # 记录汇总
            all_reply = "\n\n".join(b.text for b in reply_blocks if b.text)
            _type_summary_final = ", ".join(
                f"{k}:{v}" for k, v in sorted(
                    _event_type_counts.items(), key=lambda x: -x[1]
                )
            )
            self._dbg.log(
                "SSEParser", "FINAL ASSEMBLY",
                f"total_events={_event_count}, "
                f"raw_lines={_raw_line_count}, "
                f"event_types=[{_type_summary_final}], "
                f"reply_blocks={len(reply_blocks)}, "
                f"reasoning_blocks={len(reasoning_blocks)}, "
                f"tool_calls={len(tool_calls)}, "
                f"had_output_tool={had_output_tool}, "
                f"stalled={result.stalled}, "
                f"duration={result.total_duration:.1f}s, "
                f"final_reply_len={len(all_reply)}",
                content_after=all_reply,
            )

        _type_summary_final = ", ".join(
            f"{k}:{v}" for k, v in sorted(
                _event_type_counts.items(), key=lambda x: -x[1]
            )
        )
        if self._debug_logging:
            logger.info(
                f"SSE parse complete: "
                f"events={_event_count} | "
                f"reply_blocks={len(result.reply_blocks)}, "
                f"reasoning_blocks={len(result.reasoning_blocks)}, "
                f"tool_calls={len(result.tool_calls)}, "
                f"had_output_tool={had_output_tool}, "
                f"stalled={result.stalled}, "
                f"duration={result.total_duration:.1f}s | "
                f"types=[{_type_summary_final}]"
            )

        return result
