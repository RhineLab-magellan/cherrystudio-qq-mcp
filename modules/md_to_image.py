"""
Markdown 转图片模块

独立的 Markdown → PNG 图片转换工具。
流程: Markdown → HTML (带 CSS 美化) → PNG (浏览器全页截图)

截图策略 (三级回退):
  1. Playwright Chromium — 使用独立 Chromium 二进制，不受 Edge IPC delegation 影响，
     支持全页截图 (自动测量页面尺寸)。浏览器二进制可内置于项目 Browsers/ 目录。
  2. html2image — 使用系统 Edge 的 --screenshot 模式，仅视口截图，
     作为 Playwright 不可用时的备选方案。
  3. Pillow 纯 Python — 不依赖任何浏览器二进制，直接绘制文本图片，
     渲染效果有限但保证在沙盒等受限环境下也能输出可读图片。

用法:
    from modules.md_to_image import md_to_image, render_markdown

    # 异步 (生产环境使用)
    png_path = await md_to_image("# Hello\\n正文内容")
    png_path = await md_to_image(file_path="/path/to/doc.md")

    # 同步 (测试/脚本使用)
    png_path = md_to_image_sync("# Hello\\n正文内容")
"""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

import markdown

logger = logging.getLogger(__name__)

# ── 默认配置 ────────────────────────────────────────────────────────

DEFAULT_WIDTH = 800
MAX_HEIGHT = 8192          # QQ 图片最大边长约 8192px，超过可能被压缩

# ── 项目 Temp 目录 ──────────────────────────────────────────────────

# 项目根目录 (md_to_image.py 位于 modules/ 下，上一级即项目根)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Playwright 本地浏览器目录 ─────────────────────────────────────────
# 将浏览器二进制内置到项目 Browsers/ 目录，不依赖 %LOCALAPPDATA% 路径，
# 确保在 Cherry Studio 沙盒等受限环境下也能找到 Chromium。
_LOCAL_BROWSERS_DIR = os.path.join(_PROJECT_ROOT, "Browsers")


def _ensure_playwright_browsers_path():
    """
    设置 PLAYWRIGHT_BROWSERS_PATH 指向项目本地 Browsers/ 目录。

    必须在 import playwright 之前调用。
    如果项目 Browsers/ 目录不存在，则回退到系统默认路径。
    """
    if os.path.isdir(_LOCAL_BROWSERS_DIR):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _LOCAL_BROWSERS_DIR


_ensure_playwright_browsers_path()


def _get_project_temp() -> str:
    """获取项目 Temp/ 目录路径，不存在则自动创建。"""
    temp_dir = os.path.join(_PROJECT_ROOT, "Temp")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def _get_md2img_base() -> str:
    """获取 Temp/md2img/ 基础目录，不存在则自动创建。"""
    base = os.path.join(_get_project_temp(), "md2img")
    os.makedirs(base, exist_ok=True)
    return base


def _make_session_dir() -> str:
    """在 Temp/md2img/ 下创建基于时间戳的会话目录。"""
    ts = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 100000:05d}"
    d = os.path.join(_get_md2img_base(), ts)
    os.makedirs(d, exist_ok=True)
    return d


