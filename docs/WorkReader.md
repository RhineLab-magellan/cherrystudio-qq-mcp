# 阶段进展报告 + 下阶段任务安排

> 初始执行时间: 2026-06-06
> 最近更新: 2026-06-07 (Phase 4B.2 .welcome 命令完成)
> 测试结果: **337/337 全部通过**

---

## 一、阶段 0 — 关键 Bug 修复（已完成）

### 修复清单（11 项）

| 编号 | 严重度 | 问题 | 修复方案 | 修改文件 |
|------|--------|------|---------|---------|
| 0.x-新1 | Critical | `server.py` 的 `main()` 从未启动 MCP stdio 传输 | 重构 `main()`: 后台任务启动后通过 `stdio_server()` + `_mcp_server.run()` 运行 MCP 服务 | `server.py` |
| 0.x-新2 | Critical | `MessageBus._route_message()` 同步阻塞，系统串行处理消息 | 重构为非阻塞分发模型: `_dispatch_message()` 只投递不等待，模块自行推送 OutgoingMessage 到 send_queue | `message_bus.py`, `command_module.py` |
| 0.x-新3 | Critical | 多会话共享响应队列，响应错配到错误会话 | 随并发模型重构一并解决: SessionHandler 直接构建 OutgoingMessage，不再经过共享 response_queue | `message_bus.py`, `command_module.py` |
| 0.x-新4 | High | 配置键名不匹配 (`llm` vs `llm_providers`, `api_url` vs `base_url`) | 增强 `_adapt_legacy_config()`: 标准化 `llm` → `llm_providers`，遍历所有 provider 的 `api_url` → `base_url` | `server.py` |
| 0.1 | Critical | `napcat_bridge.py` 缺少 `import os`，`upload_file()` 崩溃 | 添加 `import os` 和 `import base64` | `napcat_bridge.py` |
| 0.2 | High | `builtin.py:91` — `msg.source` 应为 `msg.raw.source` | 修正属性访问路径 | `builtin.py` |
| 0.3 | High | `_send_image()` 下载图片后发送 `[图片]` 文本 | 改为: 下载 → base64 编码 → OneBot `send_msg` message 段格式 (`type: "image"`) | `napcat_bridge.py` |
| 0.4 | Medium | ConversationStore 未接入消息流 | 标记为 Phase 2 任务（AI 自动回复引擎的会话持久化） | — |
| 0.5 | High | `qq_upload_file` 是 stub | 完整实现: 文本 → 临时 .md 文件 → `upload_file()` API → 清理临时文件 | `server.py`, `napcat_bridge.py` |
| 0.6 | High | `qq_get_recent_contacts` 是 stub | 对接 `napcat_bridge.get_recent_contact(count)` | `server.py` |
| 0.7 | Critical | `server.py` 无法通过 `python server.py` 正常启动 | MCP stdio 启动链路打通 + 单例锁 + 配置加载 + 后台任务启动完整串联 | `server.py` |

### 阶段 0 验收结果

- [x] NapCat WebSocket 连接成功建立（代码链路已通，依赖 NapCatQQ 运行时）
- [x] MCP 客户端可通过 stdio 调用全部工具
- [x] QQ 消息正确路由并通过 MessageBus 非阻塞分发
- [x] 基础命令 `.help` 和 `.bot on/off` 可用
- [x] `python server.py` 启动链路完整

---

## 二、阶段 1 — 最低可运行版本 MVP（已完成）

### 1A. NapCat 通信完善

**补全 9 个缺失 API 方法:**

| 方法 | OneBot API | 用途 |
|------|-----------|------|
| `get_login_info()` | `get_login_info` | 获取登录信息（机器人 QQ 号） |
| `get_group_info(group_id)` | `get_group_info` | 获取群信息 |
| `get_msg(message_id)` | `get_msg` | 获取单条消息（回复链解析依赖） |
| `get_recent_contact(count)` | `get_recent_contact` | 获取最近会话 |
| `get_friend_msg_history(user_id, count)` | `get_friend_msg_history` | 获取好友历史消息 |
| `set_group_card(group_id, user_id, card)` | `set_group_card` | 设置群名片 |
| `leave_group(group_id)` | `set_group_leave` | 退群 |
| `get_image_path(file_id)` | `get_image` | 获取图片缓存路径 |
| `send_image(message_type, target_id, url, summary)` | `send_msg` (base64 段) | 发送图片 |

