# QQ-MCP Bridge v3.0 重构实施计划

## 项目概述

本文档记录从 Old QQ-MCP Bridge (v2.0) 到 New QQ-MCP Bridge (v3.0) 的完整重构实施计划。
核心原则：**先建立最低可运行版本 (MVP)，再不断拓展直到完成所有功能**。

### 目标愿景

v3.0 旨在将原有单体架构重构为模块化、可测试、可扩展的系统，同时**完整保留 v2.0 的全部功能**。

### 当前状态总结 (2026-06-07 更新)

| 模块 | 状态 | 完成度 |
|------|------|--------|
| protocols/messages.py | 完成 | 100% |
| protocols/error_codes.py | 完成 | 100% |
| state/manager.py | 完成 (含持久化增强) | 100% |
| modules/message_bus.py | 完成 (含旁观者转发) | 98% |
| modules/command_module.py | 完成 | 95% |
| modules/napcat_bridge.py | 大部分完成 | 80% |
| modules/sse_parser.py | 完成 (新建) | 95% |
| modules/cherrystudio_module.py | 完成 (含配额增强+管理通知) | 99% |
| modules/conversation_store.py | 完成 (含死锁修复) | 98% |
| modules/commands/builtin.py | 完成 (含 BotSettingConfig) | 90% |
| server.py | 完成 (含 BotSettingConfig 重建) | 95% |
| 单元测试 (365个) | 全部通过 | 100% |

> **2026-06-07 更新 (第九轮 — 全量审计 + 功能补齐)**:
> **全量审计**: 对 79 个未完成任务逐项代码验证，确认 Phase 0-2C 全部 20+ 任务已实现 (DONE)，Phase 3A-4B 大部分已实现。
> **3D.2 增强配额检测**: LLMProviderChain + VisionProviderChain 新增 402 状态码检测 + `_QUOTA_KEYWORDS` 关键词识别 (15+ 关键词)，覆盖 HTTP 200 body 内嵌错误和非标准状态码场景。
> **3D.4 管理员通知**: LLMProviderChain 新增 `_on_switch_callback` + 1小时冷却，CherryStudioModule 注册回调通过 send_queue 向 admin_qq 发私信。
> **4A.1 BotSettingConfig 自定义消息**: `_load_bot_setting()` 工具函数，BotCommand on/off 和 DismissCommand 读取可定制消息模板。
> **4A.6 旁观者转发**: MessageBus `_forward_to_observers()` — 群消息自动转发给旁观者，格式 `👁️ [旁观] 群 {group_id} — {sender_name}: {content}`，排除发送者自身。
> **6A.4 BotSettingConfig 自动重建**: Server `_ensure_bot_setting_config()` — 文件缺失时生成包含全部模块模板键的默认配置。
> **6C.3 全局上下文警告**: global_context > 500 字符时输出 BRG-5001 警告日志。
> 新增 28 个测试: TestQuotaDetection (10) + TestProviderSwitchNotification (6) + TestGlobalContextWarning (3) + TestObserverForwarding (6) + TestBotSettingConfigRebuild (3)。
> 总测试数 337 → 365。

> **2026-06-07 更新 (第七轮 — 输出处理 Phase 3C)**:
> CherryStudioSessionHandler 新增 `_extract_md_images()` (正则提取 `![alt](url)`) + `_strip_md_images()` (移除图片语法) + `_replace_name_placeholders()` ({name}/{sender}/{at} 替换)。
> SSE 响应后处理: 提取 Markdown 图片 → 去除图片语法 → 图片通过 send_queue 单独发送 → 纯文本作为正文回复。
> 3C.1 长文本转文档已在 NapCatBridge._send_text → _send_long_text_as_doc 实现 (之前轮次)。
> 新增 10 个测试: TestOutputProcessing (10)。总测试数 318 → 328。

> **2026-06-07 更新 (第六轮 — 回复链解析 Phase 3A.1)**:
> CherryStudioSessionHandler 新增 `_fetch_reply_chain()` (迭代式引用链遍历，含循环引用检测) + `_extract_plain_text()` (消息段纯文本提取) + `_extract_image_file_ids()` (图片 file ID 提取)。
> 完全移植旧项目 `_fetch_reply_chain` 逻辑: 通过 NapCatBridge.get_msg() 逐层获取引用消息，提取发送者+文本+图片，反转排序后构建引用上下文。
> 集成到 `_process_message()`: 文件处理之后、SSE 调用之前，引用链文本以 `---` 分隔拼接在用户消息前。
> 配置项 `auto_reply.reply_chain_depth` (默认 4，上限 10) 已在 handler __init__ 读取。
> 新增 14 个测试: TestFetchReplyChain (8) + TestExtractHelpers (6)。总测试数 304 → 318。

> **2026-06-07 更新 (第五轮 — 记忆注入 Phase 2C.4)**:
> CherryStudioModule 新增 `_load_workspace_context()` (读取 SOUL.md/USER.md/FACT.md，XML 标签包裹) + `_build_injection_context()` (组合工作区上下文 + 历史记忆 + 全局规则)。
> SessionHandler `_process_message()` 新会话首条消息注入改为统一调用 `_build_injection_context()`，格式: `{上下文}\n\n---\n当前消息：{原文}`。
> 完全移植旧项目 `_load_workspace_context` + `_call_agent_api_once` 注入逻辑。
> 更新已有测试 `test_memory_injected_on_first_message` 适配新注入架构 (parent_module mock)。
> 新增 15 个测试: TestWorkspaceContextLoading (9) + TestBuildContextInjection (6)。总测试数 289 → 304。

> **2026-06-07 更新 (第四轮 — 过期会话检测 + AI 摘要)**:
> Phase 2C.3 完成: ConversationStore 新增 `is_session_stale()`/`get_stale_session_keys()`/`get_session_messages_sync()` 方法。
> CherryStudioModule 新增 `_check_and_archive_stale()` (过期→摘要→归档→清理)、`_summarize_session()` (LLMProviderChain 优先 + 直接 HTTP API 回退)、`_startup_stale_check()` (启动时全量扫描)。
> SessionHandler 在 `load_session` 后检查过期，触发归档后重新加载 memory。
> **关键 Bug 修复**: ConversationStore 中 `add_message` 持有 `_lock` 调用 `load_session` (也要获取 `_lock`) → 死锁。`summarize_and_archive` 持有 `_lock` 调用 `save_session` (也要获取 `_lock`) → 死锁。
> 修复方案: 提取 `_load_session_unlocked()` 和 `_save_session_unlocked()` 内部方法，已在锁内的代码路径调用 _unlocked 版本。
> 新增 15 个测试: TestStaleSessionDetection (6) + TestStaleSessionArchival (9)。总测试数 274 → 289。

