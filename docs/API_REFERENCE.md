# QQ-MCP Bridge v3.0 系统接口文档

> 文档版本: 3.0  
> 生成日期: 2026-06-07  
> 项目路径: `C:\CherryStudio\qq-mcp-bridge`

---

## 目录

1. [MCP 工具接口](#1-mcp-工具接口)
2. [命令系统接口](#2-命令系统接口)
3. [OneBot v11 API 封装](#3-onebot-v11-api-封装)
4. [消息协议](#4-消息协议)
5. [错误码体系](#5-错误码体系)
6. [状态管理接口](#6-状态管理接口)
7. [配置接口](#7-配置接口)

---

## 1. MCP 工具接口

系统通过 **FastMCP** 以 **stdio 传输**方式注册 12 个标准 MCP 工具，供 CherryStudio 等 MCP 客户端调用。

> **版本变更说明**: v3.0 按设计决策取消了旧版的第 13 个工具 `qq_confirm_response`（其功能已由 Bridge 内部 `mark_responding`/`unmark_responding` 自动机制替代），当前版本注册 12 个工具，这是完整设计而非缺失。

注册位于 `server.py` 的 `Server._register_mcp_tools()` 方法（第 202-598 行）。

---

### 1.1 qq_send_message

**发送文本消息** -- 向指定的 QQ 私聊或群聊发送一条文本消息。这是 AI 向用户回复内容的主要方式。

Bridge 会自动处理消息长度: 当文本超过 `doc_threshold`（默认 1000 字符）时，自动转为 `.md` 文档文件发送，并附带前 300 字符的预览通知。调用方无需关心长度限制。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message_type` | `str` | 是 | 消息类型，`"private"` 或 `"group"` |
| `target_id` | `str` | 是 | 目标 ID（QQ 号或群号） |
| `message` | `str` | 是 | 消息文本内容 |

**返回值**: `str` -- 发送结果描述，如 `"消息已发送 (ID: 12345)"` 或错误信息。

**行为说明**:
- 发送前会检查 `target_id` 是否在活跃会话中（`is_target_active`），不在则返回提示。
- 遇到 `ConnectionError` 或 `TimeoutError` 时，等待 2 秒后自动重试一次。

**示例**:
```json
{
  "tool": "qq_send_message",
  "arguments": {
    "message_type": "group",
    "target_id": "123456789",
    "message": "你好，有什么可以帮你的？"
  }
}
```

---

### 1.2 qq_send_image

**发送图片** -- 向指定的 QQ 私聊或群聊发送一张图片。

Bridge 内部流程: 下载图片 URL -> base64 编码 -> 通过 OneBot `send_msg` 的 message 段格式发送（`type: "image", data: {file: "base64://..."}`）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message_type` | `str` | 是 | 消息类型，`"private"` 或 `"group"` |
| `target_id` | `str` | 是 | 目标 ID（QQ 号或群号） |
| `image_url` | `str` | 是 | 可公开访问的 HTTP/HTTPS 图片链接 |
| `summary` | `str` | 否 | 图片的文字说明，默认为空 |

**返回值**: `str` -- 发送结果描述，如 `"图片已发送 (ID: 12345)"`。

**示例**:
```json
{
  "tool": "qq_send_image",
  "arguments": {
    "message_type": "group",
    "target_id": "123456789",
    "image_url": "https://example.com/image.png",
    "summary": "这是一张示意图"
  }
}
```

---

### 1.3 qq_upload_file

**上传文件** -- 向指定的 QQ 私聊或群聊上传一个文件。支持两种模式:

- **模式 A（文本内容）**: 提供 `content` 参数，Bridge 自动保存为临时文件并上传。
- **模式 B（本地文件）**: 提供 `file_path` 参数（优先），Bridge 直接上传该文件。

底层调用 OneBot 的 `upload_private_file` / `upload_group_file` API。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message_type` | `str` | 是 | 消息类型，`"private"` 或 `"group"` |
| `target_id` | `str` | 是 | 目标 ID（QQ 号或群号） |
| `content` | `str` | 条件 | 文本内容（与 `file_path` 二选一） |
| `file_path` | `str` | 条件 | 本地文件绝对路径（与 `content` 二选一，优先） |
| `filename` | `str` | 否 | 接收方看到的文件名。模式 A 默认 `bridge_doc_{timestamp}.md` |

**返回值**: `str` -- 上传结果描述，如 `"文件已上传: report.md (1024 字符)"`。

**注意事项**:
- `content` 和 `file_path` 必须提供其一，否则返回错误。
- 临时文件在上传完成后自动清理。
- 遇到连接异常时等待 2 秒后自动重试一次。

**示例**:
```json
{
  "tool": "qq_upload_file",
  "arguments": {
    "message_type": "group",
    "target_id": "123456789",
    "content": "# 报告\n\n这是一份自动生成的报告...",
    "filename": "daily_report.md"
  }
}
```

---

### 1.4 qq_get_recent_messages

**获取最近消息** -- 获取本地缓存的最近消息。数据来源于 Bridge 运行期间 `MessageBuffer` 接收到的消息缓存。快速轻量，包含私聊和群聊。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `target` | `str` | 否 | `""` | 目标 ID，空则返回全局消息 |
| `count` | `int` | 否 | `10` | 返回消息数量 |

**返回值**: `list[dict]` -- 消息列表，每条消息包含以下字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `msg_id` | `str` | 消息 ID |
| `sender_id` | `str` | 发送者 QQ 号 |
| `sender_name` | `str` | 发送者昵称 |
| `content` | `str` | 文本内容 |
| `timestamp` | `str` | ISO 8601 时间戳 |
| `source` | `str` | `"group"` 或 `"private"` |
| `target_id` | `str` | 目标 ID |
| `message_type` | `str` | 消息类型 |
| `image_count` | `int` | 图片数量 |
| `file_count` | `int` | 文件数量 |
| `group_name` | `str\|null` | 群名称（仅群聊有值） |

**示例**:
```json
{
  "tool": "qq_get_recent_messages",
  "arguments": {
    "target": "123456789",
    "count": 5
  }
}
```

---

### 1.5 qq_get_group_msg_history

**获取群聊历史消息** -- 从 QQ 服务器拉取群聊的历史消息记录。仅支持群聊，包含 Bridge 未运行期间的历史消息。底层调用 OneBot `get_group_msg_history` API。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `group_id` | `str` | 是 | - | 群号 |
| `count` | `int` | 否 | `20` | 消息数量 |

**返回值**: `list[dict]` -- 消息列表（格式由 OneBot API 决定）。

**示例**:
```json
{
  "tool": "qq_get_group_msg_history",
  "arguments": {
    "group_id": "123456789",
    "count": 30
  }
}
```

---

### 1.6 qq_get_group_list

**获取群列表** -- 获取当前账号加入的所有群。底层调用 OneBot `get_group_list` API。

| 参数 | 无 |
|------|------|

**返回值**: `list[dict]` -- 群列表，每项通常包含 `group_id`、`group_name` 等字段。

**示例**:
```json
{
  "tool": "qq_get_group_list",
  "arguments": {}
}
```

---

### 1.7 qq_get_friend_list

**获取好友列表** -- 获取当前账号的好友列表。底层调用 OneBot `get_friend_list` API。

| 参数 | 无 |
|------|------|

**返回值**: `list[dict]` -- 好友列表，每项通常包含 `user_id`、`nickname` 等字段。

**示例**:
```json
{
  "tool": "qq_get_friend_list",
  "arguments": {}
}
```

---

### 1.8 qq_get_group_members

**获取群成员列表** -- 获取指定群的所有成员信息。底层调用 OneBot `get_group_member_list` API。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | `str` | 是 | 群号 |

**返回值**: `list[dict]` -- 成员列表，每项通常包含 `user_id`、`nickname`、`card`、`role` 等字段。

**示例**:
```json
{
  "tool": "qq_get_group_members",
  "arguments": {
    "group_id": "123456789"
  }
}
```

---

### 1.9 qq_get_user_info

**获取用户信息** -- 获取指定 QQ 号的用户信息。底层调用 OneBot `get_stranger_info` API。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_id` | `str` | 是 | QQ 号 |

**返回值**: `dict` -- 用户信息，通常包含 `user_id`、`nickname`、`sex`、`age` 等字段。

**示例**:
```json
{
  "tool": "qq_get_user_info",
  "arguments": {
    "user_id": "2712509058"
  }
}
```

---

### 1.10 qq_get_recent_contacts

**获取最近联系人** -- 获取最近有消息往来的会话列表（包含群号和 QQ 号）。底层调用 OneBot `get_recent_contact` API。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `count` | `int` | 否 | `10` | 返回数量 |

**返回值**: `list[dict]` -- 联系人列表。

**示例**:
```json
{
  "tool": "qq_get_recent_contacts",
  "arguments": {
    "count": 20
  }
}
```

---

### 1.11 qq_check_status

**检查连接状态** -- 检查 NapCat 连接状态和基本信息。

| 参数 | 无 |
|------|------|

**返回值**: `dict` -- 状态信息，包含以下字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `connected` | `bool` | NapCat WebSocket 是否已连接 |
| `host` | `str` | 连接主机地址 |
| `port` | `int` | 连接端口 |
| `bot_qq` | `str` | 机器人 QQ 号（未获取时为 `"未知"`） |
| `cached_messages` | `int` | 消息缓冲区中的全局消息数 |
| `active_targets` | `list[str]` | 所有有消息记录的目标键列表 |

**示例**:
```json
{
  "tool": "qq_check_status",
  "arguments": {}
}
```

**返回示例**:
```json
{
  "connected": true,
  "host": "127.0.0.1",
  "port": 3001,
  "bot_qq": "123456789",
  "cached_messages": 42,
  "active_targets": ["group:987654321", "private:111222333"]
}
```

---

### 1.12 qq_recall_message

**撤回消息** -- 撤回一条机器人自己发送的消息。仅能撤回机器人发送的消息，不能撤回其他用户的消息。底层调用 OneBot `delete_msg` API。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message_id` | `str` | 是 | 要撤回的消息 ID |

**返回值**: `bool` -- `true` 表示撤回成功，`false` 表示失败。

**示例**:
```json
{
  "tool": "qq_recall_message",
  "arguments": {
    "message_id": "98765"
  }
}
```

---

## 2. 命令系统接口

命令系统通过 `CommandModule` 管理，使用 `CommandRegistry` 注册 9 个内置命令。命令前缀为 `.`（英文句号）或 `。`（中文句号），支持紧凑格式回退（如 `.st力量5` 解析为命令 `st` 参数 `力量5`）。

> **版本变更说明**: 旧版系统拥有 23+ 个命令。v3.0 仅保留 9 个核心命令。已移除的命令包括: `.r`, `.rh`, `.ra`, `.show`, `.del`, `.pc`, `.nn`, `.st`, `.rk`, `.rkb`, `.rkp`, `.sck`, `.ark`, `.sn`, `.log`。

命令实现位于 `modules/commands/builtin.py`。

---

### 2.1 .help -- 显示帮助信息

| 属性 | 值 |
|------|------|
| **语法** | `.help` |
| **参数** | 无 |
| **权限** | 所有用户 |
| **适用场景** | 群聊、私聊 |
| **实现类** | `HelpCommand` |

**功能**: 动态列出所有已注册命令及其描述。如果 `CommandRegistry` 不可用，回退到硬编码的默认帮助文本。

**示例**:
```
.help
```

**输出示例**:
```
可用命令列表:

.bot - 开启或关闭机器人在本群的自动回复
.dismiss - 让机器人退出指定群 (管理员专用)
.help - 显示所有可用命令及其说明
.master - 管理员专用命令 (LLMReset/AllResetAgent/OnlyResetAgent)
.model - 查看或切换LLM模型
.ob - 管理旁观者模式
.order - 会话/Agent 管理 (切换/列表/重建/状态)
.send - 管理员消息转发到指定群或私聊
.welcome - 新成员欢迎设置 (使用 .welcome open/close/set/status 管理新成员入群欢迎)

提示: 使用 .命令名 可查看具体命令的详细帮助
```

---

### 2.2 .bot -- 机器人开关

| 属性 | 值 |
|------|------|
| **语法** | `.bot on\|off\|status\|orderwhite` |
| **参数** | `on` / `off` / `status` / `orderwhite` |
| **权限** | 所有用户（`off` 操作通过黑名单机制实现） |
| **适用场景** | 仅群聊 |
| **实现类** | `BotCommand` |

**功能**:
- `on`: 从黑名单移除当前群，恢复正常回复。
- `off`: 将当前群加入黑名单，仅响应 `.` 开头的命令。
- `status`: 显示当前开关状态。
- `orderwhite`: 切换当前群的免 @ 白名单状态。

**消息模板**: 可通过 `Configuration/BotSettingConfig.json` 的 `BuiltInOrder.bot_on_message` 和 `BuiltInOrder.bot_off_message` 自定义回复文本。

**示例**:
```
.bot off
```
**输出**: `已开启指令模式，仅响应 .开头的命令。`

---

### 2.3 .order -- 会话与 Agent 管理

| 属性 | 值 |
|------|------|
| **语法** | `.order <子命令> [参数]` |
| **权限** | 所有用户 |
| **适用场景** | 群聊、私聊 |
| **实现类** | `OrderCommand` |

**子命令一览**:

| 子命令 | 说明 |
|--------|------|
| `.order 切换 <名称>` / `.order switch <name>` | 切换到指定 Agent（持久化） |
| `.order 列表` / `.order listagents` / `.order agents` | 查看所有可用 Agent |
| `.order 重建会话` / `.order rebuild` / `.order reset` | 删除当前会话，下次对话开启新上下文 |
| `.order status` / `.order 状态` | 查看当前会话状态（Agent、模型偏好、处理器状态） |
| `.order list` | 查看免 @ 群列表 |
| `.order add [群号]` | 添加群到免 @ 白名单（不指定群号时使用当前群） |
| `.order remove <群号>` | 从免 @ 白名单移除群 |
| `.order help` / `.order 帮助` | 显示子命令帮助 |

**示例**:
```
.order 切换 麦哲伦QQ
.order status
.order add 123456789
```

---

### 2.4 .model -- 模型切换

| 属性 | 值 |
|------|------|
| **语法** | `.model list\|change\|status\|reset [模型名]` |
| **权限** | 所有用户 |
| **适用场景** | 群聊、私聊 |
| **实现类** | `ModelCommand` |

**子命令**:

| 子命令 | 说明 |
|--------|------|
| `.model list` | 列出可用模型 |
| `.model change <模型名>` | 切换到指定模型（持久化，重启后仍生效） |
| `.model status` | 查看当前会话的模型偏好 |
| `.model reset` | 清除模型偏好，恢复默认模型 |

模型偏好持久化到 `SharedState.saved_models`。

**示例**:
```
.model change gpt-4
.model status
```

---

### 2.5 .ob -- 旁观者模式

| 属性 | 值 |
|------|------|
| **语法** | `.ob join\|exit\|list\|clr\|on\|off` |
| **权限** | 所有用户 |
| **适用场景** | 仅群聊 |
| **实现类** | `ObCommand` |

**子命令**:

| 子命令 | 说明 |
|--------|------|
| `.ob on` | 开启本群的旁观者模式 |
| `.ob off` | 关闭本群的旁观者模式 |
| `.ob join` | 加入旁观者（自动开启旁观模式），收到本群所有消息的私聊转发 |
| `.ob exit` | 退出旁观者 |
| `.ob list` | 列出当前群的旁观者 |
| `.ob clr` | 清除本群所有旁观者 |

旁观者消息转发格式: `"[旁观] 群 {group_id} -- {sender_name}: {content}"`

**示例**:
```
.ob join
.ob list
```

---

### 2.6 .dismiss -- 解散 AI 会话（退群）

| 属性 | 值 |
|------|------|
| **语法** | `.dismiss <群号后四位>` |
| **权限** | **仅管理员**（`admin_qq` 配置项） |
| **适用场景** | 群聊、私聊 |
| **实现类** | `DismissCommand` |

**功能**: 让机器人退出指定群。通过群号末 4 位匹配目标群。退群后自动清理本地的黑名单和白名单数据。

**行为流程**:
1. 验证发送者是否为管理员。
2. 验证参数为 4 位数字。
3. 调用 `get_group_list()` 获取群列表并匹配末 4 位。
4. 调用 `leave_group()` 退群。
5. 清理 `StateManager` 中该群的黑名单和白名单记录。
6. 如 `BotSettingConfig.json` 配置了 `BuiltInOrder.dismiss_message`，发送告别消息。

**示例**:
```
.dismiss 5678
```

---

### 2.7 .send -- 转发消息

| 属性 | 值 |
|------|------|
| **语法** | `.send <target_type> <target_id> <message>` |
| **权限** | **仅管理员**（`admin_qq` 配置项） |
| **适用场景** | 群聊、私聊 |
| **实现类** | `SendCommand` |

**参数**:

| 参数 | 说明 |
|------|------|
| `target_type` | `group` 或 `private` |
| `target_id` | 目标群号或 QQ 号 |
| `message` | 要转发的消息内容 |

**示例**:
```
.send group 123456789 大家好，这是管理员转发的消息
.send private 987654321 你好，这是私信
```

---

### 2.8 .master -- 管理员命令

| 属性 | 值 |
|------|------|
| **语法** | `.master <子命令>` |
| **权限** | **仅管理员**（`admin_qq` 配置项） |
| **适用场景** | 群聊、私聊 |
| **实现类** | `MasterCommand` |

**子命令**:

| 子命令 | 说明 |
|--------|------|
| `.master LLMReset` | 重置所有活跃 Agent 映射，回退到默认 Provider |
| `.master AllResetAgent` | 删除所有会话数据（清空 active_agents、observers、ob_groups） |
| `.master OnlyResetAgent` | 仅清除活跃会话记录（保留其他状态） |

**示例**:
```
.master LLMReset
```

---

### 2.9 .welcome -- 入群欢迎设置

| 属性 | 值 |
|------|------|
| **语法** | `.welcome open\|close\|set\|status [消息]` |
| **权限** | 所有用户 |
| **适用场景** | 仅群聊 |
| **实现类** | `WelcomeCommand` |

**子命令**:

| 子命令 | 说明 |
|--------|------|
| `.welcome open` / `.welcome on` / `.welcome 开启` | 开启本群新成员欢迎 |
| `.welcome close` / `.welcome off` / `.welcome 关闭` | 关闭本群新成员欢迎 |
| `.welcome set <消息>` | 设置欢迎语（支持 `{at}` 占位符代表新成员 @） |
| `.welcome status` / `.welcome 状态` | 查看当前欢迎设置 |
| `.welcome help` / `.welcome 帮助` | 显示子命令帮助 |

欢迎配置持久化到 `SharedState.welcome_config`。

**默认欢迎语**: `"欢迎新人！我是本群助手，发送 .help 查看可用命令～"`

**示例**:
```
.welcome open
.welcome set 欢迎 {at} 加入本群！请阅读群公告。
.welcome status
```

---

## 3. OneBot v11 API 封装

`NapCatBridge`（位于 `modules/napcat_bridge.py`）封装了 OneBot v11 协议的所有 API 调用。所有 API 通过内部的 `_call(action, params, timeout)` 方法统一调用，使用 WebSocket JSON-RPC 方式与 NapCatQQ 通信。

### 3.1 消息发送

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `send_msg` | `send_message(msg: OutgoingMessage) -> str` | 发送消息的统一入口（自动路由到私聊/群聊） |
| `send_msg` | `_send_text(message_type, target_id, text) -> str` | 发送文本消息（内部方法，含长文本自动转文档） |
| `send_msg` | `_send_image(message_type, target_id, attachments) -> str` | 发送图片消息（内部方法，下载 URL -> base64 -> 发送） |
| `send_msg` | `send_image(message_type, target_id, image_url, summary) -> str` | 发送图片的公开 API 方法 |
| `delete_msg` | `delete_msg(message_id: str) -> bool` | 撤回消息 |

### 3.2 群管理

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `set_group_kick` | `set_group_kick(group_id, user_id, reject_add_request=False) -> bool` | 群踢人 |
| `set_group_ban` | `set_group_ban(group_id, user_id, duration=1800) -> bool` | 群禁言（单人），`duration` 秒数，0 为解除 |
| `set_group_whole_ban` | `set_group_whole_ban(group_id, enable=True) -> bool` | 群全员禁言 |
| `set_group_admin` | `set_group_admin(group_id, user_id, enable=True) -> bool` | 设置/取消群管理员 |
| `set_group_card` | `set_group_card(group_id, user_id, card) -> None` | 设置群名片 |
| `set_group_name` | `set_group_name(group_id, group_name) -> bool` | 设置群名 |
| `set_group_leave` | `leave_group(group_id) -> None` | 退出群聊 |
| `set_group_special_title` | `set_group_special_title(group_id, user_id, special_title, duration=-1) -> bool` | 设置群专属头衔 |

### 3.3 信息查询

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `get_login_info` | `get_login_info() -> dict` | 获取当前登录账号信息（`user_id`, `nickname`） |
| `get_stranger_info` | `get_stranger_info(user_id) -> dict` | 获取用户信息 |
| `get_group_list` | `get_group_list() -> list` | 获取群列表 |
| `get_group_info` | `get_group_info(group_id) -> dict` | 获取群信息 |
| `get_group_member_list` | `get_group_member_list(group_id) -> list` | 获取群成员列表 |
| `get_friend_list` | `get_friend_list() -> list` | 获取好友列表 |
| `get_status` | `get_status() -> dict` | 获取 NapCat 在线状态 |
| `get_version_info` | `get_version_info() -> dict` | 获取 NapCat 版本信息 |

### 3.4 文件操作

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `upload_private_file` | `upload_file("private", target_id, file_path) -> bool` | 上传私聊文件 |
| `upload_group_file` | `upload_file("group", target_id, file_path) -> bool` | 上传群文件 |
| `get_image` | `get_image_path(file_id) -> str` | 通过 NapCat `get_image` 获取图片本地缓存路径 |
| `get_group_root_files` | `get_group_root_files(group_id) -> dict` | 获取群根目录文件列表 |
| `get_group_files_by_folder` | `get_group_files_by_folder(group_id, folder_id) -> dict` | 获取群子目录文件列表 |
| `get_group_file_url` | `get_group_file_url(group_id, file_id, busid) -> str` | 获取群文件下载 URL |

### 3.5 历史消息与联系人

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `get_group_msg_history` | `get_group_msg_history(group_id, count=20) -> list` | 获取群历史消息 |
| `get_friend_msg_history` | `get_friend_msg_history(user_id, count=20) -> list` | 获取私聊历史消息 |
| `get_msg` | `get_msg(message_id) -> dict` | 获取单条消息详情 |
| `get_recent_contact` | `get_recent_contact(count=20) -> list` | 获取最近联系人列表 |

### 3.6 请求审批

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `set_friend_add_request` | `approve_friend_request(flag, approve=True) -> bool` | 同意/拒绝好友申请 |
| `set_group_add_request` | `approve_group_invite(flag, approve=True) -> bool` | 同意/拒绝群邀请 |

### 3.7 合并转发消息

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `send_group_forward_msg` | `send_group_forward_msg(group_id, messages) -> str` | 发送群合并转发消息 |
| `send_private_forward_msg` | `send_private_forward_msg(user_id, messages) -> str` | 发送私聊合并转发消息 |
| `get_forward_msg` | `get_forward_msg(forward_id) -> list` | 获取合并转发消息内容 |

### 3.8 其他功能

| OneBot Action | Bridge 方法 | 说明 |
|---------------|-------------|------|
| `group_poke` | `send_group_poke(group_id, user_id) -> bool` | 群内戳一戳 |
| `friend_poke` | `send_private_poke(user_id) -> bool` | 私聊戳一戳 |
| `_send_group_notice` | `send_group_notice(group_id, content, image="") -> bool` | 发送群公告（NapCat 内部 API） |
| `get_group_honor_info` | `get_group_honor_info(group_id, honor_type="all") -> dict` | 获取群荣誉信息 |
| `mark_msg_as_read` | `mark_msg_as_read(message_id) -> bool` | 标记消息为已读 |
| `get_group_at_all_remain` | `get_group_at_all_remain(group_id) -> dict` | 获取群 @全体 剩余次数 |

---

## 4. 消息协议

消息协议定义位于 `protocols/messages.py`，定义了模块间通信的标准数据格式。

### 4.1 MessageType 枚举

消息类型枚举，用于标识消息的内容类型。

| 枚举值 | 字符串值 | 说明 |
|--------|----------|------|
| `TEXT` | `"text"` | 纯文本消息 |
| `IMAGE` | `"image"` | 图片消息 |
| `FILE` | `"file"` | 文件消息 |
| `MIXED` | `"mixed"` | 混合消息（同时包含文本、图片、文件中的多种） |
| `AT` | `"at"` | @ 消息（仅有 @ 段，无文本） |
| `REPLY` | `"reply"` | 引用回复消息（仅有引用段，无文本） |

**类型检测逻辑**（`NapCatBridge._detect_message_type`）:
- 消息段中包含多种类型（文本/图片/文件）-> `MIXED`
- 仅包含图片段 -> `IMAGE`
- 仅包含文件段 -> `FILE`
- 仅有 @ 段无文本 -> `AT`
- 仅有引用段无文本 -> `REPLY`
- 其他情况 -> `TEXT`

### 4.2 MessageSource 枚举

消息来源枚举，标识消息来自群聊还是私聊。

| 枚举值 | 字符串值 | 说明 |
|--------|----------|------|
| `GROUP` | `"group"` | 群聊消息 |
| `PRIVATE` | `"private"` | 私聊消息 |

### 4.3 RawMessage 数据类

**原始消息** -- 从 NapCatQQ WebSocket 接收到的原始消息，尚未经过任何路由或命令解析。

```python
@dataclass
class RawMessage:
    msg_id: str                           # 消息唯一 ID
    source: MessageSource                 # 消息来源 (群聊/私聊)
    target_id: str                        # 目标 ID (群号或 QQ 号)
    sender_id: str                        # 发送者 QQ 号
    sender_name: str                      # 发送者昵称
    content: str                          # 文本内容
    message_type: MessageType             # 消息类型
    attachments: list[dict] = []          # 附件信息 (图片、文件等)
    timestamp: datetime = now             # 时间戳
    raw_data: dict = {}                   # 原始 OneBot 数据 (保留完整信息)
    image_files: list[str] = []           # NapCat 图片 file ID 列表
    file_infos: list[dict] = []           # [{url, name, size}] 列表
    group_name: str = ""                  # 群名称 (仅群聊消息有值)
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `msg_id` | `str` | 消息唯一 ID（来自 OneBot `message_id`） |
| `source` | `MessageSource` | 消息来源枚举 |
| `target_id` | `str` | 群聊时为群号，私聊时为对方 QQ 号 |
| `sender_id` | `str` | 发送者 QQ 号 |
| `sender_name` | `str` | 发送者昵称（优先 `nickname`，回退 `card`） |
| `content` | `str` | 解析后的文本内容（支持 17+ 种 OneBot CQ 类型） |
| `message_type` | `MessageType` | 智能检测后的消息类型 |
| `attachments` | `list[dict]` | 附件列表，如 `[{"type": "image", "url": "..."}]` |
| `timestamp` | `datetime` | 消息时间戳（来自 OneBot `time` 字段） |
| `raw_data` | `dict` | 完整的原始 OneBot JSON 数据 |
| `image_files` | `list[str]` | 所有图片段的 NapCat file ID 列表 |
| `file_infos` | `list[dict]` | 所有文件段的详细信息 `[{url, name, size}]` |
| `group_name` | `str` | 群名称（仅群聊消息有值） |

**实用方法**:

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `to_dict()` | `dict` | 转换为可序列化字典 |
| `from_dict(data)` | `RawMessage` | 从字典反序列化 |
| `get_reply_id()` | `str` | 获取被引用消息的 ID（无引用返回空字符串） |
| `format_for_ai()` | `str` | 格式化为 AI 可读字符串 |
| `extract_at_targets()` | `list[str]` | 提取所有被 @ 的 QQ 号 |
| `is_at_me(bot_qq)` | `bool` | 检查是否 @ 了指定机器人 |
| `has_at_others(bot_qq)` | `bool` | 检查是否 @ 了除机器人以外的其他人 |
| `group_id` (属性) | `str` | 获取群号（仅群聊消息有值） |

### 4.4 ParsedMessage 数据类

**解析后的消息** -- 经过 `MessageBus` 路由和命令识别后的消息，传递给 `CommandModule` 或 `CherryStudioModule`。

```python
@dataclass
class ParsedMessage:
    raw: RawMessage                       # 原始消息
    is_command: bool = False              # 是否为命令
    command_name: str | None = None       # 命令名称 (如 "help", "bot")
    command_args: str | None = None       # 命令参数
    metadata: dict = {}                   # 额外元数据
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `raw` | `RawMessage` | 关联的原始消息 |
| `is_command` | `bool` | 是否识别为命令（以 `.` 或 `。` 开头） |
| `command_name` | `str \| None` | 命令名称（小写，如 `"help"`、`"bot"`） |
| `command_args` | `str \| None` | 命令参数文本 |
| `metadata` | `dict` | 额外元数据 |
| `session_key` (属性) | `str` | 会话键，格式 `"{source}_{target_id}"`（如 `"group_123456"`） |

### 4.5 OutgoingMessage 数据类

**待发送消息** -- 模块处理后生成的待发送消息，由 `MessageBus` 收集并通过 `NapCatBridge` 发送。

```python
@dataclass
class OutgoingMessage:
    target_source: MessageSource          # 目标来源 (群聊/私聊)
    target_id: str                        # 目标 ID (群号或 QQ 号)
    content: str                          # 消息内容
    message_type: MessageType = TEXT      # 消息类型 (默认 TEXT)
    attachments: list[dict] = []          # 附件
    reply_to_msg_id: str | None = None    # 回复的消息 ID (可选)
    metadata: dict = {}                   # 元数据
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `target_source` | `MessageSource` | 目标来源（群聊或私聊） |
| `target_id` | `str` | 目标 ID |
| `content` | `str` | 消息文本内容 |
| `message_type` | `MessageType` | 消息类型，默认 `TEXT` |
| `attachments` | `list[dict]` | 附件列表（如图片 `[{"url": "...", "type": "image"}]`） |
| `reply_to_msg_id` | `str \| None` | 引用回复的消息 ID |
| `metadata` | `dict` | 元数据（如 `{"success": true, "error_code": null}`） |

### 4.6 ModuleResponse 数据类

**模块响应** -- 模块处理消息后返回的标准化响应。

```python
@dataclass
class ModuleResponse:
    success: bool                         # 是否成功
    content: str | None = None            # 响应内容 (成功时)
    error_code: str | None = None         # 错误码 (失败时，如 "BRG-3001")
    error_detail: str | None = None       # 错误详情 (仅用于日志)
    requires_confirmation: bool = False   # 是否需要用户确认
    metadata: dict = {}                   # 元数据
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | `bool` | 是否成功 |
| `content` | `str \| None` | 成功时的响应内容 |
| `error_code` | `str \| None` | 失败时的错误码（如 `"BRG-3001"`） |
| `error_detail` | `str \| None` | 错误详情（仅日志使用，不展示给用户） |
| `requires_confirmation` | `bool` | 是否需要用户确认（v3.0 暂未使用） |
| `metadata` | `dict` | 元数据，错误时包含 `custom_error_text` |

**工厂方法**:

| 方法 | 说明 |
|------|------|
| `ModuleResponse.success_response(content, **kwargs)` | 创建成功响应 |
| `ModuleResponse.error_response(error_code, error_detail, custom_text, **kwargs)` | 创建错误响应 |

**属性**:
- `user_message`: 成功时返回 `content`；失败时返回 `"{custom_text} [{error_code}]"`

---

## 5. 错误码体系

错误码定义位于 `protocols/error_codes.py`。

### 5.1 错误码格式

```
BRG-XXXX
```
- `BRG`: Bridge 前缀
- `XXXX`: 4 位数字错误码，按模块范围分配

### 5.2 完整错误码列表

#### NapCat 互联桥错误 (1000-1999)

| 错误码 | 枚举名 | 错误描述 | 用户提示 |
|--------|--------|----------|----------|
| `BRG-1001` | `NAPCAT_CONNECTION_FAILED` | NapCat WebSocket 连接失败 | 连接失败 |
| `BRG-1002` | `NAPCAT_AUTH_FAILED` | NapCat 认证失败（Access Token 无效） | 认证失败 |
| `BRG-1003` | `NAPCAT_SEND_FAILED` | 发送消息到 NapCat 失败 | 发送失败 |
| `BRG-1004` | `NAPCAT_DISCONNECTED` | NapCat 连接意外断开 | 连接断开 |
| `BRG-1005` | `NAPCAT_TIMEOUT` | NapCat API 调用超时 | 请求超时 |
| `BRG-1006` | `NAPCAT_INVALID_RESPONSE` | NapCat 返回无效响应 | 响应异常 |

#### 消息互联桥错误 (2000-2999)

| 错误码 | 枚举名 | 错误描述 | 用户提示 |
|--------|--------|----------|----------|
| `BRG-2001` | `MESSAGE_PARSE_FAILED` | 消息解析失败 | 消息解析失败 |
| `BRG-2002` | `MESSAGE_ROUTING_FAILED` | 消息路由失败 | 路由失败 |
| `BRG-2003` | `MODULE_DISABLED` | 目标模块已禁用 | 功能不可用 |
| `BRG-2004` | `FILTER_REJECTED` | 消息被过滤规则拒绝 | 消息被拦截 |
| `BRG-2005` | `RESPONSE_MERGE_FAILED` | 多模块响应合并失败 | 响应处理失败 |

#### 命令模块错误 (3000-3999)

| 错误码 | 枚举名 | 错误描述 | 用户提示 |
|--------|--------|----------|----------|
| `BRG-3001` | `COMMAND_NOT_FOUND` | 命令不存在 | 未知命令 |
| `BRG-3002` | `COMMAND_EXECUTION_FAILED` | 命令执行失败 | 命令执行失败 |
| `BRG-3003` | `COMMAND_PERMISSION_DENIED` | 命令权限不足 | 权限不足 |
| `BRG-3004` | `COMMAND_INVALID_ARGS` | 命令参数无效 | 参数错误 |
| `BRG-3005` | `COMMAND_TIMEOUT` | 命令执行超时 | 执行超时 |
| `BRG-3006` | `COMMAND_SESSION_EXPIRED` | 会话已过期 | 会话过期 |

#### CherryStudio 模块错误 (4000-4999)

| 错误码 | 枚举名 | 错误描述 | 用户提示 |
|--------|--------|----------|----------|
| `BRG-4001` | `CHERRY_STUDIO_CONNECTION_FAILED` | CherryStudio 连接失败 | AI 服务连接失败 |
| `BRG-4002` | `CHERRY_STUDIO_API_ERROR` | CherryStudio API 调用失败 | AI 服务异常 |
| `BRG-4003` | `AGENT_NOT_FOUND` | 指定的 Agent 不存在 | Agent 不存在 |
| `BRG-4004` | `LLM_PROVIDER_FAILED` | LLM Provider 调用失败（所有回退尝试均失败） | AI 处理失败 |
| `BRG-4005` | `SESSION_CREATE_FAILED` | 创建会话失败 | 会话创建失败 |
| `BRG-4006` | `VISION_PROCESSING_FAILED` | 图片识别处理失败 | 图片处理失败 |
| `BRG-4007` | `FILE_PROCESSING_FAILED` | 文件解析处理失败 | 文件处理失败 |
| `BRG-4008` | `MCP_RESPONSE_TIMEOUT` | MCP 响应超时，已切换到 HTTP API | 响应超时 |
| `BRG-4009` | `CHERRY_SESSION_EXPIRED` | CherryStudio 会话过期或停滞 | 会话已过期 |

#### Server 模块错误 (5000-5999)

| 错误码 | 枚举名 | 错误描述 | 用户提示 |
|--------|--------|----------|----------|
| `BRG-5001` | `SERVER_INIT_FAILED` | 服务器初始化失败 | 启动失败 |
| `BRG-5002` | `MCP_REGISTER_FAILED` | MCP 工具注册失败 | 工具注册失败 |
| `BRG-5003` | `CONFIG_LOAD_FAILED` | 配置文件加载失败 | 配置加载失败 |
| `BRG-5004` | `SINGLETON_CHECK_FAILED` | 检测到重复运行的实例 | 重复运行 |
| `BRG-5005` | `SHUTDOWN_TIMEOUT` | 优雅关闭超时 | 关闭超时 |

#### 通用错误 (9000-9999)

| 错误码 | 枚举名 | 错误描述 | 用户提示 |
|--------|--------|----------|----------|
| `BRG-9001` | `UNKNOWN_ERROR` | 未知错误 | 系统错误 |
| `BRG-9002` | `INTERNAL_ERROR` | 内部服务器错误 | 内部错误 |
| `BRG-9003` | `RATE_LIMITED` | 请求频率限制 | 操作过于频繁 |
| `BRG-9004` | `SERVICE_UNAVAILABLE` | 服务暂时不可用 | 服务不可用 |

### 5.3 BridgeError 异常类

所有模块抛出的异常均继承自 `BridgeError`，提供标准化的错误码和详细信息。

```python
class BridgeError(Exception):
    def __init__(
        self,
        error_code: ErrorCode | str,     # 错误码 (枚举或字符串)
        detail: str = "",                 # 详细错误信息 (用于日志)
        custom_text: str | None = None,   # 自定义用户提示文本
        original_exception: Exception | None = None,  # 原始异常
    )
```

| 属性/方法 | 类型 | 说明 |
|-----------|------|------|
| `error_code` | `str` | 错误码字符串（如 `"BRG-1001"`） |
| `detail` | `str` | 详细错误信息 |
| `custom_text` | `str \| None` | 自定义用户提示（覆盖默认） |
| `original_exception` | `Exception \| None` | 原始异常（异常链） |
| `user_message` (属性) | `str` | 用户可见消息，格式: `"{文本} [{错误码}]"` |
| `to_dict()` | `dict` | 转换为字典（用于日志） |

**使用示例**:
```python
from protocols.error_codes import ErrorCode, BridgeError

# 使用枚举
raise BridgeError(
    ErrorCode.NAPCAT_CONNECTION_FAILED,
    detail="WebSocket 连接超时 (30s)",
)

# 使用字符串错误码
raise BridgeError(
    "BRG-1001",
    detail="自定义详情",
    custom_text="自定义提示文本",
)

# 捕获异常
try:
    await napcat_bridge.send_message(msg)
except BridgeError as e:
    logger.error(f"发送失败: {e.user_message}")
    # 输出: "发送失败: 连接失败 [BRG-1001]"
```

---

## 6. 状态管理接口

状态管理由 `StateManager`（位于 `state/manager.py`）实现，提供全局共享状态的读写接口，支持自动持久化到 `Temp/shared_state.json`。

### 6.1 SharedState 数据类

系统级共享状态，所有模块共享的状态数据。

```python
@dataclass
class SharedState:
    observers: dict[str, set[str]]          # 旁观者列表 {群号: {用户ID集合}}
    ob_groups: set[str]                     # 开启旁观者模式的群
    bot_blacklist: set[str]                 # .bot off 的群 (机器人黑名单)
    order_whitelist: set[str]               # 免 @ 的群 (指令白名单)
    saved_models: dict[str, str]            # 模型偏好 {会话键: 模型名称}
    active_agents: dict[str, str]           # 活跃 Agent {会话键: Agent名称}
    modules_enabled: dict[str, bool]        # 模块启用状态
    log_blacklist: set[str]                 # 日志黑名单
    welcome_config: dict[str, dict]         # 欢迎配置 {群号: {enabled, message}}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `observers` | `dict[str, set[str]]` | `{}` | 旁观者映射，键为群号，值为该群的旁观者 QQ 号集合 |
| `ob_groups` | `set[str]` | `set()` | 开启旁观者模式的群号集合 |
| `bot_blacklist` | `set[str]` | `set()` | 机器人黑名单（`.bot off` 的群） |
| `order_whitelist` | `set[str]` | `set()` | 免 @ 指令白名单群号集合 |
| `saved_models` | `dict[str, str]` | `{}` | 会话级模型偏好，键为会话键（如 `"group_123456"`） |
| `active_agents` | `dict[str, str]` | `{}` | 会话级 Agent 映射 |
| `modules_enabled` | `dict[str, bool]` | `{"command": True, "cherrystudio": True}` | 模块启用状态 |
| `log_blacklist` | `set[str]` | `set()` | 日志黑名单群号集合 |
| `welcome_config` | `dict[str, dict]` | `{}` | 群欢迎配置，每项为 `{"enabled": bool, "message": str}` |

**序列化**: 通过 `to_dict()` 和 `from_dict()` 进行 JSON 序列化/反序列化。`set` 类型序列化为 `list`。

### 6.2 StateManager 方法

#### 初始化与生命周期

| 方法 | 签名 | 说明 |
|------|------|------|
| `initialize()` | `async` | 初始化状态管理器。从文件加载状态，不存在则创建默认状态文件 |
| `reload()` | `async` | 重新加载状态文件（用于热重载） |
| `merge_legacy_files()` | `async` | 双向合并旧项目的独立持久化文件到 SharedState |

#### 批量更新

| 方法 | 签名 | 说明 |
|------|------|------|
| `update_state(updates)` | `async` | 批量更新状态字段并持久化。`updates` 为 `{字段名: 值}` 字典 |
| `update_module_status(module_name, enabled)` | `async` | 更新模块启用状态 |

#### 黑名单管理

| 方法 | 签名 | 说明 |
|------|------|------|
| `add_to_blacklist(group_id)` | `async` | 将群加入机器人黑名单 |
| `remove_from_blacklist(group_id)` | `async` | 从机器人黑名单移除群 |
| `is_in_blacklist(group_id) -> bool` | 同步 | 检查群是否在黑名单中 |

#### 白名单管理

| 方法 | 签名 | 说明 |
|------|------|------|
| `add_to_whitelist(group_id)` | `async` | 将群加入免 @ 指令白名单 |
| `remove_from_whitelist(group_id)` | `async` | 从白名单移除群 |
| `is_in_whitelist(group_id) -> bool` | 同步 | 检查群是否在白名单中 |

#### Agent 管理

| 方法 | 签名 | 说明 |
|------|------|------|
| `set_active_agent(session_key, agent_name)` | `async` | 设置会话的活跃 Agent（持久化） |
| `get_active_agent(session_key) -> str \| None` | `async` | 获取会话的活跃 Agent |

#### 模型偏好管理

| 方法 | 签名 | 说明 |
|------|------|------|
| `set_saved_model(session_key, model_name)` | `async` | 设置会话的模型偏好（持久化） |
| `get_saved_model(session_key) -> str \| None` | `async` | 获取会话的模型偏好 |
| `remove_saved_model(session_key)` | `async` | 移除模型偏好（恢复默认） |

#### 欢迎配置管理

| 方法 | 签名 | 说明 |
|------|------|------|
| `set_welcome(group_id, enabled=None, message=None)` | `async` | 设置群聊欢迎配置（持久化） |
| `get_welcome(group_id) -> dict` | 同步 | 获取群聊欢迎配置，返回 `{"enabled": bool, "message": str}` |

#### 模块状态查询

| 方法 | 签名 | 说明 |
|------|------|------|
| `is_module_enabled(module_name) -> bool` | 同步 | 检查模块是否启用（`"command"` 或 `"cherrystudio"`） |

#### 变更通知

| 方法 | 签名 | 说明 |
|------|------|------|
| `register_change_callback(callback)` | 同步 | 注册状态变更回调，回调接收 `changed_fields: dict` 参数 |

#### 旧文件合并

`merge_legacy_files()` 方法在 `initialize()` 之后调用，负责将旧版项目的独立持久化文件双向合并到 `SharedState`:

- `Temp/order_whitelist.json` -> `order_whitelist` 字段
- `Temp/bot_blacklist.json` -> `bot_blacklist` 字段
- `Temp/log_blacklist.json` -> `log_blacklist` 字段

合并策略: 以 `SharedState` 为准，合并独立文件中的增量数据（集合取并集），然后回写独立文件保持双向一致。

---

## 7. 配置接口

配置文件为 `config.json`，位于项目根目录。支持新版和旧版两种格式，系统会自动适配。

### 7.1 完整配置 Schema

```json
{
    // ===== 基础设置 =====
    "debug_mode": 1,                          // 调试模式 (0=关闭, 1=开启)
    "log_level": "INFO",                      // 日志级别 (DEBUG/INFO/WARNING/ERROR)
    "show_console": true,                     // Windows 下是否显示独立控制台窗口
    "admin_qq": "2712509058",                 // 管理员 QQ 号

    // ===== 自动审批 =====
    "auto_accept_friend": false,              // 是否自动同意好友申请
    "auto_accept_group": false,               // 是否自动同意群邀请

    // ===== 全局上下文 (System Prompt) =====
    "global_context": "...",                  // 注入给 AI 的全局上下文指令

    // ===== NapCat 连接 =====
    "napcat": {
        "ws_host": "127.0.0.1",              // NapCat WebSocket 主机
        "ws_port": 3001,                      // NapCat WebSocket 端口
        "access_token": ""                    // NapCat Access Token
    },

    // ===== Bridge 设置 =====
    "bridge": {
        "message_buffer_size": 200,           // 消息缓冲区大小 (条)
        "sse_stall_max_retries": 4,           // SSE 停滞最大重试次数
        "ws_max_reconnect": 0,               // WebSocket 最大重连次数 (0=无限)
        "pre_tool_text_policy": "keep"        // 工具调用前文本策略 ("keep"/"discard")
    },

    // ===== CherryStudio (新版格式) =====
    "cherrystudio": {
        "mcp_server_path": null,              // MCP Server 可执行文件路径
        "http_api_base": "http://127.0.0.1:23333",  // CherryStudio HTTP API 基础 URL
        "api_key": "",                        // CherryStudio API Key
        "legacy_mode": false,                 // 是否为旧版兼容模式
        "mcp_server_name": "QQ Bridge"        // MCP Server 名称
    },

    // ===== LLM Provider 配置 =====
    "llm": [                                  // (旧键名，自动标准化为 llm_providers)
        {
            "name": "ProviderName",           // Provider 名称
            "api_url": "https://...",         // API URL (自动标准化为 base_url)
            "api_key": "sk-...",             // API Key
            "api_format": "openai",           // API 格式
            "models": ["model-a", "model-b"]  // 可用模型列表
        }
    ],
    "default_llm": {
        "provider": 0,                        // 默认 Provider 索引
        "model": "model-a"                    // 默认模型名称
    },

    // ===== Vision Provider 配置 (图片识别) =====
    "vision_providers": [
        {
            "name": "VisionProvider",
            "api_url": "https://...",
            "api_key": "sk-...",
            "api_format": "openai",
            "models": ["vision-model"]
        }
    ],
    "default_vision": {
        "provider": 0,
        "model": "vision-model"
    },

    // ===== Agent 配置 =====
    "agent_enabled": true,                    // 是否启用 Agent 功能
    "agent_timeout_seconds": 60,              // Agent 超时秒数
    "agent_whitelist": [],                    // Agent 白名单 (空=不限制)
    "agents": {},                             // 自定义 Agent 配置
    "default_agent": "默认Agent名",           // 默认 Agent 名称

    // ===== 自动回复配置 =====
    "auto_reply": {
        "enabled": true,                      // 是否启用自动回复
        "reply_to_groups": [],                // 自动回复的群列表 (空=所有)
        "reply_to_friends": [],               // 自动回复的好友列表 (空=所有)
        "reply_mode": "mention",              // 回复模式 ("mention"=被@时, "always"=总是)
        "cooldown_seconds": 5,                // 冷却时间 (秒)
        "max_context_messages": 20,           // 最大上下文消息数
        "message_split_threshold": 5.0,       // 消息分割阈值
        "reply_chain_depth": 4,              // 回复链深度
        "doc_threshold": 1000                 // 长文本转文档的字符阈值
    }
}
```

### 7.2 旧版配置格式兼容

系统支持旧版配置格式并自动适配:

| 旧版键名 | 新版对应 | 说明 |
|----------|----------|------|
| `cherry_api_key` | `cherrystudio.api_key` | CherryStudio API Key |
| `agent_api_url` | `cherrystudio.http_api_base` | HTTP API 基础 URL |
| `mcp_server_name` | `cherrystudio.mcp_server_name` | MCP Server 名称 |
| `llm` | `llm_providers` | LLM Provider 配置（键名标准化） |
| `api_url` (每个 Provider 内) | `base_url` | API URL（键名标准化） |

当检测到 `cherry_api_key`、`agent_api_url` 等旧键名时，系统自动构建 `cherrystudio` 配置节并设置 `legacy_mode: true`。

### 7.3 配置查找顺序

配置文件按以下顺序查找:

1. `config.json`（项目根目录） -- 优先
2. `Configuration/config.json`（子目录） -- 回退

如果通过 `Server.__init__` 指定了 `config_path` 参数，则直接使用指定路径。

### 7.4 BotSettingConfig.json

位于 `Configuration/BotSettingConfig.json`，用于自定义机器人的消息模板。如果文件不存在，系统自动重建默认配置。

```json
{
    "内置模块": {
        "custom_greeting": ""            // 机器人入群自定义欢迎语 (空=使用默认)
    },
    "BuiltInOrder": {
        "bot_on_message": "",            // .bot on 的自定义回复
        "bot_off_message": "",           // .bot off 的自定义回复
        "dismiss_message": ""            // .dismiss 退群时的告别消息
    },
    "dice_core": {
        "r_message": "",                 // .r 命令消息模板
        "ra_message": "",                // .ra 命令消息模板
        "st_message": "",                // .st 命令消息模板
        "del_card_message": "",          // .del 命令消息模板
        "nn_message": ""                 // .nn 命令消息模板
    },
    "arktrpg": {
        "rk_message": "",
        "rkb_message": "",
        "rkp_message": "",
        "sck_message": "",
        "ark_message": "",
        "sn_message": ""
    },
    "ob": {
        "ob_join_message": "",           // 加入旁观者的自定义提示
        "ob_list_message": ""            // 旁观者列表的自定义格式
    },
    "log": {
        "log_new_message": "",
        "log_list_message": ""
    }
}
```

> **注意**: `dice_core`、`arktrpg`、`ob`、`log` 模块的消息模板保留用于旧版命令兼容。v3.0 中这些命令（`.r`, `.ra`, `.st`, `.rk` 等）已从内置命令中移除，但模板字段仍存在于默认配置中以保持向后兼容。

---

## 附录: 核心模块交互流程

```
NapCatQQ (WebSocket)
    |
    v
NapCatBridge          -- 接收/解析 OneBot 消息
    |
    v (RawMessage)
MessageBus            -- 过滤 -> 解析 -> 路由
    |                    |
    |--- 命令消息 ------->|--- 普通消息 --------->
    |                    |                       |
    v                    v                       v
CommandModule     CherryStudioModule         旁观者转发
(9 个命令)        (SSE 流式 AI 响应)        (私聊转发)
    |                    |
    v                    v
OutgoingMessage --> send_message_queue
                         |
                         v
                  NapCatBridge.send_message()
                         |
                         v
                  NapCatQQ (WebSocket) --> QQ 用户
```

---

> 本文档基于 QQ-MCP Bridge v3.0 源代码自动生成，涵盖 MCP 工具接口、命令系统、OneBot API 封装、消息协议、错误码体系、状态管理及配置接口的完整参考。
