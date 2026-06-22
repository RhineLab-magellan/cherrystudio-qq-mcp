# Phase 2 设计方案：SSE 流式解析引擎 + MCP 工具重构

> 设计日期: 2026-06-06
> 状态: v2.0 — 已确认设计方案

---

## 一、问题诊断

### 1.1 SSE 流式解析：当前状况

NEW 项目的 `HTTPClient.send_chat_message()` 使用 `resp.json()` 同步等待完整 JSON 响应，但 CherryStudio Agent API 返回的是 **SSE 流式响应**。这意味着当前的消息发送链路**必然失败**——`resp.json()` 无法解析 SSE 格式。

OLD 项目在 `auto_reply.py` 的 `_call_agent_api_once()` 方法中实现了完整的 SSE 流式解析（约 200 行核心逻辑），处理了 12+ 种事件类型，但它存在以下已识别的问题：

- **`has_tool_calls` 语义混乱**：设为 True 后立即重置为 False，导致 post-loop 检查成为死代码
- **工具调用前的文本被无条件丢弃**：模型先输出一段分析再调用 `qq_send_message` 时，前面的分析文本被 `current_group_parts.clear()` 清空，如果工具恰好是 `qq_send_message`，后续文本也被 `_tool_result_discarding` 丢弃，最终用户**完全看不到回复**
- **流式去重的误判风险**：基于后缀-前缀匹配的去重算法可能错误去掉合法的重复文本片段

### 1.2 MCP 工具：当前状况

当前 13 个工具中存在以下问题：

- **`qq_confirm_response` 定位不明**：它仅在 Bridge 内部设置一个 `_active_confirm` 标记，不是一个真正的 QQ 操作工具。描述说"当你不想直接调用 qq_send_message 时"使用，但最终还是需要调用 `qq_send_message` 发消息，Agent 无法理解何时需要它
- **`qq_send_message` 与 `qq_upload_file` 的边界模糊**：两者的 `content`/`message` 参数都是文本字符串，Agent 无法判断何时应该从 send_message 切换到 upload_file
- **工具描述过于简短**：如 `"发送文本消息到QQ"` 不足以让 Agent 理解工具的选择策略
- **`global_context` 不包含工具使用指南**：Agent 对工具的理解完全依赖工具描述字段

---

## 二、SSE 流式解析引擎设计

### 2.1 设计目标

构建一个独立的、可测试的 SSE 解析器模块，使其能够：

1. **精确区分思考与回复**：`reasoning-*` 事件产生的内容标记为"思考"，`text-*` 事件产生的内容标记为"回复"
2. **工具调用感知**：跟踪模型是否调用了输出类 MCP 工具（`qq_send_message` / `qq_send_image` / `qq_upload_file`），并据此决定 SSE 流中的文本是否应该发送
3. **兜底提取**：当模型忘记调用 MCP 工具时，从 SSE 流中正确提取回复文本并发送
4. **停滞保护**：防止无限等待，在超时时给出用户反馈

### 2.2 架构：独立 SSE 解析器类

将 SSE 解析逻辑从 `auto_reply.py` 的 800 行方法中解耦，构建独立的 `SSEParser` 类：

```
modules/
  sse_parser.py          # 新增：独立的 SSE 流式解析器
```

`SSEParser` 的职责边界清晰：它只负责**解析 SSE 流并返回结构化结果**，不负责发送消息、不负责会话管理。发送逻辑由调用方（`CherryStudioSessionHandler`）根据解析结果决定。

### 2.3 SSEParser 核心数据结构

