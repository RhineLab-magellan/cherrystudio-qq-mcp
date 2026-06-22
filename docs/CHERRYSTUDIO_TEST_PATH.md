# CherryStudio 连接测试实施路径

> 生成时间: 2026-06-06
> 状态: **可执行方案**
> 基于: 代码审查 + 文档分析

---

## 一、当前项目状态精确评估

### 1.1 已完成且可工作的部分

经过逐文件代码审查，以下是 NEW QQ-MCP Bridge v3.0 的真实完成度（区别于文档声称的完成度）：

| 模块 | 文件 | 声称完成度 | 实际完成度 | 差距说明 |
|------|------|-----------|-----------|---------|
| MCP 工具注册 | `server.py` | 55% | **85%** | 11 个工具已注册（qq_confirm_response 已按 Phase 2 设计删除），启动链路完整，FastMCP stdio 传输正常 |
| NapCat 通信 | `napcat_bridge.py` | 65% | **80%** | 17+ CQ 类型解析、9 个 API 方法已补全、事件分发、图片 base64 发送、文件上传均已实现 |
| 消息路由 | `message_bus.py` | 95% | **95%** | 非阻塞分发模型已实现，消息过滤/路由/命令检测完备 |
| 命令系统 | `command_module.py` | 90% | **85%** | 8 个命令可用，SessionHandler 非阻塞模型已适配 |
| 内置命令 | `commands/builtin.py` | 40% | **70%** | .help/.bot/.order/.model/.ob/.dismiss/.send/.master 已实现 |
| CherryStudio 模块 | `cherrystudio_module.py` | 30% | **70%** | SSE 流式调用、会话管理、Vision/File 前置处理、LLM/Vision Provider Chain 全部实现 |
| SSE 解析器 | `sse_parser.py` | 新建 | **95%** | 完整的 SSEParser 类，12+ 事件类型、3 种场景策略、停滞检测、流式去重 |
| 状态管理 | `state/manager.py` | 100% | **100%** | — |
| 协议定义 | `protocols/*.py` | 100% | **100%** | — |
| 会话存储 | `conversation_store.py` | 90% | **90%** | 已实现但未接入消息流 |
| 单元测试 | `tests/` | 100% (98个) | **100%** | 全部通过 |

### 1.2 关键未实现功能（阻断端到端测试的）

以下功能在代码中缺失或不完整，会直接影响通过 CherryStudio 进行端到端测试：

1. **自动回复过滤逻辑缺失**: `CherryStudioModule.start()` 将所有非命令消息无差别转发到 CherryStudio，缺少旧版中的 `_should_reply()` 判断（@mention 检测、群/好友白名单、冷却控制、bot 黑名单、自消息过滤）
2. **多 Agent 发现缺失**: 没有实现 `_fetch_agents_from_cherrystudio()` 来从 CherryStudio `/v1/agents` API 获取 Agent 列表，也没有 MCP 绑定验证（`_filter_mcp_agents`）
3. **会话持久化未接入**: `ConversationStore` 已实现但 `CherryStudioSessionHandler._process_message()` 中没有调用它来保存/加载消息历史
4. **`_init_self_qq` 重复定义**: `server.py` 中有两个初始化机器人 QQ 号的路径（`_init_self_qq` 方法和 `_wait_napcat_ready` 函数），可能导致竞态
5. **长文本自动转文档未实现**: `NapCatBridge._send_text()` 中的 `doc_threshold` 检查逻辑在代码中未找到实际实现
6. **回复链解析未实现**: `_process_message` 没有递归获取引用消息的逻辑

### 1.3 文档与代码的不一致

| 文档声称 | 实际代码状态 |
|---------|------------|
| WorkReader.md: "13 个 MCP 工具" | server.py 实际注册了 **11 个**工具（qq_confirm_response 已被删除） |
| WorkReader.md: "CherryStudioModule 的 AI 处理为空壳" | cherrystudio_module.py 已实现完整的 SSE 流式调用管线 |
| WorkReader.md: "LLMProviderChain 未被调用" | 代码中 LLMProviderChain 已初始化且 Chat API 回退逻辑存在 |
| IMPLEMENTATION_PLAN.md: Phase 1B.2 "添加 qq_confirm_response" | 该工具在 Phase 2 设计中被删除，实际代码已不包含 |
| IMPLEMENTATION_PLAN.md: "12+1 个工具" | 实际是 11 个工具 |

---

## 二、CherryStudio 连接测试分层方案

### 测试分为 4 层，每层独立可验证，从简到繁逐步推进。

---

### 第 1 层：MCP 连接验证（纯工具调用）

**目标**: 验证 CherryStudio 能通过 stdio 连接 Bridge 并调用 MCP 工具。
**前置条件**: 仅需 CherryStudio 运行，不需要 NapCat 或 Agent。
**难度**: 低

