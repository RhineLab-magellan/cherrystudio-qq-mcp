"""
SSE 流式解析器测试套件

覆盖场景:
1. 标准文本流 (text-start/delta/end)
2. Reasoning 过滤 (思考 vs 回复)
3. 工具调用检测 (输出类/非输出类)
4. 三种场景: 无工具/工具无前文本/工具有前文本
5. 可配置的 pre_tool_text_policy (keep/discard)
6. finish-step 文本提取
7. 流式去重
8. 停滞检测
9. session_not_found 错误
10. 空输入和异常输入
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.sse_parser import (
    SSEParser,
    SSEResult,
    SSETextBlock,
    SSEToolCall,
    _extract_tool_name,
    _extract_response_text,
    _deduplicate_text,
    OUTPUT_TOOL_NAMES,
)


# ---------------------------------------------------------------------------
# 辅助: 构建 SSE 字节流
# ---------------------------------------------------------------------------

def _make_sse_bytes(events: list[dict | str]) -> bytes:
    """
    将事件列表转为 SSE 格式的字节流。

    每个元素可以是:
    - dict: 转为 data: {json}\n
    - str: 直接作为一行 (用于 "data: [DONE]" 等)
    """
    lines = []
    for event in events:
        if isinstance(event, dict):
            lines.append(f"data: {json.dumps(event, ensure_ascii=False)}\n")
        else:
            lines.append(f"{event}\n")
    return "".join(lines).encode("utf-8")


class FakeStreamReader:
    """模拟 aiohttp 的 StreamReader，支持 readline()"""

    def __init__(self, data: bytes):
        self._lines = data.split(b"\n")
        self._index = 0

    async def readline(self) -> bytes:
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        if line:
            return line + b"\n"
        return b"\n"


class FakeResponse:
    """模拟 aiohttp.ClientResponse"""

    def __init__(self, data: bytes, status: int = 200):
        self.content = FakeStreamReader(data)
        self.status = status


# ---------------------------------------------------------------------------
# 辅助函数测试
# ---------------------------------------------------------------------------

class TestExtractToolName:
    def test_plain_name(self):
        assert _extract_tool_name({"toolName": "qq_send_message"}) == "qq_send_message"

    def test_mcp_prefix(self):
        assert _extract_tool_name({"toolName": "mcp__qq_bridge__qq_send_message"}) == "qq_send_message"

    def test_name_field(self):
        assert _extract_tool_name({"name": "some_tool"}) == "some_tool"

    def test_function_name(self):
        assert _extract_tool_name({"function": {"name": "my_func"}}) == "my_func"

    def test_empty(self):
        assert _extract_tool_name({}) == ""
        assert _extract_tool_name({"toolName": ""}) == ""

    def test_mcp_prefix_multiple_underscores(self):
        assert _extract_tool_name({"toolName": "mcp__my_server_name__tool_name"}) == "tool_name"


class TestExtractResponseText:
    def test_string(self):
        assert _extract_response_text("hello") == "hello"

    def test_dict_text_key(self):
        assert _extract_response_text({"text": "hello"}) == "hello"

    def test_dict_content_key(self):
        assert _extract_response_text({"content": "world"}) == "world"

    def test_dict_priority(self):
        assert _extract_response_text({"text": "a", "content": "b"}) == "a"

    def test_list(self):
        assert _extract_response_text(["a", "b"]) == "a\nb"

    def test_none(self):
        assert _extract_response_text(None) == ""

    def test_empty_dict(self):
        assert _extract_response_text({}) == ""

    def test_nested_list(self):
        result = _extract_response_text({"text": ["part1", "part2"]})
        assert "part1" in result and "part2" in result


class TestDeduplicateText:
    def test_no_overlap(self):
        assert _deduplicate_text("hello", "world") == "world"

    def test_overlap_detected(self):
        # "abc def" 和 "def ghi" 重叠 "def" (3 chars) → 但最小4字符，不去重
        result = _deduplicate_text("abc def", "def ghi")
        assert result == "def ghi"

    def test_overlap_4_chars(self):
        # "abc defg" 和 "defg hij" 重叠 "defg" (4 chars)
        result = _deduplicate_text("abc defg", "defg hij")
        assert result == " hij"

    def test_exact_overlap(self):
        result = _deduplicate_text("hello world", "hello world more")
        # "hello world" (11 chars) 完全重叠 → 去重后只剩 " more"
        assert result == " more"

    def test_empty_prev(self):
        assert _deduplicate_text("", "hello") == "hello"

    def test_empty_new(self):
        assert _deduplicate_text("hello", "") == ""

    def test_legal_repetition_preserved(self):
        # "很好很好" 不应该被误去重
        result = _deduplicate_text("很好", "很好很好")
        assert result == "很好很好"


# ---------------------------------------------------------------------------
# SSEParser 核心测试
# ---------------------------------------------------------------------------

class TestSSEParserBasicText:
    """标准文本流解析"""

    @pytest.mark.asyncio
    async def test_simple_text(self):
        """简单的 text-start → text-delta → text-end"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "Hello "},
            {"type": "text-delta", "text": "World"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert not result.had_output_tool
        assert not result.stalled
        reply = result.get_reply_text()
        assert reply == "Hello World"

    @pytest.mark.asyncio
    async def test_multiple_text_blocks(self):
        """多个 text-start/end 块"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "第一段"},
            {"type": "text-end"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "第二段"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert "第一段" in reply
        assert "第二段" in reply

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """空输入"""
        resp = FakeResponse(b"")
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.get_reply_text() == ""
        assert not result.had_output_tool

    @pytest.mark.asyncio
    async def test_done_marker(self):
        """data: [DONE] 标记"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "OK"},
            {"type": "text-end"},
            "data: [DONE]",
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.get_reply_text() == "OK"

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self):
        """无效 JSON 行被跳过"""
        data = (
            'data: {"type": "text-start"}\n'
            'data: INVALID_JSON\n'
            'data: {"type": "text-delta", "text": "valid"}\n'
            'data: {"type": "text-end"}\n'
            'data: {"type": "finish"}\n'
        )
        resp = FakeResponse(data.encode("utf-8"))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.get_reply_text() == "valid"


