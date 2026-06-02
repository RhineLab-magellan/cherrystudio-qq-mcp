# QQ-MCP Bridge

> CherryStudio (MCP Client) ↔ 桥接服务器 ↔ NapCatQQ (WebSocket 双向)

将 QQ 机器人接入 CherryStudio，使 AI 助手能够收发 QQ 私聊/群聊消息，支持多 Agent 自动回复、图片识别、文件处理等功能。

---

## 架构概览

```
┌─────────────────┐     STDIO (MCP)      ┌──────────────────┐     WebSocket      ┌──────────────┐
│  CherryStudio    │ ◄──────────────────► │  qq_mcp_bridge   │ ◄────────────────► │  NapCatQQ     │
│  (MCP Client)    │                      │  (本服务器)        │                    │  (QQ Bot)     │
└─────────────────┘                      └──────────────────┘                    └──────────────┘
                                                  │
                                                  ├── Agent API (CherryStudio 本地)
                                                  ├── Chat API (OpenCode / DeepSeek)
                                                  ├── Vision API (图片识别)
                                                  └── MinerU (文件解析)
```

---

## 目录结构

```
qq_mcp_bridge/
├── server.py                 # MCP 服务器主入口，注册所有 MCP 工具
├── napcat_client.py          # NapCatQQ WebSocket 双向客户端
├── auto_reply.py             # 自动回复引擎（多 Agent / 图片识别 / 文件处理）
├── conversation_store.py     # 会话持久化存储（按 Agent 分目录）
├── config.json               # 全局配置文件
├── generate_install_url.py   # 生成 CherryStudio 一键安装链接
├── install_info.txt          # 安装说明输出
├── agents_dump.json          # CherryStudio Agent 列表导出
├── agents.txt                # Agent 会话列表（调试用）
├── requirements.txt          # Python 依赖
├── start.bat                 # Windows 启动脚本
├── bridge.log                # 运行日志（debug 模式）
└── QQConversationRecord/     # 会话记录持久化目录
    ├── mapping.json          # 会话 → Agent 绑定映射
    └── {agent_name}/         # 每个 Agent 独立的会话目录
        └── {msg_type}_{target_id}/
            ├── session.json      # 当前会话日志
            ├── memory.json       # 历史摘要记忆
            └── meta.json         # 元数据（活跃时间等）
```

---

## 核心模块

### 1. `server.py` — MCP 服务器

基于 `mcp` 库实现的 STDIO MCP 服务器，向 CherryStudio 暴露以下 **12 个工具**：

| 工具名称 | 功能 |
|---|---|
| `qq_send_message` | 发送消息到私聊/群聊 |
| `qq_get_recent_messages` | 获取最近缓存的 QQ 消息 |
| `qq_get_group_list` | 获取群聊列表 |
| `qq_get_friend_list` | 获取好友列表 |
| `qq_get_group_members` | 获取群成员列表 |
| `qq_get_user_info` | 获取用户基本信息 |
| `qq_check_status` | 检查机器人在线状态 |
| `qq_recall_message` | 撤回消息 |
| `qq_get_group_msg_history` | 拉取群历史消息 |
| `qq_get_recent_contacts` | 获取最近会话列表 |
| `qq_upload_file` | 上传文件到私聊/群聊 |
| `qq_send_image` | 发送图片到私聊/群聊 |

启动时自动连接 NapCatQQ 并初始化自动回复模块。

---

### 2. `napcat_client.py` — NapCatQQ 客户端

通过 **单一 WebSocket 连接** 实现双向通信：

- **事件接收**：监听 QQ 消息 (`post_type: message`)、通知 (`post_type: notice`)
- **API 调用**：通过 OneBot 协议的 `action`/`echo` 机制发起请求-响应

**数据模型**：

- `QQMessage`：统一的消息模型，包含 `message_id`、`message_type`、`sender_id`、文本、图片文件 ID、文件信息等
- `MessageBuffer`：消息缓冲区，按会话 (`group:xxx` / `private:xxx`) 分类存储，全局容量可配置

**支持的 API**：

发送消息、获取消息、图片下载/发送、文件上传、群/好友列表、群成员、用户信息、撤回、登录信息、最近联系人、聊天记录等。

**特性**：断线自动重连（指数退避）、连接超时处理、请求超时保护。

---

### 3. `auto_reply.py` — 自动回复引擎

#### 触发机制

- **群聊**：需 @机器人（`reply_mode: "mention"`）
- **私聊**：直接触发
- 支持白名单过滤（`reply_to_groups` / `reply_to_friends`）
- 防自激：过滤机器人自己的消息
- **冷却时间**：同一会话连续消息间隔控制（`cooldown_seconds`）

