# 模块标准通信协议和错误码定义表

## 一、模块架构概览

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

---

## 二、标准消息协议

### 2.1 消息类型定义

#### RawMessage (原始消息)
**方向**: NapCatBridge → MessageBus

| 字段 | 类型 | 说明 |
|------|------|------|
| msg_id | str | 消息唯一ID |
| source | MessageSource | 消息来源 (GROUP/PRIVATE) |
| target_id | str | 目标ID (群号或QQ号) |
| sender_id | str | 发送者QQ号 |
| sender_name | str | 发送者昵称 |
| content | str | 文本内容 |
| message_type | MessageType | 消息类型 (TEXT/IMAGE/FILE等) |
| attachments | list[dict] | 附件信息 |
| timestamp | datetime | 时间戳 |
| raw_data | dict | 原始OneBot数据 |

#### ParsedMessage (解析后消息)
**方向**: MessageBus → CommandModule/CherryStudioModule

| 字段 | 类型 | 说明 |
|------|------|------|
| raw | RawMessage | 原始消息 |
| is_command | bool | 是否为命令 |
| command_name | str\|None | 命令名称 |
| command_args | str\|None | 命令参数 |
| metadata | dict | 额外元数据 |

**属性**:
- `session_key`: str - 会话键，格式 `{source}_{target_id}`

#### OutgoingMessage (待发送消息)
**方向**: MessageBus → NapCatBridge

| 字段 | 类型 | 说明 |
|------|------|------|
| target_source | MessageSource | 目标来源 |
| target_id | str | 目标ID |
| content | str | 消息内容 |
| message_type | MessageType | 消息类型 |
| attachments | list[dict] | 附件 |
| reply_to_msg_id | str\|None | 回复的消息ID |
| metadata | dict | 元数据 |

#### ModuleResponse (模块响应)
**方向**: CommandModule/CherryStudioModule → MessageBus

| 字段 | 类型 | 说明 |
|------|------|------|
| success | bool | 是否成功 |
| content | str\|None | 响应内容 |
| error_code | str\|None | 错误码 (BRG-XXXX) |
| error_detail | str\|None | 错误详情 (仅日志) |
| requires_confirmation | bool | 是否需要确认 |
| metadata | dict | 元数据 |

**方法**:
- `user_message`: str - 获取展示给用户的消息
- `success_response(content)`: 创建成功响应
- `error_response(error_code, detail, custom_text)`: 创建错误响应

---

### 2.2 枚举类型

#### MessageSource
```python
GROUP = "group"      # 群聊
PRIVATE = "private"  # 私聊
```

#### MessageType
```python
TEXT = "text"    # 文本
IMAGE = "image"  # 图片
FILE = "file"    # 文件
MIXED = "mixed"  # 混合
AT = "at"        # @消息
REPLY = "reply"  # 引用回复
```

---

### 2.3 消息流转示例

#### 场景1: 用户发送命令 `.help`
```
1. NapCatQQ → RawMessage → NapCatBridge
2. NapCatBridge → raw_message_queue → MessageBus
3. MessageBus 解析为 ParsedMessage (is_command=True)
4. MessageBus → command_queue → CommandModule
5. CommandModule 执行 → ModuleResponse
6. ModuleResponse → command_response_queue → MessageBus
7. MessageBus 构建 OutgoingMessage
8. OutgoingMessage → send_message_queue → NapCatBridge
9. NapCatBridge → NapCatQQ → 用户收到 "帮助内容"
```

#### 场景2: 用户发送普通消息 "你好"
```
1. NapCatQQ → RawMessage → NapCatBridge
2. NapCatBridge → raw_message_queue → MessageBus
3. MessageBus 解析为 ParsedMessage (is_command=False)
4. MessageBus → cherrystudio_queue → CherryStudioModule
5. CherryStudioModule 调用LLM → ModuleResponse
6. ModuleResponse → cherrystudio_response_queue → MessageBus
7. MessageBus 构建 OutgoingMessage
8. OutgoingMessage → send_message_queue → NapCatBridge
9. NapCatBridge → NapCatQQ → 用户收到 "AI回复"
```

---

## 三、错误码定义表

### 错误码格式
```
BRG-XXXX
- BRG: Bridge 前缀
- XXXX: 4位数字错误码
```

### 3.1 NapCat 互联桥错误 (1000-1999)

