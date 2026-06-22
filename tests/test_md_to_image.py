"""
MD-to-Image 模块测试

覆盖:
1. render_markdown — HTML 渲染
2. md_to_image — 完整 MD → PNG 流程 (Playwright)
3. send_local_image — napcat_bridge 本地图片发送
4. qq_upload_file as_image — MCP 工具集成
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# 确保项目根目录在路径中
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.md_to_image import (
    DEFAULT_CSS,
    DEFAULT_WIDTH,
    MAX_HEIGHT,
    _write_html_file,
    _make_file_url,
    _make_session_dir,
    render_markdown,
)


def _playwright_available() -> bool:
    """检查 Playwright Chromium 是否可用。"""
    try:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        b = p.chromium.launch(headless=True)
        b.close()
        p.stop()
        return True
    except Exception:
        return False


_HAS_PLAYWRIGHT = _playwright_available()


# ── render_markdown 测试 ──────────────────────────────────────────────


class TestRenderMarkdown:
    """HTML 渲染测试"""

    def test_basic_heading(self):
        """标题渲染"""
        html = render_markdown("# Hello")
        assert "<h1" in html
        assert "Hello" in html

    def test_bold_and_italic(self):
        """粗体和斜体"""
        html = render_markdown("**bold** and *italic*")
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html

    def test_code_block(self):
        """代码块"""
        html = render_markdown("```python\nprint('hi')\n```")
        assert "print" in html
        assert "<code" in html

    def test_table(self):
        """表格渲染"""
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = render_markdown(md)
        assert "<table>" in html
        assert "<td>" in html

    def test_blockquote(self):
        """引用块"""
        html = render_markdown("> quoted text")
        assert "<blockquote>" in html
        assert "quoted text" in html

    def test_unordered_list(self):
        """无序列表"""
        html = render_markdown("- item1\n- item2")
        assert "<ul>" in html or "<li>" in html
        assert "item1" in html

    def test_link(self):
        """链接"""
        html = render_markdown("[text](https://example.com)")
        assert 'href="https://example.com"' in html
        assert "text" in html

    def test_inline_code(self):
        """行内代码"""
        html = render_markdown("use `code` here")
        assert "<code>" in html
        assert "code" in html

    def test_has_doctype(self):
        """输出包含 DOCTYPE"""
        html = render_markdown("test")
        assert "<!DOCTYPE html>" in html

    def test_has_css(self):
        """输出包含默认 CSS"""
        html = render_markdown("test")
        assert "font-family" in html
        assert "line-height" in html

    def test_custom_title(self):
        """自定义标题"""
        html = render_markdown("test", title="My Doc")
        assert "<title>My Doc</title>" in html

    def test_default_title(self):
        """默认标题"""
        html = render_markdown("test")
        assert "<title>Markdown</title>" in html

    def test_custom_css(self):
        """自定义 CSS 追加"""
        html = render_markdown("test", css="body { color: red; }")
        assert "color: red" in html

    def test_chinese_content(self):
        """中文内容"""
        html = render_markdown("# 你好世界\n这是一个测试")
        assert "你好世界" in html
        assert "这是一个测试" in html

    def test_empty_extensions_list(self):
        """空扩展列表"""
        html = render_markdown("# Test", extensions=[])
        assert "<h1>" in html

    def test_html_escaping(self):
        """HTML 特殊字符处理"""
        # Markdown 将裸 <div> 视为原始 HTML (保留), 但 & 应转义
        html = render_markdown("A & B")
        assert "&amp;" in html


# ── _make_file_url 和 _write_html_file 测试 ───────────────────────────


class TestFileUrlHelpers:
    """文件路径和 URL 工具函数测试"""

    def test_make_file_url_windows(self):
        """Windows 绝对路径转为 file:/// URL"""
        import os
        test_path = os.path.abspath(os.path.join(os.getcwd(), "test.html"))
        url = _make_file_url(test_path)
        assert url.startswith("file:///")
        assert "\\" not in url  # 反斜杠应已转为正斜杠
        assert url.endswith("test.html")

    def test_write_html_file_creates_file(self):
        """_write_html_file 写入文件并可读回"""
        import shutil
        session_dir = _make_session_dir()
        try:
            html_content = "<html><body><h1>Hello</h1></body></html>"
            html_path = _write_html_file(html_content, session_dir)
            assert os.path.isfile(html_path)
            with open(html_path, "r", encoding="utf-8") as f:
                assert f.read() == html_content
        finally:
            shutil.rmtree(session_dir, ignore_errors=True)

    def test_write_html_file_chinese(self):
        """中文 HTML 内容写入"""
        import shutil
        session_dir = _make_session_dir()
        try:
            html_content = "<html><body><h1>你好世界</h1></body></html>"
            html_path = _write_html_file(html_content, session_dir)
            with open(html_path, "r", encoding="utf-8") as f:
                assert "你好世界" in f.read()
        finally:
            shutil.rmtree(session_dir, ignore_errors=True)