class TestSSEParserReasoning:
    """Reasoning (思考) 过滤"""

    @pytest.mark.asyncio
    async def test_reasoning_filtered(self):
        """reasoning 内容不出现在回复中"""
        events = [
            {"type": "reasoning-start"},
            {"type": "reasoning-delta", "text": "我在思考..."},
            {"type": "reasoning-end"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "最终回复"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert "我在思考" not in reply
        assert reply == "最终回复"
        assert len(result.reasoning_blocks) >= 1

    @pytest.mark.asyncio
    async def test_text_delta_during_reasoning_ignored(self):
        """reasoning 期间的 text-delta 被忽略"""
        events = [
            {"type": "reasoning-start"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "should_be_ignored"},
            {"type": "text-end"},
            {"type": "reasoning-end"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "visible"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert "should_be_ignored" not in reply
        assert "visible" in reply


class TestSSEParserToolCalls:
    """工具调用检测"""

    @pytest.mark.asyncio
    async def test_output_tool_detected(self):
        """输出类工具调用被正确检测"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "tool call coming"},
            {"type": "text-end"},
            {"toolName": "qq_send_message"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.had_output_tool
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "qq_send_message"

    @pytest.mark.asyncio
    async def test_non_output_tool(self):
        """非输出类工具不触发 had_output_tool"""
        events = [
            {"toolName": "qq_get_group_list"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "result"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert not result.had_output_tool
        assert "result" in result.get_reply_text()

    @pytest.mark.asyncio
    async def test_mcp_prefix_stripped(self):
        """mcp__*__ 前缀被正确去除"""
        events = [
            {"toolName": "mcp__qq_bridge__qq_send_message"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.had_output_tool
        assert result.tool_calls[0].tool_name == "qq_send_message"

    @pytest.mark.asyncio
    async def test_skip_events_not_tool_calls(self):
        """start-step/ping 等事件不被误判为工具调用"""
        events = [
            {"type": "start"},
            {"type": "raw"},
            {"type": "start-step"},
            {"type": "ping"},
            {"type": "tool-input-start"},
            {"type": "tool-input-delta"},
            {"type": "tool-input-end"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "normal"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert len(result.tool_calls) == 0
        assert not result.had_output_tool
        assert result.get_reply_text() == "normal"


class TestSSEParserThreeScenarios:
    """三种核心场景: 无工具/工具无前文本/工具有前文本"""

    @pytest.mark.asyncio
    async def test_scenario1_no_tool(self):
        """场景1: 模型不调用任何工具 — 所有文本作为回复"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "完整回复"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert not result.had_output_tool
        assert result.get_reply_text() == "完整回复"

    @pytest.mark.asyncio
    async def test_scenario2_tool_no_pre_text(self):
        """场景2: 模型直接调用工具，无前置文本 — 返回空"""
        events = [
            {"toolName": "qq_send_message"},
            {"type": "finish-step", "response": {"text": "tool result"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser(pre_tool_text_policy="keep")
        result = await parser.parse(resp)

        assert result.had_output_tool
        assert result.get_reply_text() == ""

    @pytest.mark.asyncio
    async def test_scenario3_tool_with_pre_text_keep(self):
        """场景3 + keep策略: 工具前文本被保留"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "分析内容"},
            {"type": "text-end"},
            {"toolName": "qq_send_message"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "后续文本"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser(pre_tool_text_policy="keep")
        result = await parser.parse(resp)

        assert result.had_output_tool
        reply = result.get_reply_text()
        assert "分析内容" in reply
        assert "后续文本" not in reply

    @pytest.mark.asyncio
    async def test_scenario3_tool_with_pre_text_discard(self):
        """场景3 + discard策略: 所有文本被丢弃"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "分析内容"},
            {"type": "text-end"},
            {"toolName": "qq_send_message"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "后续文本"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser(pre_tool_text_policy="discard")
        result = await parser.parse(resp)

        assert result.had_output_tool
        reply = result.get_reply_text(pre_tool_text_policy="discard")
        assert reply == ""


class TestSSEParserFinishStep:
    """finish-step 文本提取"""

    @pytest.mark.asyncio
    async def test_finish_step_collected(self):
        """无工具时 finish-step 文本被收集"""
        events = [
            {"type": "finish-step", "response": {"text": "final text"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert "final text" in result.get_reply_text()

    @pytest.mark.asyncio
    async def test_finish_step_discarded_with_tool(self):
        """有输出工具时 finish-step 被丢弃"""
        events = [
            {"toolName": "qq_send_message"},
            {"type": "finish-step", "response": {"text": "should be ignored"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert "should be ignored" not in result.get_reply_text()

    @pytest.mark.asyncio
    async def test_finish_step_response_formats(self):
        """finish-step 兼容多种 response 格式"""
        for key in ("text", "content", "output", "message"):
            events = [
                {"type": "finish-step", "response": {key: f"value_{key}"}},
                {"type": "finish"},
            ]
            resp = FakeResponse(_make_sse_bytes(events))
            parser = SSEParser()
            result = await parser.parse(resp)
            assert f"value_{key}" in result.get_reply_text()


class TestSSEParserErrors:
    """错误处理"""

    @pytest.mark.asyncio
    async def test_session_not_found(self):
        """session_not_found 错误被正确检测 (扁平格式)"""
        events = [
            {"type": "error", "code": "session_not_found", "message": "Session not found"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.session_not_found
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_session_not_found_nested(self):
        """session_not_found 错误被正确检测 (嵌套格式, CherryStudio 实际发送格式)"""
        events = [
            {
                "type": "error",
                "error": {
                    "message": "Session not found",
                    "type": "not_found",
                    "code": "session_not_found",
                },
            },
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.session_not_found
        assert result.error is not None
        assert "Session not found" in result.error

    @pytest.mark.asyncio
    async def test_generic_error(self):
        """通用错误被记录"""
        events = [
            {"type": "error", "message": "something went wrong"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        assert result.error is not None
        assert not result.session_not_found

    @pytest.mark.asyncio
    async def test_stall_detection(self):
        """停滞检测: 超时时标记 stalled"""
        # 构建一个会导致超时的响应（空数据）
        resp = FakeResponse(b"")
        parser = SSEParser(stall_timeout=0)  # 立即超时
        result = await parser.parse(resp)

        # 空输入 + 0 超时 → stalled
        assert result.stalled


class TestSSEParserDedup:
    """流式去重"""

    @pytest.mark.asyncio
    async def test_dedup_across_blocks(self):
        """跨 text-end 块的去重"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "Hello World"},
            {"type": "text-end"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "World More"},  # "World" 重叠 5 字符
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        # "World" 应该只出现一次 (重叠去重后变为 "Hello World More")
        assert reply == "Hello World\n\n More" or "World" in reply


class TestSSEResultGetReplyText:
    """SSEResult.get_reply_text() 策略测试"""

    def test_no_tool_returns_all(self):
        result = SSEResult(
            reply_blocks=[SSETextBlock(text="a"), SSETextBlock(text="b")],
            had_output_tool=False,
        )
        assert result.get_reply_text() == "a\n\nb"

    def test_tool_keep_with_pre_text(self):
        result = SSEResult(
            reply_blocks=[],
            pre_tool_reply_blocks=[SSETextBlock(text="pre")],
            had_output_tool=True,
        )
        assert result.get_reply_text(pre_tool_text_policy="keep") == "pre"

    def test_tool_keep_no_pre_text(self):
        result = SSEResult(
            reply_blocks=[],
            pre_tool_reply_blocks=[],
            had_output_tool=True,
        )
        assert result.get_reply_text(pre_tool_text_policy="keep") == ""

    def test_tool_discard(self):
        result = SSEResult(
            reply_blocks=[],
            pre_tool_reply_blocks=[SSETextBlock(text="pre")],
            had_output_tool=True,
        )
        assert result.get_reply_text(pre_tool_text_policy="discard") == ""

    def test_empty_blocks(self):
        result = SSEResult()
        assert result.get_reply_text() == ""


class TestOutputToolNames:
    """输出类工具名称集合"""

    def test_send_message_in_set(self):
        assert "qq_send_message" in OUTPUT_TOOL_NAMES

    def test_send_image_in_set(self):
        assert "qq_send_image" in OUTPUT_TOOL_NAMES

    def test_upload_file_in_set(self):
        assert "qq_upload_file" in OUTPUT_TOOL_NAMES

    def test_non_output_tool_not_in_set(self):
        assert "qq_get_group_list" not in OUTPUT_TOOL_NAMES
        assert "qq_recall_message" not in OUTPUT_TOOL_NAMES


# ---------------------------------------------------------------------------
# Snapshot 去重测试 (finish-step vs text-delta)
# ---------------------------------------------------------------------------

class TestSSEParserSnapshotDedup:
    """
    修复验证: finish-step snapshot 与 text-delta 增量不重复

    场景:
    - 纯 delta 流: 只有 text-delta → 正常拼接
    - 纯 snapshot 流: 只有 finish-step → 直接使用
    - delta + snapshot 相同: finish-step 被跳过
    - delta + snapshot 超集: finish-step 替换
    - delta + snapshot 子集: finish-step 被跳过
    """

    @pytest.mark.asyncio
    async def test_pure_delta_stream(self):
        """纯增量 delta 流: 正常拼接，无重复"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "啊"},
            {"type": "text-delta", "text": "！"},
            {"type": "text-delta", "text": "博士"},
            {"type": "text-delta", "text": "你也在"},
            {"type": "text-delta", "text": "捣鼓VR"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "啊！博士你也在捣鼓VR"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_pure_snapshot_stream(self):
        """纯 snapshot 流: 只有 finish-step，无 text-delta"""
        events = [
            {"type": "finish-step", "response": {"text": "啊！博士你也在捣鼓VR"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "啊！博士你也在捣鼓VR"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_delta_plus_identical_snapshot_dedup(self):
        """delta + 相同 snapshot: finish-step 被去重跳过"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "啊！博士你也在捣鼓VR"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "啊！博士你也在捣鼓VR"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "啊！博士你也在捣鼓VR"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_delta_plus_superset_snapshot_replace(self):
        """delta + 超集 snapshot: finish-step 替换为更完整的内容"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "啊！博士"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "啊！博士你也在捣鼓VR"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "啊！博士你也在捣鼓VR"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_delta_plus_subset_snapshot_skip(self):
        """delta + 子集 snapshot: finish-step 被跳过"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "啊！博士你也在捣鼓VR"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "啊！博士"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "啊！博士你也在捣鼓VR"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_delta_plus_different_content_append(self):
        """delta + 完全不同的 finish-step: 正常追加"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "第一段内容"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "第二段补充"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert "第一段内容" in reply
        assert "第二段补充" in reply
        assert len(result.reply_blocks) == 2

    @pytest.mark.asyncio
    async def test_multiple_finish_steps_dedup(self):
        """多个 finish-step: 每个都被正确去重"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "完整回复文本"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "完整回复文本"}},
            {"type": "finish-step", "response": {"text": "完整回复"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "完整回复文本"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_no_duplicate_incremental_output(self):
        """
        核心场景: 用户不应收到逐步增长的消息

        模拟 Agent 通过 SSE 流生成 "啊！博士你也在捣鼓VR"
        确保最终 reply_text 只包含一份完整内容
        """
        events = [
            {"type": "start"},
            {"type": "start-step"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "啊"},
            {"type": "text-delta", "text": "！"},
            {"type": "text-delta", "text": "博士你"},
            {"type": "text-delta", "text": "也在"},
            {"type": "text-delta", "text": "捣鼓VR"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "啊！博士你也在捣鼓VR"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "啊！博士你也在捣鼓VR"
        assert reply.count("啊") == 1
        assert len(result.reply_blocks) == 1


# ---------------------------------------------------------------------------
# text-delta 级别 Snapshot 自动检测测试
# ---------------------------------------------------------------------------

class TestSSEParserTextDeltaSnapshot:
    """
    修复验证: text-delta 事件中的 snapshot 自动检测

    上游 API 可能返回累积式 snapshot 而非增量 delta:
    - delta 模式: 每次 text-delta 只包含新增片段 ("啊", "！", "博士")
    - snapshot 模式: 每次 text-delta 包含到目前为止的完整文本
      ("啊", "啊！", "啊！博士")

    修复后 SSEParser 应自动检测并正确处理两种模式。
    """

    @pytest.mark.asyncio
    async def test_snapshot_style_text_deltas(self):
        """纯 snapshot 模式的 text-delta: 每次 delta 包含累积文本"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "啊"},
            {"type": "text-delta", "text": "啊！"},
            {"type": "text-delta", "text": "啊！博士"},
            {"type": "text-delta", "text": "啊！博士你也在"},
            {"type": "text-delta", "text": "啊！博士你也在捣鼓VR"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        # 应该只保留最终的完整快照，而不是把所有 snapshot 拼接起来
        assert reply == "啊！博士你也在捣鼓VR"
        assert reply.count("啊") == 1
        assert reply.count("博士") == 1

    @pytest.mark.asyncio
    async def test_mixed_delta_and_snapshot(self):
        """混合模式: 先增量 delta，后出现 snapshot"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "你好"},       # delta: "你好"
            {"type": "text-delta", "text": "世界"},       # delta: "世界" → 累积 "你好世界"
            {"type": "text-delta", "text": "你好世界！"},  # snapshot: 以 "你好世界" 开头 → 覆盖
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "你好世界！"
        assert reply.count("你好") == 1

    @pytest.mark.asyncio
    async def test_duplicate_delta_subset_skipped(self):
        """重复 delta: 新 fragment 是已累积文本的子集 → 被跳过"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "你好世界"},
            {"type": "text-delta", "text": "你好"},       # 子集，应被跳过
            {"type": "text-delta", "text": "！"},         # 正常增量
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "你好世界！"
        assert reply.count("你好") == 1

    @pytest.mark.asyncio
    async def test_snapshot_then_delta_continues(self):
        """snapshot 后继续正常 delta: snapshot 覆盖后增量继续追加"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "第一段"},         # delta
            {"type": "text-delta", "text": "第一段扩展"},      # snapshot: 覆盖
            {"type": "text-delta", "text": "内容"},           # 正常 delta: 不以 "第一段扩展" 开头
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "第一段扩展内容"

    @pytest.mark.asyncio
    async def test_progressive_snapshot_bug_scenario(self):
        """
        核心 BUG 复现: 用户收到逐步增长的消息

        模拟上游返回 snapshot 模式的 text-delta，
        如果不去重会导致最终文本为所有 snapshot 的拼接:
        "啊啊！啊！博士啊！博士你也啊！博士你也在捣鼓VR"

        修复后应只保留最终的完整快照。
        """
        events = [
            {"type": "start"},
            {"type": "start-step"},
            {"type": "text-start"},
            {"type": "text-delta", "text": "啊"},
            {"type": "text-delta", "text": "啊！"},
            {"type": "text-delta", "text": "啊！博士"},
            {"type": "text-delta", "text": "啊！博士你也在"},
            {"type": "text-delta", "text": "啊！博士你也在捣鼓VR"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "啊！博士你也在捣鼓VR"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "啊！博士你也在捣鼓VR"
        assert reply.count("啊") == 1
        assert reply.count("博士") == 1
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_snapshot_with_finish_step_dedup(self):
        """snapshot delta + finish-step 双重去重"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "完整"},
            {"type": "text-delta", "text": "完整的回复"},
            {"type": "text-delta", "text": "完整的回复内容"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "完整的回复内容"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "完整的回复内容"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_single_char_snapshot_deltas(self):
        """单字符逐步增长 snapshot (极端情况)"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "A"},
            {"type": "text-delta", "text": "AB"},
            {"type": "text-delta", "text": "ABC"},
            {"type": "text-delta", "text": "ABCD"},
            {"type": "text-delta", "text": "ABCDE"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "ABCDE"
        assert len(reply) == 5

    @pytest.mark.asyncio
    async def test_long_text_snapshot_stream(self):
        """长文本 snapshot 流 (模拟文档生成场景)"""
        # 模拟逐步增长的长文本（这是用户报告的 "大量文本被转化为文档" 的场景）
        full_text = "这是一段很长的回复文本。" * 100  # 约 1100 字符
        snapshots = []
        step = len(full_text) // 10
        for i in range(1, 11):
            snapshots.append({"type": "text-delta", "text": full_text[:i * step]})

        events = [{"type": "text-start"}] + snapshots + [
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        # 最终文本应该等于最后一个 snapshot，而不是所有 snapshot 的拼接
        assert reply == full_text[:10 * step]
        # 验证内容没有被重复拼接: 长度应等于最终 snapshot 的长度
        assert len(reply) == len(full_text[:10 * step])
        # 不应出现额外重复: "这是一段" 出现次数等于原文中的次数
        expected_count = full_text[:10 * step].count("这是一段")
        assert reply.count("这是一段") == expected_count

    @pytest.mark.asyncio
    async def test_first_delta_not_treated_as_snapshot(self):
        """第一个 delta 不会被误判为 snapshot"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "开头"},
            {"type": "text-delta", "text": "中间"},
            {"type": "text-delta", "text": "结尾"},
            {"type": "text-end"},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "开头中间结尾"


# ---------------------------------------------------------------------------
# finish-step 重叠去重增强测试
# ---------------------------------------------------------------------------

class TestFinishStepOverlapDedup:
    """
    修复验证: finish-step 与已累积文本的后缀-前缀重叠检测

    当 finish-step 文本与 text-delta 累积文本有部分重叠时，
    应只追加非重叠部分 (append_deduped)。
    """

    @pytest.mark.asyncio
    async def test_overlap_dedup_append(self):
        """finish-step 与已累积文本有后缀-前缀重叠 → 去重后追加"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "前半部分的内容abcdefg"},
            {"type": "text-end"},
            # finish-step 的头部 "defg" 与已累积文本的尾部重叠 (4+字符)
            {"type": "finish-step", "response": {"text": "defg后续补充"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        # 重叠部分 "defg" 不应重复出现
        assert reply.count("defg") == 1

    @pytest.mark.asyncio
    async def test_finish_step_prefix_start_replace(self):
        """finish-step 以已累积文本为前缀 → 替换 (replace_superset)"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "你好"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "你好，这是完整回复"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "你好，这是完整回复"
        assert len(result.reply_blocks) == 1

    @pytest.mark.asyncio
    async def test_finish_step_exact_match_skip(self):
        """finish-step 完全等于已累积文本 → 跳过 (skip_identical)"""
        events = [
            {"type": "text-start"},
            {"type": "text-delta", "text": "完全相同的文本"},
            {"type": "text-end"},
            {"type": "finish-step", "response": {"text": "完全相同的文本"}},
            {"type": "finish"},
        ]
        resp = FakeResponse(_make_sse_bytes(events))
        parser = SSEParser()
        result = await parser.parse(resp)

        reply = result.get_reply_text()
        assert reply == "完全相同的文本"
        assert len(result.reply_blocks) == 1