| 错误码 | 错误描述 | 用户提示 | 触发场景 |
|--------|----------|----------|----------|
| BRG-1001 | NapCat WebSocket 连接失败 | 连接失败 | WebSocket无法建立连接 |
| BRG-1002 | NapCat 认证失败 | 认证失败 | Access Token无效 |
| BRG-1003 | 发送消息到 NapCat 失败 | 发送失败 | API调用失败 |
| BRG-1004 | NapCat 连接意外断开 | 连接断开 | WebSocket异常断开 |
| BRG-1005 | NapCat API 调用超时 | 请求超时 | API响应超时 |
| BRG-1006 | NapCat 返回无效响应 | 响应异常 | 响应格式错误 |

### 3.2 消息互联桥错误 (2000-2999)

| 错误码 | 错误描述 | 用户提示 | 触发场景 |
|--------|----------|----------|----------|
| BRG-2001 | 消息解析失败 | 消息解析失败 | 无法解析OneBot格式 |
| BRG-2002 | 消息路由失败 | 路由失败 | 模块未初始化 |
| BRG-2003 | 目标模块已禁用 | 功能不可用 | 模块被禁用 |
| BRG-2004 | 消息被过滤规则拒绝 | 消息被拦截 | 黑白名单拦截 |
| BRG-2005 | 多模块响应合并失败 | 响应处理失败 | 响应冲突 |

### 3.3 命令模块错误 (3000-3999)

| 错误码 | 错误描述 | 用户提示 | 触发场景 |
|--------|----------|----------|----------|
| BRG-3001 | 命令不存在 | 未知命令 | 输入无效命令 |
| BRG-3002 | 命令执行失败 | 命令执行失败 | 命令内部错误 |
| BRG-3003 | 命令权限不足 | 权限不足 | 非管理员执行 |
| BRG-3004 | 命令参数无效 | 参数错误 | 参数格式错误 |
| BRG-3005 | 命令执行超时 | 执行超时 | 超过60秒 |
| BRG-3006 | 会话已过期 | 会话过期 | 会话超时 |

### 3.4 CherryStudio 模块错误 (4000-4999)

| 错误码 | 错误描述 | 用户提示 | 触发场景 |
|--------|----------|----------|----------|
| BRG-4001 | CherryStudio 连接失败 | AI服务连接失败 | MCP/HTTP连接失败 |
| BRG-4002 | CherryStudio API 调用失败 | AI服务异常 | API返回错误 |
| BRG-4003 | 指定的 Agent 不存在 | Agent不存在 | Agent名称错误 |
| BRG-4004 | LLM Provider 调用失败 | AI处理失败 | 所有回退尝试失败 |
| BRG-4005 | 创建会话失败 | 会话创建失败 | 会话初始化失败 |
| BRG-4006 | 图片识别处理失败 | 图片处理失败 | Vision API失败 |
| BRG-4007 | 文件解析处理失败 | 文件处理失败 | MinerU失败 |
| BRG-4008 | MCP 响应超时 | 响应超时 | 切换到HTTP API |

### 3.5 Server 模块错误 (5000-5999)

| 错误码 | 错误描述 | 用户提示 | 触发场景 |
|--------|----------|----------|----------|
| BRG-5001 | 服务器初始化失败 | 启动失败 | 配置加载失败 |
| BRG-5002 | MCP 工具注册失败 | 工具注册失败 | MCP协议错误 |
| BRG-5003 | 配置文件加载失败 | 配置加载失败 | JSON格式错误 |
| BRG-5004 | 检测到重复运行的实例 | 重复运行 | PID锁冲突 |
| BRG-5005 | 优雅关闭超时 | 关闭超时 | 超过30秒 |

### 3.6 通用错误 (9000-9999)

| 错误码 | 错误描述 | 用户提示 | 触发场景 |
|--------|----------|----------|----------|
| BRG-9001 | 未知错误 | 系统错误 | 未分类错误 |
| BRG-9002 | 内部服务器错误 | 内部错误 | 系统异常 |
| BRG-9003 | 请求频率限制 | 操作过于频繁 | 限流触发 |
| BRG-9004 | 服务暂时不可用 | 服务不可用 | 维护中 |

---

## 四、模块接口规范

### 4.1 NapCatBridge 接口

```python
class NapCatBridge:
    async def initialize()
    async def start()
    async def stop()
    async def wait_ready(timeout: float)
    async def send_message(msg: OutgoingMessage) -> str
    def register_message_handler(handler: Callable)
    property is_connected: bool
```

### 4.2 MessageBus 接口