> **2026-06-07 更新 (第三轮 — 持久化层)**:
> StateManager 新增 `set_saved_model()`/`get_saved_model()`/`remove_saved_model()` 模型偏好持久化 API + `merge_legacy_files()` 双向合并旧项目独立文件 (order_whitelist.json/bot_blacklist.json/log_blacklist.json)。
> ModelCommand 修复: 从 `active_agents` 迁移到独立的 `saved_models` 字段，新增 `reset` 子命令。
> CherryStudioModule 会话创建时读取持久化模型偏好 (saved_model → create_session model 参数)。
> `.order status` 增加模型偏好显示行。`.order 切换` 响应确认持久化。
> 旧项目双向合并集成到 server.py 启动流程 (initialize → merge_legacy_files)。
> 新增 18 个测试: TestModelPersistence (7) + TestLegacyFileMigration (8) + ModelCommand 增强 (3)。总测试数 256 → 274。

> **2026-06-07 更新 (第二轮)**: CherryStudioModule 从 80% 提升到 85%（新增多 Agent 自动发现 `_discover_agents()`、MCP 绑定验证 `_filter_mcp_agents()`、桥接 MCP ID 查找 `_find_bridge_mcp_id()`、HTTPClient Agent 发现 API）。ConversationStore 从 90% 提升到 95%（已接入消息流）。测试数从 206 增加到 241（+35 个 Agent 发现/HTTPClient API 测试）。

### 关键 Bug (阶段 0 已修复全部; 以下为代码审查发现的新问题)

1. ~~`napcat_bridge.py:597` — `NameError`: `os` 未导入~~ — **已修复**
2. ~~`builtin.py:91` — `AttributeError`: 应为 `msg.raw.source` 而非 `msg.source`~~ — **已修复**
3. ~~图片发送逻辑：下载图片后丢弃，实际发送 `[图片]` 文本~~ — **已修复** (base64 编码发送)
4. ~~`server.py` — `_init_self_qq` 方法与 `_wait_napcat_ready` 函数重复定义，存在竞态风险~~ — **已修复** (2026-06-07: 统一到 `_init_self_qq`)
5. ~~`cherrystudio_module.py` — Agent ID 获取使用 `mcp_server_name` 而非 `default_agent` 配置~~ — **已修复** (2026-06-07: 使用 `default_agent` 优先)
6. ~~ConversationStore 已实现但未接入消息流 (Phase 2C 任务)~~ — **已修复** (2C.1/2C.2 已接入)
7. ~~ConversationStore `add_message`/`summarize_and_archive` 在持有 `_lock` 时调用 `load_session`/`save_session` (也要获取 `_lock`) → asyncio.Lock 死锁~~ — **已修复** (提取 `_unlocked` 内部方法)

---

## 阶段 0：关键 Bug 修复与基础加固

**目标**: 修复阻断性 Bug，确保现有代码可以正确运行。

### 任务清单

- [x] **0.1** 修复 `napcat_bridge.py` 中 `os` 未导入的 NameError ✅
- [x] **0.2** 修复 `builtin.py` 中 `msg.source` 应为 `msg.raw.source` 的 AttributeError ✅
- [x] **0.3** 修复图片发送逻辑 — `send_message()` 中 IMAGE 类型应正确调用 NapCat `send_msg` API (CQ码或消息段) ✅
- [x] **0.4** 验证 ConversationStore 与消息流的接入点，确保消息到达时能持久化 ✅ (Phase 2C.1)
- [x] **0.5** 修复 `qq_upload_file` 工具 — 从 stub 变为完整实现（临时文件写入 + NapCat upload_file API）✅
- [x] **0.6** 修复 `qq_get_recent_contacts` 工具 — 从 stub 变为完整实现（调用 NapCat `get_recent_contact` API）✅
- [x] **0.7** 确保 server.py 可以正常通过 `python server.py` 启动并通过 stdio 与 MCP 客户端通信 ✅

### 验收标准

- NapCat WebSocket 连接成功建立
- MCP 客户端 (CherryStudio) 通过 stdio 连接后可调用全部 11 个工具
- 收到 QQ 消息后能正确路由并通过 MessageBus 分发
- 基础命令 `.help` 和 `.bot on/off` 可用

---

## 阶段 1：最低可运行版本 (MVP)

**目标**: 实现桥接的核心功能 — 消息收发 + 基础命令 + MCP 工具完整可用。

### 任务清单

#### 1A. NapCat 通信完善

- [x] **1A.1** 补全 NapCatBridge 缺失的 API 方法 ✅ (全部方法已在 napcat_bridge.py 中实现):
  - `send_image()` — 图片发送 (支持 URL 下载 + base64/CQ码)
  - `upload_file()` — 文件上传 (临时文件 + API 调用)
  - `get_image_path()` — 获取图片路径 (用于 Vision)
  - `download_image()` — 下载图片到本地
  - `leave_group()` — 退群
  - `approve_friend_request()` — 同意好友申请
  - `approve_group_invite()` — 同意群邀请
  - `get_recent_contact()` — 获取最近会话
  - `get_friend_msg_history()` — 获取好友历史消息
  - `set_group_card()` — 设置群名片
  - `get_group_info()` — 获取群信息
  - `get_msg()` — 获取单条消息 (用于回复链)
- [x] **1A.2** 消息解析补全 — 从 Old 项目的 `_extract_text` 移植所有 17+ CQ 消息类型 ✅
- [x] **1A.3** 提取图片文件信息 (`_extract_image_files`) 和文件信息 (`_extract_file_infos`) ✅
- [x] **1A.4** 消息事件分发 — `on_notice` (群成员增加/好友添加) 和 `on_request` (好友/群申请) ✅

#### 1B. MCP 工具完整化

- [x] **1B.1** 确保 11 个 MCP 工具全部端到端可用 (已完成)
- [x] ~~**1B.2** 添加 `qq_confirm_response` 工具~~ — **已取消**: 按 Phase 2 设计删除此工具，改为 `mark_responding`/`unmark_responding` 自动机制
- [x] **1B.3** 活跃目标验证 (`_ensure_active_target`) — 已通过 `mark_responding` + `is_target_active` 实现
- [x] **1B.4** MCP 工具调用断线自动重试 (ConnectionError/TimeoutError 时 2s 后重试一次)