def _cleanup_dir(path: str):
    """安全删除目录，忽略错误。"""
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _write_html_file(html_content: str, session_dir: str) -> str:
    """将 HTML 内容写入会话目录下的 output.html，返回绝对路径。"""
    html_path = os.path.join(session_dir, "output.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return html_path


def _make_file_url(file_path: str) -> str:
    """
    将本地绝对路径转为 file:/// URL。

    Windows 路径的反斜杠会转为正斜杠，非 ASCII 字符 (如中文) 保留原样 ——
    Chromium 原生支持 Unicode file:// URL，无需 percent-encoding。
    """
    abs_path = os.path.abspath(file_path)
    # Windows 反斜杠转正斜杠
    url_path = abs_path.replace("\\", "/")
    # Windows 绝对路径需要三个斜杠: file:///C:/...
    if len(url_path) >= 2 and url_path[1] == ":":
        return f"file:///{url_path}"
    else:
        # Unix 路径: file:///path/...
        return f"file://{url_path}"


# ── CSS 主题 ────────────────────────────────────────────────────────

DEFAULT_CSS = """
* { box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
                 "PingFang SC", "Helvetica Neue", Roboto, sans-serif;
    font-size: 15px;
    line-height: 1.75;
    color: #1a1a2e;
    background: #ffffff;
    padding: 36px 44px;
    margin: 0;
    max-width: 100%;
    word-wrap: break-word;
    overflow-wrap: break-word;
}

h1 { font-size: 1.9em; margin: 0.8em 0 0.4em; color: #16213e;
     border-bottom: 2px solid #e8ecf1; padding-bottom: 0.3em; }
h2 { font-size: 1.5em; margin: 0.7em 0 0.35em; color: #1a1a2e;
     border-bottom: 1px solid #e8ecf1; padding-bottom: 0.25em; }
h3 { font-size: 1.25em; margin: 0.6em 0 0.3em; color: #1a1a2e; }
h4, h5, h6 { font-size: 1.1em; margin: 0.5em 0 0.2em; }

p  { margin: 0.6em 0; }

a  { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }

code {
    background: #f0f3f8;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.88em;
    font-family: "Cascadia Code", "Fira Code", Consolas, "Courier New", monospace;
    color: #c7254e;
}

pre {
    background: #f6f8fa;
    padding: 16px 20px;
    border-radius: 8px;
    overflow-x: auto;
    border: 1px solid #e8ecf1;
    line-height: 1.5;
}
pre code {
    background: none;
    padding: 0;
    color: inherit;
    font-size: 0.88em;
}

blockquote {
    border-left: 4px solid #d0d7de;
    padding: 0.5em 1em;
    color: #57606a;
    margin: 0.8em 0;
    background: #f6f8fa;
    border-radius: 0 6px 6px 0;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.8em 0;
    font-size: 0.95em;
}
th, td {
    border: 1px solid #d0d7de;
    padding: 8px 12px;
    text-align: left;
}
th {
    background: #f0f3f8;
    font-weight: 600;
}
tr:nth-child(even) { background: #f9fafb; }

ul, ol { padding-left: 2em; margin: 0.5em 0; }
li { margin: 0.2em 0; }

img { max-width: 100%; border-radius: 4px; }

hr {
    border: none;
    border-top: 1px solid #e8ecf1;
    margin: 1.5em 0;
}
"""

# ── Markdown 扩展可用性缓存 ──────────────────────────────────────────

# 运行期间扩展可用性不会变化，一次性检测后缓存
_MD_EXT_CACHE: dict[str, bool] = {}


def _check_md_extension(name: str) -> bool:
    """检测 python-markdown 扩展是否可用 (结果缓存)。"""
    if name not in _MD_EXT_CACHE:
        try:
            markdown.Markdown(extensions=[name])
            _MD_EXT_CACHE[name] = True
        except Exception:
            _MD_EXT_CACHE[name] = False
            logger.debug(f"Markdown extension '{name}' unavailable")
    return _MD_EXT_CACHE[name]


# ── Markdown → HTML ─────────────────────────────────────────────────


def render_markdown(
    md_text: str,
    *,
    title: str = "",
    css: str = "",
    extensions: list[str] | None = None,
) -> str:
    """
    将 Markdown 文本渲染为完整 HTML 页面字符串。

    Args:
        md_text:    Markdown 源文本
        title:      HTML <title> (可选)
        css:        额外 CSS，追加到默认主题之后
        extensions: python-markdown 扩展列表

    Returns:
        完整 HTML 字符串 (含 <!DOCTYPE>)
    """
    default_ext = [
        "tables",
        "fenced_code",
        "codehilite",
        "toc",
        "nl2br",
        "sane_lists",
    ]
    ext = extensions if extensions is not None else default_ext

    # 过滤掉未安装的扩展 (缓存检测，避免重复创建 Markdown 实例)
    available_ext = [e for e in ext if _check_md_extension(e)]

    body_html = markdown.markdown(md_text, extensions=available_ext)

    extra_css = f"\n/* ── 自定义 CSS ── */\n{css}" if css else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title or "Markdown"}</title>
<style>
{DEFAULT_CSS}{extra_css}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


# ── Playwright 截图 ─────────────────────────────────────────────────