**实施步骤**:

**步骤 1.1 — CherryStudio MCP 配置**

在 CherryStudio 的 MCP 设置中，添加一个新的 MCP Server:

```json
{
  "mcpServers": {
    "QQ Bridge": {
      "command": "C:\\Users\\magellan\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
      "args": ["C:\\CherryStudio\\qq-mcp-bridge\\NEW QQ-MCP-Bridge\\server.py"],
      "env": {}
    }
  }
}
```

> 注意: 使用 python.exe 直接运行 server.py，而非 start.bat。CherryStudio MCP 需要直接控制 stdin/stdout。

**步骤 1.2 — 连接验证**

1. 在 CherryStudio 中刷新 MCP 列表
2. 确认 "QQ Bridge" 显示为已连接状态
3. 确认工具列表显示 11 个工具

**步骤 1.3 — 逐个工具调用测试**

在 CherryStudio 的对话中，逐个测试工具：

```
① qq_check_status()
   预期: 返回 {"connected": false, "host": "127.0.0.1", "port": 3001, ...}
   说明: 即使 NapCat 未连接，也应返回状态信息

② qq_get_group_list()
   预期: 返回 [] 或 "错误: NapCat 未连接"

③ qq_get_friend_list()
   预期: 返回 [] 或 "错误: NapCat 未连接"

④ qq_get_recent_messages(count=5)
   预期: 返回 [] 或 "错误: NapCat 未连接"

⑤ qq_get_recent_contacts(count=5)
   预期: 返回 [] 或 "错误: NapCat 未连接"

⑥ qq_send_message(message_type="private", target_id="12345", message="test")
   预期: "错误: NapCat 未连接" 或 "目标 12345 不在活跃会话中"

⑦ qq_send_image(message_type="private", target_id="12345", image_url="https://example.com/test.png")
   预期: "错误: NapCat 未连接"

⑧ qq_upload_file(message_type="private", target_id="12345", content="test content")
   预期: "错误: NapCat 未连接"

⑨ qq_get_group_msg_history(group_id="12345", count=5)
   预期: 返回 [] 或 "错误: NapCat 未连接"

⑩ qq_get_group_members(group_id="12345")
   预期: 返回 [] 或 "错误: NapCat 未连接"

⑪ qq_get_user_info(user_id="12345")
   预期: 返回 {} 或 "错误: NapCat 未连接"
```

**验收标准**: CherryStudio 能列出全部 11 个工具，每个工具调用均有响应（即使是错误响应也说明 MCP 链路通了）。

**可能遇到的问题及解决**:

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| CherryStudio 无法发现 MCP Server | Python 路径错误或权限问题 | 检查 python.exe 路径是否正确；以管理员运行 CherryStudio |
| 连接超时 | server.py 启动过程中阻塞 | 检查 stderr 日志，确认配置文件路径正确 |
| 工具列表为空 | FastMCP 注册失败 | 查看 PlayerLog/bridge.log 中的错误信息 |
| 调用工具无响应 | stdio 通信异常 | 检查是否有日志输出到 stdout（会破坏 MCP 协议） |

---

### 第 2 层：NapCat 连接验证（QQ 消息收发）

**目标**: 验证 Bridge 能连接 NapCat 并进行 QQ 消息收发。
**前置条件**: NapCatQQ 已运行并登录，WebSocket 端口 3001 可用。
**难度**: 中

**实施步骤**:

**步骤 2.1 — 启动 NapCatQQ**

确保 NapCatQQ 已启动并已登录 QQ 号。WebSocket 反向服务器配置:
- 地址: `127.0.0.1`
- 端口: `3001`
- 启用: `true`

**步骤 2.2 — 检查 Bridge 日志**

在 CherryStudio MCP 连接后（第 1 层通过后），观察 Bridge 日志:

```
预期日志序列:
1. "QQ-MCP Bridge v3.0 正在启动..."
2. "NapCatBridge 已初始化"
3. "NapCat WebSocket 已连接"
4. "NapCat 已登录: <昵称> (<QQ号>)"
```

如果看到 "NapCat 连接等待中 (将在后台继续重试)"，说明 WebSocket 端口未开放。

**步骤 2.3 — 通过 MCP 工具验证 QQ 操作**

在 CherryStudio 中:

```
① qq_check_status()
   预期: {"connected": true, "bot_qq": "<QQ号>", "cached_messages": 0}

② qq_get_group_list()
   预期: 返回实际的群列表

③ qq_get_friend_list()
   预期: 返回实际的好友列表

④ qq_get_recent_contacts(count=10)
   预期: 返回最近的活跃会话
```

**步骤 2.4 — 消息发送测试**