#### 1C. 基础命令系统

- [x] **1C.1** 完善已有命令：`.help`, `.bot on/off`, `.order list`, `.model list/status` ✅
- [x] **1C.2** 添加 `.bot orderwhite` — 切换免@模式 ✅ (BotCommand.orderwhite)
- [x] **1C.3** 添加 `.dismiss` — 退群功能（含末4位验证）✅ (DismissCommand)
- [x] **1C.4** 添加 `.ob` — 旁观者模式 (join/exit/list) ✅ (ObCommand)
- [x] **1C.5** 添加 `.send` — 管理员消息转发 ✅ (SendCommand)

#### 1D. 事件处理

- [x] **1D.1** 入群欢迎 — 机器人被拉入群时发送欢迎信息 (读取 BotSettingConfig) ✅ (_build_bot_greeting)
- [x] **1D.2** 新成员欢迎 — 其他用户入群时发送欢迎消息 ✅ (_build_member_welcome + WelcomeCommand)
- [x] **1D.3** 新好友 — 自动发送欢迎消息 ✅ (_build_friend_greeting)
- [x] **1D.4** 自动同意好友/群申请 (可配置) ✅ (on_request handler)

### 验收标准

- 从 CherryStudio MCP 客户端可完整使用全部 11 个工具
- QQ 消息收发全链路通畅 (私聊 + 群聊)
- 基础命令全部可用 (.help, .bot, .order, .dismiss, .ob, .send)
- 图片/文件可正确发送和接收
- 群/好友事件正确处理

---

## 阶段 2：自动回复引擎 (Agent 集成)

**目标**: 接入 CherryStudio Agent API，实现 AI 自动回复功能。

> **2026-06-07 更新 (第二轮)**: Phase 2A 已基本完成 (~95%)，新增多 Agent 自动发现 + MCP 绑定验证。
> Phase 2B 自动回复过滤已完成 (100%)，含命令消息拦截、冷却控制、mention 检测。
> Phase 2C ConversationStore **全部完成**: 2C.1 消息流接入 ✅、2C.2 映射持久化 ✅、2C.3 过期检测+AI摘要 ✅、2C.4 记忆注入 ✅。详见 [WorkReader.md](./WorkReader.md) 和 [CHERRYSTUDIO_TEST_PATH.md](./CHERRYSTUDIO_TEST_PATH.md)。

### 任务清单

#### 2A. CherryStudio Agent API 接入

- [x] **2A.1** 实现 CherryStudio HTTP API 客户端 (完成)：
  - `GET /v1/agents` — 获取 Agent 列表 ✅ (`fetch_all_agents()` + `fetch_agent_id()`)
  - `GET /v1/mcps` — 获取 MCP 列表 ✅ (`fetch_mcp_servers()`)
  - `GET /v1/models` — 获取模型列表 ✅ (`resolve_model()`)
  - `GET /v1/agents/{id}` — Agent 详情 ✅ (`fetch_agent_detail()`)
  - `POST /v1/agents/{id}/sessions` — 创建会话 ✅
  - `POST /v1/agents/{id}/sessions/{sid}/messages` — 发送消息 (SSE 流式) ✅ (注: 端点是 `/messages` 非 `/chat`)
  - `DELETE /v1/agents/{id}/sessions/{sid}` — 删除会话 ✅
- [x] **2A.2** SSE 流式响应解析器 (`modules/sse_parser.py` 已完成)：
  - `text-start/text-delta/text-end` — 增量文本累积 + 去重 ✅
  - `reasoning-start/reasoning-end` — 过滤思考内容 ✅
  - Tool call 检测 — 剥离 `mcp__*__` 前缀 ✅
  - `finish-step` — 提取最终响应文本 ✅
  - `qq_send_message` 特殊处理 — 通过 `had_output_tool` + `pre_tool_text_policy` 策略 ✅
  - 停滞检测 (30s 超时)，最大重试次数，每 25s "正在烧烤中" 通知 ✅
  - 总超时: 600s (10分钟) ✅
- [x] **2A.3** 多 Agent 支持 (2026-06-07 完成)：
  - Agent 自动发现 (从 CherryStudio 拉取) ✅ (`_discover_agents()`)
  - MCP 绑定验证 (`_filter_mcp_agents`) ✅ (`_find_bridge_mcp_id()` + `fetch_agent_detail()`)
  - 手动配置 Agent 优先于自动发现 ✅ (白名单模式跳过 MCP 验证)
  - Agent 白名单过滤 ✅ (`agent_whitelist` 配置)
- [x] **2A.4** 会话生命周期管理 (完成)：
  - 创建/复用/删除 ✅ (CherryStudioSessionHandler)
  - 会话 ID 持久化 (ConversationStore) ✅ (P2-1 已接入)
  - 停滞处理 (2-strike 系统) ✅ (已实现)

#### 2B. 自动回复核心逻辑

- [x] **2B.1** 消息处理管线接入 CherryStudioModule (大部分完成)：
  - 按会话创建独立 asyncio.Queue 和 worker task ✅
  - Worker 空闲 2 分钟自动退出 (timeout=120s，含竞态保护) ✅
  - ~~命令拦截 (`.xxx` 格式消息优先走命令系统)~~ (MessageBus 层面已实现，但 Agent 层面未显式过滤)
- [x] **2B.2** 回复模式控制 (2026-06-07 完成)：
  - `mention` 模式 — 群聊需 @bot ✅ (`_is_at_me` 检测)
  - `always` 模式 — 总是回复 ✅
  - 私聊总是处理 ✅
- [x] **2B.3** 冷却控制 — 可配置的最小回复间隔 (2026-06-07 完成, 默认 3s, `time.monotonic`)
- [x] **2B.4** 群/好友白名单过滤 (2026-06-07 完成)
- [x] **2B.5** Bot 黑名单 — `.bot off` 群仅响应命令 (2026-06-07 完成)
- [x] **2B.6** 自消息过滤 — 防止反馈循环 (2026-06-07 完成, `self_qq` 比对)

#### 2C. 会话持久化

- [x] **2C.1** 接入 ConversationStore (已实现，需连接到消息流)：
  - 目录结构: `QQConversationRecord/{agent_name}/{msg_type}_{target_id}/`
  - session.json — 消息日志
  - memory.json — 历史摘要
  - meta.json — 元数据