async def _screenshot_playwright(
    html_path: str,
    output_path: str,
    width: int,
    height: int,
    session_dir: str,
) -> str:
    """
    使用 Playwright 自带的 Chromium 截取完整页面。

    Playwright 使用独立的 Chromium 二进制，不受系统 Edge IPC delegation 影响，
    可在 CherryStudio MCP 子进程环境下稳定工作。

    流程:
      1. 启动 Playwright Chromium (headless)
      2. 通过 file:/// URL 加载本地 HTML
      3. 测量页面实际内容尺寸
      4. 设置视口为完整内容尺寸
      5. 截图保存为 PNG
    """
    from playwright.async_api import async_playwright

    file_url = _make_file_url(html_path)
    h = min(height, MAX_HEIGHT)

    t0 = time.time()
    async with async_playwright() as p:
        browser = await asyncio.wait_for(
            p.chromium.launch(headless=True),
            timeout=30,
        )
        try:
            page = await browser.new_page(viewport={"width": width, "height": h})
            await page.goto(file_url, wait_until="networkidle", timeout=15000)

            # 测量页面实际内容尺寸
            dims = await page.evaluate(
                """() => ({
                    w: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
                    h: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
                })"""
            )
            content_w = max(dims.get("w", width), width)
            content_h = min(dims.get("h", h), MAX_HEIGHT)

            # 设置视口为完整内容尺寸，实现全页截图
            await page.set_viewport_size({"width": content_w, "height": content_h})
            await page.wait_for_timeout(500)

            await page.screenshot(path=output_path, full_page=True)

            elapsed = time.time() - t0
            file_size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0

            logger.info(
                f"Playwright screenshot done: {output_path} "
                f"({content_w}x{content_h}, {file_size}B, {elapsed:.2f}s)"
            )
            return output_path

        finally:
            await browser.close()


# ── html2image 回退截图 ─────────────────────────────────────────────

# Edge 浏览器常见路径 (Windows)
_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]


def _find_edge() -> str | None:
    """查找系统 Edge 浏览器可执行文件路径。"""
    for p in _EDGE_PATHS:
        if os.path.isfile(p):
            return p
    # 最后尝试 PATH
    return shutil.which("msedge")


async def _screenshot_html2image(
    html_path: str,
    output_path: str,
    width: int,
    height: int,
    session_dir: str,
) -> str:
    """
    使用 html2image 库 (系统 Edge --screenshot 模式) 进行视口截图。

    作为 Playwright 不可用时的回退方案。
    注意: html2image 只能做视口截图，不支持全页截图；
    长文档可能被截断。

    流程:
      1. 查找系统 Edge 浏览器
      2. 使用独立 --user-data-dir 避免 IPC delegation
      3. 通过 file:/// URL 加载本地 HTML 并截图
    """
    from html2image import Html2Image

    edge_path = _find_edge()
    if not edge_path:
        raise RuntimeError("Edge browser not found for html2image fallback")

    h = min(height, MAX_HEIGHT)

    t0 = time.time()

    # html2image 使用独立 user-data-dir 避免 IPC delegation
    hti_output = os.path.join(session_dir, "h2i_out")
    os.makedirs(hti_output, exist_ok=True)

    # 在线程池中运行 html2image (它是同步的)
    def _do_screenshot():
        hti = Html2Image(
            browser_executable=edge_path,
            output_path=hti_output,
            size=(width, h),
            custom_flags=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                f"--user-data-dir={os.path.join(session_dir, 'edge_profile')}",
            ],
        )
        file_url = _make_file_url(html_path)
        out_name = "output.png"
        hti.screenshot(url=file_url, save_as=out_name, size=(width, h))
        return os.path.join(hti_output, out_name)

    loop = asyncio.get_running_loop()
    src_path = await loop.run_in_executor(None, _do_screenshot)

    # 移动到目标输出路径
    if os.path.isfile(src_path) and os.path.getsize(src_path) > 0:
        if os.path.abspath(src_path) != os.path.abspath(output_path):
            shutil.move(src_path, output_path)
    else:
        raise RuntimeError(
            f"html2image produced no output (src={src_path})"
        )

    elapsed = time.time() - t0
    file_size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0

    logger.info(
        f"html2image fallback screenshot done: {output_path} "
        f"({width}x{h}, {file_size}B, {elapsed:.2f}s)"
    )
    return output_path


# ── Pillow 纯 Python 回退截图 ─────────────────────────────────────────