```
① 先给自己发一条消息触发 MessageBuffer
② 然后调用 qq_send_message:
   qq_send_message(message_type="private", target_id="<自己的QQ号>", message="Bridge 测试消息")
   预期: "消息已发送 (ID: xxxxx)"，QQ 客户端收到消息
```

**验收标准**: qq_check_status 返回 connected=true，能通过 MCP 工具查询 QQ 信息并发送消息。

---

### 第 3 层：CherryStudio Agent API 验证（AI 处理链路）

**目标**: 验证 Bridge 能通过 CherryStudio Agent API 调用 AI 模型并获得回复。
**前置条件**: CherryStudio 中已创建 Agent 并配置了 LLM 模型。
**难度**: 中

**实施步骤**:

**步骤 3.1 — 确认 CherryStudio Agent 可用**

在 CherryStudio 中:
1. 确认至少有一个 Agent 已创建（如 "麦哲伦QQ"）
2. 确认 Agent 绑定了 LLM 模型
3. 确认 Agent 的工作区配置正确

**步骤 3.2 — 手动验证 Agent API**

在 Bridge 的 config.json 中，`cherry_api_key` 和 `agent_api_url` 配置:

```json
{
  "cherry_api_key": "cs-sk-xxxx",
  "mcp_server_name": "QQ Bridge"
}
```

Bridge 会自动适配为 legacy_mode，使用 Agent API 端点:
- `POST /v1/agents/{agent_id}/sessions` — 创建会话
- `POST /v1/agents/{agent_id}/sessions/{sid}/messages` — 发送消息 (SSE)

**步骤 3.3 — 检查 API 连通性**

Bridge 启动时会在日志中输出:
```
"HTTP API 连接成功: http://127.0.0.1:23333"
```
或
```
"HTTP API 健康检查超时: http://127.0.0.1:23333"
```

确认 CherryStudio 的 Agent API 服务在 23333 端口运行。

**步骤 3.4 — 验证 Agent 名称匹配**

Bridge config.json 中的 `default_agent` 值（当前为 "麦哲伦QQ"）必须与 CherryStudio 中的 Agent 名称匹配。如果不匹配，会话创建会失败。

**验收标准**: Bridge 日志显示 HTTP API 连接成功。

---

### 第 4 层：端到端自动回复验证

**目标**: 从 QQ 发送消息 → Bridge 接收 → CherryStudio Agent 处理 → MCP 工具回复 → QQ 收到回复。
**前置条件**: 第 1-3 层全部通过。
**难度**: 高

**实施步骤**:

**步骤 4.1 — 确认自动回复配置**

config.json 中:
```json
{
  "agent_enabled": true,
  "auto_reply": {
    "enabled": true,
    "reply_mode": "always"     // 测试期间设为 always，避免 @mention 问题
  }
}
```

**步骤 4.2 — 发送测试消息**

通过 QQ 客户端向机器人发送:
```
你好，请介绍一下你自己
```

**步骤 4.3 — 观察日志序列**

预期日志:
```
1. "收到消息: private_<QQ号>: 你好，请介绍一下你自己"
2. "会话创建成功: private_<QQ号> (ID: xxx)"
3. "SSE 解析完成: reply_blocks=N, tool_calls=1, had_output_tool=True"
4. "消息已发送: private_<QQ号>"
```

**步骤 4.4 — 验证 QQ 收到回复**

QQ 客户端应该收到机器人的回复消息。

**验收标准**: 完整的 QQ → AI → QQ 消息环路通畅。

**当前代码的阻断问题**:

第 4 层测试在当前代码下可能遇到以下阻断问题，需要先修复:

| 阻断问题 | 影响 | 修复建议 |
|---------|------|---------|
| 无自动回复过滤 | 所有消息都转发给 Agent，包括系统消息和自己的消息 | 添加 `_should_reply()` 过滤逻辑 |
| 无 @mention 检测 | reply_mode=mention 时无法判断是否被 @ | 实现 `_is_at_me()` 检查 |
| 无冷却控制 | 同一用户短时间内发多条消息会创建多个 Agent 请求 | 实现 cooldown_seconds 逻辑 |
| 会话重建无自动重试 | session_not_found 时只返回错误消息，不会自动重试 | 实现 SSE 解析后的自动重试一次逻辑 |
| mark_responding 机制需要 NapCat 已连接 | 如果 NapCat 未连接，Agent 调用 qq_send_message 会被活跃验证拦截 | 在 mark_responding 前检查 napcat_bridge 是否已连接 |

---

## 三、最小可测试路径：优先修复清单

根据上述分析，要实现 CherryStudio 端到端测试，需要按优先级修复以下问题:

### 优先级 P0 — 阻断 MCP 连接

无。第 1 层 MCP 连接在当前代码下应该已经可用。