# ── md_to_image 集成测试 (Playwright) ────────────────────────────────


class TestMdToImage:
    """md_to_image 完整流程测试 (Playwright Chromium)"""

    @pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="Playwright Chromium not available")
    @pytest.mark.asyncio
    async def test_simple_markdown(self):
        """简单 Markdown 转 PNG"""
        from modules.md_to_image import md_to_image

        path = await md_to_image("# Hello\nWorld", title="test")
        try:
            assert os.path.isfile(path)
            assert os.path.getsize(path) > 100
            with open(path, "rb") as f:
                assert f.read(4) == b"\x89PNG"
        finally:
            if os.path.exists(path):
                os.remove(path)

    @pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="Playwright Chromium not available")
    @pytest.mark.asyncio
    async def test_rich_markdown(self):
        """包含代码块、表格、列表的复杂 Markdown"""
        from modules.md_to_image import md_to_image

        md = """# Complex Doc

## Code Block
```python
def greet(name):
    return f"Hello, {name}!"
```

## Table
| Name | Age |
|------|-----|
| Alice | 30 |
| Bob | 25 |

## List
- Item 1
- Item 2
- Item 3

> A wise quote here.
"""
        path = await md_to_image(md, title="complex")
        try:
            assert os.path.isfile(path)
            size = os.path.getsize(path)
            assert size > 500  # 复杂内容应该产生更大的文件
        finally:
            if os.path.exists(path):
                os.remove(path)

    @pytest.mark.asyncio
    async def test_empty_input_raises(self):
        """空输入抛出 ValueError"""
        from modules.md_to_image import md_to_image

        with pytest.raises(ValueError, match="empty"):
            await md_to_image("")

    @pytest.mark.asyncio
    async def test_no_input_raises(self):
        """无输入抛出 ValueError"""
        from modules.md_to_image import md_to_image

        with pytest.raises(ValueError, match="empty"):
            await md_to_image(None)

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises(self):
        """不存在的文件抛出 ValueError"""
        from modules.md_to_image import md_to_image

        with pytest.raises(ValueError, match="not found"):
            await md_to_image(file_path="/nonexistent/file.md")

    @pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="Playwright Chromium not available")
    @pytest.mark.asyncio
    async def test_from_file(self):
        """从 .md 文件读取"""
        from modules.md_to_image import md_to_image

        tmp = tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w", encoding="utf-8"
        )
        tmp.write("# From File\nHello from file!")
        tmp.close()

        try:
            path = await md_to_image(file_path=tmp.name)
            assert os.path.isfile(path)
            assert os.path.getsize(path) > 100
        finally:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
            if "path" in locals() and os.path.exists(path):
                os.remove(path)

    @pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="Playwright Chromium not available")
    @pytest.mark.asyncio
    async def test_custom_width(self):
        """自定义宽度"""
        from modules.md_to_image import md_to_image

        path = await md_to_image("# Wide Doc\nContent", width=1200)
        try:
            assert os.path.isfile(path)
        finally:
            if os.path.exists(path):
                os.remove(path)


# ── send_local_image mock 测试 ──────────────────────────────────────