async def _screenshot_pillow(
    html_path: str,
    output_path: str,
    width: int,
    height: int,
    session_dir: str,
    md_text: str = "",
) -> str:
    """
    使用 Pillow 将 Markdown 文本直接渲染为 PNG 图片。

    作为 Playwright 和 html2image 均不可用时的最终回退方案。
    不依赖任何浏览器二进制，纯 Python 实现。
    注意: 渲染效果有限 (无完整 CSS 支持)，但保证可读。

    流程:
      1. 解析 Markdown 为结构化文本块
      2. 使用 Pillow ImageDraw 逐块绘制
      3. 输出 PNG
    """
    from PIL import Image, ImageDraw, ImageFont

    t0 = time.time()

    # ── 字体选择 ──
    # 优先使用系统中文字体，回退到默认
    _FONT_CANDIDATES = [
        "msyh.ttc",      # Microsoft YaHei
        "msyhbd.ttc",    # Microsoft YaHei Bold
        "simhei.ttf",    # SimHei
        "simsun.ttc",    # SimSun
        "arial.ttf",     # Arial (无中文支持)
    ]

    def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for name in _FONT_CANDIDATES:
            try:
                return ImageFont.truetype(name, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    font_body = _load_font(15)
    font_h1 = _load_font(26, bold=True)
    font_h2 = _load_font(22, bold=True)
    font_h3 = _load_font(18, bold=True)
    font_code = _load_font(13)

    # ── 解析 Markdown 为行列表 ──
    lines = md_text.split("\n") if md_text else []

    # ── 布局参数 ──
    padding_x = 44
    padding_y = 36
    line_height = 26
    h1_height = 40
    h2_height = 34
    h3_height = 28
    code_bg = (246, 248, 250)
    text_color = (26, 26, 46)
    heading_color = (22, 33, 62)
    code_color = (199, 37, 78)
    bg_color = (255, 255, 255)

    content_width = width - 2 * padding_x

    # ── 第一遍: 计算总高度 ──
    total_height = padding_y * 2
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            total_height += 8  # spacing
            continue
        if in_code_block:
            total_height += 20
            continue
        if stripped.startswith("# "):
            total_height += h1_height
        elif stripped.startswith("## "):
            total_height += h2_height
        elif stripped.startswith("### "):
            total_height += h3_height
        elif stripped == "":
            total_height += 12
        else:
            total_height += line_height

    total_height = max(total_height, 200)
    total_height = min(total_height, MAX_HEIGHT)

    # ── 第二遍: 绘制 ──
    img = Image.new("RGB", (width, total_height), bg_color)
    draw = ImageDraw.Draw(img)
    y = padding_y
    in_code_block = False

    for line in lines:
        if y >= total_height - padding_y:
            break

        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            y += 8
            continue

        if in_code_block:
            # 代码块: 等宽字体 + 灰色背景
            draw.rectangle([padding_x - 4, y, width - padding_x + 4, y + 20], fill=code_bg)
            draw.text((padding_x, y), stripped, fill=code_color, font=font_code)
            y += 20
            continue

        if stripped.startswith("# "):
            text = stripped[2:]
            draw.text((padding_x, y), text, fill=heading_color, font=font_h1)
            y += h1_height
            # 下划线
            draw.line([(padding_x, y - 6), (width - padding_x, y - 6)], fill=(232, 236, 241), width=2)
        elif stripped.startswith("## "):
            text = stripped[3:]
            draw.text((padding_x, y), text, fill=heading_color, font=font_h2)
            y += h2_height
            draw.line([(padding_x, y - 6), (width - padding_x, y - 6)], fill=(232, 236, 241), width=1)
        elif stripped.startswith("### "):
            text = stripped[4:]
            draw.text((padding_x, y), text, fill=heading_color, font=font_h3)
            y += h3_height
        elif stripped.startswith("> "):
            # 引用块: 左边竖线 + 灰色文字
            text = stripped[2:]
            draw.rectangle([padding_x, y, padding_x + 4, y + line_height], fill=(208, 215, 222))
            draw.text((padding_x + 12, y), text, fill=(87, 96, 106), font=font_body)
            y += line_height
        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:]
            draw.text((padding_x + 8, y), "•", fill=text_color, font=font_body)
            draw.text((padding_x + 24, y), text, fill=text_color, font=font_body)
            y += line_height
        elif stripped == "":
            y += 12
        else:
            # 普通文本: 自动换行
            # 简单估算: 每行约能容纳 content_width / 9 个字符
            chars_per_line = max(int(content_width / 9), 20)
            text = stripped
            while len(text) > chars_per_line and y < total_height - padding_y:
                chunk = text[:chars_per_line]
                draw.text((padding_x, y), chunk, fill=text_color, font=font_body)
                text = text[chars_per_line:]
                y += line_height
            if text and y < total_height - padding_y:
                draw.text((padding_x, y), text, fill=text_color, font=font_body)
                y += line_height

    # ── 裁剪到实际内容高度 ──
    actual_height = min(y + padding_y, total_height)
    img = img.crop((0, 0, width, actual_height))

    # ── 在线程池中保存 (Pillow save 是同步的) ──
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, img.save, output_path, "PNG")

    elapsed = time.time() - t0
    file_size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0

    logger.info(
        f"Pillow fallback screenshot done: {output_path} "
        f"({width}x{actual_height}, {file_size}B, {elapsed:.2f}s)"
    )
    return output_path


# ── 主入口 ──────────────────────────────────────────────────────────