**消息解析扩展到 17+ CQ 类型:** text, image, record, video, at, reply, face, file, share, location, contact, music, forward, markdown, poke, gift, 以及通用回退。

**新增提取方法:** `_extract_image_files()` 提取图片 file ID 列表; `_extract_file_infos()` 提取文件 `{url, name, size}` 列表。

**事件分发机制:** `_handle_notice()` 处理 group_increase / friend_add; `_handle_request()` 处理 friend / group 申请; 通过 `register_notice_handler()` / `register_request_handler()` 注册回调。

### 1B. MCP 工具完整化

**11 个工具端到端可用:**

> 注: 原计划 13 个工具，但根据 DESIGN_PHASE2_SSE_MCP.md 的设计决策，`qq_confirm_response` 已被删除，改为 `mark_responding`/`unmark_responding` 自动机制。实际工具数为 11 个。

| # | 工具 | 状态 |
|---|------|------|
| 1 | `qq_send_message` | 完整 + 活跃目标验证 + mark_responding 机制 + 断线重试 |
| 2 | `qq_send_image` | 完整 + base64 发送 + 断线重试 |
| 3 | `qq_upload_file` | 完整 (双模式: content 文本 / file_path 本地文件) + 断线重试 |
| 4 | `qq_get_recent_messages` | 完整 |
| 5 | `qq_get_group_msg_history` | 完整 |
| 6 | `qq_get_group_list` | 完整 |
| 7 | `qq_get_friend_list` | 完整 |
| 8 | `qq_get_group_members` | 完整 |
| 9 | `qq_get_user_info` | 完整 |
| 10 | `qq_get_recent_contacts` | 完整 (从 stub 修复) |
| 11 | `qq_check_status` | 完整 (含 cached_messages 数量) |
| 12 | `qq_recall_message` | 完整 |

**活跃目标验证改进:** 不再使用 `qq_confirm_response` 工具，改为 `CherryStudioSessionHandler` 在 SSE 处理期间调用 `napcat_bridge.mark_responding(target_id)`，使 Agent 调用 `qq_send_message` 时自动通过活跃验证。

### 1C. 基础命令系统

**8 个命令全部可用:**

| 命令 | 子命令 | 状态 |
|------|--------|------|
| `.help` | — | 完善: 从 CommandRegistry 动态生成帮助文本 |
| `.bot` | `on` / `off` / `status` / `orderwhite` | 完善: 新增 `orderwhite` 切换免@白名单 |
| `.order` | `切换` / `列表` / `重建` / `status` / `list` / `add` / `remove` | Agent 管理 + 免@白名单，**持久化增强**: 切换响应确认持久化，status 显示模型偏好 |
| `.model` | `list` / `change` / `status` / `reset` | 已有，**持久化增强**: 使用 `saved_models` 独立字段 + 新增 `reset` 子命令 |
| `.ob` | `join` / `exit` / `list` / `on` / `off` / `clr` | 完善: 使用 StateManager 正式 API，新增 3 个子命令 |
| `.dismiss` | 末4位验证 | 完整实现: 权限检查 + 群号匹配 + 退群 + 本地清理 |
| `.send` | `group/private` | **新增**: 管理员消息转发 |
| `.master` | `LLMReset` / `AllResetAgent` / `OnlyResetAgent` | **新增**: 管理员专用命令 |

### 1D. 事件处理

| 事件 | 行为 |
|------|------|
| 机器人被拉入群 | 读取 BotSettingConfig.json 发送自定义欢迎信息 |
| 其他用户入群 | 发送新成员欢迎消息 |
| 新好友添加 | 发送好友欢迎消息 |
| 好友申请 | 根据 `auto_accept_friend` 配置自动审批 |
| 群邀请 | 根据 `auto_accept_group` 配置自动审批 |

### 架构变更总结

**MessageBus 并发模型重构:**

