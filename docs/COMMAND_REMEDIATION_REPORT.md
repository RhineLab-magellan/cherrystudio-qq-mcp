# 命令系统整改报告

> **状态: ✅ 全部整改完成** (2026-06-07)
>
> 审查对象: `modules/commands/builtin.py` (658行) + `modules/command_module.py` (419行)
> 对照基准: 旧项目 `OrderSystem/BuiltInOrder.py`, `Plugins/*.py`, `Configuration/BotSettingConfig.json`
> 日期: 2026-06-07

---

## 规则 1: 空参数行为 — 指向特定函数 / 指向 help / 指向子系统

**设计要求:** 没有特定子系统的命令在收到空参数时，应按以下三种模式之一响应:
1. **指向特定函数** (如 `.bot` → 显示版本信息/欢迎语)
2. **指向 help** (显示帮助)
3. **指向特定子系统** (如 `.ob` → 等同于 `.ob on`)

### 逐命令审查

| 命令 | 当前行为 (空参数) | 旧系统行为 | 判定 | 整改 |
|------|------------------|-----------|------|------|
| `.bot` | `"用法: .bot on/off/status/orderwhite"` | 调用 `ar.build_greeting()` → 显示自定义欢迎语 + 版本号 + reminder 列表 + 命令列表 | **需整改** | 空参应调用 `build_greeting()` 显示版本/欢迎信息 |
| `.order` | 显示子命令帮助 | 显示子命令帮助 | 合规 | — |
| `.model` | `"用法: .model list/change/status/reset"` | 显示子命令帮助 | 合规 | — |
| `.ob` | `"用法: .ob join/exit/list/clr/on/off"` | 等同于 `.ob join` (自动加入旁观) | **需整改** | 空参应等同于 `.ob join` |
| `.dismiss` | `"用法: .dismiss <群号后四位>"` | 返回用法提示 | 合规 | — |
| `.send` | `"用法: .send <target_type> <target_id> <message>"` | `"用法: .send <消息>"` | 合规 | — |
| `.master` | 显示子命令列表 | 显示子命令帮助 | 合规 | — |
| `.welcome` | 显示子命令帮助 | 显示子命令帮助 | 合规 | — |
| `.help` | 显示命令列表 | 显示命令列表 | 合规 | — |

### ✅ 整改项 1.1: `.bot` 空参 → build_greeting

当前 `.bot` 空参只返回一行用法提示。旧系统的 `.bot` 空参调用 `build_greeting()`，该函数生成一段完整的欢迎消息:

```
[自定义问候语 (BotSettingConfig.内置模块.custom_greeting)]
Cherry Agent Bot！by ARK-Magellan Ver 2.0.0
---
[各命令的 reminder 字段]
---
[请使用 .help 来获取详细帮助]
```

**整改方案:**
- `BotCommand.handle()` 空参分支调用新函数 `_build_greeting()`
- `_build_greeting()` 读取 `BotSettingConfig.内置模块.custom_greeting` + 版本号 + 所有命令的 reminder + help 引导
- 需要 CommandContext 提供 command_registry 来收集 reminder (已可用)

### ✅ 整改项 1.2: `.ob` 空参 → `.ob join`

旧系统的 `.ob` 空参直接执行 join 逻辑 (`if not action or action in ("join",):`)。当前系统空参返回用法提示。

**整改方案:**
- `ObCommand.handle()` 将 `if not parts:` 分支改为直接执行 join 逻辑

---

## 规则 2: 群聊限制 — 仅必须群聊的命令才设置 group-only

**设计要求:** 仅有需要使用群聊特定功能的命令才应设置为群聊 only，大部分方法应该私聊和群聊都允许使用。

### 逐命令审查