```python
@dataclass
class SSETextBlock:
    """SSE 流中提取的一个文本块"""
    text: str
    is_reasoning: bool     # True = 思考内容, False = 回复内容
    is_tool_result: bool   # True = 来自 finish-step 的工具结果文本

@dataclass  
class SSEToolCall:
    """SSE 流中检测到的一次工具调用"""
    tool_name: str         # 去除 mcp__*__ 前缀后的工具名
    raw_name: str          # 原始工具名（含前缀）

@dataclass
class SSEResult:
    """SSE 流的完整解析结果"""
    reply_blocks: list[SSETextBlock]    # 回复文本块列表（按出现顺序）
    reasoning_blocks: list[SSETextBlock] # 思考文本块列表（仅调试用）
    tool_calls: list[SSEToolCall]        # 工具调用列表
    had_output_tool: bool               # 是否调用了输出类工具 (send_message/image/upload_file)
    error: str | None                   # 错误信息（如有）
    session_not_found: bool             # 是否收到 session_not_found 错误
    stalled: bool                       # 是否因停滞而提前终止
    total_duration: float               # 总耗时（秒）
```

### 2.4 SSE 事件分类处理规则

将 CherryStudio Agent API 的 SSE 事件分为 5 个处理类别：

#### 类别 A：回复文本事件（收集为用户可见内容）

| 事件 | 处理 |
|------|------|
| `text-start` | 开启新的文本收集窗口，重置 delta 缓冲区 |
| `text-delta` | 当不处于 reasoning 阶段时，追加 `text` 字段到 delta 缓冲区 |
| `text-end` | 拼接 delta 缓冲区为一个完整文本块，标记 `is_reasoning=False`，追加到 `reply_blocks` |

#### 类别 B：思考文本事件（收集但标记为不可见）

| 事件 | 处理 |
|------|------|
| `reasoning-start` | 设置 `in_reasoning=True`，后续 text-delta 归入思考缓冲区 |
| `reasoning-delta` | 显式跳过（CherryStudio 单独发送的思考增量） |
| `reasoning-end` | 设置 `in_reasoning=False`，保存已收集的思考内容到 `reasoning_blocks` |

注意：即使在 reasoning 阶段，`text-start` / `text-end` 仍正常执行状态切换，但 `text-delta` 会被 `in_reasoning` 标志过滤，不会进入回复缓冲区。

#### 类别 C：工具调用事件（跟踪但不收集文本）

| 事件 | 处理 |
|------|------|
| 包含 `toolName` / `name` / `function.name` 的 JSON | 提取工具名（去除 `mcp__*__` 前缀），记录到 `tool_calls` 列表 |
| `tool-input-start` / `tool-input-delta` / `tool-input-end` | 显式跳过（工具参数流，不是工具调用本身） |

工具调用检测后的关键判断：
- 若工具名为 `qq_send_message` / `qq_send_image` / `qq_upload_file`，标记 `had_output_tool=True`
- 记录工具调用时间点，用于后续的"工具前文本"处理策略

#### 类别 D：步骤完成事件（条件性收集）

| 事件 | 处理 |
|------|------|
| `finish-step` | 从 `response` 字段提取文本（兼容 `text`/`content`/`output`/`message` 四个 key）。**仅当 `had_output_tool=False` 时**追加到 `reply_blocks`，否则标记为 `is_tool_result=True` 并丢弃 |
| `finish` | 流结束标记，退出循环 |

#### 类别 E：跳过事件（静默忽略）

| 事件 | 处理 |
|------|------|
| `start` / `raw` / `start-step` / `ping` | 跳过 |
| `error` | 记录错误信息到 `SSEResult.error`；若 `code == "session_not_found"` 则设置 `session_not_found=True` |

### 2.5 核心设计决策：工具调用 vs 文本回复的关系

这是本次重构的**核心改进点**。OLD 项目的问题是"工具调用前的文本被无条件丢弃"。新设计采用**三段式文本管理**：

```
SSE 流时间线：
  [文本段 A] → [工具调用] → [文本段 B] → [finish-step 文本 C]
```

**OLD 项目的行为：**
- 文本段 A → 工具调用时 `current_group_parts.clear()` → 丢弃
- 文本段 B → `_tool_result_discarding` 生效 → 丢弃（如果工具是 qq_send_message）
- 文本 C → `finish-step` 被 `_tool_result_discarding` 拦截 → 丢弃
- 最终结果：用户什么都看不到

**新设计的行为——分场景策略：**

**场景 1：模型没有调用任何输出类工具（`had_output_tool=False`）**

