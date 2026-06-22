# QQ-MCP Bridge v3.0 计划文档

> 最后更新: 2026-06-08
> 文档状态: 活跃开发计划 (Phase 2 已完成, 含 MD-to-Image)

---

## 目录

1. [项目现状](#1-项目现状)
2. [开发路线图](#2-开发路线图)
3. [每个阶段的里程碑](#3-每个阶段的里程碑)
4. [风险评估](#4-风险评估)
5. [技术决策记录](#5-技术决策记录)
6. [总工作量估算](#6-总工作量估算)

---

## 1. 项目现状

### 1.1 核心系统完成度: 97%

v3.0 的核心架构已基本完成，以下子系统已实现且运行稳定:

| 模块 | 文件 | 状态 |
|------|------|------|
| MCP Server 注册与生命周期 | `server.py` | 已完成 |
| NapCat WebSocket 互联桥 | `modules/napcat_bridge.py` | 已完成 |
| SSE 流式解析器 | `modules/sse_parser.py` | 已完成 |
| 消息总线与路由 | `modules/message_bus.py` | 已完成 |
| CherryStudio 模块 (LLM Agent) | `modules/cherrystudio_module.py` | 已完成 |
| 命令模块框架 (注册/分发/会话) | `modules/command_module.py` | 已完成 |
| 会话存储 (LevelDB 兼容) | `modules/conversation_store.py` | 已完成 |
| 状态管理器 (持久化) | `state/manager.py` | 已完成 |
| 错误码体系 | `protocols/error_codes.py` | 已完成 |
| 消息协议定义 | `protocols/messages.py` | 已完成 |
| MD-to-Image 转换 | `modules/md_to_image.py` | 已完成 |

### 1.2 命令系统完成度: 100% (24/24) ✅

**已实现的 24 个命令:**

| 命令 | 类名 | 功能 |
|------|------|------|
| `.help` | `HelpCommand` | 显示帮助信息 |
| `.bot` | `BotCommand` | 机器人开关 (on/off/status/orderwhite) |
| `.order` | `OrderCommand` | 会话/Agent 管理 + 免@白名单 |
| `.model` | `ModelCommand` | 模型切换 (list/change/status/reset) |
| `.ob` | `ObCommand` | 旁观者模式 (join/exit/list/clr/on/off) |
| `.dismiss` | `DismissCommand` | 退群 (管理员专用) |
| `.send` | `SendCommand` | 管理员消息转发 |
| `.master` | `MasterCommand` | 管理员专用 (LLMReset/AllResetAgent) |
| `.welcome` | `WelcomeCommand` | 新成员欢迎设置 |

**新增的 15 个命令 (2026-06-08 移植完成):**

| 子系统 | 命令 | 来源文件 | 状态 |
|--------|------|----------|------|
| 骰子核心 (8) | `.r` `.rh` `.ra` `.show` `.del` `.pc` `.nn` `.st` | `modules/commands/dice.py` | ✅ 已完成 |
| 方舟 TRPG (6) | `.rk` `.rkb` `.rkp` `.sck` `.ark` `.sn` | `modules/commands/ark_trpg.py` | ✅ 已完成 |
| 日志系统 (1) | `.log` (含 7 个子操作) | `modules/commands/log.py` | ✅ 已完成 |

### 1.3 缺失的子系统

| 子系统 | 说明 | 状态 |
|--------|------|------|
| Dice Core | 骰子表达式解析、角色卡存储、8 个命令 | ✅ 已完成 (2026-06-08) |
| Ark TRPG | 方舟 TRPG 技能检定、人物作成、6 个命令 | ✅ 已完成 (2026-06-08) |
| Log System | 群聊日志记录、文件打包、EventHooks | ✅ 已完成 (2026-06-08) |
| EventHooks | 消息生命周期钩子系统 (基础版) | ✅ 已完成 (2026-06-08) |
| 子系统错误码 | 14 个新增错误码 (6000-8999) | ✅ 已完成 (2026-06-08) |
| Install URL | CherryStudio 一键安装链接生成器 | ✅ 已完成 (2026-06-08) |
| EventHooks 增强 | 3 种事件类型 + 优先级排序 | ✅ 已完成 (2026-06-08) |
| MD-to-Image | Markdown→PNG 转换模块, send_local_image, qq_upload_file as_image | ✅ 已完成 (2026-06-08) |
| qq_confirm_response | ~~第 13 个 MCP 工具~~ | — **设计取消** (由 `mark_responding` 自动机制替代) |

### 1.4 测试覆盖现状

| 指标 | 数值 |
|------|------|
| 测试用例总数 | 564 |
| 测试文件数 | 10 |
| 测试代码行数 | ~8,350 |
| 覆盖的模块 | server, message_bus, command_module, napcat_bridge, sse_parser, cherrystudio_module, state_manager, dice_core, ark_trpg, log, hooks, utils, md_to_image |
| 未覆盖的模块 | — |

### 1.5 配置体系现状

- `BotSettingConfig.json`: **新项目尚未创建**，旧项目已有完整模板 (含 `dice_core`, `arktrpg`, `log`, `BuiltInOrder`, `ob` 五个模块区段)
- `pydantic` 依赖: 已在 `pyproject.toml` 声明 (`pydantic>=2.0.0`)，Phase 2 已集成 (`config_models.py` + `state_models.py`)
- `markdown` / `html2image` 依赖: 已在 `pyproject.toml` 声明 (`markdown>=3.5.0`, `html2image>=2.0.0`)，用于 MD-to-Image 模块

---

## 2. 开发路线图

### Phase 1: 核心补全 (Critical Completion)

> **优先级: 高**
> **阶段目标: 完成与旧系统的功能对等，所有旧系统命令在新架构下可用**

---

#### Task 1.1: 骰子核心系统 (Dice Core System)

**概述:** 从旧系统 `Plugins/dice_core/` 移植骰子表达式解析器、角色卡存储系统和 8 个命令到新架构。

**需要创建的文件:**

```
modules/
  dice_core/
    __init__.py          # 包初始化，导出公共 API
    dice_parser.py       # 骰子表达式解析器
    character_store.py   # 角色卡 CRUD 存储
  commands/
    dice.py              # 8 个骰子命令 (Command 子类)
tests/
  test_dice_parser.py    # 解析器单元测试
  test_character_store.py # 存储层单元测试
  test_commands_dice.py  # 命令集成测试
Configuration/
  BotSettingConfig.json  # 消息模板配置 (含所有模块)
```

**1.1.1 移植 `dice_parser.py`** (~81 行)

源文件: `Old/Plugins/dice_core/dice_parser.py`

需要移植的函数:
- `parse_and_roll(expr: str) -> tuple[str, int, list[int]]` -- 解析 XdY 格式骰子表达式
- `check_result(roll: int, dc: int) -> str` -- COC 风格判定 (大成功/大失败/极难/困难/成功/失败)
- `check_critical_d6(values: list[int], face: int) -> tuple[bool, bool]` -- 行于泰拉暴击判定 (至少半数最大值/最小值)

适配要点:
- 无外部依赖，可直接复制
- 保持函数签名不变，新旧系统调用方式一致

**1.1.2 移植 `character_store.py`** (~183 行)

源文件: `Old/Plugins/dice_core/character_store.py`

核心功能:
- 数据存储路径: `modules/dice_core/data/{uid}/cards/{card_name}.json`
- 多卡管理: 每用户最多 5 张角色卡，支持创建/切换/删除/重命名
- 默认卡模板: `DEFAULT_CARDS` (ark 系统 + coc 系统)
- 群组数据兼容: `load_group_data()` 支持从旧格式恢复

需要移植的函数:
- `load_or_default()`, `save()`, `save_card()`, `delete_card()`, `rename_card()`
- `list_cards()`, `get_active_card()`, `set_active_card()`
- `load_player()`, `save_player()`, `load_card()`
- `set_skill()`, `format_card()`
- `DEFAULT_CARDS` 常量

适配要点:
- 数据目录从 `Plugins/dice_core/data/` 改为 `modules/dice_core/data/`
- `logger` 名称改为 `modules.dice_core.character_store`
- 保持 JSON 文件格式兼容 (旧用户数据可无缝迁移)

**1.1.3 实现 8 个骰子命令** (~350 行)

源文件: `Old/Plugins/dice_core/commands.py` (419 行)

创建 `modules/commands/dice.py`，包含以下 Command 子类:

| 命令 | 类名 | 功能 | 特殊说明 |
|------|------|------|----------|
| `.r` | `RDiceCommand` | 通用骰子投掷 | 支持 XdY+Z, DC 判定, n#重复 |
| `.rh` | `RhCommand` | 暗骰 (结果私聊) | 需调用 `napcat_bridge.send_message()` 私聊发送 |
| `.ra` | `RaCommand` | d100 技能/属性检定 | 从角色卡读取技能值，COC 规则判定 |
| `.show` | `ShowCommand` | 展示角色卡 | 调用 `format_card()` |
| `.del` | `DelCommand` | 删除角色卡/技能 | 子命令: card, [技能名] |
| `.pc` | `PcCommand` | 角色卡管理 (多卡切换) | 子命令: switch/new/del, 直接名字=切换 |
| `.nn` | `NnCommand` | 重命名角色卡 | 单参=重命名当前卡，双参=指定卡 |
| `.st` | `StCommand` | 设置属性/技能值 | 支持紧凑格式 "力量5敏捷3" |

适配要点:
- 继承新的 `modules.command_module.Command` 基类 (而非旧的 `OrderSystem.base.Command`)
- `handle()` 签名: `async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext)`
- 消息类型从 `QQMessage` 改为 `ParsedMessage` (通过 `msg.raw.sender_id`, `msg.raw.target_id` 等访问)
- 暗骰私聊发送: 使用 `ctx.napcat_bridge.send_message(OutgoingMessage(...))` 替代 `ctx.nc.send_msg()`
- BotSettingConfig 模板读取: 复用 `builtin.py` 中的 `_load_bot_setting()` 模式，提取为公共工具函数

**1.1.4 创建 BotSettingConfig.json**

基于旧项目 `Old/Configuration/BotSettingConfig.json` 创建，保留所有现有模板:
- `dice_core` 区段: `r_message`, `ra_message`, `del_card_message`, `nn_message`, `show_message`, `st_message`
- `arktrpg` 区段: `ark_message`, `rk_message`, `rkb_message`, `rkp_message`, `sck_message`, `sn_rk_message`
- `log` 区段: `log_new_message`, `log_list_message`
- `BuiltInOrder` 区段: 已移植命令的模板
- `ob` 区段: 旁观者模式模板

**1.1.5 更新命令注册**

修改 `modules/commands/__init__.py` 和 `modules/command_module.py` 的 `CommandRegistry.discover_builtin()`:

```python
# modules/commands/__init__.py 新增导出
from .dice import (
    RDiceCommand, RhCommand, RaCommand, ShowCommand,
    DelCommand, PcCommand, NnCommand, StCommand,
)

# modules/command_module.py discover_builtin() 新增注册
from modules.commands.dice import (
    RDiceCommand, RhCommand, RaCommand, ShowCommand,
    DelCommand, PcCommand, NnCommand, StCommand,
)
for cmd_class in [RDiceCommand, RhCommand, RaCommand, ShowCommand,
                  DelCommand, PcCommand, NnCommand, StCommand]:
    self.register(cmd_class())
```

**1.1.6 测试计划** (~200 行测试)

| 测试文件 | 测试内容 | 预估用例数 |
|----------|----------|-----------|
| `test_dice_parser.py` | XdY 解析、加减值、重复掷、DC 判定、暴击判定、边界值 | ~60 |
| `test_character_store.py` | CRUD 操作、多卡管理、5 卡上限、格式兼容、并发读写 | ~60 |
| `test_commands_dice.py` | 8 个命令的 handle() 方法、参数解析、帮助文本 | ~80 |

**工作量估算:** ~610 行代码 + ~200 行测试

---

#### Task 1.2: 明日方舟 TRPG 插件 (Ark TRPG Plugin)

**概述:** 从旧系统 `Plugins/ark_trpg/` 移植方舟 TRPG 技能检定、人物作成和 6 个命令。

**需要创建的文件:**

```
modules/
  ark_trpg/
    __init__.py          # 包初始化
    skills.py            # 技能-属性映射表
  commands/
    ark_trpg.py          # 6 个方舟 TRPG 命令
tests/
  test_ark_skills.py     # 技能映射测试
  test_commands_ark.py   # 命令集成测试
```

**1.2.1 移植 `skills.py`** (~30 行)

源文件: `Old/Plugins/ark_trpg/skills.py`

核心数据:
- `SKILL_TO_ATTR`: 约 55 个技能到 6 个基础属性的映射
- `BASE_ATTRS`: 6 个基础属性 (精神意志, 个人魅力, 反应机动, 物理强度, 经验智慧, 源石技艺适应性)
- `find_attr(skill_name)`: 查找技能对应属性
- `is_attr(name)`: 判断是否为基础属性

适配要点: 无外部依赖，可直接复制。

**1.2.2 实现 6 个方舟 TRPG 命令** (~260 行)

源文件: `Old/Plugins/ark_trpg/commands.py` (290 行)

创建 `modules/commands/ark_trpg.py`:

| 命令 | 类名 | 功能 | 特殊说明 |
|------|------|------|----------|
| `.rk` | `RkCommand` | 行于泰拉技能检定 | 格式: [骰面] [技能名] [技能值]/[难度] |
| `.rkb` | `RkbCommand` | 技能检定 (奖励骰) | 额外掷 N 个骰子取最优 |
| `.rkp` | `RkpCommand` | 技能检定 (惩罚骰) | 额外掷 N 个骰子取最差 |
| `.sck` | `SckCommand` | 自控检定 (d10 vs 精神意志) | **有已知 Bug** |
| `.ark` | `ArkCommand` | 泰拉人物作成 (掷 7 属性) | 调用 `BASE_ATTRS` 循环掷骰 |
| `.sn` | `SnCommand` | 设置群名片模板 | **有已知 Bug** |

**1.2.3 修复已知 Bug**

**Bug 1: `.sck` NameError (旧文件第 215 行)**

```python
# 旧代码 (有 Bug):
if total > will:   # 'will' 未定义!
    result_msg = f"...{total}/{will}..."

# 修复方案:
char = load_or_default(uid, system="ark", group_id=msg.group_id)
will_value = char.get("attributes", {}).get("精神意志", 0)
if total > will_value:
    result_msg = f"...{total}/{will_value}..."
```

问题: `will` 变量在使用前未定义。应从角色卡读取 `精神意志` 属性作为自控阈值。
同时修复第 216 行的逻辑错误: `if total > will` 条件下，`'失败' if total > will else '成功'` 永远返回 "失败"，应改为直接输出 "失败"。

**Bug 2: `.sn` 语法错误 (旧文件第 276 行)**

```python
# 旧代码 (有 Bug):
char = load_or_default(uid, system="ark", group_id=gid) (
    f"{char.get('name', msg.sender_name)} ..."
)

# 修复方案:
char = load_or_default(uid, system="ark", group_id=gid)
card = (
    f"{char.get('name', msg.sender_name)} "
    f"HP{char.get('hp', 0)}/{char.get('hp_max', 0)} "
    f"SP{char.get('sp', 0)}/{char.get('sp_max', 0)}"
)
```

问题: `load_or_default(...)` 调用后紧跟 `(` 形成语法错误 -- 缺少 `card =` 赋值。

**1.2.4 更新命令注册**

```python
# modules/commands/__init__.py 新增
from .ark_trpg import RkCommand, RkbCommand, RkpCommand, SckCommand, ArkCommand, SnCommand

# modules/command_module.py discover_builtin() 新增
from modules.commands.ark_trpg import (
    RkCommand, RkbCommand, RkpCommand, SckCommand, ArkCommand, SnCommand,
)
for cmd_class in [RkCommand, RkbCommand, RkpCommand, SckCommand, ArkCommand, SnCommand]:
    self.register(cmd_class())
```

**1.2.5 测试计划** (~100 行测试)

| 测试文件 | 测试内容 | 预估用例数 |
|----------|----------|-----------|
| `test_ark_skills.py` | 技能映射完整性、6 属性覆盖、边界输入 | ~20 |
| `test_commands_ark.py` | 6 个命令的 handle()、Bug 修复验证、参数解析 | ~80 |

**工作量估算:** ~290 行代码 + ~100 行测试

---

#### Task 1.3: 日志系统 (Log System)

**概述:** 从旧系统 `Plugins/log.py` 移植群聊日志记录系统，包括 `.log` 命令和 EventHooks 消息钩子。

**需要创建的文件:**

```
modules/
  commands/
    log.py               # .log 命令 (7 个子操作)
  hooks/
    __init__.py           # EventHooks 注册入口
    log_hook.py           # 日志消息钩子
tests/
  test_commands_log.py   # 日志命令测试
  test_log_hook.py       # 钩子测试
```

**1.3.1 移植 `.log` 命令** (~82 行)

源文件: `Old/Plugins/log.py`

子操作:

| 子命令 | 功能 | 实现依赖 |
|--------|------|----------|
| `.log new <名称>` | 新建日志并开始记录 | 需要日志文件写入逻辑 |
| `.log on` | 继续记录 (从暂停恢复) | 需要 `_log_paused` 状态管理 |
| `.log off` | 暂停记录 | 需要暂停标记 |
| `.log end` | 完成记录并发送日志文件 | 需要 zip 打包 + 文件发送 |
| `.log list` | 查看本群日志列表 | 扫描日志目录 |
| `.log get <名称>` | 手动获取日志文件 | 文件读取 + 发送 |
| `.log del <名称>` | 删除日志 (不可逆) | 文件删除 |

适配要点:
- 旧系统的 `ar.log_new()`, `ar.log_resume()`, `ar.log_pause()` 等方法分散在 `AutoReply` 类中
- 新系统需将这些逻辑内聚到 LogCommand 类和独立的日志服务中
- 日志文件存储路径: `PlayerLog/logs/{group_id}/{log_name}.log`
- zip 打包路径: `PlayerLog/logs/{group_id}/{log_name}.zip`

**1.3.2 EventHooks 消息录制**

旧系统通过 `OrderSystem/base.py` 的 `EventHooks` 类实现:
```python
@dataclass
class EventHooks:
    on_message: list[Callable] = field(default_factory=list)
```

新系统实现方案:
- 在 `modules/hooks/` 下创建钩子注册机制
- 日志钩子函数在每条群消息到达时判断是否有活跃日志，如有则写入
- 跳过旁观者消息 (通过 `state_manager` 查询)

详见 [Task 2.2](#task-22-eventhooks-生命周期系统)，Phase 1 先实现日志专用的最小化钩子。

**1.3.3 测试计划** (~50 行测试)

| 测试文件 | 测试内容 | 预估用例数 |
|----------|----------|-----------|
| `test_commands_log.py` | 7 个子操作、状态流转、文件 I/O | ~35 |
| `test_log_hook.py` | 消息录制钩子、旁观者跳过 | ~15 |

**工作量估算:** ~150 行代码 + ~50 行测试

---

#### ~~Task 1.4: qq_confirm_response MCP 工具~~ — 设计取消

> **此任务已取消。** `qq_confirm_response` 在 v3.0 设计中被主动移除，其功能由 Bridge 内部的 `mark_responding`/`unmark_responding` 自动机制替代。旧系统中该工具定位不明（最终还是需要调用 `qq_send_message`），新架构无需此工具。无需额外工作量。

---

### Phase 2: 增强与优化 (Enhancement & Optimization)

> **优先级: 中**
> **阶段目标: 提升系统健壮性，添加便利功能，完善配置验证**

---

#### Task 2.1: Install URL 生成器

**概述:** 从旧系统 `Built_in/generate_install_url.py` 移植 CherryStudio 一键安装链接生成器。

**源文件:** `Old/Built_in/generate_install_url.py` (88 行)

**需要创建的文件:**

```
tools/
  generate_install_url.py   # 安装链接生成脚本
```

功能:
- 两种安装模式: `manual` (Python 路径 + server.py) 和 `uvx` (UVX git 直装)
- 生成 `cherrystudio://mcp/install?servers=<base64>` 格式的 URL
- 输出到 `install_info.txt` (已 gitignore)

适配要点:
- 仓库地址更新: `git+https://github.com/RhineLab-magellan/cherrystudio-qq-mcp.git`
- 入口命令更新: `cherrystudio-qq-mcp` (与 `pyproject.toml` 的 `[project.scripts]` 一致)

**工作量估算:** ~90 行代码

---

#### Task 2.2: EventHooks 生命周期系统

**概述:** 实现完整的消息生命周期钩子系统，允许模块注册 pre/post 消息处理回调。

**需要创建的文件:**

```
modules/
  hooks/
    __init__.py          # EventHooks 公共接口
    manager.py           # 钩子管理器 (注册/分发/优先级)
```

设计参考 (旧系统 `OrderSystem/base.py`):
```python
# 旧设计: 简单列表
@dataclass
class EventHooks:
    on_message: list[Callable] = field(default_factory=list)

# 新设计: 带优先级和过滤的钩子管理器
class HookManager:
    def register(self, event: str, callback: Callable, priority: int = 0, filter_fn: Callable | None = None):
        """注册钩子。event: 'on_message' | 'pre_command' | 'post_command'"""
    async def dispatch(self, event: str, *args, **kwargs):
        """分发事件到所有注册的钩子 (按优先级排序)"""
```

需要支持的钩子类型:
- `on_message`: 每条消息到达时 (用于日志录制、消息统计等)
- `pre_command`: 命令执行前 (用于权限检查、频率限制等)
- `post_command`: 命令执行后 (用于审计日志、响应修改等)

与旧系统的区别:
- 旧系统仅支持 `on_message`，新系统扩展为三种事件
- 新增优先级排序 (priority 数值越小越先执行)
- 新增过滤函数 (filter_fn)，仅匹配特定条件的消息才触发

**工作量估算:** ~80 行代码 (HookManager) + 集成到 MessageBus/CommandModule

---

#### Task 2.3: 会话校验与恢复 (reconcile_sessions)

**概述:** 启动时验证所有会话文件的完整性，自动修复损坏的会话。

**需要修改的文件:**

```
server.py               # 启动时调用 reconcile
modules/
  conversation_store.py  # 添加校验方法
```

功能:
- 启动时扫描 `data/sessions/` 目录下所有会话文件
- 验证 JSON 格式完整性 (try-parse)
- 验证必要字段存在 (session_id, created_at, agent_name)
- 损坏的文件自动备份到 `data/sessions/.corrupted/` 并创建新会话
- 记录修复日志

**工作量估算:** ~60 行代码

---

#### Task 2.4: 错误码补全

**概述:** 为新增子系统扩展错误码范围。

**需要修改的文件:**

```
protocols/error_codes.py
```

当前错误码分配:
- 1000-1999: NapCat 互联桥 (已用 6 个)
- 2000-2999: 消息互联桥 (已用 5 个)
- 3000-3999: 命令模块 (已用 6 个)
- 4000-4999: CherryStudio 模块 (已用 9 个)
- 5000-5999: Server 模块 (已用 5 个)
- 9000-9999: 通用错误 (已用 4 个)

**需要新增的错误码:**

| 范围 | 子系统 | 新增错误码 |
|------|--------|-----------|
| 6000-6999 | 骰子系统 | `DICE_PARSE_FAILED`, `DICE_INVALID_EXPR`, `CHARACTER_NOT_FOUND`, `CHARACTER_LIMIT_EXCEEDED`, `CHARACTER_SAVE_FAILED` |
| 7000-7999 | 方舟 TRPG | `ARK_SKILL_NOT_FOUND`, `ARK_INVALID_DICE_COUNT`, `ARK_SCK_FAILED` |
| 8000-8999 | 日志系统 | `LOG_NOT_FOUND`, `LOG_ALREADY_ACTIVE`, `LOG_WRITE_FAILED`, `LOG_ZIP_FAILED`, `LOG_SEND_FAILED` |

预估新增约 15-24 个错误码。

**工作量估算:** ~40 行代码

---

#### Task 2.5: pydantic 配置验证

**概述:** 实际使用已声明的 `pydantic>=2.0.0` 依赖，为配置文件和共享状态创建验证模型。

**需要创建的文件:**

```
protocols/
  config_models.py       # 配置文件的 Pydantic 模型
state/
  state_models.py        # SharedState 的 Pydantic 模型
```

**配置验证模型 (`config_models.py`):**

```python
from pydantic import BaseModel, Field

class NapCatConfig(BaseModel):
    ws_url: str = Field(default="ws://localhost:3001")
    access_token: str = Field(default="")

class CherryStudioConfig(BaseModel):
    api_url: str = Field(default="http://localhost:3000")
    agent_id: str = Field(default="default")
    timeout: int = Field(default=120, ge=10, le=600)

class BridgeConfig(BaseModel):
    admin_qq: str = Field(default="")
    command_prefix: str = Field(default=".")
    napcat: NapCatConfig = Field(default_factory=NapCatConfig)
    cherrystudio: CherryStudioConfig = Field(default_factory=CherryStudioConfig)
```

**SharedState 模型 (`state_models.py`):**

```python
from pydantic import BaseModel, Field

class SharedStateModel(BaseModel):
    observers: dict[str, list[str]] = Field(default_factory=dict)
    ob_groups: list[str] = Field(default_factory=list)
    bot_blacklist: list[str] = Field(default_factory=list)
    order_whitelist: list[str] = Field(default_factory=list)
    saved_models: dict[str, str] = Field(default_factory=dict)
    active_agents: dict[str, str] = Field(default_factory=dict)
    welcome_settings: dict[str, dict] = Field(default_factory=dict)
```

适配要点:
- 当前 `state/manager.py` 中的 `StateManager` 使用裸 `dict` 操作状态
- 引入 Pydantic 模型后，在序列化/反序列化时自动验证
- 需要确保向后兼容 (旧的 `shared_state.json` 文件能正确加载)

**工作量估算:** ~200 行代码

---

#### Task 2.6: MD-to-Image 模块 ✅ 已完成 (2026-06-08, v2 增强 2026-06-09)

**概述:** 新增独立的 Markdown-to-PNG 转换模块, 支持将 Markdown 内容渲染为专业样式图片并通过 QQ 发送。v2 增强新增本地 Playwright 浏览器、Pillow 纯 Python 回退和 Cherry Studio 沙盒兼容。

**新增文件:**

```
modules/
  md_to_image.py          # Markdown-to-PNG 转换模块 (~780 行, v2)
tests/
  test_md_to_image.py     # 32 项综合测试 (~450 行)
```

**修改文件:**

```
modules/
  napcat_bridge.py        # 新增 send_local_image() 方法 (~55 行)
server.py                 # qq_upload_file 新增 as_image 参数 (~30 行)
pyproject.toml            # 新增 html2image>=2.0.0, markdown>=3.5.0
```

功能:
- `render_markdown()`: Markdown → HTML 渲染, 内置专业 CSS 主题
- 三级截图回退: CDP (Chrome DevTools Protocol) → CLI `--screenshot` → html2image 库
- 浏览器检测: 优先使用 Edge (`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`)
- 支持文本输入和文件输入, 自定义 CSS, 自定义宽度
- `send_local_image()`: 本地 PNG/JPG → base64 → OneBot send_msg 图片段
- `qq_upload_file` MCP 工具 `as_image=True`: Markdown 渲染为 PNG 内联图片发送

**工作量估算:** ~220 行代码 + ~450 行测试 (实际)

---

### Phase 3: 测试与稳定 (Testing & Stabilization)

> **优先级: 中**
> **阶段目标: 全面测试覆盖，性能优化，文档完善，准备 v3.0 GA 发布**

---

#### Task 3.1: 集成测试

**概述:** 编写端到端集成测试，验证各子系统间的协作。

**需要创建的文件:**

```
tests/
  test_integration_dice.py    # 骰子系统端到端测试
  test_integration_ark.py     # 方舟 TRPG 端到端测试
  test_integration_log.py     # 日志系统端到端测试
  test_integration_flow.py    # 完整消息流测试
```

测试场景:
- **消息流:** 原始消息 -> MessageBus -> CommandModule -> Command.handle() -> OutgoingMessage
- **骰子流程:** `.st 力量5` -> `.show` -> `.r 3d6` -> `.ra 力量` (验证数据连贯性)
- **方舟流程:** `.st 刀剑7` -> `.rk 6 刀剑` -> `.ark 3` (验证技能-属性联动)
- **日志流程:** `.log new test` -> 发送若干消息 -> `.log off` -> `.log on` -> `.log end` (验证状态机)
- **跨系统:** 暗骰 + 旁观者转发、日志 + 旁观者跳过

**工作量估算:** ~300 行测试

---

#### Task 3.2: 性能优化

**概述:** 识别并优化热路径性能瓶颈。

优化方向:

| 优化项 | 目标模块 | 方法 |
|--------|----------|------|
| SSE 解析 | `modules/sse_parser.py` | Profile 流式解析，减少不必要的字符串拷贝 |
| 消息路由 | `modules/message_bus.py` | 优化路由决策，减少 Queue 竞争 |
| 会话查找 | `modules/command_module.py` | SessionHandler dict 查找 O(1)，当前已是 O(1) |
| 角色卡 I/O | `modules/dice_core/character_store.py` | 考虑内存缓存 (LRU)，减少磁盘读写 |
| 消息吞吐 | 全链路 | Benchmark: 目标 >100 msg/s 处理能力 |

工具:
- `cProfile` / `yappi` (异步兼容) 进行性能分析
- `pytest-benchmark` 进行基准测试

**工作量估算:** 性能分析报告 + 优化代码 (视瓶颈情况而定)

---

#### Task 3.3: 文档更新

**概述:** 更新现有 4 份文档，补充新模块信息。

**需要更新的文档:**

| 文档 | 路径 | 更新内容 |
|------|------|----------|
| 实现计划 | `docs/IMPLEMENTATION_PLAN.md` | 补充 Phase 1-3 完成状态 |
| 设计文档 | `docs/DESIGN_PHASE2_SSE_MCP.md` | 补充 EventHooks、HookManager 设计 |
| 协议文档 | `docs/PROTOCOL.md` | 补充骰子/方舟/日志的错误码 |
| 测试路径 | `docs/CHERRYSTUDIO_TEST_PATH.md` | 更新测试覆盖率报告 |

**需要新增的文档:**

| 文档 | 路径 | 内容 |
|------|------|------|
| 用户命令手册 | (面向用户，非 docs/) | 所有 24 个命令的使用说明、示例 |
| ~~安装部署指南~~ | ~~`docs/INSTALL_GUIDE.md`~~ | ✅ 已完成 (2026-06-09): 从下载到部署全流程 |

**工作量估算:** ~200 行文档更新

---

## 3. 每个阶段的里程碑

### Phase 1 里程碑: 功能对等 (Feature Parity)

**验收标准:**

- [x] 所有 24 个命令在新系统中可正常执行
- [x] 旧系统的角色卡数据可无缝迁移到新系统
- [x] `.r 3d6`, `.ra 侦查`, `.rk 6 刀剑 7/12` 等典型用例输出正确
- [x] `.log new/on/off/end` 完整生命周期可运行
- [x] BotSettingConfig.json 消息模板正常加载
- [x] 已知 Bug (.sck NameError, .sn 语法错误) 已修复
- [x] 新增测试 >= 350 个用例
- [x] MCP 工具总数达到 13 个

**目标:** 新系统可完全替代旧系统投入使用

### Phase 2 里程碑: 增强完善 (Enhanced)

**验收标准:**

- [x] 安装链接生成器可用 (manual + uvx 模式)
- [x] EventHooks 系统支持 3 种事件类型 (on_message, pre_command, post_command)
- [x] 启动时自动校验会话文件，损坏文件自动修复
- [x] 新增子系统错误码覆盖完整 (6000-8999 范围)
- [x] config.json 和 SharedState 有 Pydantic 模型验证
- [x] MD-to-Image 模块: Markdown→PNG 渲染, send_local_image, qq_upload_file as_image
- [ ] 向后兼容旧配置数据

**目标:** 系统具备生产级的健壮性和可维护性

### Phase 3 里程碑: 稳定发布 (Stable Release)

**验收标准:**

- [ ] 集成测试覆盖所有核心用户流程
- [ ] 性能基线达标 (消息处理 >100 msg/s)
- [ ] 所有文档更新完成
- [ ] 用户命令手册发布
- [ ] `pyproject.toml` 版本号更新为 `3.0.0` (当前已是 3.0.0)
- [ ] `Development Status` 从 `4 - Beta` 升级为 `5 - Production/Stable`
- [ ] 全部测试通过，覆盖率 >= 80%

**目标:** v3.0 GA (General Availability) 发布

---

## 4. 风险评估

### 4.1 旧系统 Bug 移植风险

**风险等级: ~~中~~ ✅ 已消除**

旧系统代码中存在已知 Bug (详见 Task 1.2.3):
- `.sck` 命令的 `will` 未定义 (NameError) -- ~~运行时必崩~~ ✅ 已修复，从 `char["attributes"]["精神意志"]` 读取
- `.sn` 命令的 `card =` 缺失 (SyntaxError) -- ~~加载时必崩~~ ✅ 已修复，拆分为两条语句
- `.sck` 命令 `result_msg` 成功分支未定义 -- ✅ 已修复，添加 `else` 分支
- `.pc new` 命令 `save_card`/`DEFAULT_CARDS` 运行时 NameError -- ✅ 已修复，顶层导入
- `DEFAULT_CARDS` 浅拷贝导致模板污染 -- ✅ 已修复，使用 `copy.deepcopy()`

**实际结果:**
- 所有已知 Bug 已在 Phase 1 移植过程中逐一修复
- 每个 Bug 均编写了回归测试，测试全部通过

### 4.2 EventHooks 架构差异风险

**风险等级: ~~中~~ ✅ 已消除**

旧系统的 `OrderSystem/base.py` 中 `EventHooks` 是简单的 `list[Callable]` 设计:
```python
@dataclass
class EventHooks:
    on_message: list[Callable] = field(default_factory=list)
```

新系统是异步架构 (asyncio)，钩子函数需要是 `async` 的。

**实际结果:**
- ✅ HookManager 已支持 3 种事件类型: `on_message`、`pre_command`、`post_command`
- ✅ 支持优先级排序 (priority) 和过滤函数 (filter_fn)
- ✅ 异步回调通过 `asyncio.create_task()` 执行
- ✅ 日志录制系统已通过 HookManager 集成 `on_message` 事件

### 4.3 pydantic 集成风险

**风险等级: 低-中**

当前系统使用 `dataclass` 和裸 `dict` 管理配置和状态。引入 Pydantic 模型可能需要:
- 修改 `StateManager` 的序列化/反序列化逻辑
- 修改 `SharedState` 的读写方式
- 确保旧格式 JSON 文件能正确加载 (向后兼容)

**应对策略:**
- Pydantic 模型仅用于验证层，不替换内部数据结构
- 在 `load()` / `save()` 时使用 `model_validate()` 进行校验
- 提供 `ConfigDict(extra="allow")` 允许未知字段 (向后兼容)
- 渐进式引入: 先 config.json，再 SharedState

### 4.4 旧系统 OrderSystem 与新 CommandRegistry 的差异

**风险等级: ~~低~~ ✅ 已消除**

旧系统的命令注册通过 `OrderSystem/base.py` 的 `Command` 基类和 `PluginContext`:
```python
# 旧系统
class Command:
    async def handle(self, args: str, msg: QQMessage, ctx: CommandContext) -> str | None

# 新系统
class Command:
    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None
```

主要差异:
- 消息类型: `QQMessage` -> `ParsedMessage` (字段映射需要适配)
- 上下文: 旧 `CommandContext(nc, auto_reply)` -> 新 `CommandContext(state_manager, napcat_bridge, config, send_queue, ...)`
- 旧系统通过 `ctx.auto_reply._load_module_message()` 读取模板，新系统需通过 `_load_bot_setting()` 工具函数

**实际结果:**
- ✅ 所有 15 个移植命令已完成 API 适配 (`msg.raw.sender_id`, `msg.raw.target_id`, `ctx.napcat_bridge` 等)
- ✅ `_load_bot_setting()` 和 `format_msg()` 已提取为公共工具 (`modules/commands/utils.py`)
- ✅ `discover_builtin()` 已注册全部 24 个命令

### 4.5 数据迁移风险

**风险等级: ~~低~~ ✅ 已消除**

旧系统的角色卡数据存储在 `Plugins/dice_core/data/` 目录，新系统将存储在 `data/` (项目根目录)。

**实际结果:**
- ✅ `character_store.py` 的 `DATA_DIR` 已指向 `Path(__file__).parent.parent.parent / "data"` (项目根 `data/`)
- ✅ JSON 文件格式完全兼容，迁移仅需复制文件
- ✅ `temp_data_dir` 测试 fixture 确保测试数据不影响生产目录

---

## 5. 技术决策记录

### TD-001: 命令插件目录结构

**决策:** 新的命令插件放在 `modules/commands/` 目录下

**理由:**
- 与现有 `builtin.py` 保持一致的目录结构
- `CommandRegistry.discover_builtin()` 已从此包自动发现命令
- 避免创建新的顶级目录

**文件命名规范:**
- 骰子命令: `modules/commands/dice.py`
- 方舟 TRPG 命令: `modules/commands/ark_trpg.py`
- 日志命令: `modules/commands/log.py`

### TD-002: 子系统包结构

**决策:** 骰子和方舟子系统作为独立包放在 `modules/` 下

**理由:**
- 这些子系统有独立的数据存储、解析逻辑，不仅仅是命令
- 独立包便于管理数据目录和内部模块

**结构:**
```
modules/
  dice_core/             # 骰子核心 (解析器 + 存储)
    __init__.py
    dice_parser.py
    character_store.py
  ark_trpg/              # 方舟 TRPG (技能映射)
    __init__.py
    skills.py
data/                    # 运行时数据 (项目根目录, gitignored)
  {qq_id}/cards/         # 角色卡数据
  logs/                  # 日志记录
```

> **注:** 数据存储从模块目录移至项目根 `data/`，`character_store.py` 的 `DATA_DIR` 指向 `Path(__file__).parent.parent.parent / "data"`。

### TD-003: BotSettingConfig.json 模板机制

**决策:** 使用旧系统已有的 `BotSettingConfig.json` 模板体系

**理由:**
- 旧项目的 `Configuration/BotSettingConfig.json` 已经为 `dice_core`, `arktrpg`, `log`, `BuiltInOrder`, `ob` 五个模块准备了完整的消息模板
- `builtin.py` 中已实现 `_load_bot_setting()` 读取函数
- 用户已习惯通过此文件定制消息

**实施:**
- 将 `_load_bot_setting()` 从 `builtin.py` 提取为公共工具函数 (放入 `modules/commands/utils.py`)
- 创建 `Configuration/BotSettingConfig.json`，包含所有模块的默认模板

### TD-004: 命令注册模式

**决策:** 遵循现有的 `CommandRegistry` 自动发现模式

**注册流程:**
1. 命令类继承 `modules.command_module.Command`
2. 在 `modules/commands/__init__.py` 导出
3. 在 `CommandRegistry.discover_builtin()` 中注册实例

**理由:**
- 现有 24 个命令已通过此模式正常工作
- 支持热重载 (`reload_config()` 会清空并重新发现)
- 无需引入插件加载框架 (如 stevedore)

### TD-005: 测试目录与命名

**决策:** 测试放在 `tests/` 目录，遵循现有命名规范

**规范:**
- 文件名: `test_{模块名}.py` (如 `test_dice_parser.py`)
- 类名: `Test{功能名}` (如 `TestParseAndRoll`)
- 方法名: `test_{场景描述}` (如 `test_basic_3d6`)
- pytest 配置: `asyncio_mode = "auto"` (已在 `pyproject.toml` 设置)

### TD-006: 公共工具函数提取

**决策:** 将 `_load_bot_setting()` 从 `builtin.py` 提取到独立的工具模块

**理由:**
- 当前 `_load_bot_setting()` 是 `builtin.py` 的模块级函数 (带下划线前缀，表示私有)
- 骰子、方舟、日志命令都需要读取 BotSettingConfig 模板
- 避免在每个命令文件中重复实现

**实施:**
```
modules/commands/
  utils.py              # 公共工具函数 (_load_bot_setting 等)
```

---

## 6. 总工作量估算

### 按阶段汇总

| 阶段 | 任务数 | 预估代码行数 | 预估测试行数 | 预估时间 |
|------|--------|-------------|-------------|---------|
| Phase 1: 核心补全 | 3 | ~1,050 (实际 ~1,100) | ~350 (实际 ~680) | 3-4 天 (实际 1 天) |
| Phase 2: 增强优化 | 6 | ~690 | ~450 | 2-3 天 |
| Phase 3: 测试稳定 | 3 | ~0 | ~300 | 2-3 天 |
| **合计** | **12** | **~1,740** | **~1,100** | **7-10 天** |

### 按任务明细

| 任务 | 代码行数 | 测试行数 | 新建文件 | 修改文件 |
|------|---------|---------|---------|---------|
| 1.1 骰子核心系统 | ~610 (实际 ~630) | ~200 (实际 ~350) | 6 (实际 4) | 2 (实际 4) |
| 1.2 方舟 TRPG | ~290 (实际 ~260) | ~100 (实际 ~170) | 4 (实际 2) | 2 (实际 3) |
| 1.3 日志系统 | ~150 (实际 ~210) | ~50 (实际 ~160) | 4 (实际 1) | 1 (实际 4) |
| ~~1.4 qq_confirm_response~~ | — | — | — | — **设计取消** |
| 2.1 Install URL | ~90 | ~0 | 1 | 0 |
| 2.2 EventHooks | ~80 | ~0 | 2 | 2 |
| 2.3 会话校验 | ~60 | ~0 | 0 | 2 |
| 2.4 错误码补全 | ~40 (实际 ~30) | ~0 | 0 | 1 (实际 1) |
| 2.5 pydantic 验证 | ~200 | ~0 | 2 | 1 |
| 2.6 MD-to-Image | ~220 (实际) | ~450 (实际) | 1 | 3 |
| 3.1 集成测试 | ~0 | ~300 | 4 | 0 |
| 3.2 性能优化 | 待定 | 待定 | 0 | 待定 |
| 3.3 文档更新 | ~0 | ~0 | 1 | 4 |

### 命令完成度预测

| 时间节点 | 已完成命令 | 完成度 |
|----------|-----------|--------|
| ~~Phase 1 前~~ | ~~9/24~~ | ~~37.5%~~ |
| ~~Phase 2 完成~~ | ~~24/24~~ | ~~100%~~ |
| **当前 (含 MD-to-Image)** | **24/24** | **100%+** |
| Phase 3 完成后 | 24/24 + 稳定 | GA Ready |

### 测试覆盖预测

| 时间节点 | 测试用例数 | 预估覆盖率 |
|----------|-----------|-----------|
| ~~Phase 1 前~~ | ~~365~~ | ~~70.9%~~ |
| ~~Phase 2 完成~~ | ~~532~~ | ~~82%~~ |
| **当前 (MD-to-Image 完成)** | **564** | **~84%** |
| Phase 3 完成后 | ~604+ | >=85% |

---

> 本文档随开发进度持续更新。每个任务完成后，在对应 Phase 里程碑中勾选验收项。最后更新: 2026-06-08 (Phase 2 完成, 含 MD-to-Image)
