# Changelog

All notable changes to this project will be documented in this file.

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