这是"模型忘记调用 MCP Send Message"的场景。

策略：**所有 `reply_blocks` 中的文本块拼接后作为最终回复发送。** `finish-step` 的文本也被收集。这是兜底机制——确保即使模型不调用工具，用户也能收到回复。

**场景 2：模型调用了输出类工具，且工具调用前没有文本（`had_output_tool=True`, 工具前无文本）**

这是正常的"模型直接用工具发消息"场景。

策略：**丢弃所有后续文本。** 工具已经自行发送了消息，SSE 流中的文本是冗余的。

**场景 3：模型先输出了文本，然后调用了输出类工具（`had_output_tool=True`, 工具前有文本）**

这是 OLD 项目的 bug 场景。

策略：**根据配置项 `pre_tool_text_policy` 决定行为**（见 2.5.1）。

#### 2.5.1 可配置的工具前文本策略

在 `config.json` 中新增配置项 `bridge.pre_tool_text_policy`，支持两种策略：

```json
{
    "bridge": {
        "pre_tool_text_policy": "keep"   // "keep" 或 "discard"
    }
}
```

- **`"keep"`（默认）**：保留工具调用前的文本段 A，丢弃工具调用后的文本段 B 和 finish-step 文本 C。理由：文本段 A 是模型在调用工具之前主动输出的内容，可能是对用户的补充说明或分析，应该被发送。
- **`"discard"`**：丢弃所有 SSE 文本（与 OLD 项目行为一致）。工具已自行发送消息，SSE 流中的所有文本都视为冗余。

实现方式：在 `SSEParser` 中维护一个 `pre_tool_reply_blocks` 列表。当检测到输出类工具调用时，将当前 `reply_blocks` 的内容快照到 `pre_tool_reply_blocks`，然后清空 `reply_blocks`。最终返回时：
- 若 `had_output_tool=True` 且策略为 `"keep"` → 返回 `pre_tool_reply_blocks`
- 若 `had_output_tool=True` 且策略为 `"discard"` → 返回空列表
- 若 `had_output_tool=False` → 返回全部 `reply_blocks`（不受策略影响）

### 2.6 流式去重策略

保留 OLD 项目的后缀-前缀重叠检测算法，但增加安全限制：

- **最小重叠长度**：只有当重叠长度 >= 4 个字符时才触发去重（避免 "很好很好" 这样的合法重复被误判）
- **仅在同一 `text-start` ~ `text-end` 窗口与前一个 `reply_blocks[-1]` 之间检测**

### 2.7 停滞检测与超时

```
STALL_TIMEOUT = 30s        # 单次 readline 超时
TOTAL_TIMEOUT = 600s       # 总超时 (10 分钟)
NOTIFY_INTERVAL = 25s      # 通知间隔
MAX_STALL_RETRIES = 4      # 最大停滞重试次数 (从 config.json 读取)
```

停滞处理策略（与 OLD 项目一致，但提升为 SSEParser 的配置参数）：

- 收到数据 → 停滞计数归零
- 超时且有过输出 → 停滞计数递增：
  - 未达上限 → 通过回调通知调用方发送"烧烤中"提示
  - 达到上限 → 标记 `stalled=True`，终止解析
- 超时且从未有输出 → 通过回调通知调用方发送兜底消息，标记 `stalled=True`

SSEParser 通过**回调函数**与调用方通信，而非直接操作 NapCat 发送消息：

```python
class SSEParser:
    def __init__(
        self,
        stall_timeout: int = 30,
        total_timeout: int = 600,
        max_stall_retries: int = 4,
        pre_tool_text_policy: str = "keep",  # "keep" 或 "discard"
        notify_callback: Callable[[str], Awaitable[None]] | None = None,
        # notify_callback 接收一个消息字符串，由调用方负责发送到 QQ
    )
```

### 2.8 SSEParser 公共接口

```python
class SSEParser:
    async def parse(self, response: aiohttp.ClientResponse) -> SSEResult:
        """
        解析 SSE 流式响应，返回结构化结果。
        
        response: aiohttp POST 请求的响应对象（Content-Type: text/event-stream）
        返回: SSEResult 包含回复文本、工具调用、错误信息等
        """
```