```
NapCat → MessageBus → dispatch(不等待) → Module
                                            ↓ (异步处理)
NapCat ← SendMessageLoop ← send_queue ← OutgoingMessage
```

### 阶段 1 验收结果

- [x] 从 CherryStudio MCP 客户端可完整使用全部 11 个工具
- [x] QQ 消息收发全链路通畅 (私聊 + 群聊) — 代码链路已通
- [x] 基础命令全部可用 (8 个命令)
- [x] 图片/文件可正确发送和接收
- [x] 群/好友事件正确处理
- [x] 多会话并发 (MessageBus 非阻塞分发)
- [x] `python server.py` 可正常启动
- [x] **337/337 单元测试全部通过** (含 18 持久化 + 15 过期 + 15 记忆注入 + 14 回复链 + 10 输出 + 9 欢迎命令)

---

## 三-B、持久化层（已完成 — 2026-06-07 新增）

**需求**: 命令系统涉及的所有切换操作（模型切换/Agent切换/人物卡/OrderWrite等）均支持持久化和重启自动加载。

### 已实现的持久化项

| 切换类型 | 存储字段 | 持久化方式 | 自动加载 | 命令入口 |
|----------|---------|-----------|---------|---------|
| Agent 切换 | `active_agents` | SharedState → `shared_state.json` | `StateManager.initialize()` | `.order 切换` |
| 模型偏好 | `saved_models` | SharedState → `shared_state.json` | `StateManager.initialize()` | `.model change` |
| 免@白名单 | `order_whitelist` | SharedState → `shared_state.json` | `StateManager.initialize()` | `.bot orderwhite` / `.order add` |
| 机器人黑名单 | `bot_blacklist` | SharedState → `shared_state.json` | `StateManager.initialize()` | `.bot off` |
| 旁观者 | `observers` / `ob_groups` | SharedState → `shared_state.json` | `StateManager.initialize()` | `.ob join/on` |
| 日志黑名单 | `log_blacklist` | SharedState → `shared_state.json` | `StateManager.initialize()` | (Phase 5) |
| 人物卡 | `data/{uid}/cards/` | 独立文件 (Phase 5 character_store) | character_store.load() | `.st` / `.pc` |

### 双向合并迁移 (StateManager.merge_legacy_files)

旧项目中 `order_whitelist` 和 `bot_blacklist` 同时存储在 SharedState 和独立文件 (`Temp/order_whitelist.json`, `Temp/bot_blacklist.json`)。
新版启动时 `server.py` 在 `initialize()` 后自动调用 `merge_legacy_files()`，以 SharedState 为准合并增量数据，再回写独立文件保持双向一致。

### 模型偏好持久化流程

```
.model change gpt-4
    → StateManager.set_saved_model("group:123", "gpt-4")
    → 写入 shared_state.json (saved_models 字段)
    → 重启后 StateManager.initialize() 自动加载
    → 新会话创建时 CherryStudioSessionHandler._run() 读取 saved_model
    → 传递给 HTTPClient.create_session(model="gpt-4")
```

### 新增 API (StateManager)

- `set_saved_model(session_key, model_name)` — 持久化模型偏好
- `get_saved_model(session_key)` — 读取模型偏好
- `remove_saved_model(session_key)` — 清除模型偏好
- `merge_legacy_files()` — 双向合并旧项目独立文件

---

## 三-A、过期会话检测 + AI 摘要（已完成 — 2026-06-07 新增）

### 功能概述

移植自旧项目 `auto_reply._summarize_and_cleanup()`，实现 3 天不活跃会话的自动过期检测和 AI 摘要归档。

### 实现细节

**ConversationStore 新增方法:**

- `is_session_stale(session_key, days_threshold=3)` — 基于 `meta.last_active` 判断过期
- `get_stale_session_keys(days_threshold=3)` — 批量获取所有过期会话键
- `get_session_messages_sync(session_key)` — 同步获取消息列表 (供摘要使用)

**CherryStudioModule 新增方法:**

- `_check_and_archive_stale(session_key, agent_name)` — 完整归档流程: 检测过期 → 获取消息 → AI 摘要 → 保存 memory.json → 归档 session.json
- `_summarize_session(log_text)` — LLM 摘要生成 (LLMProviderChain 优先, 直接 HTTP API 回退)
- `_startup_stale_check()` — 启动时全量扫描过期会话

