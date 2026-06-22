# QQ-MCP Bridge v3.0 - 重构版

## 项目概述

QQ-MCP Bridge v3.0 是一个完全重构的模块化桥接系统，实现 CherryStudio (MCP Client) 与 QQ (通过 NapCatQQ) 之间的双向通信。

### 核心特性

- **模块化架构**: 5个独立模块，清晰的责任边界
- **异步任务隔离**: 按会话创建独立asyncio.Task
- **标准化通信**: 统一的消息协议和错误码体系
- **热重载支持**: 配置变更无需重启
- **完善测试**: 91个单元测试全部通过

---

## 架构设计

```
┌──────────────┐
│   Server     │ ← MCP注册、系统初始化、模块生命周期管理
└──────┬───────┘
       │
┌──────▼───────┐
│ NapCatBridge │ ← WebSocket连接、消息收发
└──────┬───────┘
       │ RawMessage
┌──────▼───────┐
│ MessageBus   │ ← 过滤、解析、路由
└──┬───────┬───┘
   │       │
┌──▼──┐ ┌──▼──────────┐
│Cmd  │ │CherryStudio │ ← 按会话异步任务隔离
│Mod  │ │Module       │
└─────┘ └─────────────┘
```

### 模块职责

| 模块 | 职责 | 文件 |
|------|------|------|
| **NapCatBridge** | WebSocket连接、消息收发 | `modules/napcat_bridge.py` |
| **MessageBus** | 消息过滤、解析、路由 | `modules/message_bus.py` |
| **CommandModule** | 命令解析与执行 | `modules/command_module.py` |
| **CherryStudioModule** | LLM调用、会话管理 | `modules/cherrystudio_module.py` |
| **StateManager** | 全局状态管理 | `state/manager.py` |

---

## 快速开始

### 环境要求

- Python >= 3.10
- NapCatQQ (已登录并启用WebSocket)
- CherryStudio (可选，用于AI功能)

### 安装依赖

```bash
pip install -e .
```

或开发模式：

```bash
pip install -e ".[dev]"
```

### 配置

1. 复制示例配置：
```bash
cp Configuration/config.example.json Configuration/config.json
```

2. 编辑 `Configuration/config.json`，填入你的配置：
```json
{
  "napcat": {
    "ws_host": "127.0.0.1",
    "ws_port": 3001,
    "access_token": "your_token"
  },
  "cherrystudio": {
    "mcp_server_path": "/path/to/mcp/server",
    "http_api_base": "http://127.0.0.1:8080",
    "api_key": "your_api_key"
  }
}
```

### 运行测试

```bash
pytest tests/ -v
```

---

## 模块标准通信协议

详细协议文档请查看 [docs/PROTOCOL.md](docs/PROTOCOL.md)

### 消息类型

- **RawMessage**: 原始消息 (NapCat → MessageBus)
- **ParsedMessage**: 解析后消息 (MessageBus → 模块)
- **OutgoingMessage**: 待发送消息 (模块 → NapCat)
- **ModuleResponse**: 模块响应 (模块 → MessageBus)

### 错误码体系

错误码格式: `BRG-XXXX`

| 范围 | 模块 | 示例 |
|------|------|------|
| 1000-1999 | NapCat互联桥 | BRG-1001 连接失败 |
| 2000-2999 | 消息互联桥 | BRG-2001 消息解析失败 |
| 3000-3999 | 命令模块 | BRG-3001 未知命令 |
| 4000-4999 | CherryStudio模块 | BRG-4001 AI服务连接失败 |
| 5000-5999 | Server模块 | BRG-5001 启动失败 |
| 9000-9999 | 通用错误 | BRG-9001 未知错误 |

---

## 内置命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `.help` | 显示帮助信息 | `.help` |
| `.bot on/off` | 开启/关闭机器人回复 | `.bot off` |
| `.order list/add/remove` | 管理免@白名单 | `.order add 123456` |
| `.model list/change/status` | 管理LLM模型 | `.model change gpt-4` |
| `.ob join/exit/list` | 旁观者模式 | `.ob join` |
| `.dismiss` | 退群 (管理员) | `.dismiss 6789` |