### 2.9 调用方（CherryStudioSessionHandler）的使用流程

```python
async def _process_message(self, msg):
    # ... 前置处理（图片识别、文件处理等）...
    
    # 1. 发送消息到 CherryStudio Agent API（SSE 流式）
    async with self._http.post(url, json=body, timeout=...) as resp:
        # 2. 使用 SSEParser 解析流式响应
        parser = SSEParser(
            stall_timeout=30,
            total_timeout=self._agent_timeout,
            max_stall_retries=self._sse_stall_max_retries,
            notify_callback=lambda text: self._send_to_user(text)
        )
        result = await parser.parse(resp)
    
    # 3. 根据解析结果决定行为
    if result.session_not_found:
        # 会话失效 → 清除 SID，触发重建
        self._clear_session_id()
        raise SessionNotFoundError()
    
    if result.error and not result.reply_blocks:
        # 有错误且无回复 → 发送错误消息
        await self._send_to_user("小企鹅看不懂拉，您发的太深奥了拉")
        return
    
    if result.had_output_tool and not result.reply_blocks:
        # 工具已发送消息，无额外文本 → 不发送任何东西
        return
    
    if result.reply_blocks:
        # 有回复文本 → 拼接并发送
        full_reply = "\n\n".join(block.text for block in result.reply_blocks if block.text)
        if full_reply:
            # 长文本转文档、Markdown 图片提取等后处理
            await self._send_reply(full_reply)
```

### 2.10 `session_not_found` 与会话重建

当 SSE 解析结果中 `session_not_found=True` 时：
1. 清除本地 session ID
2. 抛出 `SessionNotFoundError`
3. 外层 `_call_agent_api()` 捕获异常，等待 1 秒后重试一次
4. 重试时因无 session ID，自动创建新会话

### 2.11 停滞的 2-strike 会话处理

与 OLD 项目一致，在 `CherryStudioSessionHandler` 层面实现：
- 第 1 次 SSE 停滞（`result.stalled=True`）：保留会话 SID，仅记录日志
- 连续第 2 次停滞：销毁 CherryStudio 端会话，清除本地 SID，下次自动重建

---

## 三、MCP 工具重构设计

### 3.1 设计原则

- **三个输出类工具定位清晰**：文本消息 / 图片 / 文件上传，各自独立
- **Bridge 智能后处理**：Agent 不需要关心文本长度阈值，Bridge 在发送层自动将超长文本转为 MD 文件上传
- **工具描述不给 Agent 具体的分界数值**：描述保持泛化，阈值由 `global_context` 初始化时设定，Bridge 内部执行
- **删除定位不明的工具**：`qq_confirm_response` 删除
- **`qq_upload_file` 重构为真正的文件上传系统**：基于 NapCat API，支持实际文件上传

### 3.2 工具变更总览

| 变更 | 工具 | 原因 |
|------|------|------|
| **保留并优化描述** | `qq_send_message` | 核心文本输出工具 |
| **保留并优化描述** | `qq_send_image` | 图片输出工具 |
| **重构为真正的文件上传系统** | `qq_upload_file` | 原名保留，但功能从"仅发送文本内容"扩展为支持实际文件上传 |
| **删除** | `qq_confirm_response` | 定位不明，由 Bridge 内部自动机制替代 |
| **保留并优化描述** | 其余 9 个查询/控制类工具 | 定位清晰 |

最终工具数：12 个（13 - 1 删除）

### 3.3 三个输出类工具的重新定位

#### `qq_send_message` — 发送文本消息

```
定位: 向 QQ 发送文本消息。这是 Agent 最主要的回复工具。
参数: message_type (private/group), target_id, message
```

工具描述（不包含具体长度阈值）：
> 向指定的 QQ 私聊或群聊发送一条文本消息。这是你向用户回复内容的主要方式。Bridge 会自动处理消息长度——如果内容过长，会自动转为文档文件发送。你不需要关心消息长度限制。不要使用此工具发送图片（请用 qq_send_image）。