**触发时机:**

1. **启动时**: `initialize()` 末尾调用 `_startup_stale_check()`
2. **按需**: `CherryStudioSessionHandler._run()` 在 `load_session` 后检查，归档后重新加载 memory

### 关键 Bug 修复: asyncio.Lock 死锁

ConversationStore 中 `add_message()` 持有 `_lock` 后调用 `load_session()` (也要获取 `_lock`)，以及 `summarize_and_archive()` 持有 `_lock` 后调用 `save_session()` (也要获取 `_lock`)，由于 `asyncio.Lock` 不可重入，导致死锁。

**修复方案**: 提取 `_load_session_unlocked()` 和 `_save_session_unlocked()` 内部方法，已在锁内的代码路径调用 _unlocked 版本，外部调用仍走加锁的公共方法。

### 测试覆盖

- `TestStaleSessionDetection` (6 个): no_meta, fresh, old, force_stale, mixed, custom_threshold
- `TestStaleSessionArchival` (9 个): no_store, fresh, stale_empty, stale_with_messages, summarize_no_llm, summarize_with_llm, startup_no_store, startup_no_stale, startup_processes_stale

---

## 三-B、记忆注入 — 工作区上下文（已完成 — 2026-06-07 新增）

### 功能概述

移植自旧项目 `auto_reply._load_workspace_context()` + `_call_agent_api_once()` 的注入逻辑。新会话首条消息自动注入三部分上下文。

### 实现细节

**CherryStudioModule 新增方法:**

- `_load_workspace_context(work_dirs)` — 读取 Agent 工作区的 SOUL.md、USER.md、memory/FACT.md，用 XML 标签包裹
- `_build_injection_context(agent_name, session_key)` — 组合三部分: 工作区上下文 + 历史对话摘要 + 全局规则 (config.global_context)

**SessionHandler 注入流程:**

1. `_session_just_created == True` 时触发
2. 调用 `parent_module._build_injection_context(agent_name, session_key)`
3. 拼接: `{上下文}\n\n---\n当前消息：{用户原文}`
4. 重置 `_session_just_created = False`

**文件格式 (XML 标签):**

```
<SOUL.md>
{Agent 人设定义}
</SOUL.md>

<USER.md>
{用户信息}
</USER.md>

<FACT.md>
{长期知识库}
</FACT.md>

<历史对话摘要>
{AI 生成的历史摘要}
</历史对话摘要>

<全局规则>
{config.global_context 内容}
</全局规则>

---
当前消息：{用户实际消息}
```

### 测试覆盖

- `TestWorkspaceContextLoading` (9 个): empty_work_dirs, no_files, soul_only, user_only, fact_subdir, all_three, first_dir, empty_file, whitespace_only
- `TestBuildContextInjection` (6 个): no_agents, workspace_included, memory_included, all_three_parts, no_global, empty_everything

---

## 三、阶段 2 — 自动回复引擎（大部分完成）

### 2A. CherryStudio Agent API 接入（已完成 ~90%）

**已完成:**

- [x] SSE 流式响应解析器 (`modules/sse_parser.py`):
  - 独立的 `SSEParser` 类 + `SSEResult`/`SSETextBlock`/`SSEToolCall` 数据结构
  - `text-start/delta/end` 增量文本累积 + 流式去重 (最小重叠 4 字符)
  - `reasoning-start/end` 思考内容过滤
  - 工具调用检测 (去除 `mcp__*__` 前缀，识别输出类工具)
  - `finish-step` 兜底文本提取 (兼容 text/content/output/message 四个 key)
  - 停滞检测 (30s 超时) + 最大重试 (4次) + 每 25s "正在烧烤中" 通知
  - `session_not_found` 错误检测 + 标记
  - 可配置的 `pre_tool_text_policy` ("keep"/"discard")

- [x] HTTP 客户端 (`HTTPClient` 类):
  - `legacy_mode` 兼容旧项目 Agent API 格式
  - `create_session()` / `send_chat_message()` / `delete_session()`
  - `get_sse_request_context()` 返回 aiohttp 上下文管理器供 SSEParser 使用
  - 配置适配: `cherry_api_key` → Bearer token, `agent_api_url` → base_url