| 命令 | 当前限制 | 旧系统限制 | 判定 | 整改 |
|------|---------|-----------|------|------|
| `.bot` | 群聊 only (L94) | 群聊 only (`bot_set` 内检查 `msg_type != "group"`) | 合规 | — (操作群级黑名单) |
| `.ob` | 群聊 only (L369) | 群聊 only (`msg.message_type != "group"`) | 合规 | — (旁观者按群管理) |
| `.welcome` | 群聊 only (L611) | 群聊 only (`msg.message_type != "group"`) | 合规 | — (入群欢迎) |
| `.dismiss` | 无限制 (但语义需要群号) | 群聊 only (`msg.message_type != "group"`) | **需整改** | 旧系统在私聊中也可退群 (通过传入群号)，但当前系统已无此限制。保持现状可接受 |
| `.order` | 无限制 | 无限制 | 合规 | — |
| `.model` | 无限制 | 无限制 (但需管理员权限) | **需整改** | 旧系统 `.model` 需 admin 权限，新系统去掉了权限检查 |
| `.send` | 管理员 only | 管理员 only | **需整改** | 旧系统 `.send` 是发送给 Master (固定目标)，新系统改为任意目标转发。功能语义不同 |
| `.master` | 管理员 only | 部分子命令需 admin | **需整改** | 旧系统仅 LLMReset 需 admin，AllResetAgent/OnlyResetAgent 也需 admin。当前顶层 admin 检查过于宽泛 |

### ✅ 整改项 2.1: `.model` 恢复管理员权限检查

旧系统的 `.model` 命令明确限制为管理员:
```python
# 旧系统
if not ar.check_admin(msg.sender_id):
    return "⛔ 权限不足。.model 指令仅限管理员使用。"
```

新系统完全去掉了权限检查，任何人都可以切换模型。

**整改方案:**
- `ModelCommand.handle()` 入口添加 admin 权限检查
- 或: 仅 `change`/`reset` 子命令需要 admin 权限，`list`/`status` 对所有人开放 (推荐)

### ✅ 整改项 2.2: `.send` 功能语义差异

旧系统 `.send` 是向管理员 (Master QQ) 发送消息:
```python
# 旧系统
await nc.send_msg("private", master_qq, text)  # 固定发给 Master
return f"✅ 已发送给 Master"
```

新系统改为向任意目标转发:
```python
# 新系统
.send <target_type> <target_id> <message>  # 任意目标
```

**整改方案:**
- 保留新系统的扩展功能，但同时支持简化格式 `.send <消息>` 直接发给 Master
- 当参数只有 1 个时，回退为旧行为

---

## 规则 3: 子命令未找到时 → 指向 .help

**设计要求:** 当子命令未找到时，应指向 help 指令而非仅返回用法文本。

### 逐命令审查

| 命令 | 当前未知子命令行为 | 判定 | 整改 |
|------|-------------------|------|------|
| `.bot` | `"用法: .bot on/off/status/orderwhite"` | **需整改** | 改为 `"未知子命令: .bot {args}\n请使用 .help 查看帮助"` |
| `.order` | `f"未知指令: .order {args}\n\n{self._sub_help()}"` | 部分合规 (显示了子帮助，但未引导到 .help) | 追加 `"\n或输入 .help 查看完整命令列表"` |
| `.model` | `"未知操作。用法: .model list/change/status/reset"` | **需整改** | 同 `.bot` |
| `.ob` | `"未知操作。用法: .ob join/exit/list/clr/on/off"` | **需整改** | 同 `.bot` |
| `.welcome` | `f"未知子命令: .welcome {args}\n\n{self._sub_help()}"` | 部分合规 | 追加 help 引导 |
| `.master` | `f"未知子命令: {sub_cmd}\n可用: LLMReset, AllResetAgent, OnlyResetAgent"` | **需整改** | 追加 help 引导 |

### ✅ 整改项 3.1: 统一未知子命令响应格式

所有命令的 else 分支应统一为:

```
未知子命令: .命令名 {args}

{self._sub_help() if 有子帮助}

输入 .help 查看完整命令列表。
```

**整改方案:**
- 在 `Command` 基类中添加 `_unknown_sub_help(args: str)` 方法，统一生成标准格式的未知子命令提示
- 所有命令的 else 分支调用此方法

---

## 规则 4: 每个指令的 .help 未实现

**设计要求:** `.help <命令名>` 应显示该命令的详细帮助。当前 `.help` 命令只列出所有命令，不支持查看单个命令的详细帮助。

### 当前 .help 实现