**Bridge 智能后处理**：在 NapCatBridge 的 `send_message()` 层，当检测到文本长度超过 `doc_threshold`（从 `config.json` 的 `auto_reply.doc_threshold` 读取，默认 1000 字符）时，自动将文本保存为 `.md` 临时文件，通过 `upload_file()` API 上传，并附带一段预览文本（前 300 字符）作为普通消息发送。Agent 对此过程无感知。

#### `qq_send_image` — 发送图片

```
定位: 向 QQ 发送图片。
参数: message_type (private/group), target_id, image_url, summary(可选)
```

工具描述：
> 向指定的 QQ 私聊或群聊发送一张图片。image_url 必须是可公开访问的 HTTP/HTTPS 图片链接。可选附带 summary 参数作为图片的文字说明。如果你需要发送的是文本内容而非图片，请使用 qq_send_message。

#### `qq_upload_file` — 上传文件（重构）

```
定位: 向 QQ 上传实际文件。支持文本内容（自动保存为文件）和本地文件路径两种模式。
参数: message_type (private/group), target_id, content(可选), file_path(可选), filename(可选)
```

**重构要点**：当前 `qq_upload_file` 只接受 `content` 文本参数（内部写临时 .md 文件再上传）。重构后支持两种调用模式：

**模式 A：文本内容模式（向后兼容）**
Agent 提供 `content` 文本，Bridge 自动写入临时文件并上传。与当前行为一致，保留向后兼容。

**模式 B：本地文件路径模式（新增）**
Agent 提供 `file_path`（本地文件的绝对路径），Bridge 直接调用 NapCat `upload_group_file` / `upload_private_file` API 上传。支持任意文件格式（.md、.zip、.pdf、.py 等），不再局限于文本内容。

参数约束：`content` 和 `file_path` 二选一。若都提供则优先使用 `file_path`。若都不提供则返回错误。

重构后的工具描述：
> 向指定的 QQ 私聊或群聊上传一个文件。支持两种方式：(1) 提供 content 文本参数，Bridge 会自动保存为文件并上传；(2) 提供 file_path 本地文件路径，Bridge 直接上传该文件。filename 可选，用于指定接收方看到的文件名。适用于发送长文档、代码文件、压缩包等。如果只是想发送一段文字消息，请使用 qq_send_message。

### 3.4 删除 `qq_confirm_response` 的理由与替代方案

**删除理由：**

`qq_confirm_response` 的唯一作用是设置 `_active_confirm` 标记，使 `qq_send_message` 的活跃目标验证通过。但这个设计存在根本性的逻辑问题：

1. Agent 在 CherryStudio 中处理消息时，Bridge 内部已经有该会话的活跃记录（MessageBuffer 中存有最近消息），正常情况下 `qq_send_message` 的活跃验证应该自动通过
2. 如果验证不通过，说明该会话确实不是当前活跃的，强行通过验证反而可能导致消息错发
3. OLD 项目的描述暗示了一个不存在的"思考 vs 回复"过滤机制，实际代码中并没有
4. 它增加了 Agent 的工具选择复杂度，但带来的安全价值极低

**替代方案：基于 SSE 会话关联的自动机制**

- CherryStudio Agent API 的消息请求中已包含 session_id，Bridge 可以在发起 SSE 请求时记录当前处理的 `target_id`
- 当 Agent 在同一个 SSE 请求的处理过程中调用 `qq_send_message` 时，Bridge 自动认为该 `target_id` 是活跃的
- 无需额外的确认工具

具体实现：在 `CherryStudioSessionHandler._process_message()` 中，发起 SSE 请求前将当前消息的 `target_id` 注册到 `NapCatBridge` 的 `_responding_targets` 集合中，SSE 请求结束后注销。`qq_send_message` 的活跃验证同时检查 MessageBuffer 的活跃会话和 `_responding_targets` 集合。