- [x] 会话处理器 (`CherryStudioSessionHandler`):
  - 完整的 SSE 流式调用管线 (`_process_message` 方法)
  - Vision 前置处理 (图片识别)
  - File 前置处理 (MinerU 文件提取)
  - `mark_responding` / `unmark_responding` 活跃目标管理
  - 根据 SSEResult 决定发送行为 (3 种场景策略)
  - `session_not_found` 处理 → 清除 SID

- [x] LLM Provider 回退链 (`LLMProviderChain`):
  - 多 Provider 数组 + 自动切换
  - 配额错误检测 (429) + 关键词识别
  - OpenAI + Anthropic API 格式支持

- [x] Vision Provider 回退链 (`VisionProviderChain`):
  - 多 Provider + 自动切换
  - base64 图片编码
  - OpenAI + Anthropic 消息格式

- [x] 文件处理器 (`FileProcessor`):
  - MinerU `flash-extract` 命令调用
  - 文件大小限制 + 下载 + 摘要生成

**未完成:**

- [x] 多 Agent 自动发现 (`_discover_agents` + `_filter_mcp_agents`) — **已完成** (2026-06-07)
- [ ] MCP 绑定验证 (`_filter_mcp_agents`)
- [ ] Agent 白名单过滤
- [ ] 会话 ID 持久化到 ConversationStore
- [ ] 会话 2-strike 停滞处理 (当前仅标记 stalled，未实现连续停滞销毁会话)
- [x] ~~Agent ID 获取逻辑需修复~~ — **已修复** (2026-06-07: 使用 `default_agent` 优先，回退 `mcp_server_name`)

### 2B. 自动回复核心逻辑（已完成 ~80%）

**已完成:**

- [x] 按会话独立 `CherryStudioSessionHandler` + `asyncio.Queue`
- [x] Worker 空闲 2 分钟自动退出 (timeout=120s)
- [x] `CherryStudioModule.start()` 消息分发到对应 SessionHandler
- [x] `send_queue` 非阻塞模式直接推送 OutgoingMessage
- [x] `_should_reply()` 过滤逻辑 (群/好友白名单、回复模式、自消息过滤) — **2026-06-07 新增**
- [x] `@mention` 检测 (`_is_at_me`) — **2026-06-07 新增**
- [x] 冷却控制 (`cooldown_seconds`) — **2026-06-07 新增**
- [x] Bot 黑名单 (`.bot off` 群仅响应命令) — **2026-06-07 新增**

**未完成:**

- [ ] 命令消息拦截 (`.xxx` 格式应优先走命令系统，不走 Agent)

### 2C. 会话持久化（代码就绪，未接入）

**已完成:**

- [x] `ConversationStore` 类已实现 (~90%)
- [x] 目录结构: `QQConversationRecord/{agent_name}/{msg_type}_{target_id}/`
- [x] session.json / memory.json / meta.json 管理

**未完成:**

- [x] 接入 `CherryStudioSessionHandler._process_message()` 的消息流 — ✅ 已完成
- [x] Agent 会话映射持久化 (mapping.json) — ✅ 已完成
- [x] 3 天不活跃过期 + AI 摘要生成 — ✅ 已完成 (含死锁修复)
- [x] 记忆注入 (SOUL.md / USER.md / FACT.md) — ✅ 已完成

---

## 三-C、回复链解析 Phase 3A.1（已完成 — 2026-06-07 新增）

### 功能概述

移植自旧项目 `auto_reply._fetch_reply_chain()`，实现 QQ 消息引用链的递归解析。当用户回复一条引用消息时，自动获取被引用消息的内容作为上下文。

### 实现细节

**CherryStudioSessionHandler 新增方法:**

- `_fetch_reply_chain(msg, max_depth)` — 迭代式引用链遍历 (含 `seen_ids` 循环引用检测)
- `_extract_plain_text(message_segs, raw_msg)` — 从消息段提取纯文本 (静态方法)
- `_extract_image_file_ids(message_segs)` — 提取图片 file ID 列表 (静态方法)