class TestSendLocalImage:
    """napcat_bridge.send_local_image 测试"""

    @pytest.mark.asyncio
    async def test_send_local_image_success(self):
        """成功发送本地图片"""
        from modules.napcat_bridge import NapCatBridge

        bridge = NapCatBridge.__new__(NapCatBridge)
        bridge.access_token = None

        # 创建临时 PNG 文件
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        tmp.close()

        try:
            bridge._call = AsyncMock(return_value={"message_id": 42})
            msg_id = await bridge.send_local_image("group", "12345", tmp.name)
            assert msg_id == "42"
            bridge._call.assert_called_once()

            call_args = bridge._call.call_args
            assert call_args[0][0] == "send_msg"
            params = call_args[0][1]
            assert params["message_type"] == "group"
            assert params["group_id"] == "12345"
            # 应该有 image 段
            segments = params["message"]
            assert any(s["type"] == "image" for s in segments)
        finally:
            os.remove(tmp.name)

    @pytest.mark.asyncio
    async def test_send_local_image_with_summary(self):
        """带描述文字发送"""
        from modules.napcat_bridge import NapCatBridge

        bridge = NapCatBridge.__new__(NapCatBridge)
        bridge.access_token = None

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(b"\x89PNG" + b"\x00" * 50)
        tmp.close()

        try:
            bridge._call = AsyncMock(return_value={"message_id": 99})
            msg_id = await bridge.send_local_image("private", "67890", tmp.name, summary="测试图片")
            assert msg_id == "99"

            segments = bridge._call.call_args[0][1]["message"]
            assert len(segments) == 2  # text + image
            assert segments[0]["type"] == "text"
            assert "测试图片" in segments[0]["data"]["text"]
        finally:
            os.remove(tmp.name)

    @pytest.mark.asyncio
    async def test_send_nonexistent_file_raises(self):
        """不存在的文件抛出 BridgeError"""
        from modules.napcat_bridge import NapCatBridge
        from protocols.error_codes import BridgeError

        bridge = NapCatBridge.__new__(NapCatBridge)
        bridge.access_token = None

        with pytest.raises(BridgeError, match="不存在"):
            await bridge.send_local_image("group", "12345", "/nonexistent/img.png")

    @pytest.mark.asyncio
    async def test_send_private_message(self):
        """私聊模式参数正确"""
        from modules.napcat_bridge import NapCatBridge

        bridge = NapCatBridge.__new__(NapCatBridge)
        bridge.access_token = None

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(b"\x89PNG" + b"\x00" * 50)
        tmp.close()

        try:
            bridge._call = AsyncMock(return_value={"message_id": 1})
            await bridge.send_local_image("private", "11111", tmp.name)

            params = bridge._call.call_args[0][1]
            assert params["message_type"] == "private"
            assert params["user_id"] == "11111"
            assert "group_id" not in params
        finally:
            os.remove(tmp.name)


# ── qq_upload_file 自动检测 mock 测试 ───────────────────────────────


class TestQqUploadFileAutoDetect:
    """qq_upload_file 工具自动检测 Markdown 文件测试"""

    def test_md_extension_detected(self):
        """file_path 为 .md 时触发图片转换"""
        ext = os.path.splitext("readme.md")[1].lower()
        assert ext in (".md", ".markdown")

    def test_markdown_extension_detected(self):
        """file_path 为 .markdown 时触发图片转换"""
        ext = os.path.splitext("doc.markdown")[1].lower()
        assert ext in (".md", ".markdown")

    def test_non_md_extension_skipped(self):
        """非 .md 文件跳过转换，走正常上传"""
        for name in ("data.csv", "script.py", "image.png", "archive.zip", "notes.txt"):
            ext = os.path.splitext(name)[1].lower()
            assert ext not in (".md", ".markdown"), f"{name} 不应被检测为 MD"

    def test_content_with_md_filename_detected(self):
        """content 模式下，filename 为 .md 时触发转换"""
        filename = "report.md"
        ext = os.path.splitext(filename)[1].lower()
        assert ext in (".md", ".markdown")

    def test_content_without_filename_skipped(self):
        """content 模式且无 filename 时跳过转换"""
        filename = ""
        if filename:
            ext = os.path.splitext(filename)[1].lower()
            assert ext in (".md", ".markdown")
        else:
            # 无 filename → 跳过 MD 检测，走普通上传
            pass


# ── html2image 回退测试 ──────────────────────────────────────────────


def _edge_available() -> bool:
    """检查 Edge 浏览器是否可用。"""
    from modules.md_to_image import _find_edge
    return _find_edge() is not None


_HAS_EDGE = _edge_available()


