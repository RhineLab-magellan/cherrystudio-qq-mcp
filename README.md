# QQ-MCP Bridge

> CherryStudio (MCP Client, STDIO) ↔ 桥接服务器 ↔ NapCatQQ (WebSocket 双向)

将 QQ 机器人接入 CherryStudio，支持多 Agent 自动回复、群聊日志、图片识别、文件处理等功能。

---

## 功能特性

- 🔌 **MCP STDIO 协议** — 标准 MCP Server，无缝接入 CherryStudio
- 🤖 **多 Agent 自动回复** — 支持 CherryStudio 多 Agent 切换与群聊回复
- 👁️ **图片识别** — 多模态 Vision 模型自动识别图片内容
- 📄 **文件处理** — 通过 MinerU 解析处理文件
- 💬 **12 个 MCP 工具** — 发送消息、图片、文件、获取群列表/好友、撤回等
- 📝 **模块化命令系统** — `.help` `.bot` `.order` `.model` 等，新增命令只需添加 `.py` 文件
- 🔄 **LLM Provider 回退链** — 多 API Key 支持，索引递增回退
- 🔒 **单例进程锁** — 防止重复启动

---

## 前置条件

1. **[NapCatQQ](https://github.com/NapNeko/NapCatQQ)** — QQ 机器人框架，需安装并登录
2. **[CherryStudio](https://cherrystudio.ai/)** — MCP 客户端
3. **Python >= 3.10**
4. **[uv](https://docs.astral.sh/uv/)** — Python 包管理器（仅 UVX 方式需要）

---

## 安装方式
### 方式零：CherryStudio Agent 安装：

```txt```
安装并注册MCP：
https://github.com/RhineLab-magellan/cherrystudio-qq-mcp
自动完成：
- 克隆仓库
- 安装依赖
- 创建 Configuration/config.json
- 自动读取 NapCat 配置中的 ws_host、ws_port、access_token
- 如检测到 NapCat 正在运行，则测试 get_login_info WebSocket 连接
- 生成 CherryStudio stdio MCP 配置
- 生成 cherrystudio://mcp/install?servers=<base64>
最后汇报：
- 安装目录
- Python 路径
- server.py 路径
- NapCat 测试结果（如已执行）
- MCP 安装链接
- 需要用户补充的配置项
不要询问用户已可自动发现的信息。
遇到错误先尝试自动修复。
如未检测到 NapCat 或其配置文件，则跳过连接测试并继续安装流程。
```


### 方式一：UVX（推荐）

```bash
python Built_in/generate_install_url.py uvx
```

CherryStudio MCP 配置：

```json
{
  "mcpServers": {
    "qq-bridge": {
      "name": "QQ Bridge",
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/RhineLab-magellan/cherrystudio-qq-mcp.git", "cherrystudio-qq-mcp"],
      "env": {}
    }
  }
}
```

### 方式二：手动安装

```bash
git clone https://github.com/RhineLab-magellan/cherrystudio-qq-mcp.git
cd cherrystudio-qq-mcp
pip install -r requirements.txt
cp config.example.json Configuration/config.json
# 编辑 Configuration/config.json 填入你的配置
python server.py
```

---

## 配置字段说明

配置文件位于 `Configuration/config.json`（首次使用需从 `config.example.json` 复制）。

### 基础设置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `debug_mode` | `int` | `1` | 调试模式：`0`=关闭日志文件, `1`=开启 |
| `log_level` | `string` | `"INFO"` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `show_console` | `bool` | `true` | 是否显示独立控制台窗口 (Windows) |
| `admin_qq` | `string` | — | **管理员 QQ 号**，用于权限控制 |
| `auto_accept_friend` | `bool` | `true` | 是否自动同意好友申请 |
| `auto_accept_group` | `bool` | `true` | 是否自动同意群邀请 |
| `global_context` | `string` | `""` | 注入每次 LLM 调用的全局 System Prompt |
| `mcp_server_name` | `string` | `"QQ Bridge"` | CherryStudio 中显示的 MCP 名称 |

### NapCat 连接

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `napcat.ws_host` | `string` | `"127.0.0.1"` | WebSocket 地址 |
| `napcat.ws_port` | `int` | `3001` | WebSocket 端口 |
| `napcat.access_token` | `string` | — | NapCat 访问令牌 |

### Bridge

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `bridge.message_buffer_size` | `int` | `200` | 消息缓存上限 |

### CherryStudio

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cherry_api_key` | `string` | — | CherryStudio API Key（拉取 Agent 列表等） |

### LLM Provider（大语言模型）

`llm` 为数组，多 Provider 索引递增回退。

| 子字段 | 说明 |
|------|------|
| `name` | 显示名称 |
| `api_url` | API 地址 (OpenAI 兼容) |
| `api_key` | API 密钥 |
| `api_format` | 固定为 `"openai"` |
| `models` | 模型名称数组 |

- `default_llm.provider` — 默认 Provider 索引（从 0 开始）
- `default_llm.model` — 默认模型名

### Vision Provider（视觉模型）

`vision_providers` 结构与 `llm` 相同。用于图片识别。

- `default_vision.provider` — 默认 Provider 索引
- `default_vision.model` — 默认模型名

### Agent

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent_enabled` | `bool` | `true` | 启用 Agent 模式 |
| `agent_timeout_seconds` | `int` | `60` | API 超时（秒） |
| `agent_whitelist` | `string[]` | `[]` | Agent ID 白名单，空=自动拉取全部 |
| `agents` | `object` | `{}` | 手动配置的 Agent，空=自动拉取 |
| `default_agent` | `string` | — | 默认 Agent 名称 |

### 自动回复

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auto_reply.enabled` | `bool` | `true` | 开关自动回复 |
| `auto_reply.reply_to_groups` | `string[]` | `[]` | 限定群号，空=所有群 |
| `auto_reply.reply_to_friends` | `string[]` | `[]` | 限定好友，空=所有 |
| `auto_reply.reply_mode` | `string` | `"mention"` | `"mention"`=需@, `"always"`=总是回复 |
| `auto_reply.cooldown_seconds` | `int` | `3` | 同会话最小间隔（秒） |
| `auto_reply.max_context_messages` | `int` | `20` | 最大上下文消息数 |
| `auto_reply.message_split_threshold` | `float` | `5.0` | 分割标记阈值（秒） |
| `auto_reply.reply_chain_depth` | `int` | `4` | 回复链回溯深度 |
| `auto_reply.doc_threshold` | `int` | `1500` | 文档截断字符数 |

### Vision

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `vision.enabled` | `bool` | `true` | 启用图片识别 |
| `vision.prompt` | `string` | — | 图片识别 System Prompt |

### 文件处理 (MinerU)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `file_processing.enabled` | `bool` | `true` | 启用文件处理 |
| `file_processing.mineru_command` | `string` | `"mineru-open-api"` | MinerU 命令 |
| `file_processing.max_file_size_mb` | `int` | `10` | 最大文件大小 (MB) |
| `file_processing.summary_max_chars` | `int` | `1500` | 摘要最大字符数 |

### Bot 设定

`Configuration/BotSettingConfig.json`：

| 字段 | 说明 |
|------|------|
| `内置模块.custom_greeting` | 入群欢迎消息前缀 |
| `指令模块.bot_on_message` | `.bot on` 时的消息 |
| `指令模块.bot_off_message` | `.bot off` 时的消息 |
| `指令模块.dismiss_message` | 退群告别消息 |

---

## MCP 工具 (12个)

| 工具 | 参数 |
|------|------|
| `qq_send_message` | `message_type`(private/group), `target_id`, `message` |
| `qq_send_image` | `message_type`, `target_id`, `image_url`, `summary`(可选) |
| `qq_upload_file` | `message_type`, `target_id`, `content`, `filename`(可选) |
| `qq_get_recent_messages` | `target`(可选), `count` |
| `qq_get_group_msg_history` | `group_id`, `count` |
| `qq_get_group_list` | 无 |
| `qq_get_friend_list` | 无 |
| `qq_get_group_members` | `group_id` |
| `qq_get_user_info` | `user_id` |
| `qq_get_recent_contacts` | `count` |
| `qq_check_status` | 无 |
| `qq_recall_message` | `message_id` |

---

## 命令系统

以 `.` 开头自动识别为指令。新增命令在 `OrderSystem/` 下创建 `.py` 文件。

| 命令 | 功能 |
|------|------|
| `.help` | 显示所有命令 |
| `.bot on/off` | 开关指令模式 |
| `.bot orderwhite` | 切换免@ |
| `.order 切换 <名称>` | 切换 Agent |
| `.order 列表` | Agent 列表 |
| `.order 重建会话` | 重建会话 |
| `.order status` | 会话状态 |
| `.model list/change/status` | 模型管理（管理员） |
| `.master LLMReset` | 重置主 KEY |
| `.master AllResetAgent` | 删除所有会话（管理员） |
| `.master OnlyResetAgent` | 仅删 API 会话（管理员） |
| `.log new/list/get/del` | 群聊日志管理 |
| `.log on/off/end` | 日志录制控制 |
| `.ob join/exit/list` | 旁观模式 |
| `.dismiss <群号>` | 退群并清理 |
| `.send <消息>` | 发给管理员 |

---

## 目录结构

```
qq_mcp_bridge/
├── server.py                   # 唯一入口
├── pyproject.toml              # Python 包定义
├── start.bat                   # Windows 启动脚本
├── Built_in/                   # 核心模块
│   ├── auto_reply.py           # 自动回复引擎
│   ├── napcat_client.py        # NapCat WS 客户端
│   ├── conversation_store.py   # 会话持久化
│   └── generate_install_url.py # 一键安装 URL 生成器
├── Configuration/              # 配置文件
│   ├── config.json             # 全局设置（不纳入版本控制）
│   └── BotSettingConfig.json   # 机器人显示文本
├── OrderSystem/                # 命令系统（模块化）
├── Temp/                       # 运行时数据
├── QQConversationRecord/       # Agent 会话（自动生成）
└── PlayerLog/                  # 群聊日志（自动生成）
```

---

## 常见问题

**Q: NapCat 连接失败？**  
确保 NapCatQQ 已启动并启用 WebSocket。检查 `config.json` 中 `napcat` 配置。

**Q: 如何限制只在特定群回复？**  
设置 `auto_reply.reply_to_groups` 为群号列表。

**Q: 如何添加新 LLM？**  
在 `llm` 数组中添加新条目，需 OpenAI 兼容 API。

**Q: bridge.pid 是什么？**  
运行时进程锁，防止重复启动。已在 `.gitignore` 中排除。

---

## 许可证

[MIT License](LICENSE)