### 优先级 P1 — 阻断 Agent 自动回复

1. **添加 `_should_reply()` 过滤方法**
   - 文件: `modules/cherrystudio_module.py`
   - 在 `CherryStudioModule.start()` 中，将消息放入 SessionHandler 前先过滤
   - 过滤条件: `auto_reply.enabled`, `reply_mode`, `reply_to_groups`, `reply_to_friends`, 自消息过滤, `.bot off` 黑名单

2. **添加 @mention 检测**
   - 文件: `modules/cherrystudio_module.py` 或 `modules/message_bus.py`
   - 实现 `_is_at_me(msg)` 方法，检查消息中的 at 段是否包含机器人 QQ 号
   - 在 reply_mode=mention 且为群聊时，只有 @bot 才触发自动回复

3. **修复 Agent ID 获取逻辑**
   - 当前 `self.agent_id = mcp_config.get("mcp_server_name", "QQ Bridge")` 不正确
   - 应该从 `default_agent` 配置读取，并通过 Agent API 查找对应的 agent_id
   - 临时方案: 使用 `default_agent` 配置值直接作为 agent_id

### 优先级 P2 — 影响体验但不阻断测试

4. **实现冷却控制**
   - 在 `CherryStudioSessionHandler` 中记录上次处理时间
   - 如果距上次处理不足 `cooldown_seconds`，丢弃消息

5. **实现会话自动重试**
   - 当 SSE 返回 session_not_found 时，自动清除 SID 并重试一次
   - 在 `_process_message` 外层添加 try/except + 重试逻辑

6. **修复 `_init_self_qq` 重复问题**
   - 合并 `_init_self_qq` 方法和 `_wait_napcat_ready` 函数为单一逻辑

### 优先级 P3 — 完善性修复

7. **接入 ConversationStore 到消息流**
8. **实现长文本自动转文档**
9. **实现回复链解析**
10. **添加多 Agent 发现逻辑**

---

## 四、CherryStudio 配置指南

### 4.1 MCP Server 配置

在 CherryStudio 中添加 MCP Server:

- **名称**: QQ Bridge
- **类型**: stdio
- **命令**: `C:\Users\magellan\AppData\Local\Programs\Python\Python311\python.exe`
- **参数**: `C:\CherryStudio\qq-mcp-bridge\NEW QQ-MCP-Bridge\server.py`
- **工作目录**: `C:\CherryStudio\qq-mcp-bridge\NEW QQ-MCP-Bridge`

### 4.2 Agent 配置

在 CherryStudio 中创建 Agent:

- **名称**: 麦哲伦QQ（需与 config.json 的 `default_agent` 一致）
- **模型**: 选择一个可用的 LLM 模型
- **MCP 绑定**: 绑定 "QQ Bridge" MCP Server
- **系统提示**: 可选，global_context 会自动注入

### 4.3 Bridge 配置文件检查

确认 `config.json` 中以下字段正确:

```json
{
  "napcat": {
    "ws_host": "127.0.0.1",
    "ws_port": 3001,
    "access_token": "<NapCat的access_token>"
  },
  "cherry_api_key": "<CherryStudio的API Key>",
  "mcp_server_name": "QQ Bridge",
  "default_agent": "麦哲伦QQ",
  "agent_enabled": true,
  "auto_reply": {
    "enabled": true,
    "reply_mode": "always"
  }
}
```

---

## 五、测试检查表

### 第 1 层 — MCP 连接

- [ ] CherryStudio 能发现 "QQ Bridge" MCP Server
- [ ] 工具列表显示 11 个工具
- [ ] `qq_check_status()` 返回状态信息
- [ ] `qq_get_group_list()` 有响应
- [ ] `qq_send_message()` 有响应（即使是错误也说明链路通）

### 第 2 层 — NapCat 连接

- [ ] Bridge 日志显示 "NapCat WebSocket 已连接"
- [ ] Bridge 日志显示机器人 QQ 号
- [ ] `qq_check_status()` 返回 `connected: true`
- [ ] `qq_get_group_list()` 返回实际群列表
- [ ] `qq_send_message()` 能成功发送消息到 QQ

### 第 3 层 — Agent API

- [ ] Bridge 日志显示 "HTTP API 连接成功"
- [ ] Agent 名称与 config.json 的 `default_agent` 匹配
- [ ] CherryStudio Agent 已绑定 "QQ Bridge" MCP Server

### 第 4 层 — 端到端

- [ ] QQ 发送消息后，Bridge 日志显示消息接收
- [ ] 日志显示 SSE 流式调用成功
- [ ] Agent 通过 MCP 工具发送回复
- [ ] QQ 客户端收到 AI 回复

---

*文档版本: v1.0*
*生成时间: 2026-06-06*
*基于完整代码审查 + 文档交叉验证*