```python
# 当前 HelpCommand.handle()
async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
    if ctx.command_registry:
        commands = ctx.command_registry.list_all()
        lines = ["📖 可用命令列表:\n"]
        for cmd in commands:
            line = f".{cmd.name} - {cmd.description}"
            ...
        return "\n".join(lines)
    # 回退硬编码列表
```

**问题:**
- `.help` 始终显示全部命令列表，忽略 args 参数
- `.help bot` 不会显示 BotCommand 的详细帮助
- `.help 不存在的命令` 不会提示用户

### 旧系统 .help 实现

```python
# 旧系统
async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None:
    cmds = _list_cmds()
    lines = ["可用命令:"]
    for cmd in cmds:
        lines.append(f"  .{cmd.name:<10} - {cmd.description}")
    if args.strip():
        return f"{args} 命令的帮助信息。\n\n" + "\n".join(lines)
    return "\n".join(lines)
```

旧系统也较为简陋，但至少检测了 `args.strip()` 并做了不同处理。

### ✅ 整改项 4.1: HelpCommand 支持单命令详细帮助

**整改方案:**

```python
async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
    query = args.strip().lstrip(".")  # 允许 ".help bot" 和 ".help .bot"

    if query and ctx.command_registry:
        # 单命令详细帮助
        cmd = ctx.command_registry.get(query)
        if cmd:
            return self._cmd_detail(cmd)
        else:
            return f"未找到命令: .{query}\n\n{self._all_commands_list(ctx)}"

    # 无参数 → 显示全部命令列表
    return self._all_commands_list(ctx)

def _cmd_detail(self, cmd: Command) -> str:
    """生成单个命令的详细帮助"""
    lines = [
        f"📖 .{cmd.name} — {cmd.description}",
    ]
    if cmd.reminder:
        lines.append(f"📌 {cmd.reminder}")
    # 如果命令有 _sub_help() 方法，调用它
    if hasattr(cmd, "_sub_help") and callable(cmd._sub_help):
        lines.append("")
        lines.append(cmd._sub_help())
    return "\n".join(lines)
```

### ✅ 整改项 4.2: 每个命令的 reminder 字段完善

当前 reminder 仅 `.welcome` 和旧系统 `.ob` 设置了。所有命令都应设置 reminder 以便 `.help` 和 `.bot` 空参时显示:

| 命令 | 当前 reminder | 建议 reminder |
|------|-------------|-------------|
| `.help` | `""` | `"输入 .help <命令名> 查看详细帮助"` |
| `.bot` | `""` | `"使用 .bot on/off 开关机器人；.bot orderwhite 切换免@模式"` (移植自旧系统) |
| `.order` | `""` | `"使用 .order 切换 <Agent名> 切换 Agent；.order 重建会话 重置上下文"` |
| `.model` | `""` | `"使用 .model list 查看模型；.model change <名> 切换 (管理员)"` |
| `.ob` | `""` | `"使用 .ob join 加入旁观，发言不计入日志；.ob on/off 开关旁观模式"` (移植自旧系统) |
| `.dismiss` | `""` | `"使用 .dismiss <群号后四位> 退群 (管理员)"` |
| `.send` | `""` | `"使用 .send <消息> 发送给管理员；.send <类型> <ID> <消息> 转发"` |
| `.master` | `""` | `"管理员: .master LLMReset/AllResetAgent/OnlyResetAgent"` |
| `.welcome` | 已有 | 保持 |

---

## 规则 5: 回复自定义性 — BotSettingConfig 模板机制

**设计要求:** 命令系统具有回复自定义性，通过 `Configuration/BotSettingConfig.json` 进行自定义。模板占位符:
- `{}` → 命令原回复 (命令的执行结果)
- `[]` / `<>` → 玩家角色卡名称 / 发送者昵称

### 旧系统模板机制

旧系统有两个核心方法:

```python
# 1. 读取模板
def _load_module_message(module: str, key: str, default: str) -> str:
    """从 BotSettingConfig.json → module → key 读取"""

# 2. 格式化模板
def format_msg(template: str, result: str) -> str:
    """{} 替换为 result，无 {} 则直接返回模板"""
    if "{}" in template:
        return template.replace("{}", result)
    return template
```