- [x] **2C.2** Agent 会话映射持久化 (mapping.json)
- [x] **2C.3** 3 天不活跃过期检测 + AI 摘要生成 ✅
- [x] **2C.4** 记忆注入 — 新会话注入工作区上下文 (SOUL.md, USER.md, FACT.md) + 历史记忆 ✅

### 验收标准

- CherryStudio Agent API 全链路可用
- SSE 流式响应正确解析 (含思考过滤、工具调用检测)
- 多 Agent 切换正常
- 会话持久化 + 3天过期 + AI 摘要
- 自动回复在群聊和私聊中正常工作

---

## 阶段 3：高级消息处理

**目标**: 移植所有高级消息处理功能。

### 任务清单

#### 3A. 回复链与上下文

- [x] **3A.1** 回复链解析 — 递归获取引用消息 (可配置深度, 默认 4) ✅
- [x] **3A.2** 上下文构建 — 最大上下文消息数 (默认 20) ✅ (由 CherryStudio Agent 会话管理)
- [x] **3A.3** 消息分割阈值 — 基于时间戳的对话分割 (默认 5.0s) ✅ (由 CherryStudio Agent 会话管理)
- [x] **3A.4** 全局上下文注入 — 每次 LLM 调用注入 global_context ✅ (Phase 2C.4 _build_injection_context)

#### 3B. 多模态处理

- [x] **3B.1** 图片识别 (Vision) ✅ (VisionProviderChain):
  - 多 Vision Provider 支持 + 索引递增回退 ✅
  - OpenAI + Anthropic API 格式支持 ✅
  - base64 图片编码 ✅
  - NapCat `get_image` API 获取图片文件 ✅
  - 合并当前消息 + 回复链中的图片 ✅
- [x] **3B.2** 文件处理 (MinerU) ✅ (FileProcessor):
  - 文件下载 (通过 NapCat 文件 URL) ✅
  - MinerU `flash-extract` 命令执行 ✅
  - 文档摘要 (可配置最大字符数, 默认 1500) ✅
  - 最大文件大小限制 (默认 10MB) ✅

#### 3C. 输出处理

- [x] **3C.1** 长文本转文档 — 超过 `doc_threshold` (默认 1500 字符) 自动保存为 .md 并上传 ✅ (NapCatBridge._send_text 已实现)
- [x] **3C.2** Markdown 图片提取 — 解析 `![alt](url)` 并单独发送图片 ✅
- [x] **3C.3** 名称占位符替换 — `{name}`, `{sender}`, `{at}` 替换 ✅

#### 3D. LLM/Vision Provider 管理

- [x] **3D.1** 多 Provider 数组 + 索引递增回退 ✅ (LLMProviderChain)
- [x] **3D.2** 配额错误检测 (429/402 + 15+ 关键词识别) ✅ (_is_quota_exceeded_text + _QUOTA_KEYWORDS)
- [x] **3D.3** 自动切换 — 配额耗尽时切换到下一个 Provider ✅ (QuotaError → _switch_to_next_provider)
- [x] **3D.4** 管理员通知 — Provider 切换时私信通知 (1小时冷却) ✅ (_on_switch_callback + _notify_admin_provider_switch)
- [x] **3D.5** 模型名称解析 — 短名 -> `provider:model_id` (通过 CherryStudio `/v1/models` API) ✅
- [x] **3D.6** 模型偏好持久化 — 保存在 SharedState `saved_models` 字段 (StateManager 自动持久化到 `shared_state.json`) ✅

### 验收标准

- 回复链正确解析 (含多层引用)
- 图片可被 Vision 模型正确识别
- 文件可通过 MinerU 处理
- 长文本自动转文档发送
- LLM 配额耗尽时自动切换并通知管理员

---

## 阶段 4：完整命令系统

**目标**: 移植 Old 项目的全部命令 (20+ 个命令)。

### 任务清单

#### 4A. 内置命令完善

- [x] **4A.1** `.bot` 命令完善 ✅ (BotCommand + BotSettingConfig 自定义消息):
  - `on/off` — 切换群回复模式 ✅ (含可定制消息模板)
  - `orderwhite` — 切换免@白名单 ✅
  - `help` — 显示帮助 ✅
  - 可自定义消息 (读取 BotSettingConfig.json) ✅ (_load_bot_setting)
- [x] **4A.2** `.order` 命令完善 ✅ (OrderCommand):
  - `切换/switch <名称>` — 切换 Agent ✅
  - `列表/list` — 显示 Agent 列表 (含当前绑定标记) ✅
  - `重建会话/rebuild/reset` — 重建当前会话 ✅
  - `status` — 显示会话状态 (Agent, 模型, 消息数, 会话时长) ✅
- [x] **4A.3** `.model` 命令完善 ✅ (ModelCommand):
  - `list` — 列出可用模型 ✅ (静态列表)
  - `change <模型名>` — 切换模型 ✅ (持久化)
  - `status` — 显示当前模型 ✅
  - `reset` — 清除偏好 ✅
  - 管理员权限限制 ✅ (change 需管理员)
- [x] **4A.4** `.master` 命令完善 ✅ (MasterCommand):
  - `LLMReset` — 重置主 KEY (回退到默认 Provider) ✅
  - `AllResetAgent` — 删除所有会话 (管理员) ✅
  - `OnlyResetAgent` — 仅删 API 会话 (管理员) ✅
- [x] **4A.5** `.dismiss <末4位>` — 退群 (含验证 + 本地数据清理 + 可定制告别消息) ✅
- [x] **4A.6** `.ob` 命令完善 ✅ (ObCommand + MessageBus 旁观者转发):
  - `join/exit/list/clr/on/off` ✅
  - 旁观者消息转发 ✅ (_forward_to_observers)

#### 4B. 插件命令移植

- [x] **4B.1** `.help` — 动态命令列表 (扫描所有已注册命令) ✅ (HelpCommand + CommandRegistry.list_all)
- [x] **4B.2** `.welcome` 命令：
  - `open/close/set/status/help`
  - 按群配置新成员欢迎消息
  - `{at}` 占位符支持
  - 持久化到 SharedState ✅
- [ ] **4B.3** `.log` 命令：
  - `new/on/off/end/list/get/del/help`
  - 群聊日志全生命周期管理
  - EventHooks 消息钩子 (写入活跃日志文件)
  - 日志打包上传 (zip + 文件发送)
  - 日志黑名单 (旁观者排除)
- [ ] **4A.4** `.send` — 管理员消息转发 ✅ (SendCommand 已实现，重复条目)