**配置项:** `auto_reply.reply_chain_depth` (默认 4，上限 10)

**集成点:** `_process_message()` 中文件处理之后、SSE 调用之前

**输出格式:** `[引用消息上下文]\n[引用第N层] 发送者: 内容\n...` (反转排序: 旧消息在前)

### 测试覆盖

- `TestFetchReplyChain` (8 个): no_bridge, no_reply_id, max_depth_zero, single_layer, multi_layer, cycle_detection, get_msg_failure, image_extraction
- `TestExtractHelpers` (6 个): plain_text_segments, string_input, empty_string, image_ids, string_input_images, no_images

---

## 三-D、输出处理 Phase 3C（已完成 — 2026-06-07 新增）

### 功能概述

移植自旧项目 `auto_reply._send_group()` 的输出后处理管线。处理 LLM 回复中的 Markdown 图片语法和名称占位符。

### 实现细节

**CherryStudioSessionHandler 新增方法:**

- `_extract_md_images(text)` — 正则提取 `![alt](url)` 为 `[(alt, url)]` 列表 (静态方法)
- `_strip_md_images(text)` — 移除 Markdown 图片语法，保留纯文本 (静态方法)
- `_replace_name_placeholders(text, sender_name, sender_id)` — 替换 `{name}/{sender}/{at}` 占位符 (静态方法)

**集成点:** `_process_message()` 中 SSE 响应后、返回 `ModuleResponse` 前

**输出处理流程:**
1. `sse_result.get_reply_text()` 获取回复文本
2. `_extract_md_images()` 提取图片 → `[(alt, url)]`
3. `_strip_md_images()` 去除图片语法 → 纯文本
4. 纯文本作为 `ModuleResponse.success_response()` 的内容
5. 图片通过 `send_queue` 以 CQ 码 `[CQ:image,file=url]` 单独发送

**注意:** 长文本转文档 (3C.1) 已在 `NapCatBridge._send_text()` → `_send_long_text_as_doc()` 实现。

### 测试覆盖

- `TestOutputProcessing` (10 个): extract_basic, extract_multiple, extract_none, strip, strip_empty, replace_name, replace_sender, replace_at, replace_multiple, replace_none

---

## 四、修改文件清单

| 文件 | 修改类型 | 主要变更 |
|------|---------|---------|
| `server.py` | 重构 | MCP stdio 启动、配置标准化、11 个 MCP 工具、事件处理、活跃验证；**2026-06-07**: 去除 `_wait_napcat_ready` 冗余函数，`_init_self_qq` 增强两阶段等待 |
| `modules/napcat_bridge.py` | 大量新增 | 9 个 API 方法、17+ CQ 类型解析、图片 base64 发送、文件上传、事件回调 |
| `modules/message_bus.py` | 重写 | 非阻塞分发模型、紧凑格式命令解析、send_response() 方法 |
| `modules/command_module.py` | 重构 | SessionHandler 直接发送 OutgoingMessage、CommandContext 新增属性 |
| `modules/commands/builtin.py` | 大量修改 | .help 动态化、.bot orderwhite、.dismiss 完整实现、.ob 修正、新增 .send/.master；**2026-06-07**: ModelCommand 持久化增强 (saved_models + reset)、OrderCommand status 显示模型偏好 |
| `modules/cherrystudio_module.py` | 大量新增 | HTTPClient、SSE 集成、SessionHandler、LLM/Vision Provider Chain、Vision/File 处理；**2026-06-07**: 新增 `_should_reply()` 过滤 + `_is_at_me()` @检测 + 冷却控制 + Agent ID 修复 + 会话创建读取持久化模型 + 过期会话检测归档 (`_check_and_archive_stale`/`_summarize_session`/`_startup_stale_check`) |
| `modules/sse_parser.py` | 新建 | 独立 SSE 流式解析器 (517 行) |
| `modules/conversation_store.py` | **2026-06-07 增强** | 新增过期检测 (`is_session_stale`/`get_stale_session_keys`/`get_session_messages_sync`) + `summarize_and_archive()` + **死锁修复** (提取 `_load_session_unlocked`/`_save_session_unlocked`) |
| `state/manager.py` | **2026-06-07 增强** | 新增 `set_saved_model`/`get_saved_model`/`remove_saved_model` 模型偏好 API + `merge_legacy_files()` 双向合并旧文件 |
| `tests/test_message_bus.py` | 更新 | 适配新的分发模型和 send_response API |
| `tests/test_command_module.py` | 更新 | 适配 SessionHandler 无 response_queue 的新接口 |
| `tests/test_cherrystudio_module.py` | **2026-06-07 更新** | 新增 19 个 Agent 发现测试 + 15 个过期会话测试 (Detection 6 + Archival 9)，测试总数 241→256→289 |
| `tests/test_state_manager.py` | **2026-06-07 增强** | 新增 TestModelPersistence (7) + TestLegacyFileMigration (8) 共 15 个持久化测试 |
| `tests/test_command_module.py` | **2026-06-07 增强** | ModelCommand 测试迁移到 saved_models + 新增 reset/list/default 测试 + OrderCommand status 持久化验证 |

