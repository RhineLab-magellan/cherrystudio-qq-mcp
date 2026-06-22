# QQ-MCP Bridge v3.0 安装部署指南

> **版本**: v3.0.0
> **包名**: `cherrystudio-qq-mcp`
> **运行环境**: Python >= 3.10
> **最后更新**: 2026-06-09

---

## 目录

1. [系统要求](#1-系统要求)
2. [前置准备](#2-前置准备)
3. [获取项目安装包](#3-获取项目安装包)
4. [环境部署 (自动化)](#4-环境部署-自动化)
5. [环境部署 (手动)](#5-环境部署-手动)
6. [配置 Bridge](#6-配置-bridge)
7. [集成 CherryStudio](#7-集成-cherrystudio)
8. [首次运行验证](#8-首次运行验证)
9. [常见问题](#9-常见问题)
10. [更新与维护](#10-更新与维护)

---

## 1. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 (x64) |
| Python | >= 3.10 |
| QQ 协议端 | NapCatQQ (OneBot v11 WebSocket) |
| AI 平台 | CherryStudio 桌面应用 |
| 磁盘空间 | 至少 2GB 可用 (含 Playwright Chromium 浏览器约 680MB) |
| 网络环境 | 需要能访问 CherryStudio HTTP API 和 LLM API 服务 |

---

## 2. 前置准备

### 2.1 安装 Python

- 从 https://www.python.org/downloads/ 下载 Python 3.10+
- 安装时勾选 **"Add Python to PATH"**
- 验证安装:

```bash
python --version
```

应显示 `Python 3.10.x` 或更高版本。

### 2.2 安装 NapCatQQ

- 从 https://github.com/NapNeko/NapCatQQ 获取
- 配置 OneBot v11 WebSocket 正向连接:
  - **主机**: `127.0.0.1`
  - **端口**: `3001` (默认)
  - **Access Token**: 自行设置 (需与 Bridge 配置一致)
- 确保 QQ 账号已登录 NapCat

### 2.3 安装 CherryStudio

- 从 https://cherrystudio.com 下载并安装
- 获取 API Key (用于 Bridge 连接)
- 记录 HTTP API 端口 (默认 `23333`)

---

## 3. 获取项目安装包

### 3.1 从 GitHub 克隆

```bash
git clone https://github.com/RhineLab-magellan/cherrystudio-qq-mcp.git
cd cherrystudio-qq-mcp
```

### 3.2 从 Release 下载

- 前往 GitHub Releases 页面下载最新版本的 zip 包
- 解压到目标目录 (如 `C:\CherryStudio\qq-mcp-bridge\`)

### 3.3 项目目录结构

```
qq-mcp-bridge/
├── server.py                  # 系统入口
├── pyproject.toml             # 项目配置
├── start_bridge.bat           # MCP 启动入口 (CherryStudio 调用)
├── start.bat                  # 独立启动脚本
├── Install/
│   ├── install.bat            # 一键安装脚本
│   └── setup_env.py           # 自动化环境配置
├── modules/                   # 核心模块
├── protocols/                 # 协议定义
├── state/                     # 状态管理
├── tools/                     # 工具集
├── tests/                     # 测试套件
├── Configuration/             # 配置文件
│   ├── config.json            # 运行时配置
│   └── BotSettingConfig.json  # 消息模板
├── Browsers/                  # 内置 Playwright 浏览器 (~680MB)
└── docs/                      # 文档
```

---

## 4. 环境部署 (自动化)

这是**推荐的安装方式**，一键完成所有环境配置。

### 4.1 运行安装脚本

双击 `Install\install.bat` 或在命令行运行:

```bash
cd qq-mcp-bridge
Install\install.bat
```

安装脚本会自动执行以下步骤:

1. **查找 Python**: 搜索 py launcher、PATH、已知安装目录、Microsoft Store
2. **创建虚拟环境**: 在项目根目录创建 `.venv/` 虚拟环境
3. **升级 pip**: 确保使用最新版本
4. **安装依赖**: 从 `pyproject.toml` 安装所有 Python 包 (mcp, aiohttp, websockets, pydantic, playwright, Pillow, markdown, html2image 等)
5. **安装 Playwright Chromium**: 下载 Chromium 浏览器到项目 `Browsers/` 目录 (设置 `PLAYWRIGHT_BROWSERS_PATH` 环境变量)
6. **验证安装**: 运行冒烟测试 (核心导入、Playwright 导入、Chromium 启动、md_to_image E2E)

### 4.2 安装参数

```bash
Install\install.bat                                           # 完整安装
Install\install.bat --skip-playwright                         # 跳过 Chromium 下载
Install\install.bat --python C:\path\to\python.exe            # 指定 Python 路径
Install\install.bat --skip-verify                             # 跳过安装验证
```

### 4.3 安装日志

安装日志保存在 `Install\install.log`。

---

## 5. 环境部署 (手动)

如果自动安装失败，可以手动执行以下步骤:

### 5.1 创建虚拟环境

```bash
python -m venv .venv
```

### 5.2 激活虚拟环境

```bash
.venv\Scripts\activate
```

### 5.3 安装依赖

```bash
pip install --upgrade pip
pip install -e .
```

这将安装 `pyproject.toml` 中声明的所有依赖:

| 依赖 | 版本 | 用途 |
|------|------|------|
| `mcp` | >= 1.0.0 | MCP SDK (FastMCP, stdio 传输) |
| `aiohttp` | >= 3.9.0 | HTTP 客户端 (CherryStudio API) |
| `websockets` | >= 12.0 | WebSocket 客户端 (NapCat 连接) |
| `pydantic` | >= 2.0.0 | 数据校验 (配置/状态验证) |
| `playwright` | >= 1.40.0 | 截图引擎 (Chromium 浏览器) |
| `Pillow` | >= 10.0.0 | 纯 Python 图片渲染 (最终回退) |
| `markdown` | >= 3.5.0 | Markdown -> HTML 转换 |
| `html2image` | >= 2.0.0 | Edge 截图回退 |

### 5.4 安装 Playwright 浏览器

```bash
set PLAYWRIGHT_BROWSERS_PATH=%cd%\Browsers
.venv\Scripts\python.exe -m playwright install chromium
```

> **重要**: `PLAYWRIGHT_BROWSERS_PATH` 环境变量使浏览器安装到项目目录而非系统目录 (`%LOCALAPPDATA%\ms-playwright`)。这确保了 Cherry Studio 沙盒环境也能找到浏览器。

### 5.5 手动验证

```bash
# 核心导入测试
.venv\Scripts\python.exe -c "import markdown, aiohttp, websockets, pydantic, PIL; print('OK')"

# Playwright 测试
.venv\Scripts\python.exe -c "from playwright.async_api import async_playwright; print('OK')"

# 运行测试套件
.venv\Scripts\python.exe -m pytest
```

---

## 6. 配置 Bridge

### 6.1 编辑配置文件

编辑 `Configuration\config.json` (或项目根目录的 `config.json`):

```json
{
  "napcat": {
    "ws_host": "127.0.0.1",
    "ws_port": 3001,
    "access_token": "你的 NapCat Access Token"
  },
  "cherrystudio": {
    "http_api_base": "http://127.0.0.1:23333",
    "api_key": "cs-sk-你的 CherryStudio API Key",
    "mcp_server_name": "QQ Bridge"
  },
  "llm_providers": [
    {
      "name": "Provider 名称",
      "base_url": "https://api.example.com/v1/chat/completions",
      "api_key": "sk-你的 API Key",
      "models": ["model-name"]
    }
  ],
  "admin_qq": "你的 QQ 号",
  "global_context": "你是一个 QQ 群助手..."
}
```

### 6.2 配置说明

| 配置项 | 说明 |
|--------|------|
| `napcat.access_token` | 必须与 NapCat 配置的 Token 一致 |
| `cherrystudio.api_key` | CherryStudio 的 API 密钥 |
| `llm_providers` | 按优先级排列的 LLM 供应商列表，前一个失败时自动切换到下一个 |
| `admin_qq` | 管理员 QQ 号，用于接收系统通知 (如 Provider 配额耗尽) |

---

## 7. 集成 CherryStudio

### 7.1 方式 A: MCP stdio 集成 (推荐)

在 CherryStudio 的 MCP 服务器设置中添加:

| 配置项 | 值 |
|--------|-----|
| **名称** | QQ Bridge |
| **类型** | stdio |
| **命令** | `C:\CherryStudio\qq-mcp-bridge\start_bridge.bat` |

CherryStudio 启动时会自动执行 `start_bridge.bat`，通过 stdin/stdout 建立 MCP JSON-RPC 2.0 通信。

### 7.2 方式 B: 独立运行

```bash
cd C:\CherryStudio\qq-mcp-bridge
.venv\Scripts\python.exe server.py
```

或使用 `start.bat` 双击运行。

### 7.3 验证连接

1. 启动 CherryStudio
2. 在 MCP 工具列表中应能看到 **12 个** `qq_*` 工具
3. 在 QQ 中向机器人发送 `.help` 命令，应收到帮助信息

---

## 8. 首次运行验证

### 8.1 启动顺序

1. 确保 NapCat QQ 已登录
2. 确保 CherryStudio 已启动
3. Bridge 自动启动 (CherryStudio MCP 集成) 或手动启动

### 8.2 检查清单

| 检查项 | 预期结果 | 排查方法 |
|--------|---------|---------|
| NapCat 连接 | 日志显示 "NapCat WebSocket connected" | 检查 NapCat 端口和 Token |
| Bot QQ 识别 | 日志显示 "Bot QQ: xxxxx" | NapCat 是否正常登录 |
| MCP 握手 | 日志显示 "Client handshake complete" | CherryStudio MCP 配置是否正确 |
| Agent 发现 | 日志显示 Agent 列表 | CherryStudio API 是否可达 |
| 命令响应 | QQ 发送 `.help` 收到回复 | MessageBus 是否正常分发 |
| AI 回复 | QQ @机器人 收到 AI 回复 | LLM Provider 是否可用 |
| 图片渲染 | 长文本自动转为图片发送 | Playwright/Pillow 是否正常 |

### 8.3 日志位置

| 日志类型 | 路径 |
|---------|------|
| 运行日志 | `PlayerLog\bridge.log` |
| 安装日志 | `Install\install.log` |

### 8.4 调试模式

在 `config.json` 中设置:

```json
{
  "debug_mode": 3,
  "cherry_debug": true
}
```

| 参数 | 说明 |
|------|------|
| `debug_mode` | `0`=静默, `1`=ERROR, `2`=WARNING, `3`=INFO, `4`=DEBUG |
| `cherry_debug` | 启用 SSE 详细追踪日志 |

---

## 9. 常见问题

### 9.1 Playwright 浏览器找不到

**现象**: 日志显示 `ModuleNotFoundError: No module named 'playwright'` 或 Chromium 启动失败

**解决**:

```bash
set PLAYWRIGHT_BROWSERS_PATH=C:\CherryStudio\qq-mcp-bridge\Browsers
.venv\Scripts\python.exe -m playwright install chromium
```

### 9.2 Pillow 未安装

**现象**: 日志显示 `ModuleNotFoundError: No module named 'PIL'`

**解决**:

```bash
.venv\Scripts\python.exe -m pip install Pillow
```

### 9.3 Cherry Studio 沙盒问题

Cherry Studio 的 MCP 服务器运行在隔离的 Python 环境中。确保:

- `.venv/` 虚拟环境已正确创建
- `start_bridge.bat` 指向正确的 venv Python 路径
- 所有依赖已安装到 venv 中 (而非系统 Python)

### 9.4 NapCat TLS 断连

**现象**: `Client network socket disconnected before secure TLS connection was established`

**解决**: 这是 NapCat 连接 QQ 服务器的网络层问题，与 Bridge 无关。检查:

- 网络环境是否稳定 (代理/VPN 干扰)
- NapCat 版本是否最新
- QQ 账号状态是否正常

### 9.5 Edge IPC 代理问题 (已解决)

旧版本使用 Edge 浏览器截图时，如果 Edge 已在运行，headless 实例会通过 IPC 委托给已有进程并静默退出。v3.0 已通过以下方式解决:

- 使用独立的 Playwright Chromium (不依赖系统浏览器)
- html2image 回退使用隔离的 `--user-data-dir`
- Pillow 纯 Python 回退完全不依赖浏览器

---

## 10. 更新与维护

### 10.1 代码更新

```bash
cd C:\CherryStudio\qq-mcp-bridge
git pull origin main
.venv\Scripts\python.exe -m pip install -e .
```

### 10.2 依赖更新

```bash
.venv\Scripts\python.exe -m pip install --upgrade -e .
```

### 10.3 运行测试

```bash
.venv\Scripts\python.exe -m pytest
```

预期结果: `575 passed, 0 failed`

---

> **文档版本**: v1.0
> **适用版本**: cherrystudio-qq-mcp 3.0.0
> **最后更新**: 2026-06-09
> **作者**: RhineLab-magellan