#### 多 Agent 系统

每个 QQ 会话可绑定不同的 CherryStudio Agent：

```
Agent 绑定流程:
1. 内存中的 Conversation 对象
2. 持久化 mapping.json（从上次绑定恢复）
3. 回退到 default_agent
4. 回退到第一个可用 Agent
```

Agent 配置包含 `agent_id`、`work_dirs`（工作区路径）。

#### 会话管理

- **Worker 模式**：每个会话一个消息队列 + Worker，保证顺序处理
- **空闲回收**：5 分钟无消息自动退出 Worker
- **新会话注入**：首次对话注入工作区上下文 + 历史记忆 + 全局规则
- **过期处理**：3 天无交互 → AI 自动摘要 → 保存 `memory.json` → 归档旧日志 → 新建会话

#### 双 API 组 + 故障切换

支持两套 API 组配置（如 OpenCode + DeepSeek），主组故障时自动切换到备用组：

- **Chat API**：用于历史摘要
- **Agent API**：通过 CherryStudio 本地 API (`http://127.0.0.1:23333`) 调用 Agent 会话
- **Vision API**：图片识别
- 额度错误自动触发切换并通知管理员

#### 图片识别 (Vision)

- 自动识别消息中的图片（包括引用链中的图片）
- 主备双 Vision API
- 支持 `openai` / `anthropic` 两种 API 格式

#### 文件处理 (MinerU)

- 自动下载消息中的文件
- 通过 MinerU CLI 提取文本内容
- 摘要注入对话上下文

#### 引用链追踪

递归获取被引用消息的内容和图片（深度可配置 `reply_chain_depth`），让 Agent 理解完整上下文。

#### 自言自语过滤器

内置大量正则模式过滤 Agent 的内部独白（如"好的博士，我已经回复了"、"让我先看看…"），确保发送到 QQ 的只有真正面向用户的内容。

#### Markdown 图片处理

自动提取 Agent 回复中的 Markdown 图片语法 `![alt](url)`，转为 QQ 图片消息发送。

#### 长文本处理

超过阈值（`doc_threshold`）的回复自动以文件形式发送，避免消息过长被截断。

---

### 4. `conversation_store.py` — 会话持久化

按 Agent 分目录存储，每个会话目录包含：

| 文件 | 内容 |
|---|---|
| `session.json` | 当前会话的完整消息日志 |
| `memory.json` | AI 生成的历史摘要 |
| `meta.json` | `agent_session_id`、`last_active`、`message_count` |

**关键函数**：

- `get_conversation_agent()` / `set_conversation_agent()` — 会话→Agent 绑定
- `load_session_log()` / `append_to_log()` — 消息记录
- `load_memory()` / `save_memory()` — 摘要记忆
- `is_stale()` / `force_stale()` / `delete_session()` — 过期管理
- `get_agent_session_id()` / `set_agent_session_id()` — Agent 会话 ID 持久化

---

## 配置说明 (`config.json`)

按 **全局设置 → 连接设置 → API 设置 → Agent 设置 → 功能设置** 排序：

```jsonc
{
  // ===== 全局设置 =====
  "debug_mode": 1,              // 0=关闭, 1=开启日志文件
  "show_console": true,         // Windows 下显示控制台窗口
  "admin_qq": "2712509058",     // 管理员 QQ（接收故障通知）
  "global_context": "...",      // 全局上下文（注入所有 Agent 会话）

  // ===== NapCat 连接 =====
  "napcat": {
    "ws_host": "127.0.0.1",     // WebSocket 地址
    "ws_port": 3001,            // WebSocket 端口
    "access_token": "xxx"       // 鉴权 token
  },

  // ===== 桥接设置 =====
  "bridge": {
    "message_buffer_size": 200  // 消息缓冲区容量
  },

  // ===== API 设置 =====
  "active_api_group": 0,        // 当前使用的 API 组 (0 或 1)
  "cherry_api_key": "xxx",      // CherryStudio API Key
  "api_groups": {
    "0": {
      "name": "OpenCode",
      "models": { "default": "minimax-m2.5", "available": [...] },
      "llm": { "api_url": "...", "api_key": "..." },
      "vision": { "api_url": "...", "api_key": "...", "model": "...", "api_format": "openai" }
    },
    "1": {
      "name": "DeepSeek+Qwen",
      "models": { "default": "deepseek-v4-flash", "available": [...] },
      "llm": { "api_url": "https://api.deepseek.com/v1/chat/completions", "api_key": "..." },
      "vision": { "api_url": "...", "api_key": "...", "model": "qwen3-vl-plus" }
    }
  },

  // ===== Agent 设置 =====
  "agent_enabled": true,
  "agent_timeout_seconds": 60,
  // 白名单：仅列表中的 agent_id 可被自动拉取。手动配置的 agents 不受此限制。
  "agent_whitelist": ["agent_xxx", "agent_yyy"],
  // agents 为空 + agent_whitelist 非空 → 自动从 CherryStudio /v1/agents 拉取
  // agents 手动填写 → 优先使用（向后兼容）
  "agents": {
    "麦哲伦": { "agent_id": "agent_xxx", "work_dirs": ["C:\\..."] }
  },
  "default_agent": "麦哲伦",

  // ===== 自动回复 =====
  "auto_reply": {
    "enabled": true,
    "reply_to_groups": ["912389435"],        // 群聊白名单
    "reply_to_friends": ["2712509058"],      // 好友白名单
    "reply_mode": "mention",                 // mention=仅@触发
    "cooldown_seconds": 5,                   // 冷却时间
    "max_context_messages": 20,              // 最大上下文消息数
    "message_split_threshold": 5.0,          // 消息分条时间阈值(秒)
    "reply_chain_depth": 4,                  // 引用链追踪深度
    "doc_threshold": 2000                    // 长文本自动转文件阈值
  },

  // ===== 图片识别 (Vision) =====
  "vision": { "enabled": true, "prompt": "请描述这张图片..." },

  // ===== 文件处理 (MinerU) =====
  "file_processing": {
    "enabled": false,
    "mineru_command": "mineru-open-api",
    "max_file_size_mb": 10,
    "summary_max_chars": 2000
  }
}
```