### 验收标准

- Old 项目全部 20+ 命令在新项目中可用
- 命令自动发现框架工作正常 (扫描 modules/commands/ 和 plugins/ 目录)
- 紧凑格式支持 (`.st力量5` 无需空格)
- 中英文命令别名

---

## 阶段 5：插件系统 (骰子与 TRPG)

**目标**: 移植全部骰子引擎和 Ark TRPG 插件。

### 任务清单

#### 5A. 骰子核心引擎

- [ ] **5A.1** 骰子解析器 (`dice_parser.py`)：
  - `parse_and_roll` — 解析 `XdY`, `XdY+Z`, `XdY#N` (重复), `d100` 表达式
  - `check_result` — COC 风格结果判定 (大成功 <=5, 大失败 >=96, 极难/困难/普通成功)
  - `check_critical_d6` — Ark TRPG 风格 (半最大=暴击, 半最小=失误)
- [ ] **5A.2** 角色卡存储 (`character_store.py`)：
  - 按玩家数据: `data/{uid}/player.json` (活跃卡名)
  - 按卡数据: `data/{uid}/cards/{name}.json`
  - 多卡支持 (最多 5 张)
  - 默认卡模板 (ark/coc 系统)
  - 群数据回退加载 (兼容旧格式)
  - `format_card` — 美化打印角色卡
  - CRUD 操作: load/save/delete/rename/set_skill