---

## 测试覆盖

### 测试统计

| 模块 | 测试数 | 状态 |
|------|--------|------|
| StateManager | 14 | ✅ 全部通过 |
| NapCatBridge | 17 | ✅ 全部通过 |
| MessageBus | 19 | ✅ 全部通过 |
| CommandModule | 23 | ✅ 全部通过 |
| CherryStudioModule | 18 | ✅ 全部通过 |
| **总计** | **91** | **✅ 100%通过** |

### 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定模块测试
pytest tests/test_state_manager.py -v
pytest tests/test_napcat_bridge.py -v
pytest tests/test_message_bus.py -v
pytest tests/test_command_module.py -v
pytest tests/test_cherrystudio_module.py -v

# 生成覆盖率报告
pytest tests/ --cov=. --cov-report=html
```

---

## 项目结构

```
NEW QQ-MCP-Bridge/
├── modules/                    # 核心模块
│   ├── __init__.py
│   ├── napcat_bridge.py       # NapCat互联桥
│   ├── message_bus.py         # 消息互联桥
│   ├── command_module.py      # 命令模块
│   ├── cherrystudio_module.py # CherryStudio模块
│   └── commands/              # 内置命令
│       ├── __init__.py
│       └── builtin.py         # 内置命令实现
├── protocols/                  # 协议定义
│   ├── __init__.py
│   ├── messages.py            # 消息协议
│   └── error_codes.py         # 错误码定义
├── state/                      # 状态管理
│   ├── __init__.py
│   └── manager.py             # 状态管理器
├── tests/                      # 单元测试
│   ├── __init__.py
│   ├── test_state_manager.py
│   ├── test_napcat_bridge.py
│   ├── test_message_bus.py
│   ├── test_command_module.py
│   └── test_cherrystudio_module.py
├── Configuration/              # 配置文件
│   └── config.example.json
├── docs/                       # 文档
│   └── PROTOCOL.md            # 协议文档
├── Temp/                       # 运行时数据
├── PlayerLog/                  # 群聊日志
├── QQConversationRecord/       # Agent会话记录
├── pyproject.toml              # 项目配置
└── README.md                   # 本文档
```

---

## 开发指南

### 添加新命令

1. 在 `modules/commands/builtin.py` 中创建命令类：

```python
from modules.command_module import Command, CommandContext
from protocols.messages import ParsedMessage

class MyCommand(Command):
    name = "mycmd"
    description = "我的命令"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        return "Hello from my command!"
```

2. 在 `CommandRegistry.discover_builtin()` 中注册：

```python
from modules.commands.builtin import MyCommand
self.register(MyCommand())
```

### 添加新过滤器

1. 创建过滤器类：

```python
from modules.message_bus import MessageFilter
from protocols.messages import RawMessage

class MyFilter(MessageFilter):
    async def should_pass(self, msg: RawMessage) -> bool:
        # 返回 True 允许通过，False 拦截
        return True
```

2. 添加到 MessageBus：

```python
message_bus.add_filter(MyFilter())
```

---

## 故障排查

### 常见问题

1. **NapCat 连接失败 (BRG-1001)**
   - 检查 NapCatQQ 是否运行
   - 验证 WebSocket 端口配置
   - 检查 Access Token

2. **命令无响应 (BRG-3001)**
   - 确认命令语法正确
   - 检查命令模块是否启用

3. **AI服务失败 (BRG-4001)**
   - 检查 CherryStudio 是否运行
   - 验证 API Key 配置
   - 查看网络连接

### 日志位置

- 控制台输出: 实时日志
- 文件日志: `PlayerLog/` 目录

---

## 许可证

MIT License

---

## 版本历史

### v3.0.0 (2026-06-06)
- ✨ 完全重构的模块化架构
- ✨ 标准化消息协议
- ✨ 统一错误码体系
- ✨ 91个单元测试
- ✨ 热重载支持
- ✨ 异步任务隔离

### v2.0.0
- 初始版本

---

*最后更新: 2026-06-06*