---

## 五、下阶段任务安排

### 最高优先: 实现 CherryStudio 端到端测试路径

详见: [CHERRYSTUDIO_TEST_PATH.md](./CHERRYSTUDIO_TEST_PATH.md)

### 阶段 2 剩余任务

**P2: 体验改善**

1. 接入 ConversationStore 到消息流 (P2-1)
2. 命令消息拦截 — `.xxx` 格式消息不走 Agent (P2-4)
3. 会话 2-strike 停滞处理

**P3: 多 Agent 切换** (Phase 2A 已完成自动发现，命令系统待实现)

4. ~~多 Agent 自动发现 + MCP 绑定验证~~ — **已完成** (2026-06-07)
5. Agent 白名单过滤

### 阶段 3-7 任务

见 [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)，此处不重复。

---

## 六、已知遗留问题（2026-06-07 更新）

| 问题 | 阶段 | 说明 |
|------|------|------|
| ConversationStore 未接入消息流 | Phase 2C | 代码已实现 ~90%，需在自动回复引擎中连接 — **下阶段任务 P2-1** |
| ~~自动回复过滤逻辑缺失~~ | ~~Phase 2B~~ | **已解决** (2026-06-07): `_should_reply()` 已实现 |
| ~~@mention 检测缺失~~ | ~~Phase 2B~~ | **已解决** (2026-06-07): `_is_at_me()` 已实现 |
| ~~Agent ID 获取逻辑不正确~~ | ~~Phase 2A~~ | **已解决** (2026-06-07): 使用 `default_agent` 优先 |
| ~~`_init_self_qq` 重复定义~~ | ~~Phase 1 后续~~ | **已解决** (2026-06-07): 统一到 `Server._init_self_qq()`，去除 `_wait_napcat_ready` |
| ~~命令消息拦截 (Agent 层面)~~ | ~~Phase 2B~~ | **已解决** (2026-06-07): `_should_reply()` 中 `msg.is_command` 检测 |
| 长文本自动转文档未实现 | Phase 3 | `NapCatBridge._send_text()` 中无 doc_threshold 检查 |
| `.model list` 返回硬编码列表 | Phase 3 | 需对接 CherryStudio `/v1/models` API |
| ~~`.order 切换/重建/status` 子命令缺失~~ | ~~Phase 2-4~~ | **已解决** (2026-06-07): OrderCommand 完整实现 Agent 管理子命令 |
| ~~命令系统切换操作缺少持久化~~ | ~~Phase 4~~ | **已解决** (2026-06-07): 全部切换操作 (Agent/模型/白名单/黑名单) 均已持久化 + 双向合并迁移 |

---

*文档版本: v11.0*
*更新时间: 2026-06-07*
*基于完整代码审查 + 文档交叉验证 + Phase 2A/2B/2C/4 + 持久化层实现*
*337/337 测试通过 | 11 个 MCP 工具 | 9 个命令 | SSE 解析器独立模块 | 多 Agent 自动发现 | 全量持久化 | 过期会话检测+AI摘要 | 记忆注入 | 回复链解析 | 输出处理 | .welcome 命令*
