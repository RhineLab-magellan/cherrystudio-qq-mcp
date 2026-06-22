# QQ-MCP Bridge v3.0 模块实现进度

> 最后更新: 2026-06-09
> 项目版本: 3.0.0 (RC — Release Candidate, Phase 3 测试进行中)
> 仓库: [RhineLab-magellan/cherrystudio-qq-mcp](https://github.com/RhineLab-magellan/cherrystudio-qq-mcp)

---

## 目录

1. [总体进度概览](#1-总体进度概览)
2. [已实现模块](#2-已实现模块)
3. [已实现命令](#3-已实现命令)
4. [MCP 工具清单](#4-mcp-工具清单)
5. [未实现功能](#5-未实现功能)
6. [测试覆盖分析](#6-测试覆盖分析)
7. [已知问题](#7-已知问题)
8. [工作量汇总](#8-工作量汇总)

---

## 1. 总体进度概览

```
总体完成度: ██████████████████████████████  99%
```

| 维度 | 已完成 | 总计 | 完成率 |
|------|--------|------|--------|
| 核心模块 | 14 | 14 | 100% |
| 内置命令 | 24 | 24 | 100% |
| MCP 工具 | 12 | 12 | 100% |
| 错误码定义 | 40 | ~50 | 80% |
| 测试覆盖 | 664 项 | ~680 项 (预估) | ~98% |

### 统计摘要

- **核心代码**: 22 个源文件, **~12,200 行** (含 Phase 2 新增 install_url, config_models, state_models, hooks 增强, conversation_store 校验, md_to_image v2 模块)
- **测试代码**: 13 个测试文件 + conftest.py, **~10,600 行**, **664 项测试用例**
- **代码测试比**: ~10,600 / ~12,200 = **86.9%**
- **运行环境**: Python >=3.10, 构建后端 Hatchling, 包管理 uvx/pip
- **Phase 2 新增**: Install URL 生成器, HookManager 增强 (优先级+过滤+3 事件类型), 会话完整性校验, Pydantic 配置/状态验证
- **MD-to-Image 新增**: Markdown-to-PNG 转换模块, send_local_image 方法, qq_upload_file as_image 参数
- **MD-to-Image v2 增强**: Playwright Chromium (本地 Browsers/ 目录) + Pillow 纯 Python 回退 + venv 依赖修复

---

## 2. 已实现模块 (✅ 14/14)

所有核心基础设施模块、游戏插件模块和 MD-to-Image 转换模块均已实现并通过测试。

### 2.1 ✅ Server — 系统核心

| 属性 | 值 |
|------|------|
| 文件 | `server.py` |
| 行数 | ~1,170 行 |
| 测试 | `test_server.py` — 10 项测试, 233 行 |

**已实现功能:**

- ✅ MCP Server 注册与生命周期管理 (`FastMCP` + 底层 `stdio_server`)
- ✅ 配置加载与旧格式适配 (`_adapt_legacy_config`: `cherry_api_key` → `cherrystudio.api_key`, `api_url` → `base_url`)
- ✅ 12 个 MCP 工具注册 (详见 §3 MCP 工具表); `qq_upload_file` 支持 `as_image` 参数 (§2.15)
- ✅ 系统初始化顺序: StateManager → NapCatBridge → MessageBus → CommandModule → CherryStudioModule → MCP
- ✅ 模块队列连接 (`_connect_queues`): 非阻塞并发模式, `send_message_queue` 直推
- ✅ 事件处理器: 入群欢迎 (`on_notice`), 好友/群审批 (`on_request`), 机器人入群问候
- ✅ BotSettingConfig.json 自动重建 (`_ensure_bot_setting_config`): 含 `dice_core`、`arktrpg`、`log` 等全部默认可定制消息模板
- ✅ 旧持久化文件双向合并 (`merge_legacy_files`)
- ✅ 优雅关闭 (`shutdown`): 取消任务 → 等待完成 → 逐模块关闭
- ✅ Windows 独立控制台窗口 + VT 序列支持
- ✅ PID 单例锁已禁用 (兼容 CherryStudio 频繁重启 MCP 服务器)

### 2.2 ✅ NapCatBridge — NapCat 互联桥

| 属性 | 值 |
|------|------|
| 文件 | `modules/napcat_bridge.py` |
| 行数 | ~1,540 行 |
| 测试 | `test_napcat_bridge.py` — 56 项测试, 906 行 + `test_md_to_image.py` — 4 项 (send_local_image mock) |

**已实现功能:**

- ✅ WebSocket 连接管理 (建连、认证、自动重连、最大重连次数配置)
- ✅ `MessageBuffer`: 全局 + 按目标分桶缓存, 可配置大小 (默认 200), `has_target` / `get_all_targets`
- ✅ 30+ OneBot v11 API 封装:
  - 消息类: `send_message` (自动转文档)、`delete_msg`、`get_group_msg_history`
  - 信息类: `get_login_info`、`get_stranger_info、`get_group_list`、`get_friend_list`、`get_group_member_list`、`get_recent_contact`
  - 文件类: `upload_file` (群/私聊), `send_local_image` (本地 PNG/JPG → base64 → OneBot 图片段)
  - 管理类: `leave_group`、`approve_friend_request`、`approve_group_invite`
- ✅ 事件回调系统: `register_notice_handler`、`register_request_handler`、`_on_message_handlers`
- ✅ 响应目标管理: `mark_responding` / `unmark_responding` (SSE 期间防重复)
- ✅ 活跃目标验证: `is_target_active` (MCP 工具调用时校验)
- ✅ 长文本自动转文档: 超过 `_doc_threshold` (默认 1000 字符) 自动保存为文件上传
- ✅ `send_local_image()`: 读取本地 PNG/JPG 文件 → base64 编码 → OneBot send_msg 图片段 (支持私聊/群聊, 可选 summary 文本)
- ✅ echo-based pending 机制: API 调用结果异步等待

### 2.3 ✅ MessageBus — 消息互联桥

| 属性 | 值 |
|------|------|
| 文件 | `modules/message_bus.py` |
| 行数 | 332 行 |
| 测试 | `test_message_bus.py` — 25 项测试, 544 行 |

**已实现功能:**

- ✅ 过滤器链: `BlacklistFilter` (机器人黑名单)、`ModuleEnabledFilter` (模块启用状态)
- ✅ 消息解析: 标准格式 (`.help` / `。help`) + 紧凑格式回退 (`.st力量5` → 命令 `st`, 参数 `力量5`)
- ✅ 非阻塞分发: 命令消息 → `command_queue`, 普通消息 → `cherrystudio_queue`
- ✅ 旁观者转发 (`_forward_to_observers`): 群消息私聊转发给旁观者列表
- ✅ `send_response()`: 模块直接推送 `OutgoingMessage` 到 `send_message_queue`
- ✅ 可插拔过滤器: `add_filter` / `remove_filter`

### 2.4 ✅ CommandModule — 命令模块

| 属性 | 值 |
|------|------|
| 文件 | `modules/command_module.py` |
| 行数 | 418 行 |
| 测试 | `test_command_module.py` — 52 项测试, 917 行 |

**已实现功能:**

- ✅ `Command` 基类: `name` / `description` / `reminder` / `group` / `usage` 属性, `handle()` 异步方法, `_sub_help()` 子帮助方法
- ✅ `CommandContext` 依赖注入: `state_manager`、`napcat_bridge`、`config`、`send_queue`、`command_registry`、`cherrystudio_module`
- ✅ `CommandRegistry` 自动发现: `discover_builtin()` 注册 24 个命令 (9 内置 + 8 骰子 + 6 方舟 TRPG + 1 日志)
- ✅ `SessionHandler`: 按会话 (群/私聊) 创建独立异步任务, 5 分钟超时自动清理
- ✅ 热重载: `reload_config()` 清空 → 重新发现 → 更新上下文
- ✅ 命令执行与异常处理: `BridgeError` → 结构化 `ModuleResponse`
- ✅ `_format_msg()` 模板格式化: `{}` → 命令结果, `<>` → 玩家名称, 支持 BotSettingConfig 消息定制

### 2.5 ✅ CherryStudioModule — CherryStudio 集成

| 属性 | 值 |
|------|------|
| 文件 | `modules/cherrystudio_module.py` |
| 行数 | 3,175 行 |
| 测试 | `test_cherrystudio_module.py` — 143 项测试, 2,731 行 |

**已实现功能:**

- ✅ `MCPClient`: STDIO JSON-RPC 2.0 通信, 初始化握手, 请求/响应/通知
- ✅ `LLMProviderChain`: 多 Provider 回退链, 自动轮询失败节点
- ✅ `VisionProviderChain`: 图片识别专用 Provider 链
- ✅ `FileProcessor`: 文件内容提取 (文本文件直接读取, 二进制文件 base64)
- ✅ `HTTPClient`: CherryStudio Agent API HTTP 调用, SSE 流式解析
- ✅ Agent 自动发现: 启动时从 `/v1/agents` 获取可用 Agent 列表
- ✅ 会话管理: `SessionHandler` (独立于 CommandModule 的同名类), 按 `session_key` 独立处理
- ✅ 过期会话管理: `SUMMARY_PROMPT` 摘要压缩, `force_stale` 标记, 自动重建
- ✅ `rebuild_session()`: 删除远程会话 + 清理本地处理器
- ✅ 模型偏好集成: 从 `StateManager.get_saved_model()` 读取持久化偏好
- ✅ 活跃 Agent 集成: 从 `StateManager.get_active_agent()` 读取持久化 Agent

### 2.6 ✅ SSEParser — SSE 流式响应解析器

| 属性 | 值 |
|------|------|
| 文件 | `modules/sse_parser.py` |
| 行数 | 516 行 |
| 测试 | `test_sse_parser.py` — 52 项测试, 617 行 |

**已实现功能:**

- ✅ `SSETextBlock`: 文本块 (区分 `is_reasoning` 思考内容 vs `is_tool_result` 工具结果)
- ✅ `SSEToolCall`: 工具调用跟踪 (名称、参数、状态)
- ✅ `SSEResult`: 结构化解析结果 (文本块列表、工具调用列表、停滞标记等)
- ✅ `SSEParser`: 流式解析 aiohttp SSE 响应
- ✅ 输出工具检测: `OUTPUT_TOOL_NAMES` = {`qq_send_message`, `qq_send_image`, `qq_upload_file`}
- ✅ 停滞检测 + 超时保护 + `session_not_found` 错误处理
- ✅ `notify_callback` 回调: 解析过程中向调用方报告状态

### 2.7 ✅ ConversationStore — 会话持久化存储

| 属性 | 值 |
|------|------|
| 文件 | `modules/conversation_store.py` |
| 行数 | 455 行 |
| 测试 | 由 `test_cherrystudio_module.py` 覆盖 |

**已实现功能:**

- ✅ `SessionMeta`: 会话元数据 (session_key, agent_name, created_at, last_active, message_count, force_stale)
- ✅ 按 Agent 分目录存储: `ConversationStore/{agent_name}/{session_key}/`
- ✅ 持久化内容: 会话日志 (`conversation.jsonl`)、元数据 (`meta.json`)、记忆摘要 (`summary.md`)
- ✅ 不活跃会话检测: `STALE_THRESHOLD` 超时自动标记
- ✅ 会话摘要压缩 (`summarize_session`): 读取对话日志 → LLM 摘要 → 写入 summary
- ✅ `reconcile_sessions`: 启动时扫描本地会话目录, 与远程会话列表对齐

### 2.8 ✅ StateManager — 状态管理器

| 属性 | 值 |
|------|------|
| 文件 | `state/manager.py` |
| 行数 | 395 行 |
| 测试 | `test_state_manager.py` — 29 项测试, 468 行 |

**已实现功能:**

- ✅ `SharedState` 数据类: 9 个状态字段 (observers, ob_groups, bot_blacklist, order_whitelist, saved_models, active_agents, modules_enabled, log_blacklist, welcome_config)
- ✅ JSON 持久化: `Temp/shared_state.json`, 自动加载/保存
- ✅ 异步锁保护: 所有写操作在 `asyncio.Lock` 下执行
- ✅ 便捷 API: `add_to_blacklist` / `remove_from_blacklist` / `add_to_whitelist` / `remove_from_whitelist` / `set_active_agent` / `get_active_agent` / `set_saved_model` / `get_saved_model` / `remove_saved_model`
- ✅ 欢迎配置 API: `set_welcome` / `get_welcome` (per-group 持久化)
- ✅ 状态变更通知: `register_change_callback` → `_notify_changes`
- ✅ 旧文件双向合并: `merge_legacy_files` (order_whitelist.json, bot_blacklist.json, log_blacklist.json)
- ✅ 热重载: `reload()` 从文件重新加载状态

### 2.9 ✅ Protocols — 协议定义

| 文件 | 行数 | 说明 |
|------|------|------|
| `protocols/messages.py` | 267 行 | 消息协议定义 |
| `protocols/error_codes.py` | 160 行 | 错误码定义 (26 个) |
| **合计** | **427 行** | |

**messages.py 已定义类型:**

- ✅ `MessageType` 枚举: TEXT, IMAGE, FILE, MIXED, AT, REPLY
- ✅ `MessageSource` 枚举: GROUP, PRIVATE
- ✅ `RawMessage`: 来自 NapCat 的原始消息 (含 image_files, file_infos 扩展字段)
- ✅ `ParsedMessage`: 解析后的消息 (含 `is_command`, `command_name`, `command_args`, `session_key`)
- ✅ `OutgoingMessage`: 待发送消息 (含 `reply_to_msg_id`, `metadata`, `attachments`)
- ✅ `ModuleResponse`: 模块响应 (含 `success_response` / `error_response` 工厂方法)

**error_codes.py 已定义错误码 (26 个):**

| 范围 | 模块 | 数量 | 错误码列表 |
|------|------|------|------------|
| 1000-1999 | NapCat 互联桥 | 6 | BRG-1001 ~ BRG-1006 |
| 2000-2999 | 消息互联桥 | 5 | BRG-2001 ~ BRG-2005 |
| 3000-3999 | 命令模块 | 6 | BRG-3001 ~ BRG-3006 |
| 4000-4999 | CherryStudio 模块 | 9 | BRG-4001 ~ BRG-4009 |
| 5000-5999 | Server 模块 | 5 | BRG-5001 ~ BRG-5005 |
| 9000-9999 | 通用错误 | 4 | BRG-9001 ~ BRG-9004 |

- ✅ `BridgeError` 异常类: 标准化错误码 + 详细信息 + 自定义用户提示 + 异常链

### 2.10 ✅ DiceCore — 骰子核心

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/dice_core/dice_parser.py` | 81 行 | 骰子表达式解析器 |
| `modules/dice_core/character_store.py` | 184 行 | 角色卡存储系统 |
| **合计** | **~265 行** | |

**已实现功能:**

- ✅ `parse_and_roll()`: XdY 骰子表达式解析, 支持加值 (+Z)、重复 (#N)
- ✅ `check_result()`: COC 风格判定 (大成功/大失败/极难/困难/成功/失败)
- ✅ `check_critical_d6()`: 行于泰拉暴击判定 (半数最大值/最小值)
- ✅ `DEFAULT_CARDS`: ark / coc 两套角色卡模板 (deepcopy 防污染)
- ✅ 角色卡 CRUD: `load_or_default`, `save_card`, `delete_card`, `rename_card`
- ✅ 多卡管理: `list_cards`, `get_active_card`, `set_active_card` (最多 5 张)
- ✅ 群组旧格式兼容: `load_group_data()` 自动合并旧数据
- ✅ 技能读写: `set_skill()`, `format_card()` 格式化展示

### 2.11 ✅ Ark TRPG — 行于泰拉插件

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/ark_trpg/skills.py` | 30 行 | 技能→属性映射表 |
| `modules/commands/ark_trpg.py` | ~230 行 | 6 个方舟 TRPG 命令 |
| **合计** | **~260 行** | |

**已实现功能:**

- ✅ `SKILL_TO_ATTR`: ~55 个技能到 6 个基础属性的映射
- ✅ `BASE_ATTRS`: 6 个基础属性列表
- ✅ `.rk` 技能检定: Xd6+属性+技能值, DC 判定, 暴击检测
- ✅ `.rkb` 奖励骰: 额外掷 N 个骰子取最优
- ✅ `.rkp` 惩罚骰: 额外掷 N 个骰子取最差
- ✅ `.sck` 自控检定: Xd10 vs 精神意志 (已修复旧代码 NameError)
- ✅ `.ark` 人物作成: 随机生成 7 属性 + 经济评级 + 社交点数
- ✅ `.sn` 群名片设置: 自动从角色卡生成名片 (已修复旧代码 SyntaxError)

**已修复旧系统 Bug:**

- ✅ `.sck` 的 `will` 未定义 NameError → 从角色卡 `attributes["精神意志"]` 读取
- ✅ `.sck` 的 `result_msg` 未定义 → 补全成功/失败两个分支
- ✅ `.sn` 的 `load_or_default(...) (` 语法错误 → 拆分为赋值 + 字符串构造

### 2.12 ✅ Log — 日志系统

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/commands/log.py` | ~180 行 | 日志命令 + 日志写入逻辑 |
| `modules/hooks/__init__.py` | ~60 行 | HookManager 事件钩子 |
| **合计** | **~240 行** | |

**已实现功能:**

- ✅ `HookManager`: 异步事件钩子注册/分发, 支持 on_message 事件
- ✅ `_log_on_message()`: 群消息自动写入活跃日志, 跳过旁观者
- ✅ `.log new <名称>`: 新建日志并开始记录
- ✅ `.log on`: 从暂停恢复记录
- ✅ `.log off`: 暂停记录
- ✅ `.log end`: 完成记录并导出日志
- ✅ `.log list`: 查看本群日志列表
- ✅ `.log get <名称>`: 查看日志内容 (超过 50 条截断显示)
- ✅ `.log del <名称>`: 删除日志 (不可逆)
- ✅ 日志文件存储: `data/logs/{group_id}/{log_name}.log`

### 2.13 ✅ 公共工具函数

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/commands/utils.py` | ~50 行 | 共享工具函数 |

**已实现功能:**

- ✅ `load_bot_setting()`: 从 BotSettingConfig.json 读取消息模板 (所有命令模块共用)
- ✅ `format_msg()`: 模板格式化 (`{}` → 结果, `<>` → 玩家名称)

### 2.14 ✅ Phase 2 新增模块 — Install URL / HookManager 增强 / 会话校验 / Pydantic 验证

> Phase 2 于 2026-06-08 完成, 新增 5 个文件/模块, 38 项测试。

#### 2.14.1 Install URL 生成器

| 属性 | 值 |
|------|------|
| 文件 | `tools/generate_install_url.py` |
| 行数 | ~100 行 |
| 测试 | `test_phase2.py` — Install URL 相关测试 |

**已实现功能:**

- ✅ 生成 CherryStudio 一键安装链接 (`cherrystudio://mcp/install?servers=...`)
- ✅ `manual` 模式: 使用当前 Python 路径 + server.py (传统模式)
- ✅ `uvx` 模式: 使用 `uvx --from git+https://...` (Git 直装模式)
- ✅ base64 编码 JSON 配置 → 拼接 URL
- ✅ 独立工具, 不依赖运行时模块

#### 2.14.2 HookManager 增强

| 属性 | 值 |
|------|------|
| 文件 | `modules/hooks/__init__.py` |
| 行数 | ~120 行 (从 ~60 行增强) |
| 测试 | `test_phase2.py` — HookManager 增强测试 |

**已实现功能:**

- ✅ 3 种事件类型: `on_message`, `pre_command`, `post_command`
- ✅ 优先级排序: 钩子按 `priority` 参数排序执行 (数值越小越先执行)
- ✅ 过滤函数: `filter_fn` 参数, 按条件选择性触发钩子
- ✅ 集成到 MessageBus: `on_message` 事件在消息分发时触发
- ✅ 集成到 CommandModule: `pre_command` / `post_command` 在命令执行前后触发

#### 2.14.3 会话完整性校验

| 属性 | 值 |
|------|------|
| 文件 | `modules/conversation_store.py` |
| 行数 | ~80 行 (新增方法) |
| 测试 | `test_phase2.py` — validate_sessions 测试 |

**已实现功能:**

- ✅ `validate_sessions()`: 启动时扫描本地会话目录
- ✅ 检测损坏会话: 缺失 meta.json、JSON 解析失败、结构不完整
- ✅ 自动备份: 损坏会话移动到 `_corrupted/` 目录, 不影响正常启动
- ✅ 日志报告: 校验结果写入日志, 便于排查

#### 2.14.4 Pydantic 配置验证

| 属性 | 值 |
|------|------|
| 文件 | `protocols/config_models.py` |
| 行数 | ~100 行 |
| 测试 | `test_phase2.py` — config_models 测试 |

**已实现功能:**

- ✅ Pydantic BaseModel 定义 `config.json` 结构
- ✅ 启动时自动验证配置文件字段类型和必填项
- ✅ 提供清晰的验证错误信息, 便于用户修正配置
- ✅ 替代手动 `.get()` 链式读取

#### 2.14.5 Pydantic 状态验证

| 属性 | 值 |
|------|------|
| 文件 | `state/state_models.py` |
| 行数 | ~60 行 |
| 测试 | `test_phase2.py` — state_models 测试 |

**已实现功能:**

- ✅ Pydantic BaseModel 定义 `shared_state.json` 结构
- ✅ 加载时自动验证状态文件字段类型
- ✅ 与 StateManager 集成, 确保状态数据一致性

### 2.15 ✅ MD-to-Image — Markdown 转图片模块

> 于 2026-06-08 完成, 2026-06-09 v2 增强, 新增 1 个模块 + 43 项测试。

#### 2.15.1 Markdown-to-PNG 转换模块

| 属性 | 值 |
|------|------|
| 文件 | `modules/md_to_image.py` |
| 行数 | ~780 行 |
| 测试 | `test_md_to_image.py` — 43 项测试 |

**已实现功能:**

- ✅ `render_markdown(md_text, css=None, width=800)`: Markdown → HTML 渲染, 内置专业 CSS 主题
- ✅ 三级截图回退策略:
  1. Playwright Chromium (本地 Browsers/ 目录): 通过 `playwright.async_api` 启动本地 Chromium 浏览器截图
  2. html2image (Edge 隔离模式): 调用 `Html2Image(browser_executable=..., custom_flags=["--no-sandbox"])` 使用 Edge 截图
  3. Pillow (纯 Python): ImageDraw/ImageFont 渲染, 支持中文字体 (msyh.ttc, simhei.ttf 等), 自动换行, 内容高度裁剪, 零浏览器依赖
- ✅ `PLAYWRIGHT_BROWSERS_PATH` 环境变量自动设置, 指向项目本地 `Browsers/` 目录, 使 Playwright 在 Cherry Studio 沙盒中可用
- ✅ Markdown 扩展缓存 (`_MD_EXT_CACHE`): 避免重复初始化 `markdown.Markdown` 实例
- ✅ Playwright 启动超时 (30s): 防止浏览器卡死导致系统挂起
- ✅ Pillow 纯 Python 回退方案: ImageDraw/ImageFont 渲染, 支持中文字体, 自动换行, 内容高度裁剪
- ✅ 截图成功后自动清理会话目录 (仅保留 output.png)
- ✅ 修复 `md_to_image_sync` 协程泄漏: 使用闭包 `_run()` 延迟创建协程
- ✅ 支持文本输入 (Markdown 字符串) 和文件输入 (`.md` 文件路径)
- ✅ 自定义 CSS 覆盖, 自定义输出宽度 (默认 800px)
- ✅ 异步 API (`md_to_image()`) + 同步封装 (`md_to_image_sync()`)
- ✅ 依赖: `markdown>=3.5.0` (MD→HTML), `html2image>=2.0.0` (截图回退), `Pillow>=10.0.0` (纯 Python 回退)

#### 2.15.2 send_local_image 方法 (NapCatBridge)

| 属性 | 值 |
|------|------|
| 文件 | `modules/napcat_bridge.py` |
| 新增行数 | ~55 行 |
| 测试 | `test_md_to_image.py` — 4 项 mock 测试 (TestSendLocalImage) |

**已实现功能:**

- ✅ `send_local_image(target, image_path, summary=None)`: 读取本地 PNG/JPG 文件
- ✅ 文件内容 → base64 编码 → OneBot v11 `send_msg` 图片段 (`{"type": "image", "data": {"file": "base64://..."}}`)
- ✅ 支持私聊 (`target_type="private"`) 和群聊 (`target_type="group"`)
- ✅ 可选 `summary` 文本 (图片附带的文字说明)

#### 2.15.3 qq_upload_file MCP 工具增强 (as_image 参数)

| 属性 | 值 |
|------|------|
| 文件 | `server.py` |
| 修改行数 | ~30 行 |
| 测试 | `test_md_to_image.py` — 2 项测试 (TestQqUploadFileAsImage) |

**已实现功能:**

- ✅ 新增 `as_image: bool` 参数 (默认 `False`)
- ✅ 当 `as_image=True` 时: 将 `content` 视为 Markdown 文本, 通过 `md_to_image` 渲染为 PNG, 再通过 `send_local_image` 作为内联图片发送
- ✅ 支持文本内容 (直接 Markdown 字符串) 和 `.md` 文件路径 (自动读取文件内容)
- ✅ 原始文件上传行为完全不变 (`as_image=False` 为默认值)

---

## 3. 已实现命令 (✅ 24/24)

所有命令已实现, 分为三个模块: 内置管理 (`builtin.py`), 骰子 (`dice.py`), 方舟 TRPG (`ark_trpg.py`), 日志 (`log.py`)。

> **2026-06-07 命令系统整改完成:** 全部 11 项整改已实施, 包括 `_format_msg()` 模板格式化、HelpCommand 重写 (标准结构输出 + 单命令详细帮助)、`.model` 管理员权限检查、`.send` 简化格式、统一未知子命令响应、所有命令 `_sub_help()` / `group` / `usage` / `reminder` 属性完善、BotSettingConfig 模板集成扩展、`.model list` 动态模型列表。测试修复 6 项, 全部 367 项通过。
>
> **2026-06-08 插件系统移植完成:** 从旧系统移植骰子系统 (8 命令)、方舟 TRPG (6 命令)、日志系统 (1 命令 7 子操作)。修复旧代码 3 个已知 Bug (.sck NameError, .sck result_msg 未定义, .sn SyntaxError)。新增 82 项测试, 总计 486 项全部通过。

### 内置管理命令 (9 个)

| 状态 | 命令 | 说明 | 所在类 | 行数 |
|------|------|------|--------|------|
| ✅ | `.help` | 显示所有可用命令 (标准结构输出), `.help <命令名>` 查看单命令详细帮助 | `HelpCommand` | ~30 |
| ✅ | `.bot on/off/status/orderwhite` | 机器人开关 + 状态查询 + 免@切换; 空参 → `_build_greeting()` 显示版本/欢迎; BotSettingConfig 消息定制 | `BotCommand` | ~43 |
| ✅ | `.order` | 会话/Agent 管理 (切换/列表/重建/状态) + 免@白名单管理, group="会话管理" | `OrderCommand` | ~100 |
| ✅ | `.model list/change/status/reset` | 模型偏好管理; list/status 全员可用, change/reset 需管理员权限; 动态模型列表 (`llm_providers`) | `ModelCommand` | ~45 |
| ✅ | `.ob join/exit/list/clr/on/off` | 旁观者模式完整管理; 空参 → 自动 join; BotSettingConfig 模板集成 | `ObCommand` | ~68 |
| ✅ | `.dismiss <后四位>` | 管理员退群 (群号末4位匹配 + 退群告别 + 数据清理) | `DismissCommand` | ~75 |
| ✅ | `.send <消息>` | 简化格式发送给 Master; 完整格式 `.send <type> <id> <msg>` 管理员消息转发 | `SendCommand` | ~35 |
| ✅ | `.master <子命令>` | 管理员专用: LLMReset / AllResetAgent / OnlyResetAgent | `MasterCommand` | ~43 |
| ✅ | `.welcome open/close/set/status` | 新成员欢迎设置 (支持 `{at}` 占位符, 持久化) | `WelcomeCommand` | ~57 |

### 骰子命令 (8 个) — `modules/commands/dice.py`

| 状态 | 命令 | 说明 | 所在类 | group |
|------|------|------|--------|-------|
| ✅ | `.r` | 骰子投掷 (3d6, d100, 3d6+2, n#重复, DC判定) | `RDiceCommand` | 骰子 |
| ✅ | `.rh` | 暗骰 (结果私聊发送, 旁观者转发) | `RhCommand` | 骰子 |
| ✅ | `.ra` | d100 技能/属性检定 (COC 规则, 从角色卡读取) | `RaCommand` | 骰子 |
| ✅ | `.show` | 展示角色卡 (ark/coc 双系统) | `ShowCommand` | 骰子 |
| ✅ | `.del` | 删除角色卡或技能 | `DelCommand` | 骰子 |
| ✅ | `.pc` | 角色卡管理 (list/switch/new/del, 5 卡上限) | `PcCommand` | 骰子 |
| ✅ | `.nn` | 重命名角色卡 | `NnCommand` | 骰子 |
| ✅ | `.st` | 设置属性/技能值 (支持紧凑格式 "力量5敏捷3") | `StCommand` | 骰子 |

### 方舟 TRPG 命令 (6 个) — `modules/commands/ark_trpg.py`

| 状态 | 命令 | 说明 | 所在类 | group |
|------|------|------|--------|-------|
| ✅ | `.rk` | 行于泰拉技能检定 (Xd6+属性+技能值, DC判定, 暴击) | `RkCommand` | 行于泰拉 |
| ✅ | `.rkb` | 技能检定 (奖励骰, 额外掷 N 骰取最优) | `RkbCommand` | 行于泰拉 |
| ✅ | `.rkp` | 技能检定 (惩罚骰, 额外掷 N 骰取最差) | `RkpCommand` | 行于泰拉 |
| ✅ | `.sck` | 自控检定 (Xd10 vs 精神意志) | `SckCommand` | 行于泰拉 |
| ✅ | `.ark` | 泰拉人物作成 (掷 7 属性 + 经济 + 社交) | `ArkCommand` | 行于泰拉 |
| ✅ | `.sn` | 设置群名片模板 (自动从角色卡生成) | `SnCommand` | 行于泰拉 |

### 日志命令 (1 个, 7 子操作) — `modules/commands/log.py`

| 状态 | 命令 | 说明 | 所在类 | group |
|------|------|------|--------|-------|
| ✅ | `.log new <名称>` | 新建日志并开始记录 | `LogCommand` | 日志 |
| ✅ | `.log on` | 继续记录 (从暂停恢复) | `LogCommand` | 日志 |
| ✅ | `.log off` | 暂停记录 | `LogCommand` | 日志 |
| ✅ | `.log end` | 完成记录并导出日志文件 | `LogCommand` | 日志 |
| ✅ | `.log list` | 查看本群日志列表 | `LogCommand` | 日志 |
| ✅ | `.log get <名称>` | 查看日志内容 | `LogCommand` | 日志 |
| ✅ | `.log del <名称>` | 删除日志 (不可逆) | `LogCommand` | 日志 |

### BotSettingConfig 消息模板集成

所有命令均支持从 `Configuration/BotSettingConfig.json` 读取可定制消息模板, 通过 `_format_msg()` 函数统一格式化 (`{}` → 命令结果, `<>` → 玩家名称):

```json
{
  "BuiltInOrder": { "bot_on_message", "bot_off_message", "bot_orderwhite_message", "dismiss_message" },
  "dice_core": { "r_message", "ra_message", "st_message", "del_card_message", "nn_message" },
  "arktrpg": { "rk_message", "rkb_message", "rkp_message", "sck_message", "ark_message", "sn_message" },
  "ob": { "ob_join_message", "ob_list_message" },
  "log": { "log_new_message", "log_list_message" }
}
```

> **已集成:** 全部模板已通过 `format_msg()` 接入命令系统:
> - `BuiltInOrder`: `bot_on_message`, `bot_off_message`, `bot_orderwhite_message`, `dismiss_message`
> - `ob`: `ob_join_message`, `ob_list_message`
> - `dice_core`: `r_message`, `ra_message`, `st_message`, `del_card_message`, `nn_message`, `show_message`
> - `arktrpg`: `rk_message`, `rkb_message`, `rkp_message`, `sck_message`, `ark_message`, `sn_rk_message`
> - `log`: `log_new_message`, `log_list_message`

---

## 4. MCP 工具清单 (✅ 12/12)

所有 MCP 工具在 `server.py` 的 `_register_mcp_tools()` 中注册。

| 状态 | 工具名称 | 功能说明 |
|------|----------|----------|
| ✅ | `qq_send_message` | 发送文本消息 (自动转文档, 断线重试) |
| ✅ | `qq_send_image` | 发送图片 (HTTP/HTTPS URL) |
| ✅ | `qq_upload_file` | 上传文件 (文本内容或本地路径, 断线重试); `as_image=True` 时将 Markdown 渲染为 PNG 内联图片发送 |
| ✅ | `qq_get_recent_messages` | 获取本地缓存的最近消息 |
| ✅ | `qq_get_group_msg_history` | 从 QQ 服务器拉取群历史消息 |
| ✅ | `qq_get_group_list` | 获取群列表 |
| ✅ | `qq_get_friend_list` | 获取好友列表 |
| ✅ | `qq_get_group_members` | 获取群成员列表 |
| ✅ | `qq_get_user_info` | 获取用户信息 |
| ✅ | `qq_get_recent_contacts` | 获取最近活跃会话 |
| ✅ | `qq_check_status` | 检查 NapCat 连接状态 |
| ✅ | `qq_recall_message` | 撤回机器人发送的消息 |
| ⛔ | `qq_confirm_response` | ~~确认响应~~ — **设计取消** (由 `mark_responding` 自动机制替代) |

---

## 5. 未实现功能 (❌ 待移植)

以下功能在旧系统中存在, 尚未移植到 v3.0 新架构。Phase 1 (骰子/TRPG/日志/EventHooks/错误码) 及 Phase 2 (Install URL/HookManager 增强/会话校验/Pydantic/MD-to-Image) 均已完成。

### 5.1 ✅ 骰子系统 (Dice System) — 已完成

已于 2026-06-08 完成移植。详见 §2.10, §2.11。

### 5.2 ✅ 明日方舟 TRPG 插件 (Ark TRPG) — 已完成

已于 2026-06-08 完成移植, 含 3 个旧 Bug 修复。详见 §2.11。

### 5.3 ✅ 日志系统 (Log System) — 已完成

已于 2026-06-08 完成移植, 含 HookManager 事件钩子。详见 §2.12。

### 5.4 ⛔ MCP 确认响应工具 — 设计取消

`qq_confirm_response` 已在 v3.0 设计中**主动取消**，不属于缺失功能。旧系统中该工具用于让 AI 确认收到消息但不立即回复，但此设计定位不明（最终还是需要调用 `qq_send_message`）。v3.0 改为 Bridge 内部的 `mark_responding`/`unmark_responding` 自动机制，在 SSE 处理期间自动标记活跃目标，无需 Agent 显式调用。

### 5.5 ✅ Install URL 生成器 — 已完成 (2026-06-08)

已实现 `tools/generate_install_url.py` (~100 行), 支持 manual + uvx 两种模式。详见 §2.14。

### 5.6 ✅ 增强功能 — 部分已完成 (2026-06-08)

**估计工作量:** ~350 行代码

| 功能 | 说明 | 优先级 | 状态 |
|------|------|--------|------|
| ✅ `validate_sessions` | 启动时会话完整性校验 (扫描本地目录, 备份损坏会话) | 中 | ✅ 已完成 (2026-06-08) |
| ✅ `EventHooks` 增强 | 扩展为 3 种事件类型 (on_message, pre_command, post_command) + 优先级排序 + 过滤函数 | 中 | ✅ 已完成 (2026-06-08) |
| ❌ PID 文件锁 | 当前已禁用 (兼容 CherryStudio), 可能需要条件启用 | 低 | 待定 |

> **注:** `EventHooks` 基础版 (on_message) 已在 §2.12 实现。增强版 (3 事件类型 + 优先级 + 过滤函数) 已在 Phase 2 完成, 详见 §2.14。

### 5.7 ✅ 子系统错误码 — 已完成

已于 2026-06-08 新增 14 个错误码 (6000-8999 范围)。详见 `protocols/error_codes.py`。

### 5.8 ✅ Pydantic 集成 — 已完成 (2026-06-08)

已实现配置验证和状态验证:

- `protocols/config_models.py` (~100 行): Pydantic 配置验证, 启动时验证 `config.json`
- `state/state_models.py` (~60 行): Pydantic 状态验证, 加载时验证 `shared_state.json`

详见 §2.14。

---

## 6. 测试覆盖分析

### 测试文件统计

| 测试文件 | 测试数 | 行数 | 覆盖模块 |
|----------|--------|------|----------|
| `test_cherrystudio_module.py` | 143 | 2,731 | CherryStudioModule, MCPClient, LLMProviderChain, VisionProviderChain, FileProcessor |
| `test_dice_ark_log.py` | 90 | ~780 | DiceCore, CharacterStore, ArkSkills, HookManager, 15 个命令, utils |
| `test_conversation_store.py` | 54 | ~450 | ConversationStore (会话 CRUD, 消息管理, 记忆, 映射, 过期检测, 归档, 远程 session_id, 校验) |
| `test_command_module.py` | 52 | 917 | CommandModule, CommandRegistry, SessionHandler, CommandContext |
| `test_napcat_bridge.py` | 56 | 906 | NapCatBridge, MessageBuffer |
| `test_sse_parser.py` | 52 | 617 | SSEParser, SSETextBlock, SSEToolCall, SSEResult |
| `test_integration_flow.py` | 35 | ~430 | 端到端消息流, 骰子/TRPG/日志集成, 跨系统联动, HookManager, 过滤器链, 热重载 |
| `test_state_manager.py` | 29 | 468 | StateManager, SharedState, merge_legacy_files |
| `test_message_bus.py` | 25 | 544 | MessageBus, BlacklistFilter, ModuleEnabledFilter |
| `test_phase2.py` | 38 | ~700 | InstallURL, HookManager 增强, validate_sessions, config_models, state_models |
| `test_md_to_image.py` | 43 | 614 | md_to_image (渲染+截图+三级回退), send_local_image, qq_upload_file as_image |
| `test_server.py` | 10 | 233 | Server (初始化, 配置加载, MCP 注册) |
| `conftest.py` | — | ~100 | 共享 fixtures (state_manager, 消息工厂, CommandContext 工厂, DATA_DIR 隔离) |
| **合计** | **664** | **~10,600** | **全部模块 + Phase 2 + Phase 3 集成测试** |

### 测试质量指标

- **代码测试比**: ~10,600 / ~12,200 = **86.9%**
- **平均每模块测试数**: 664 / 16 = **41.5 项/模块**
- **最大测试文件**: `test_cherrystudio_module.py` (143 项, 占总测试的 21.5%)
- **最小测试文件**: `test_server.py` (10 项, 占总测试的 1.5%)

### 缺失测试 (随未实现功能一同补充)

| 待测模块 | 预估测试数 | 说明 |
|----------|-----------|------|
| ~~Install URL~~ | ~~10 项~~ | ✅ 已完成 (Phase 2, 含于 test_phase2.py) |
| ~~HookManager 增强~~ | ~~10 项~~ | ✅ 已完成 (Phase 2, 含于 test_phase2.py) |
| ~~集成测试 (Phase 3)~~ | ~~40 项~~ | ✅ 已完成 (35 项集成 + 54 项 ConversationStore) |
| **合计** | **~16 项** | 性能基准测试、文档相关测试 |

---

## 7. 已知问题

### 7.1 代码层面

| 问题 | 严重程度 | 说明 |
|------|----------|------|
| ~~pydantic 声明但未使用~~ | ✅ 已修复 | Phase 2 已集成: `config_models.py` (配置验证) + `state_models.py` (状态验证), 启动/加载时自动校验 |
| PID 锁已禁用 | 低 | `_check_singleton()` 和 `_cleanup_pid_file()` 均为空实现。这是为兼容 CherryStudio 频繁重启 MCP 服务器的刻意设计, 但可能导致手动运行时出现多实例 |
| `.model list` 已改为动态列表 | ✅ 已修复 | `ModelCommand` 的 `list` 子命令现从 `ctx.config.get("llm_providers", [])` 动态读取配置 |

### 7.2 旧系统 Bug (已修复)

| Bug | 位置 | 状态 | 修复说明 |
|-----|------|------|----------|
| `.sck` NameError | `Old/Plugins/ark_trpg/commands.py` ~215 行 | ✅ 已修复 | 从角色卡读取 `attributes["精神意志"]` 作为 `will` 值 |
| `.sck` result_msg 未定义 | `Old/Plugins/ark_trpg/commands.py` ~216 行 | ✅ 已修复 | 补全成功/失败两个分支的 `result_msg` |
| `.sn` 语法错误 | `Old/Plugins/ark_trpg/commands.py` ~276 行 | ✅ 已修复 | 拆分为 `char = load_or_default(...)` + `card = f"..."` 两步 |

### 7.3 `<>` 占位符未替换 Bug (已修复)

| Bug | 位置 | 状态 | 修复说明 |
|-----|------|------|----------|
| `.st` 命令 `<>` 占位符未替换 | `dice.py` (2处), `ark_trpg.py` (4处) | ✅ 已修复 | `load_bot_setting()` 直接返回未调用 `format_msg()`，已包裹 `format_msg()` 并传入 `player_name` |
| `char.get("name", fallback)` 空串回退 | `dice.py`, `ark_trpg.py` (共 6 处) | ✅ 已修复 | `DEFAULT_CARDS["ark"]["name"]` 为空串时 `dict.get()` fallback 不生效，改用 `or` 模式 |
| `builtin.py` 重复定义 | `modules/commands/builtin.py` | ✅ 已修复 | `_load_bot_setting` / `_format_msg` 改为从 `utils.py` 导入，消除重复代码 |

> 新增 8 个回归测试覆盖上述修复，总计 564 用例。

### 7.4 架构待优化

| 项目 | 说明 |
|------|------|
| ~~ConversationStore 缺少独立测试~~ | ✅ 已修复 (Phase 3, `test_conversation_store.py` — 54 项测试) |
| ~~命令模块缺少集成测试~~ | ✅ 已修复 (Phase 3, `test_integration_flow.py` — 35 项测试) |
| BotSettingConfig 读取无缓存 | `_load_bot_setting()` 每次调用都读取磁盘文件, 高频命令 (如 `.r`) 可能有性能影响 |
| `.st` 单属性紧凑格式不生效 | `StCommand.handle()` 中 `len(parsed) >= 2` 导致单个紧凑属性 (如 `.st 力量5`) 走查询路径而非设置路径，应改为 `>= 1` |

---

## 8. 工作量汇总

### 待实现功能工作量估计

| 子系统 | 估计代码量 | 估计测试量 | 依赖 | 优先级 | 难度 |
|--------|-----------|-----------|------|--------|------|
| ✅ 骰子系统 | ~450 行 (实际) | 82 项 (含 ark+log) | — | ~~🔴 高~~ ✅ 已完成 | 中 |
| ✅ Ark TRPG | ~260 行 (实际) | 含上 | 骰子系统 | ~~🔴 高~~ ✅ 已完成 | 高 |
| ✅ 日志系统 | ~240 行 (实际) | 含上 | EventHooks | ~~🟡 中~~ ✅ 已完成 | 中 |
| ✅ EventHooks 基础 | 含在日志系统 | 含上 | — | ~~🟡 中~~ ✅ 已完成 | 低 |
| ✅ 子系统错误码 (14个) | ~30 行 (实际) | — | — | ~~🟢 低~~ ✅ 已完成 | 低 |
| ✅ Install URL 生成器 | ~100 行 (实际) | 含下 | 无 | ~~🟢 低~~ ✅ 已完成 | 低 |
| ✅ validate_sessions | ~80 行 (实际) | 含下 | 无 | ~~🟡 中~~ ✅ 已完成 | 低 |
| ✅ EventHooks 增强 | ~60 行 (实际增量) | 含下 | 无 | ~~🟡 中~~ ✅ 已完成 | 低 |
| ✅ Pydantic 集成 | ~160 行 (实际) | 38 项 (test_phase2) | 无 | ~~🟡 中~~ ✅ 已完成 | 中 |
| ✅ MD-to-Image 模块 | ~780 行 (实际) | 43 项 (test_md_to_image) | markdown, html2image, Pillow | ~~🟢 低~~ ✅ 已完成 | 中 |
| ❌ 集成测试 (Phase 3) | — | ~40 项 | 全部 | 🟡 中 | 高 |
| ❌ 性能优化 (Phase 3) | — | — | 全部 | 🟢 低 | 中 |
| ❌ 文档更新 (Phase 3) | — | — | — | 🟢 低 | 低 |
| **合计 (Phase 3 剩余)** | — | **~40 项** | | | |

### 总项目规模

| 指标 | Phase 1 前 | Phase 1 后 | Phase 2 后 | 当前 (MD-to-Image 后) | Phase 3 完成后 (预估) |
|------|-----------|-----------|------------|----------------------|----------------------|
| 核心代码 | 9,053 行 | ~10,700 行 | ~11,160 行 | ~12,200 行 | ~12,200 行 |
| 测试代码 | 6,419 行 | ~7,096 行 | ~7,900 行 | ~9,617 行 | ~10,000 行 |
| 测试用例 | 367 项 | 486 项 | 532 项 | 575 项 | ~615 项 |
| 命令数 | 9 | 24 | 24 | 24 | 24 |
| MCP 工具 | 12 | 12 | 12 | 12 | 12 |
| 错误码 | 26 | 40 | 40 | 40 | ~50 |
| 代码测试比 | 70.9% | 66.3% | 70.8% | 78.8% | ~82.0% |

### 实施进度

```
✅ 第一阶段 (核心玩法) — 已完成 (2026-06-08):
  1. ✅ 骰子系统 (dice_parser + character_store + 8 个命令)
  2. ✅ Ark TRPG (skills + 6 个命令, 修复 3 个旧 Bug)
  3. ✅ EventHooks 基础 (HookManager + on_message)
  4. ✅ 日志系统 (.log 命令 + on_message 钩子)
  5. ✅ 子系统错误码 (14 个新增)

✅ 第二阶段 (增强优化) — 已完成 (2026-06-08):
  6. ✅ Install URL 生成器 (manual + uvx 模式)
  7. ✅ validate_sessions 启动校验 (损坏会话自动备份)
  8. ✅ EventHooks 增强 (3 种事件类型 + 优先级排序 + 过滤函数)
  9. ✅ Pydantic 集成 (config_models + state_models)
  10. ✅ MD-to-Image 模块 (Markdown→PNG, send_local_image, qq_upload_file as_image)

❌ 第三阶段 (测试稳定, 预计 2-3 天):
  11. ✅ 集成测试 (test_integration_flow.py: 35 项 + test_conversation_store.py: 54 项)
  12. ❌ 性能优化
  13. ❌ 文档更新
```

---

## 变更记录

### 2026-06-09 — Phase 3 集成测试补全

完成 Phase 3 测试工作，新增 89 个测试用例，全部 664 测试通过，零失败。

**新增文件:**

- `tests/conftest.py` — 共享 fixtures（state_manager、make_raw_message、make_parsed_message、make_command_context、temp_data_dir）
- `tests/test_conversation_store.py` — ConversationStore 独立测试 54 项（此前零覆盖），涵盖 Session CRUD、消息管理、Memory、Mapping、Stale 检测、Summarize/Archive、Remote Session ID、Invalidate、Reconcile、Validate
- `tests/test_integration_flow.py` — 端到端集成测试 35 项，涵盖命令流、骰子、Ark TRPG、日志、跨系统、HookManager、Filter 链、SendResponse、命令热重载

**关键发现:**

- BlacklistFilter 不在 `_passes_filters()` 链中，而是作为 `_blacklist_filter` 单独检查（仅对非命令消息生效），属于设计意图
- `.st 力量5` 单属性紧凑格式存在 Bug（`len(parsed) >= 2` 导致落入查询路径），已记录至已知问题
- CommandModule 的属性名为 `self.registry`（非 `command_registry`）

**指标变化:**

- 测试总数: 575 → 664（+89）
- 测试文件: 10 → 13 + conftest.py
- 代码测试比: 78.8% → 86.9%
- 总体完成度: 97% → 99%

### 2026-06-09 — MD-to-Image v2 增强 + 环境修复

对 md_to_image 模块进行全面增强，解决 Cherry Studio 沙盒环境依赖问题。

**核心变更:**

- 三级截图回退链重构: Playwright Chromium (本地 Browsers/ 目录) → html2image (Edge 隔离模式) → Pillow (纯 Python, 零浏览器依赖)
- 新增 `PLAYWRIGHT_BROWSERS_PATH` 环境变量自动设置, 指向项目本地 `Browsers/` 目录, 使 Playwright 在 Cherry Studio 沙盒中可用
- 新增 Pillow 纯 Python 回退方案: ImageDraw/ImageFont 渲染, 支持中文字体 (msyh.ttc, simhei.ttf 等), 自动换行, 内容高度裁剪
- 新增 Markdown 扩展缓存 (`_MD_EXT_CACHE`), 避免重复初始化
- Playwright 启动超时 (30s), 防止浏览器卡死导致系统挂起
- 修复 `md_to_image_sync` 协程泄漏: 使用闭包 `_run()` 延迟创建协程
- 截图成功后自动清理会话目录 (仅保留 output.png)

**环境修复:**

- `pyproject.toml` 新增 `Pillow>=10.0.0` 依赖
- venv 中补装缺失的 `playwright` 和 `Pillow` 包
- `Install/setup_env.py` 更新: `_venv_run` 支持 `env_extra` 参数, `install_playwright` 安装到本地 `Browsers/` 目录

**项目新增:**

- `Browsers/` 目录 (~680MB): 内置 Playwright Chromium 浏览器 (chromium-1223 + chromium_headless_shell-1223)
- Cherry Studio 沙盒完全兼容: Playwright + Pillow 均可在隔离环境中正常工作

**测试更新:**

- `test_md_to_image.py` 从 32 项增加到 43 项 (新增 11 项: html2image 回退、三级回退链、会话目录清理、扩展缓存、同步封装)
- `test_all_fail_raises_runtime_error` 更新: 覆盖三级回退 (Playwright + html2image + Pillow 全部 mock 失败)

**测试结果: 575 passed, 0 failed (新增 11 项, 原有 564 项无回归)**

### 2026-06-08 — MD-to-Image 模块完成

新增 Markdown-to-PNG 转换模块, 支持将 Markdown 内容渲染为专业样式图片发送至 QQ。

**新增文件:**

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/md_to_image.py` | ~220 | Markdown-to-PNG 转换模块 (三级截图回退) |
| `tests/test_md_to_image.py` | ~450 | 32 项综合测试 |

**修改文件:**

- `modules/napcat_bridge.py`: 新增 `send_local_image()` 方法 (~55 行) — 本地 PNG/JPG → base64 → OneBot 图片段
- `server.py`: `qq_upload_file` MCP 工具新增 `as_image: bool` 参数 (~30 行) — Markdown 渲染为 PNG 内联图片
- `pyproject.toml`: 新增依赖 `html2image>=2.0.0`, `markdown>=3.5.0`

**实现内容:**

- `md_to_image.py`: Markdown → HTML 渲染 (markdown 库 + 专业 CSS 主题), 三级截图回退策略 (CDP → CLI `--screenshot` → html2image 库)
- 浏览器检测: 优先使用 Edge (`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`)
- 支持文本输入 (Markdown 字符串) 和文件输入 (`.md` 路径), 自定义 CSS, 自定义宽度
- `send_local_image()`: 读取本地图片 → base64 → OneBot send_msg, 支持私聊/群聊
- `qq_upload_file` 增强: `as_image=True` 时 Markdown 渲染为 PNG 发送, 原行为不变

**测试覆盖 (32 项):**

- TestRenderMarkdown: 16 项 HTML 渲染测试 (标题/列表/代码块/表格/嵌套结构等)
- TestFindBrowser: 2 项浏览器检测测试
- TestScreenshotCli: 1 项 CLI 截图测试
- TestMdToImage: 7 项全链路集成测试
- TestSendLocalImage: 4 项 send_local_image mock 测试
- TestQqUploadFileAsImage: 2 项 qq_upload_file as_image 测试

**测试结果: 564 passed, 0 failed (新增 32 项, 原有 532 项无回归)**

### 2026-06-08 — `<>` 占位符未替换 Bug 修复

修复 `.st` 命令及相关命令中 `<>` 占位符未被替换为玩家名称的问题。

**问题根因:**

- `load_bot_setting()` 返回模板字符串后直接返回，未调用 `format_msg()` 进行占位符替换
- `char.get("name", fallback)` 在 `DEFAULT_CARDS["ark"]["name"]` 为空字符串 `""` 时，`dict.get()` 的 fallback 不生效 (空串非 `None`，不触发默认值)

**修复内容:**

| 文件 | 修复项 | 说明 |
|------|--------|------|
| `modules/commands/dice.py` | 2 处 | `format_msg()` 包裹 + `player_name` 传入; `char.get("name") or fallback` 替代 `dict.get` 默认值 |
| `modules/commands/ark_trpg.py` | 4 处 | 同上，覆盖 `.rk` / `.rkb` / `.rkp` / `.sn` 等命令 |
| `modules/commands/builtin.py` | 去重 | `_load_bot_setting` / `_format_msg` 改为从 `utils.py` 导入，移除重复定义 |

**修复范围:**

- `.st` 命令 `<>` 占位符未替换 — `load_bot_setting()` 直接返回未调用 `format_msg()`，已包裹 `format_msg()` 并传入 `player_name`
- `char.get("name", fallback)` 空字符串回退 — `DEFAULT_CARDS["ark"]["name"]` 为空串时 fallback 不生效，改用 `or` 模式 (6 处)
- `builtin.py` 去重: `_load_bot_setting` / `_format_msg` 改为从 `utils.py` 导入

**测试结果: 532 passed, 0 failed (新增 8 项回归测试, 原有 524 项无回归)**

### 2026-06-08 — Phase 2 增强功能完成

完成 Install URL 生成器、HookManager 增强、会话完整性校验、Pydantic 配置/状态验证。

**新增文件:**

| 文件 | 行数 | 说明 |
|------|------|------|
| `tools/__init__.py` | 1 | 工具包 |
| `tools/generate_install_url.py` | ~100 | Install URL 生成器 (manual + uvx 模式) |
| `protocols/config_models.py` | ~100 | Pydantic 配置验证 (config.json 结构校验) |
| `state/state_models.py` | ~60 | Pydantic 状态验证 (shared_state.json 结构校验) |
| `tests/test_phase2.py` | ~700 | 38 项 Phase 2 综合测试 |

**修改文件:**

- `modules/hooks/__init__.py`: HookManager 增强 — 优先级排序、过滤函数 (`filter_fn`)、3 种事件类型 (`on_message`, `pre_command`, `post_command`)
- `modules/conversation_store.py`: 新增 `validate_sessions()` 方法 (~80 行) — 启动时扫描并备份损坏会话
- `modules/message_bus.py`: 集成 HookManager `on_message` 事件
- `modules/command_module.py`: 集成 HookManager `pre_command` / `post_command` 事件

**实现内容:**

- Install URL 生成器 (manual + uvx 模式): 生成 CherryStudio 一键安装链接, base64 编码 JSON 配置拼接 URL
- HookManager 增强: 优先级排序 (数值越小越先执行)、过滤函数 (`filter_fn` 按条件触发)、3 种事件类型 (`on_message`, `pre_command`, `post_command`)
- 钩子系统集成到 MessageBus (`on_message`) 和 CommandModule (`pre/post_command`)
- 会话完整性校验 `validate_sessions()`: 启动时自动扫描本地会话, 检测损坏会话 (缺失 meta.json / JSON 解析失败 / 结构不完整), 自动备份到 `_corrupted/` 目录
- Pydantic 配置验证 (`config_models.py`): 启动时验证 `config.json` 字段类型和必填项
- Pydantic 状态验证 (`state_models.py`): 加载时验证 `shared_state.json` 字段类型

**测试结果: 524 passed, 0 failed (新增 38 项, 原有 486 项无回归)**

### 2026-06-08 — Phase 1 插件系统移植完成

从旧系统移植骰子系统、方舟 TRPG、日志系统到 v3.0 新架构。

**新增文件:**

| 文件 | 行数 | 说明 |
|------|------|------|
| `modules/dice_core/__init__.py` | 1 | 骰子核心包 |
| `modules/dice_core/dice_parser.py` | 81 | 骰子表达式解析器 (直接移植) |
| `modules/dice_core/character_store.py` | 184 | 角色卡存储 (DATA_DIR 改为项目根, deepcopy 修复) |
| `modules/ark_trpg/__init__.py` | 1 | 方舟 TRPG 包 |
| `modules/ark_trpg/skills.py` | 30 | 技能→属性映射表 (直接移植) |
| `modules/commands/dice.py` | ~350 | 8 个骰子命令 (适配新 Command 接口) |
| `modules/commands/ark_trpg.py` | ~230 | 6 个方舟命令 (修复 3 个旧 Bug) |
| `modules/commands/log.py` | ~180 | 日志命令 + 日志写入 (7 子操作) |
| `modules/commands/utils.py` | ~50 | 公共工具函数 (load_bot_setting, format_msg) |
| `modules/hooks/__init__.py` | ~60 | HookManager 事件钩子 |
| `tests/test_dice_ark_log.py` | ~680 | 82 项综合测试 |

**修改文件:**

- `modules/command_module.py`: `discover_builtin()` 从 9 → 24 个命令
- `modules/commands/__init__.py`: 导出新增的 15 个命令类
- `modules/commands/builtin.py`: HelpCommand `_GROUP_ORDER` 和 `_group_desc` 扩展
- `protocols/error_codes.py`: 新增 14 个错误码 (6000-8999)
- `Configuration/BotSettingConfig.json`: 无需修改 (模板已预留)

**修复旧系统 Bug (3 项):**

- `.sck` `will` 变量未定义 → 从角色卡 `attributes["精神意志"]` 读取
- `.sck` `result_msg` 成功分支未定义 → 补全逻辑
- `.sn` `load_or_default() (` 语法错误 → 拆分为两步赋值

**新增错误码 (14 个):**

- BRG-6001 ~ BRG-6006: 骰子系统 (无效表达式, 角色卡不存在/已满, 保存/加载失败)
- BRG-7001 ~ BRG-7003: 方舟 TRPG (格式错误, 技能值无效, 名片设置失败)
- BRG-8001 ~ BRG-8005: 日志系统 (不存在, 已存在, 无活跃日志, 写入/删除失败)

**测试结果: 486 passed, 0 failed (新增 82 项, 原有 404 项无回归)**

### 2026-06-07 — 命令系统整改完成

完成 `COMMAND_REMEDIATION_REPORT.md` 中全部 11 项整改:

**代码变更 (`modules/commands/builtin.py`):**

- 新增 `_format_msg()` 模板格式化函数 (`{}` → 命令结果, `<>` → 玩家名称)
- `.bot` 命令: 移除群聊限制, 空参 → `_build_greeting()` 显示版本/欢迎信息, 新增 `_sub_help()`, group 从 "群管理" 改为 "会话管理"
- `.model change/reset`: 新增管理员权限检查 (`list`/`status` 对所有人开放)
- `.model list`: 从 `ctx.config.get("llm_providers", [])` 动态读取模型列表
- `.send`: 简化格式 `.send <消息>` 直接发送给 Master
- 全部命令: 统一未知子命令响应格式 (引导至 `.help`), 新增 `_sub_help()` 方法, 完善 `group` / `usage` / `reminder` 属性
- `.ob`: 空参 → 自动执行 join (与旧系统一致)
- BotSettingConfig 集成扩展: `.bot orderwhite`, `.ob join/list` 通过 `_format_msg()` 读取模板

**帮助系统重写:**

- HelpCommand 重构: 新增 `_full_help()`, `_cmd_detail()`, `_group_desc()` 方法
- 标准结构输出: `----\n[问候语]\n-模块：描述-\n  .cmd：desc：usage\n----`
- 支持 `.help <命令名>` 查看单命令详细帮助

**测试修复 (6 项):**

- `test_bot_on/off`: 改为验证状态变更 (非特定文本)
- `test_model_change/reset`: 新增 `config={"admin_qq": "987654321"}` 管理员配置
- `test_model_list`: 灵活断言适配空 `llm_providers`
- `test_order_unknown_subcommand`: "未知指令" → "未知子命令"

**测试结果: 367 passed, 0 failed**

---

## 附录: 项目文件结构

```
qq-mcp-bridge/
├── server.py                          # ✅ 系统核心 (~1,170 行, 含 qq_upload_file as_image)
├── pyproject.toml                     # ✅ 项目配置
│
├── modules/
│   ├── napcat_bridge.py               # ✅ NapCat 互联桥 (~1,540 行, 含 send_local_image)
│   ├── message_bus.py                 # ✅ 消息互联桥 (332 行)
│   ├── command_module.py              # ✅ 命令模块 (430 行, 24 命令注册)
│   ├── cherrystudio_module.py         # ✅ CherryStudio 集成 (3,175 行)
│   ├── sse_parser.py                  # ✅ SSE 解析器 (516 行)
│   ├── md_to_image.py                # ✅ Markdown 转图片 (~780 行, 三级回退: Playwright→html2image→Pillow)
│   ├── conversation_store.py          # ✅ 会话存储 (455 行)
│   ├── dice_core/                     # ✅ 骰子核心 (265 行)
│   │   ├── __init__.py
│   │   ├── dice_parser.py             #    表达式解析 (81 行)
│   │   └── character_store.py         #    角色卡存储 (184 行)
│   ├── ark_trpg/                      # ✅ 方舟 TRPG (31 行)
│   │   ├── __init__.py
│   │   └── skills.py                  #    技能映射 (30 行)
│   ├── hooks/                         # ✅ 事件钩子 (~120 行, Phase 2 增强)
│   │   └── __init__.py                #    HookManager (优先级+过滤+3事件类型)
│   └── commands/                      # ✅ 命令实现
│       ├── __init__.py                #    导出 24 个命令类
│       ├── builtin.py                 #    内置管理 (657 行, 9 命令)
│       ├── dice.py                    #    骰子命令 (~350 行, 8 命令)
│       ├── ark_trpg.py                #    方舟命令 (~230 行, 6 命令)
│       ├── log.py                     #    日志命令 (~180 行, 1 命令 7 子操作)
│       └── utils.py                   #    公共工具 (~50 行)
│
├── state/
│   ├── manager.py                     # ✅ 状态管理器 (395 行)
│   └── state_models.py               # ✅ Pydantic 状态验证 (~60 行, Phase 2)
│
├── protocols/
│   ├── messages.py                    # ✅ 消息协议 (267 行)
│   ├── error_codes.py                 # ✅ 错误码 (40 个, 7 个范围)
│   └── config_models.py              # ✅ Pydantic 配置验证 (~100 行, Phase 2)
│
├── tools/                             # ✅ 工具集 (Phase 2 新增)
│   ├── __init__.py
│   └── generate_install_url.py        # ✅ Install URL 生成器 (~100 行)
│
├── tests/                             # ✅ 测试 (10 文件, ~9,617 行, 575 项)
│   ├── test_cherrystudio_module.py    #    143 项
│   ├── test_dice_ark_log.py           #    90 项
│   ├── test_command_module.py         #    52 项
│   ├── test_napcat_bridge.py          #    56 项
│   ├── test_sse_parser.py             #    52 项
│   ├── test_state_manager.py          #    29 项
│   ├── test_message_bus.py            #    25 项
│   ├── test_server.py                 #    10 项
│   ├── test_phase2.py                 #    38 项 (Phase 2 新增)
│   └── test_md_to_image.py           #    43 项 (MD-to-Image v2)
│
├── Configuration/
│   ├── config.json                    # 运行时配置
│   └── BotSettingConfig.json          # 可定制消息模板 (全部已集成)
│
├── data/                              # 运行时数据 (gitignored)
│   ├── {uid}/cards/                   #    角色卡 JSON 文件
│   ├── {uid}/player.json              #    玩家元数据
│   └── logs/{group_id}/               #    群聊日志文件
│
├── Browsers/                          # ✅ Playwright Chromium 浏览器 (本地内置, ~680MB)
│
├── Install/                           # ✅ 安装部署脚本
│   ├── install.bat                    #    一键安装入口
│   └── setup_env.py                   #    自动化环境配置 (~470 行)
│
└── docs/
    ├── PROGRESS.md                    # 本文档
    ├── ROADMAP.md                     # 开发路线图
    ├── PROTOCOL.md                    # 协议文档
    ├── IMPLEMENTATION_PLAN.md         # 实现计划
    └── DESIGN_PHASE2_SSE_MCP.md       # SSE/MCP 设计文档
```