```python
# NapCatBridge 新增接口
class NapCatBridge:
    def __init__(self, ...):
        self._responding_targets: set[str] = set()  # 正在响应中的 target_id 集合
    
    def mark_responding(self, target_id: str):
        """标记某个目标正在被响应"""
        self._responding_targets.add(target_id)
    
    def unmark_responding(self, target_id: str):
        """取消标记"""
        self._responding_targets.discard(target_id)
    
    def is_target_active(self, target_id: str) -> bool:
        """检查目标是否活跃（MessageBuffer 有记录 OR 正在响应中）"""
        return (target_id in self._responding_targets 
                or self.message_buffer.has_target(target_id))
```

### 3.5 Bridge 智能后处理：长文本自动转文档

这是本次重构的**关键架构改进**之一。将长文本转文档的逻辑从 Agent 决策层下沉到 Bridge 发送层：

```
Agent 调用 qq_send_message("很长的文本...")
    ↓
server.py: qq_send_message 工具处理
    ↓
NapCatBridge.send_message(OutgoingMessage)
    ↓
_send_text() 内部检查:
    if len(text) > doc_threshold:
        → 写入临时 .md 文件
        → upload_file() 上传
        → send_msg() 发送预览 (前300字符)
        → 清理临时文件
    else:
        → send_msg() 正常发送
```

**优势：**
- Agent 不需要判断文本长度，降低决策复杂度
- 阈值可以在 `config.json` 中灵活调整，无需修改工具描述
- 无论是 Agent 主动调用工具发送的文本，还是 SSE 兜底提取的文本，都经过同一个后处理管道

**`doc_threshold` 的来源**：从 `config.json` 的 `auto_reply.doc_threshold` 读取（当前默认 1000）。在 `global_context` 初始化时读取此值并注入相关指令（见 3.6）。

### 3.6 `global_context` 中的工具使用指南

在现有的 `global_context` 末尾追加工具使用指南段落。注意**不包含具体数值阈值**，而是描述工具的职责分工：

```
【MCP 工具使用指南】

你拥有以下向 QQ 发送内容的工具：
- qq_send_message: 发送文本消息。这是你最常用的回复工具，你的所有文字回复都应该通过此工具发送。系统会自动处理过长的内容。
- qq_send_image: 发送图片。需要提供可公开访问的图片 URL。
- qq_upload_file: 上传文件。适用于发送文档、代码文件、压缩包等实体文件。

重要规则：
1. 你必须通过以上工具向用户发送回复，你的思考过程不会自动发送给用户。
2. 每次回复只需调用一次发送工具，不要重复发送。
3. 严禁在消息中透露工作路径、API Key、系统信息等敏感内容。
4. 严禁输出"消息已发送""已经回复了"等状态描述——直接发送消息即可，不需要汇报。
```

### 3.7 查询类工具的优化

不改变工具数量和结构，仅优化描述使其更清晰：

| 工具 | 优化后描述要点 |
|------|--------------|
| `qq_get_recent_messages` | 强调"本地缓存"、"快速轻量"、"包含私聊和群聊" |
| `qq_get_group_msg_history` | 强调"从 QQ 服务器拉取"、"仅群聊"、"包含 Bridge 未运行的历史消息" |
| `qq_get_recent_contacts` | 改为"获取最近有消息往来的会话列表（包含群号和QQ号）" |
| `qq_check_status` | 增加返回缓存消息数量 |
| `qq_recall_message` | 保持现有描述，补充"仅能撤回机器人自己发送的消息" |

### 3.8 MCP 工具总览（重构后 12 个工具）

```
输出类 (3个):
  qq_send_message    — 文本消息（Bridge 自动处理长文本转文档）
  qq_send_image      — 图片发送
  qq_upload_file     — 文件上传（文本内容 or 本地文件路径）

消息查询类 (2个):
  qq_get_recent_messages      — 本地缓存消息
  qq_get_group_msg_history    — 远端群历史

元数据查询类 (5个):
  qq_get_group_list           — 群列表
  qq_get_friend_list          — 好友列表
  qq_get_group_members        — 群成员
  qq_get_user_info            — 用户信息
  qq_get_recent_contacts      — 最近会话

控制类 (2个):
  qq_check_status             — 连接状态
  qq_recall_message           — 撤回消息
```