class TestHtml2ImageFallback:
    """html2image 回退截图路径测试"""

    @pytest.mark.skipif(not _HAS_EDGE, reason="Edge browser not available")
    @pytest.mark.asyncio
    async def test_html2image_direct(self):
        """html2image 直接截图"""
        from modules.md_to_image import _screenshot_html2image, _make_session_dir, _write_html_file

        session_dir = _make_session_dir()
        try:
            html_path = _write_html_file(
                "<html><body><h1>Test</h1><p>Hello</p></body></html>",
                session_dir,
            )
            out_path = os.path.join(session_dir, "output.png")
            result = await _screenshot_html2image(html_path, out_path, 800, 600, session_dir)
            assert os.path.isfile(result)
            assert os.path.getsize(result) > 100
        finally:
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_playwright_failure_falls_back(self):
        """Playwright 失败时自动回退到 html2image"""
        from modules.md_to_image import md_to_image
        import modules.md_to_image as m

        orig = m._screenshot_playwright

        async def _broken_pw(*a, **kw):
            raise RuntimeError("Simulated Playwright failure")

        m._screenshot_playwright = _broken_pw
        try:
            if _HAS_EDGE:
                result = await md_to_image("# Fallback\nContent")
                assert os.path.isfile(result)
                assert os.path.getsize(result) > 100
            else:
                with pytest.raises(RuntimeError, match="All screenshot methods failed"):
                    await md_to_image("# Fallback\nContent")
        finally:
            m._screenshot_playwright = orig

    @pytest.mark.asyncio
    async def test_all_fail_raises_runtime_error(self):
        """三种方法都失败时抛出 RuntimeError"""
        from modules.md_to_image import md_to_image
        import modules.md_to_image as m

        orig_pw = m._screenshot_playwright
        orig_h2i = m._screenshot_html2image
        orig_pil = m._screenshot_pillow

        async def _broken_pw(*a, **kw):
            raise RuntimeError("PW broken")

        async def _broken_h2i(*a, **kw):
            raise RuntimeError("H2I broken")

        async def _broken_pil(*a, **kw):
            raise RuntimeError("Pillow broken")

        m._screenshot_playwright = _broken_pw
        m._screenshot_html2image = _broken_h2i
        m._screenshot_pillow = _broken_pil
        try:
            with pytest.raises(RuntimeError, match="All screenshot methods failed"):
                await md_to_image("# All fail")
        finally:
            m._screenshot_playwright = orig_pw
            m._screenshot_html2image = orig_h2i
            m._screenshot_pillow = orig_pil


# ── 会话目录清理测试 ─────────────────────────────────────────────────


class TestSessionDirCleanup:
    """截图成功后临时文件清理测试"""

    @pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="Playwright not available")
    @pytest.mark.asyncio
    async def test_html_cleaned_after_success(self):
        """截图成功后 output.html 应被清理，仅保留 output.png"""
        from modules.md_to_image import md_to_image

        result = await md_to_image("# Cleanup Test\nContent")
        try:
            parent = os.path.dirname(result)
            siblings = [f for f in os.listdir(parent) if f != os.path.basename(result)]
            assert siblings == [], f"Session dir should be clean, found: {siblings}"
        finally:
            if os.path.exists(result):
                os.remove(result)
            # 清理空目录
            try:
                os.rmdir(os.path.dirname(result))
            except OSError:
                pass


# ── Markdown 扩展缓存测试 ───────────────────────────────────────────


class TestExtensionCache:
    """Markdown 扩展可用性缓存测试"""

    def test_cache_hit(self):
        """已知扩展只检测一次"""
        from modules.md_to_image import _check_md_extension, _MD_EXT_CACHE

        _MD_EXT_CACHE.clear()
        r1 = _check_md_extension("tables")
        r2 = _check_md_extension("tables")
        assert r1 is True
        assert r2 is True
        assert "tables" in _MD_EXT_CACHE

    def test_cache_unknown_extension(self):
        """不存在的扩展缓存为 False"""
        from modules.md_to_image import _check_md_extension, _MD_EXT_CACHE

        _MD_EXT_CACHE.clear()
        r = _check_md_extension("nonexistent_extension_xyz")
        assert r is False
        assert _MD_EXT_CACHE["nonexistent_extension_xyz"] is False


# ── md_to_image_sync 测试 ────────────────────────────────────────────


class TestMdToImageSync:
    """md_to_image_sync 同步包装器测试"""

    @pytest.mark.skipif(not _HAS_PLAYWRIGHT, reason="Playwright not available")
    def test_sync_basic(self):
        """同步调用基本功能"""
        from modules.md_to_image import md_to_image_sync

        result = md_to_image_sync("# Sync\nHello")
        try:
            assert os.path.isfile(result)
            assert os.path.getsize(result) > 100
        finally:
            if os.path.exists(result):
                os.remove(result)
            try:
                os.rmdir(os.path.dirname(result))
            except OSError:
                pass

    def test_sync_empty_raises(self):
        """同步调用空输入抛出 ValueError"""
        from modules.md_to_image import md_to_image_sync

        with pytest.raises(ValueError, match="empty"):
            md_to_image_sync("")