```python
class MessageBus:
    def __init__(state_manager: StateManager)
    def set_command_queue(queue: asyncio.Queue[ParsedMessage])
    def set_cherrystudio_queue(queue: asyncio.Queue[ParsedMessage])
    async def start()
    async def stop()
    def add_filter(filter: MessageFilter)
    def remove_filter(filter: MessageFilter)

    # 队列
    raw_message_queue: asyncio.Queue[RawMessage]
    send_message_queue: asyncio.Queue[OutgoingMessage]
    command_response_queue: asyncio.Queue[ModuleResponse]
    cherrystudio_response_queue: asyncio.Queue[ModuleResponse]
```

### 4.3 CommandModule 接口 (待实现)

```python
class CommandModule:
    def __init__(state_manager: StateManager)
    async def initialize()
    async def start()
    async def stop()
    async def reload_config()  # 热重载

    # 队列
    queue: asyncio.Queue[ParsedMessage]
    response_queue: asyncio.Queue[ModuleResponse]
```

### 4.4 CherryStudioModule 接口 (待实现)

```python
class CherryStudioModule:
    def __init__(state_manager: StateManager)
    async def initialize()
    async def start()
    async def stop()
    async def rebuild_session(session_key: str)  # 重建会话

    # 队列
    queue: asyncio.Queue[ParsedMessage]
    response_queue: asyncio.Queue[ModuleResponse]
```

### 4.5 StateManager 接口

```python
class StateManager:
    async def initialize()
    async def update_state(updates: dict)
    async def update_module_status(module: str, enabled: bool)
    async def add_to_blacklist(group_id: str)
    async def remove_from_blacklist(group_id: str)
    async def add_to_whitelist(group_id: str)
    async def remove_from_whitelist(group_id: str)
    async def set_active_agent(session_key: str, agent: str)
    async def get_active_agent(session_key: str) -> str|None
    def is_module_enabled(module: str) -> bool
    def is_in_blacklist(group_id: str) -> bool
    def is_in_whitelist(group_id: str) -> bool
    def register_change_callback(callback: Callable)
    async def reload()
```

---

## 五、会话管理策略

### 5.1 会话键格式
```
{source}_{target_id}
示例:
- group_123456789   (群聊)
- private_987654321 (私聊)
```

### 5.2 异步任务隔离
- 每个会话独立的 asyncio.Task
- 不共享状态，避免竞态条件
- 超时自动清理 (命令: 60s, CherryStudio: 120s)

### 5.3 会话生命周期
```
创建: 收到第一条消息时自动创建
活跃: 每次交互更新时间戳
销毁: 超时后自动清理 (可配置)
重建: 配置变更时主动重建
```

---

## 六、配置管理规范

### 6.1 配置文件结构
```json
{
  "napcat": {
    "ws_host": "127.0.0.1",
    "ws_port": 3001,
    "access_token": "xxx"
  },
  "cherrystudio": {
    "mcp_server_path": "...",
    "http_api_base": "...",
    "api_key": "..."
  },
  "settings": {
    "enable_command_module": true,
    "enable_cherrystudio_module": true,
    "session_timeout_minutes": 30
  }
}
```

### 6.2 状态持久化
- 文件: `Temp/shared_state.json`
- 自动保存: 每次状态变更
- 手动重载: `.reload()` 方法

---

## 七、测试规范

### 7.1 测试框架
- pytest + pytest-asyncio
- 覆盖率目标: 80%+

### 7.2 测试文件命名
```
tests/test_<module_name>.py
示例:
- tests/test_state_manager.py
- tests/test_napcat_bridge.py
- tests/test_message_bus.py
```

### 7.3 测试用例结构
```python
class Test<ClassName>:
    @pytest.mark.asyncio
    async def test_<method_name>_<scenario>(self, fixture):
        # Arrange
        # Act
        # Assert
```

---

## 八、日志规范

### 8.1 日志级别
- DEBUG: 调试信息 (消息解析细节)
- INFO: 正常流程 (连接建立、消息发送)
- WARNING: 警告 (重连、超时)
- ERROR: 错误 (API失败、异常)

### 8.2 日志格式
```
%(asctime)s [%(levelname)s] %(name)s: %(message)s
```

### 8.3 错误日志
- 用户看到: `[自定义文本] [BRG-XXXX]`
- 日志记录: 完整错误详情 + 堆栈跟踪

---

*文档版本: v1.0*
*最后更新: 2026-06-06*