**旧系统 BotSettingConfig.json 完整结构:**
```json
{
  "内置模块": {
    "custom_greeting": "[角色卡名]-角色描述...\n[格言]"
  },
  "BuiltInOrder": {
    "bot_on_message": "新的一天开始啦~~~~...",
    "bot_off_message": "是时候睡觉啦~~~晚安博士",
    "bot_orderwhite_message": "{}",
    "dismiss_message": "要分道扬镳了吗？..."
  },
  "ob": {
    "ob_join_message": "<>进入OB状态！...\n{}",
    "ob_list_message": "想要看看谁在潜水吗？\n{}"
  },
  "dice_core": {
    "r_message": "让我看看你的祈愿吧。\n{}",
    "ra_message": "命运的齿轮-转动吧！\n{}",
    ...
  },
  "arktrpg": {
    "ark_message": "愿你成为你想要的自己 \n<>进行了{}",
    ...
  }
}
```

### 当前系统自定义机制审查

| 命令/子命令 | 当前是否使用 BotSettingConfig | 旧系统是否使用 | 判定 |
|-----------|---------------------------|-------------|------|
| `.bot on` | ✅ 读取 `BuiltInOrder.bot_on_message` | ✅ | 合规 |
| `.bot off` | ✅ 读取 `BuiltInOrder.bot_off_message` | ✅ | 合规 |
| `.bot orderwhite` | ❌ 硬编码 | ✅ 读取 `BuiltInOrder.bot_orderwhite_message` | **需整改** |
| `.ob join` | ❌ 硬编码 | ❌ 硬编码 (但 config 有模板) | **需整改** |
| `.ob exit` | ❌ 硬编码 | ❌ 硬编码 | 可改进 |
| `.ob list` | ❌ 硬编码 | ❌ 硬编码 (但 config 有模板) | **需整改** |
| `.ob on/off/clr` | ❌ 硬编码 | ❌ 硬编码 | 可改进 |
| `.dismiss` | ✅ 读取 `BuiltInOrder.dismiss_message` (告别) | ✅ | 合规 |
| `.welcome open/close/set/status` | ❌ 硬编码 | ❌ 硬编码 | 合规 (功能型回复) |
| `.order 切换/列表/重建/状态` | ❌ 硬编码 | ❌ 硬编码 | 合规 (功能型回复) |
| `.model list/change/status/reset` | ❌ 硬编码 | ❌ 硬编码 | 合规 (功能型回复) |
| `.master *` | ❌ 硬编码 | ❌ 硬编码 | 合规 (功能型回复) |
| `.send` | ❌ 硬编码 | ❌ 硬编码 | 合规 (功能型回复) |

### ✅ 整改项 5.1: 新增 `format_msg()` 模板格式化方法

当前 `_load_bot_setting()` 只能读取模板，缺少 `{}` 替换的格式化工具。

**整改方案:**
- 在 `builtin.py` 中添加 `_format_msg(template: str, result: str, player_name: str = "") -> str`
- 支持 `{}` → 命令结果替换
- 支持 `<>` → 玩家名称/角色卡名称替换
- 支持 `[]` → 角色卡名称替换 (预留，供后续 dice/ark 使用)

```python
def _format_msg(template: str, result: str = "", player_name: str = "") -> str:
    """
    BotSettingConfig 模板格式化

    占位符:
      {}  → 命令执行结果
      <>  → 玩家名称 (角色卡名 or sender_name)
      []  → 角色卡名称 (预留)

    如果模板中不含 {} 则直接返回模板 (不追加 result)
    """
    text = template
    if player_name:
        text = text.replace("<>", player_name)
    if "{}" in text:
        text = text.replace("{}", result)
    return text
```

### ✅ 整改项 5.2: 补全缺失的 BotSettingConfig 集成

以下命令需要增加 BotSettingConfig 读取:

| 子命令 | BotSettingConfig 键 | 当前行为 | 整改 |
|-------|-------------------|---------|------|
| `.bot orderwhite` | `BuiltInOrder.bot_orderwhite_message` | 硬编码 `"✅ 已关闭/开启本群的免@功能"` | 读取模板，`{}` → 状态文本 |
| `.ob join` | `ob.ob_join_message` | 硬编码 `"✅ 已加入旁观者模式..."` | 读取模板，`<>` → 用户名，`{}` → 确认文本 |
| `.ob list` | `ob.ob_list_message` | 硬编码 `"📋 旁观者列表:\n..."` | 读取模板，`{}` → 列表文本 |