async def md_to_image(
    md_text: str | None = None,
    *,
    file_path: str | None = None,
    output_path: str | None = None,
    width: int = DEFAULT_WIDTH,
    css: str = "",
    title: str = "",
) -> str:
    """
    将 Markdown 转换为 PNG 图片。

    截图策略 (三级回退):
      1. Playwright Chromium — 全页截图 (首选, 需要浏览器)
      2. html2image (Edge) — 视口截图 (需要系统 Edge)
      3. Pillow 纯 Python — 文本渲染 (零外部依赖, 最终保底)

    HTML 写入临时文件后通过 file:/// URL 加载。

    Args:
        md_text:     Markdown 文本 (与 file_path 二选一)
        file_path:   .md 文件路径 (与 md_text 二选一)
        output_path: 输出 PNG 路径 (默认自动生成在 Temp/md2img/ 下)
        width:       图片宽度 (像素, 默认 800)
        css:         额外 CSS (追加到默认主题之后)
        title:       HTML 标题 (可选)

    Returns:
        生成的 PNG 文件绝对路径

    Raises:
        ValueError:  未提供输入或输入无效
        RuntimeError: 所有截图方法均失败
    """
    # ── 获取 Markdown 文本 ──
    if file_path:
        if not os.path.isfile(file_path):
            raise ValueError(f"File not found: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            md_text = f.read()
        if not title:
            title = Path(file_path).stem

    if not md_text or not md_text.strip():
        raise ValueError("Markdown content is empty")

    # ── 准备会话目录 ──
    session_dir = _make_session_dir()

    # ── 渲染 HTML 并写入临时文件 ──
    html_content = render_markdown(md_text, title=title, css=css)
    html_path = _write_html_file(html_content, session_dir)
    html_size = os.path.getsize(html_path)
    logger.info(f"md_to_image: html={html_path} ({html_size}B) session={session_dir}")

    # ── 准备输出路径 ──
    if not output_path:
        output_path = os.path.join(session_dir, "output.png")

    # ── 截图 (三级回退) ──
    # 估算页面高度 (粗算: 每 80 字符一行, 每行约 28px)
    lines = len(md_text) / 80 + md_text.count("\n")
    est_height = max(int(lines * 28 + 120), 600)

    errors: list[str] = []

    # 方法 1: Playwright Chromium (全页截图, 首选)
    result = None
    try:
        result = await _screenshot_playwright(
            html_path, output_path, width, est_height, session_dir
        )
    except Exception as e:
        errors.append(f"Playwright: {type(e).__name__}: {e}")
        logger.warning(
            f"Playwright screenshot failed ({type(e).__name__}: {e}), "
            f"trying html2image fallback"
        )

    # 方法 2: html2image (Edge 视口截图, 回退)
    if result is None:
        try:
            result = await _screenshot_html2image(
                html_path, output_path, width, est_height, session_dir
            )
        except Exception as e:
            errors.append(f"html2image: {type(e).__name__}: {e}")
            logger.warning(
                f"html2image fallback failed ({type(e).__name__}: {e}), "
                f"trying Pillow fallback"
            )

    # 方法 3: Pillow 纯 Python (零外部依赖, 最终保底)
    if result is None:
        try:
            result = await _screenshot_pillow(
                html_path, output_path, width, est_height, session_dir,
                md_text=md_text,
            )
        except Exception as e:
            errors.append(f"Pillow: {type(e).__name__}: {e}")
            _cleanup_dir(session_dir)
            raise RuntimeError(
                f"All screenshot methods failed. "
                + "; ".join(errors)
            ) from e

    # ── 清理会话目录中的临时文件 (HTML 等)，保留输出 PNG ──
    abs_result = os.path.abspath(result)
    for item in os.listdir(session_dir):
        item_path = os.path.join(session_dir, item)
        if os.path.abspath(item_path) != abs_result:
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path, ignore_errors=True)
                else:
                    os.remove(item_path)
            except OSError:
                pass

    return abs_result


# ── 同步便捷入口 (供测试/脚本使用) ──────────────────────────────────


def md_to_image_sync(
    md_text: str | None = None,
    **kwargs,
) -> str:
    """
    md_to_image 的同步包装器。

    无事件循环时直接 asyncio.run()；
    在已有事件循环中 (如 Jupyter / pytest) 会在新线程中运行，
    避免 'asyncio.run() cannot be called from a running event loop' 错误。
    """
    def _run():
        return asyncio.run(md_to_image(md_text, **kwargs))

    try:
        return _run()
    except RuntimeError:
        # 已在事件循环中 → 在新线程创建独立循环运行
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result()