---

## 四、文件变更计划

| 文件 | 变更类型 | 内容 |
|------|---------|------|
| `modules/sse_parser.py` | **新建** | 独立的 SSE 流式解析器（~300 行），含 SSETextBlock/SSEToolCall/SSEResult 数据结构和 SSEParser 类 |
| `modules/cherrystudio_module.py` | **大量修改** | HTTPClient 改为 SSE 流式调用；SessionHandler 接入 SSEParser；接入 ConversationStore；auto_reply 配置读取；`_send_reply()` 方法调用 Bridge 智能后处理 |
| `server.py` | **修改** | 删除 `qq_confirm_response` 工具；`qq_upload_file` 重构为真正的文件上传系统（新增 `file_path` 参数）；更新所有工具描述；活跃目标验证改为"正在响应中"机制 |
| `config.json` | **修改** | `global_context` 追加工具使用指南；新增 `bridge.pre_tool_text_policy` 配置项 |
| `modules/napcat_bridge.py` | **修改** | MessageBuffer 增加 `_responding_targets` 集合和 `mark_responding`/`unmark_responding`/`is_target_active` 接口；`_send_text()` 增加长文本自动转文档逻辑 |
| `tests/test_sse_parser.py` | **新建** | SSE 解析器的完整测试套件（覆盖所有事件类型组合、场景 1/2/3、可配置策略） |

---

## 五、验收标准

### SSE 解析引擎

1. SSEParser 能正确解析包含 12+ 种事件类型的 SSE 流
2. 思考内容（reasoning）与回复内容（text）被正确区分，思考内容不出现在最终回复中
3. 模型忘记调用 MCP 工具时，SSE 文本被正确提取并发送给用户
4. `pre_tool_text_policy="keep"` 时，模型先输出文本再调用工具时，工具前文本被保留发送
5. `pre_tool_text_policy="discard"` 时，工具调用后所有 SSE 文本被丢弃
6. 模型直接调用输出工具时，后续的冗余文本被正确丢弃
7. 停滞检测正常工作：30s 超时通知、重试上限后强制 flush
8. `session_not_found` 错误触发会话自动重建
9. 流式去重的最小重叠长度 >= 4 字符，避免合法重复文本被误判

### MCP 工具

1. `qq_confirm_response` 工具被删除，活跃目标验证改为 `_responding_targets` 自动机制
2. `qq_upload_file` 重构为支持 `content`（文本内容）和 `file_path`（本地文件路径）两种模式
3. `qq_send_message` 的 Bridge 发送层自动将超长文本转为 MD 文件上传
4. 三个输出工具的描述不包含具体长度阈值
5. `global_context` 包含工具使用指南（不含具体数值）
6. 所有 12 个工具端到端可用

### 测试

1. SSEParser 有独立的单元测试覆盖：标准文本流、reasoning 过滤、工具调用检测、三种场景（无工具/工具无前文本/工具有前文本）、两种 pre_tool_text_policy、停滞检测、session_not_found、流式去重
2. 现有 98 个测试继续通过（可能需要适配接口变更）
3. 新增 `qq_upload_file` 双模式上传的测试
4. 新增长文本自动转文档的测试

---

## 六、已确认的设计决策

| 决策项 | 结论 | 理由 |
|--------|------|------|
| `qq_confirm_response` | 删除 | 定位不明，Bridge 内部自动机制可完全替代 |
| `qq_upload_file` | 保留原名，重构为真正的文件上传系统 | 基于 NapCat 的 `upload_group_file`/`upload_private_file` API，支持实际文件上传 |
| 工具前文本策略 | 可配置 (`bridge.pre_tool_text_policy`) | `"keep"` 保留工具前文本，`"discard"` 全部丢弃，满足不同使用偏好 |
| 长度阈值暴露方式 | 不告诉 Agent 具体数值 | Bridge 在发送层自动处理长文本转文档，阈值由 global_context 初始化时设定 |

---

*文档版本: v2.0*
*设计日期: 2026-06-06*
*状态: 已确认设计方案，待实施*
