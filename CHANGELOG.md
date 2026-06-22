# Changelog

All notable changes to this project will be documented in this file.

## [3.0.0] - 2026-06-22

### Added
- 完全模块化架构重构：`modules/`、`protocols/`、`state/` 三层分离
- 异步任务隔离：按会话创建独立 asyncio.Task
- 标准化通信协议：统一的消息协议 (`protocols/messages.py`) 和错误码体系 (`protocols/error_codes.py`)
- 自动化环境安装脚本 (`Install/install.bat` + `Install/setup_env.py`)
- 完善的单元测试套件 (`tests/`，91个测试用例)
- 热重载配置支持
- SSE 流式解析器 (`modules/sse_parser.py`)
- Markdown 转图片功能 (`modules/md_to_image.py`)
- Hook 事件系统 (`modules/hooks/`)
- 会话持久化存储 (`modules/conversation_store.py`)
- 骰子/ARK TRPG 游戏模块 (`modules/dice_core/`, `modules/ark_trpg/`)
- 详细架构文档 (`docs/ARCHITECTURE.md` 等)
- Playwright 浏览器渲染支持

### Changed
- 架构重组：`Built_in/` + `OrderSystem/` → `modules/` (NapCatBridge、MessageBus、CommandModule、CherryStudioModule)
- 配置格式更新：新增 `bridge`、`llm`、`vision_providers`、`auto_reply` 等配置节
- 使用 `pyproject.toml` 替代 `requirements.txt` 进行依赖管理
- 改进的启动脚本 (`start_bridge.bat`) 支持自动检测虚拟环境

### Removed
- 旧的单体架构文件 (`Built_in/`、`OrderSystem/`)
- `requirements.txt`（由 `pyproject.toml` 替代）
- `Temp/` 中的运行时数据（现在由 `state/` 管理）

## [1.0.0] - 2026-06-03

### Added
- Initial public release
- MCP STDIO server with 12 QQ interaction tools
- NapCatQQ WebSocket bidirectional bridge
- Multi-agent auto-reply with CherryStudio backend
- Modular command system (`.help`, `.bot`, `.order`, `.model`, `.master`, `.log`, `.ob`, `.dismiss`, `.send`)
- Vision/image analysis support via multimodal LLM providers
- File processing support via MinerU
- Session persistence with `QQConversationRecord/`
- Group chat logging with `PlayerLog/`
- UVX installation support (via `--from git`)
- One-click CherryStudio MCP install URL generator
- Auto-accept friend/group invites
- Configurable greeting, on/off, and dismiss messages

- LLM provider fallback chain with multiple API key support
- Singleton process lock to prevent duplicate instances