> **Agent 自动拉取**: 当 `agents` 为空但 `agent_whitelist` 不为空时，服务器启动时自动调用 CherryStudio 的 `/v1/agents` API 拉取 Agent 列表，仅白名单内的 Agent 会被加载。手动填写 `agents` 时优先使用手动配置（向后兼容）。`self_qq` 由运行时登录接口自动获取，无需手动配置。

---

## 安装与运行

### 依赖

```bash
pip install -r requirements.txt
```

```
mcp>=1.0.0
aiohttp>=3.9.0
websockets>=12.0
```

### 前置条件

1. 安装并启动 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)（QQ Bot 框架）
2. 安装 [CherryStudio](https://cherrystudio.ai/)
3. 确保 NapCatQQ WebSocket 在 `127.0.0.1:3001` 可访问

### 启动

**Windows**：双击 `start.bat` 或在终端执行：

```bash
python server.py
```

### 安装到 CherryStudio

1. 运行 `python generate_install_url.py` 生成一键安装链接
2. 将 `cherrystudio://mcp/install?...` 链接粘贴到浏览器
3. CherryStudio 自动打开并安装 MCP 服务器
4. 在 CherryStudio → 设置 → MCP 服务器 → 启用 "QQ Bridge"

或手动配置：
```json
{
  "mcpServers": {
    "qq-bridge": {
      "type": "stdio",
      "command": "python路径",
      "args": ["server.py路径"]
    }
  }
}
```

---

## 数据流

```
QQ 消息 → NapCatQQ → WebSocket → napcat_client (解析 QQMessage)
    → MessageBuffer (缓存)
    → auto_reply._should_reply() (判断是否回复)
    → Worker 队列 (顺序处理)
    → 引用链追踪 + 图片识别 + 文件处理
    → Agent API (CherryStudio) / Chat API (直连LLM)
    → 自言自语过滤
    → 长文本检测
    → napcat_client.send_msg() → QQ
    → conversation_store 持久化
```

---

## 会话生命周期

```
新消息 → 创建/复用会话
   │
   ├── 活跃（< 3天无交互）
   │     └── 消息累积在 session.json
   │
   └── 过期（≥ 3天无交互）
         ├── AI 自动生成摘要 → 保存到 memory.json
         ├── 旧日志归档 → session_archive_{timestamp}.json
         ├── 创建新会话
         └── 注入：工作区上下文 + 历史记忆 + 全局规则
```

---

## 调试

- 设置 `debug_mode: 1` → 日志输出到 `bridge.log`
- 设置 `show_console: true` → Windows 下显示控制台窗口
- 日志级别可通过修改 `server.py` 中的 `logging.basicConfig(level=...)` 调整
- `agents.txt` 记录当前 Agent 会话列表

---

## 技术栈

- **Python 3.14**
- **MCP** (`mcp>=1.0.0`)：Model Context Protocol 服务器
- **WebSocket** (`websockets>=12.0`)：NapCatQQ 双向通信
- **aiohttp** (`aiohttp>=3.9.0`)：异步 HTTP 客户端（Agent API、Vision API、文件下载）
- **OneBot v11 协议**：QQ 机器人标准协议（通过 NapCatQQ 实现）