- [ ] **5A.3** 骰子命令 (`commands.py`)：
  - `.r` — 通用骰子 (3d6, 3d6+2, 3d6#3, d100 50) 含 COC DC 判定
  - `.rh` — 暗骰 (结果私发, 旁观者转发)
  - `.ra` — d100 技能/属性检定 (COC 规则), 读取角色卡
  - `.show` — 显示当前角色卡
  - `.del` — 删除角色卡或单个技能
  - `.pc` — 多卡管理: list/switch/new/del (最多5张, 溢出自动创建)
  - `.nn` — 重命名角色卡
  - `.st` — 设置属性/技能 (紧凑格式 `.st力量5敏捷3智力7`, 支持查询)

#### 5B. Ark TRPG 插件

- [ ] **5B.1** Ark 命令 (`commands.py`)：
  - `.rk` — 技能检定: `[骰面] [技能] [值]/[DC]`, 使用角色卡属性
  - `.rkb` — 奖励骰变体
  - `.rkp` — 惩罚骰变体
  - `.sck` — 自控检定 (d10 vs 精神意志)
  - `.ark` — 角色创建: 7属性(各2d4) + 经济(4d6) + 社交
  - `.sn` — 设置群名片模板 (HP/SP 显示)
- [ ] **5B.2** 技能映射 (`skills.py`)：
  - 50+ 技能到 6 基础属性的映射
  - (精神意志, 魅力, 反应, 力量, 智慧, 源石适应)

#### 5C. 插件框架

- [ ] **5C.1** EventHooks 系统 — `on_message` 钩子注册和触发
- [ ] **5C.2** PluginContext 注入 — 提供 state/napcat/auto_reply/config/data_dir
- [ ] **5C.3** SharedState 跨模块数据共享 — observers/blacklists/whitelists/model_prefs
- [ ] **5C.4** BotSettingConfig 可定制消息模板 — 按模块存储模板, 支持 `{}` 和 `<>` 占位符

### 验收标准

- 全部 8 个骰子命令可用
- 全部 6 个 Ark TRPG 命令可用
- 角色卡 CRUD + 多卡管理正常
- EventHooks 和 PluginContext 工作正常

---

## 阶段 6：基础设施与打磨

**目标**: 完善基础设施，达到生产可用质量。

### 任务清单

#### 6A. 启动与配置

- [x] **6A.1** MCP Agent 绑定自动检测 — 查询 `/v1/mcps` 找到 QQ Bridge MCP ID (最多 50s 重试) ✅ (_find_bridge_mcp_id)
- [ ] **6A.2** 启动时会话协调 (`reconcile_sessions`)：
  - 验证所有本地 session ID 与 CherryStudio 服务端一致性
  - 按名称恢复缺失 SID
  - 同名会话去重 (保留最新)
  - 检测孤立会话
  - 输出协调报告
- [x] **6A.3** 完整配置支持 — 确保 config.json 的所有字段都被正确处理 ✅ (_adapt_legacy_config):
  - `global_context`, `auto_reply`, `admin_qq` ✅
  - `auto_accept_friend`, `auto_accept_group` ✅
  - `agent_whitelist`, `agent_timeout_seconds` ✅
  - `vision`, `file_processing` ✅
- [x] **6A.4** BotSettingConfig.json 自动重建 (丢失时生成默认值) ✅ (_ensure_bot_setting_config)
- [ ] **6A.5** 一键安装 URL 生成器 (`generate_install_url.py`): manual + uvx 两种模式

#### 6B. 日志与调试

- [x] **6B.1** 可配置日志级别 (DEBUG/INFO/WARNING/ERROR) ✅ (standard logging)
- [ ] **6B.2** 调试模式 — 每次重启清空旧日志 + 文件日志
- [ ] **6B.3** Windows 独立控制台窗口 (AllocConsole + CONOUT$) — 已在 server.py main() 实现
- [x] **6B.4** 屏蔽 MCP 协议层心跳日志 ✅ (setLevel WARNING)
- [ ] **6B.5** 错误码体系完善 — 确保所有 BRG-XXXX 错误码正确使用

#### 6C. 健壮性

- [ ] **6C.1** WebSocket 自动重连 (指数退避 1s -> 60s, 可配置最大重连次数)
- [x] **6C.2** MCP 工具调用断线自动重试 ✅ (ConnectionError/TimeoutError → 2s 后重试)
- [x] **6C.3** 全局上下文长度警告 (>500 字符时) ✅ (BRG-5001)
- [ ] **6C.4** 配置文件丢失/损坏的优雅降级
- [x] **6C.5** 异步任务异常隔离 — 单个会话异常不影响其他会话 ✅ (SessionHandler try/except 隔离)

#### 6D. MCP STDIO 传输

- [x] **6D.1** 确保 server.py 通过 stdio 传输与 CherryStudio 正确通信 ✅
- [x] **6D.2** FastMCP 或 mcp.server.stdio 正确配置 ✅
- [x] **6D.3** pyproject.toml `[project.scripts]` 入口点配置 (支持 uvx) ✅ (main_sync)

### 验收标准

- 启动时自动检测 MCP 绑定 + 会话协调
- 全部配置字段生效
- 日志系统完善
- 断线自动重连
- 可通过 `uvx` 或 `python server.py` 启动

---

## 阶段 7：测试与质量保证

**目标**: 确保所有功能经过充分测试。

### 任务清单

#### 7A. 测试移植与补充

- [ ] **7A.1** 移植 Mock 基础设施 (MockNapCat, MockAutoReply)
- [x] **7A.2** 命令系统测试 — 所有命令, 所有边界情况, 管理员/非管理员 ✅ (365 测试中大部分覆盖命令)
- [x] **7A.3** 纯函数测试 ✅:
  - `_extract_text` (全部 17+ CQ 类型) ✅
  - `_extract_image_files`, `_extract_file_infos` ✅
  - `_parse_session_ts`, `_parse_sse_blocks` (8 SSE 场景) ✅
  - `_extract_md_images`, `_strip_md_images` ✅
  - `_find_command`, `_is_at_me`, `_has_at_others` ✅
  - `QQMessage.format_for_ai`, `QQMessage.get_reply_id` ✅
- [ ] **7A.4** 数据层测试：
  - `parse_and_roll` (种子化骰子)
  - `check_result` (COC 判定)
  - Temp/store (加载/保存/去重/损坏恢复)
  - character_store (全 CRUD, 多卡, 格式化)
  - conversation_store (全部持久化函数)
  - SharedState (持久/恢复往返)
- [ ] **7A.5** 集成测试 — 端到端消息流 (NapCat -> MessageBus -> Module -> Response -> NapCat)

#### 7B. 文档

- [ ] **7B.1** README.md 更新 — 完整功能列表、安装说明、配置说明
- [ ] **7B.2** 错误码文档 (docs/error_codes.md)
- [ ] **7B.3** 协议文档 (docs/PROTOCOL.md) 更新
- [ ] **7B.4** CHANGELOG.md 更新

### 验收标准

- 所有旧项目测试在新项目中通过
- 新增功能有对应测试覆盖
- 文档完整准确

---

## 功能对照表 (Old vs New)

### MCP 工具

| 工具 | Old | New 现状 | 需完成 |
|------|-----|----------|--------|
| qq_send_message | 完整 | 完整 | mark_responding 机制已替代 confirm_response |
| qq_send_image | 完整 | 完整 | — |
| qq_upload_file | 完整 | 完整 (双模式) | — |
| qq_get_recent_messages | 完整 | 完整 | — |
| qq_get_group_msg_history | 完整 | 完整 | — |
| qq_get_group_list | 完整 | 完整 | — |
| qq_get_friend_list | 完整 | 完整 | — |
| qq_get_group_members | 完整 | 完整 | — |
| qq_get_user_info | 完整 | 完整 | — |
| qq_get_recent_contacts | 完整 | 完整 | — |
| qq_check_status | 完整 | 完整 | — |
| qq_recall_message | 完整 | 完整 | — |
| ~~qq_confirm_response~~ | ~~完整~~ | ~~已删除~~ | 按 Phase 2 设计删除，由 mark_responding 替代 |

### 命令系统

| 命令 | Old | New 现状 | 需完成 |
|------|-----|----------|--------|
| .help | 完整 | 基础 | 动态扫描 |
| .bot on/off | 完整 | 基础 | 完善 |
| .bot orderwhite | 完整 | 无 | 新增 |
| .order 切换/列表/重建/状态 | 完整 | 部分 | 完善 |
| .model list/change/status | 完整 | 部分 | 完善 |
| .master LLMReset/AllReset/OnlyReset | 完整 | 无 | 新增 |
| .dismiss | 完整 | stub | 完整实现 |
| .ob join/exit/list/clr/on/off | 完整 | 部分 | 完善 |
| .welcome | 完整 | 无 | 新增 |
| .log | 完整 | 无 | 新增 |
| .send | 完整 | 无 | 新增 |
| .r | 完整 | 无 | 新增 |
| .rh | 完整 | 无 | 新增 |
| .ra | 完整 | 无 | 新增 |
| .st | 完整 | 无 | 新增 |
| .show | 完整 | 无 | 新增 |
| .del | 完整 | 无 | 新增 |
| .pc | 完整 | 无 | 新增 |
| .nn | 完整 | 无 | 新增 |
| .rk/.rkb/.rkp | 完整 | 无 | 新增 |
| .sck | 完整 | 无 | 新增 |
| .ark | 完整 | 无 | 新增 |
| .sn | 完整 | 无 | 新增 |

### 核心功能

| 功能 | Old | New 现状 (2026-06-06 更新) | 需完成 |
|------|-----|----------|--------|
| WebSocket 双向通信 | 完整 | 大部分完成 | 指数退避完善 |
| 自动重连 | 完整 | 部分 | 指数退避完善 |
| 消息解析 (17+ CQ类型) | 完整 | 完成 | — |
| 自动回复引擎 | 完整 | 大部分完成 (SSE+HTTPClient+SessionHandler+过滤+冷却) | 多Agent+ConversationStore |
| CherryStudio Agent API | 完整 | 大部分完成 (创建/消息/删除) | Agent 发现/MCP绑定验证 |
| SSE 流式解析 | 完整 | 完成 (独立模块) | 单元测试 |
| 多 Agent 支持 | 完整 | 已完成 (2026-06-07) | Agent 切换命令待实现 |
| 会话管理 (持久/过期/摘要) | 完整 | 代码就绪，已接入消息流 | 3天过期+摘要 |
| 回复链解析 | 完整 | 未实现 | 新增 |
| 图片识别 (Vision) | 完整 | 完成 (VisionProviderChain) | — |
| 文件处理 (MinerU) | 完整 | 完成 (FileProcessor) | — |
| LLM Provider 回退链 | 完整 | 完成 (LLMProviderChain) | — |
| 长文本转文档 | 完整 | 未实现 | NapCatBridge._send_text |
| Markdown 图片提取 | 完整 | 未实现 | 新增 |
| 命令自动发现框架 | 完整 | 部分 | 插件目录扫描 |
| EventHooks 插件系统 | 完整 | 未实现 | 新增 |
| PluginContext 注入 | 完整 | 未实现 | 新增 |
| SharedState 持久化 | 完整 | 完成 (含双向合并+模型偏好) | — |
| 角色卡系统 | 完整 | 未实现 | 新增 |
| 骰子引擎 | 完整 | 未实现 | 新增 |
| Ark TRPG 插件 | 完整 | 未实现 | 新增 |
| 安装 URL 生成器 | 完整 | 未实现 | 新增 |
| 会话协调 | 完整 | 未实现 | 新增 |
| PID 单例锁 | 完整 | 完成 | — |
| 控制台窗口 | 完整 | 完成 | — |

---

## 实施优先级总结

```
阶段 0: Bug 修复 ──────────────── [紧急] 阻断性问题
阶段 1: MVP ────────────────────── [高]   基础桥接可用
阶段 2: Agent 集成 ─────────────── [高]   AI 自动回复
阶段 3: 高级消息处理 ────────────── [中]   多模态 + 文件
阶段 4: 完整命令系统 ────────────── [中]   全部命令移植
阶段 5: 插件系统 ───────────────── [中]   骰子 + TRPG
阶段 6: 基础设施 ───────────────── [低]   生产打磨
阶段 7: 测试与文档 ─────────────── [持续] 质量保证
```

---

## 附录 A：验证发现的细节补充

经过交叉验证，以下是各阶段中容易被忽略的实现细节，补充到对应阶段中。

### A.1 阶段 1 补充 — MVP 细节

#### 命令前缀与解析

- **中文句号前缀**: 旧版 `dispatch()` 同时接受 `.` 和 `。` 作为命令前缀，需在新版命令模块中支持
- **紧凑格式回退**: 输入 `.st力量5` 时，如果 `st力量5` 整体匹配不到命令名，需提取 ASCII 前缀 `st` 并将 `力量5` 作为参数。这个回退机制需要在新版命令解析中实现

#### WebSocket 调优参数

- `max_size=10MB` — 消息体最大值
- `ping_interval=30`, `ping_timeout=10` — 保活设置
- `ws_max_reconnect` — 配置项位于 `napcat.ws_max_reconnect`

#### NapCat API 端点补充

- `set_group_card()` — 设置群名片 (`.sn` 命令依赖)
- `get_msg()` — 获取单条消息 (回复链解析依赖)
- `get_group_info()` — 获取群信息

### A.2 阶段 2 补充 — Agent API 细节

#### API 端点修正

- 消息发送端点为 `POST /v1/agents/{id}/sessions/{sid}/messages` (注意：不是 `/chat`)
- 会话创建请求体包含 `name`, `accessible_paths` (工作目录列表), `model` (通过 CherryStudio 解析后的格式)

#### SSE 错误处理

- **`session_not_found` 错误**: CherryStudio 返回 SSE error 且 `code: "session_not_found"` 时，需清除本地 session ID 并触发 `SessionNotFoundError` 以自动重建会话
- **响应文本提取**: `finish-step` 中需检查 `text`, `content`, `output`, `message` 四个 key，且兼容 string 和 list 两种格式

#### Chat API 回退

- 当 `agent_enabled=True` 但 `agents` 字典为空时，自动回退到直接 LLM API 调用 (`_call_chat_api`)，不走 Agent 流程
- 日志输出: `"自动回复已启用 (Chat API 回退)"`

#### ~~`qq_confirm_response` 指令模板~~ — 已删除

- 按 Phase 2 设计，`qq_confirm_response` 已被删除，由 `mark_responding`/`unmark_responding` 自动机制替代
- 旧版中 `_format_incoming` 告知 AI 使用 `qq_confirm_response` 的指令模板不再需要

#### cherry_api_key 回退逻辑

- 优先使用 `cherry_api_key`
- 若未设置，回退到第一个 LLM provider 的 `api_key` (附带 BRG-1002 警告日志)
- 若两者都无，输出 BRG-1003 警告

### A.3 阶段 3 补充 — 高级处理细节

#### 会话归档 (非删除)

- 3 天过期清理时，`session.json` 重命名为 `session_archive_{timestamp}.json` (保留，不删除)
- 清理流程: AI 摘要 → 归档旧会话 → 保存摘要到 `memory.json`

#### PlayerLog 自动注入

- 自动发现 Agent 时，自动将 `PlayerLog/` 目录追加到每个 Agent 的 `work_dirs` 列表

#### extract_at_targets() 工具方法

- 从消息中提取所有被 @的 QQ 号码，供插件系统使用

#### 共享 HTTP 会话

- 使用 `aiohttp.ClientSession` 在所有 API 调用间共享，复用 TCP 连接池

### A.4 阶段 4 补充 — 命令细节

#### `.log on` 语义

- `.log on` 实际含义是 **"恢复" (resume)**，而非创建新日志。它会恢复一个已暂停的日志或查找磁盘上未结束的日志
- 对应方法: `log_resume()`

#### `.dismiss` 验证流程

- 需要输入群号末 4 位进行验证
- 退群后执行本地数据清理 (会话记录、日志、旁观者数据等)

#### `.ob` 旁观者消息格式

- 旁观者接收到的转发消息前缀: `"👁️ [旁观] 群 {group_id} — {sender_name}:"`

#### Welcome 配置持久化

- 旧版存储在独立文件 `Temp/welcome_config.json` (格式: `{group_id: {enabled, message}}`)，非 SharedState
- 新版需决定是迁移到 SharedState 还是保持独立文件

#### BotSettingConfig 默认结构

当 `BotSettingConfig.json` 丢失时，重建的默认结构为:
```json
{
  "内置模块": {
    "custom_greeting": "欢迎消息的自定义前缀，留空则仅显示版本和命令列表"
  },
  "BuiltInOrder": {
    "bot_on_message": ".bot on 时发送的消息，留空使用默认文案",
    "bot_off_message": ".bot off 时发送的消息，留空使用默认文案",
    "dismiss_message": "退群时发送的告别消息，留空不发送"
  }
}
```

#### BotSettingConfig 模块模板键

骰子和 TRPG 命令从 BotSettingConfig 中读取可定制模板，键名如下:
- `dice_core`: `r_message`, `ra_message`, `st_message`, `del_card_message`, `nn_message`
- `arktrpg`: `rk_message`, `rkb_message`, `rkp_message`, `sck_message`, `ark_message`, `sn_message`
- `ob`: `ob_join_message`, `ob_list_message`
- `log`: `log_new_message`, `log_list_message`

模板支持 `{}` 注入结果，`<>` 注入发送者名

### A.5 阶段 5 补充 — 骰子/TRPG 细节

#### `.sck` 已知 Bug

- 旧版 `ark_trpg/commands.py` 中 `.sck` 命令使用了未定义的 `will` 变量
- 新版需修复: 应从角色卡读取精神意志属性值

#### `.r` 命令 `/DC` 语法

- 支持 `.r 3d6/12` (从 `/` 后缀解析 DC) 和 `.r 3d6 12` (空格分隔) 两种 DC 语法
- COC 判定: 大成功 <=5, 大失败 >=96, 极难/困难/普通成功

#### `.pc` 5 卡溢出处理

- 当技能设置触发新卡创建且已达 5 卡上限时，自动删除最旧的非活跃卡

#### `.del` 属性保护

- `.del card` / `.del all` 删除整张卡
- `.del [skill_name]` 删除单个技能
- 属性 (力量/敏捷等) 不可通过 `.del` 删除，需用 `.st [attr] 0` 重置

#### 角色卡模板

两套默认模板:
- **ark**: 含 hp/sp/skills/attributes (精神意志/魅力/反应/力量/智慧/源石适应)
- **coc**: 含 STR/CON/SIZ/DEX/APP/INT/POW/EDU/LUC/SAN

#### `.ark` 社交点数计算

- 7 个属性各 2d4
- 经济: 固定 4d6
- 社交: 骰子数量取决于个人魅力属性值 (非固定)

#### `.rk` 计算公式

- `dice_count = skill_value + bonus`
- 掷骰后加上 `attr_bonus` (来自映射的基础属性)
- 总和与 DC 比较

#### 群数据向后兼容

- `character_store.load_group_data()` 读取旧格式 `data/{group_id}/{uid}.json`
- 将旧数据 (name/hp/sp/skills/attributes) 合并到新卡格式

### A.6 阶段 6 补充 — 配置字段完整列表

以下为 config.json 中需要支持的全部配置字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `debug_mode` | int | 0=关闭日志文件, 1=开启 |
| `log_level` | string | DEBUG/INFO/WARNING/ERROR |
| `show_console` | bool | Windows 独立控制台窗口 |
| `admin_qq` | string | 管理员 QQ 号 |
| `auto_accept_friend` | bool | 自动同意好友申请 |
| `auto_accept_group` | bool | 自动同意群邀请 |
| `global_context` | string | 注入每次 LLM 调用的全局 System Prompt |
| `mcp_server_name` | string | CherryStudio 中 MCP 名称 (用于绑定检测) |
| `cherry_api_key` | string | CherryStudio API Key |
| `napcat.ws_host` | string | WebSocket 地址 |
| `napcat.ws_port` | int | WebSocket 端口 |
| `napcat.access_token` | string | NapCat 访问令牌 |
| `napcat.ws_max_reconnect` | int | 最大重连次数 (0=无限) |
| `bridge.message_buffer_size` | int | 消息缓存上限 (默认 200) |
| `bridge.sse_stall_max_retries` | int | SSE 停滞最大重试次数 |
| `llm[]` | array | LLM Provider 数组 |
| `default_llm.provider` | int | 默认 Provider 索引 |
| `default_llm.model` | string | 默认模型名 |
| `vision_providers[]` | array | Vision Provider 数组 |
| `default_vision.provider` | int | 默认 Vision Provider 索引 |
| `default_vision.model` | string | 默认 Vision 模型名 |
| `agent_enabled` | bool | 启用 Agent 模式 |
| `agent_timeout_seconds` | int | API 超时 (秒) |
| `agent_whitelist` | string[] | Agent ID 白名单 |
| `agents` | object | 手动配置的 Agent |
| `default_agent` | string | 默认 Agent 名称 |
| `auto_reply.enabled` | bool | 开关自动回复 |
| `auto_reply.reply_to_groups` | string[] | 限定群号 |
| `auto_reply.reply_to_friends` | string[] | 限定好友 |
| `auto_reply.reply_mode` | string | "mention" 或 "always" |
| `auto_reply.cooldown_seconds` | int | 同会话最小间隔 |
| `auto_reply.max_context_messages` | int | 最大上下文消息数 |
| `auto_reply.message_split_threshold` | float | 分割标记阈值 (秒) |
| `auto_reply.reply_chain_depth` | int | 回复链回溯深度 |
| `auto_reply.doc_threshold` | int | 文档截断字符数 |
| `vision.enabled` | bool | 启用图片识别 |
| `vision.prompt` | string | 图片识别 System Prompt |
| `file_processing.enabled` | bool | 启用文件处理 |
| `file_processing.mineru_command` | string | MinerU 命令 |
| `file_processing.max_file_size_mb` | int | 最大文件大小 |
| `file_processing.summary_max_chars` | int | 摘要最大字符数 |

#### 旧配置格式兼容

- 旧格式 `agent` 字段 `{enabled, agent_id, work_dir}` 需自动迁移为新 `agents` 字典格式 `{"默认": {agent_id, work_dirs}}`
- `work_dir` (字符串) 和 `work_dirs` (列表) 两种格式通过 `_normalize_work_dirs()` 统一处理

#### 双重持久化迁移 (已实现 ✅)

- `order_whitelist` 和 `bot_blacklist` 在旧版中同时存储于 SharedState 和独立文件 (`Temp/order_whitelist.json`, `Temp/bot_blacklist.json`)
- 新版启动时需从两者中合并加载 (双向合并确保一致)
- **已实现**: `StateManager.merge_legacy_files()` 在 `server.py` 启动时自动调用，合并 order_whitelist/bot_blacklist/log_blacklist 三个字段，并回写独立文件保持双向一致

### A.7 用户界面字符串保留

以下为旧版中的标志性用户界面字符串，新版应保留或适配:

| 字符串 | 用途 |
|--------|------|
| `"小企鹅看不懂拉，您发的太深奥了拉"` | AI 处理失败回退消息 |
| `"小企鹅正在烧烤中呜……({count}/{max})"` | SSE 停滞通知 |
| `"[小企鹅已经熟了]"` | 部分刷新标记 |
| `"已恢复正常回复。"` | .bot on 默认响应 |
| `"已开启指令模式..."` | .bot off 默认响应 |
| `"欢迎新人！... 我是本群助手..."` | 默认新成员欢迎消息 |
| `"角色卡已经设置好了~~~欢迎加入~~~"` | .st 默认响应 |

---

*生成时间: 2026-06-06 | 最近更新: 2026-06-07*
*基于 Old QQ-MCP Bridge v2.0 完整代码分析*
*附录 A: 交叉验证补充 — 覆盖 30+ 项遗漏细节*