### ✅ 整改项 5.3: BotSettingConfig.json 自动重建补全

当前 `server.py._ensure_bot_setting_config()` 生成的默认模板已包含 `ob` 和 `BuiltInOrder` 区段。确认无遗漏。

---

## 其他发现

### 问题 A: `_load_bot_setting()` 的搜索逻辑差异

**当前系统:**
```python
# 当前 builtin.py
settings.get(module_key, {}).get(setting_key, "")
# 精确匹配 module_key → setting_key
```

**旧系统:**
```python
# 旧系统 _load_bot_setting (全局搜索)
for category in sc.values():
    if isinstance(category, dict) and key in category:
        return category[key]
# 遍历所有模块，找到 key 就返回
```

当前系统要求精确指定 module_key，旧系统是全局搜索。对于 `_load_bot_setting("BuiltInOrder", "bot_on_message")` 这种用法两者结果一致。但旧系统还支持 `_load_bot_setting("custom_greeting")` 这种不指定 module 的简化调用。

**判定:** 当前方式更安全 (不会跨模块冲突)，保持现状。

### ✅ 问题 B: `.bot on/off` 模板读取后的回退逻辑

```python
# 当前 builtin.py L103
custom = _load_bot_setting("BuiltInOrder", "bot_on_message", self.DEFAULT_ON_MSG)
return custom or self.DEFAULT_ON_MSG
```

这里 `_load_bot_setting` 的 default 已经是 `DEFAULT_ON_MSG`，再 `or DEFAULT_ON_MSG` 是冗余的。且如果 BotSettingConfig 返回空字符串，`or` 会跳过空串使用默认值 — 这可能是有意为之 (空串 = 未配置)。

**判定:** 逻辑正确但代码冗余，建议清理。

### ✅ 问题 C: `.model list` 硬编码模型列表

```python
# 当前 builtin.py L329
return (
    "📊 可用模型:\n"
    "  - gpt-4\n"
    "  - gpt-3.5-turbo\n"
    "  - claude-3-opus\n"
    "  - claude-3-sonnet\n\n"
    ...
)
```

模型列表是硬编码的假数据，应从 `config.json` 的 `llm_providers` 或 CherryStudio API 动态获取。

**整改方案:** 从 `ctx.config.get("llm_providers", [])` 读取实际配置的 provider 列表。

---

## 整改优先级汇总

| 优先级 | 整改项 | 涉及命令 | 工作量 |
|--------|-------|---------|--------|
| **P0 - 核心缺陷** | 5.1 新增 `_format_msg()` | 全局基础设施 | ~20 行 |
| **P0 - 核心缺陷** | 4.1 HelpCommand 支持单命令帮助 | `.help` | ~30 行 |
| **P1 - 设计偏差** | 1.1 `.bot` 空参 → build_greeting | `.bot` | ~40 行 |
| **P1 - 设计偏差** | 1.2 `.ob` 空参 → `.ob join` | `.ob` | ~5 行 |
| **P1 - 设计偏差** | 2.1 `.model` 恢复权限检查 | `.model` | ~10 行 |
| **P1 - 设计偏差** | 5.2 补全 BotSettingConfig 集成 | `.bot orderwhite`, `.ob join/list` | ~30 行 |
| **P2 - 体验优化** | 3.1 统一未知子命令响应 | 全部 6 个有子命令的命令 | ~40 行 |
| **P2 - 体验优化** | 4.2 完善所有命令 reminder | 全部 9 个命令 | ~20 行 |
| **P2 - 体验优化** | 2.2 `.send` 兼容简化格式 | `.send` | ~15 行 |
| **P3 - 代码质量** | B 清理冗余回退逻辑 | `.bot on/off` | ~2 行 |
| **P3 - 代码质量** | C `.model list` 动态模型列表 | `.model` | ~20 行 |

**预估总工作量:** ~230 行代码修改/新增
