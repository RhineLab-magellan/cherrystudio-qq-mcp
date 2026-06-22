"""
骰子命令 (.r .rh .ra .show .del .pc .nn .st)

移植自旧项目 Plugins/dice_core/commands.py
适配新架构 Command / CommandContext / ParsedMessage 接口。

修复:
  - .pc new: save_card 未导入 (NameError) -> 直接从模块顶层导入
  - DEFAULT_CARDS 浅拷贝 -> 改用 copy.deepcopy
  - 移除无用 RDiceTemplate / _fmt 死代码
  - 移除旧 QQMessage / OrderSystem.base 依赖
"""

import copy
import random
import re
import logging

from modules.command_module import Command, CommandContext
from protocols.messages import ParsedMessage, OutgoingMessage, MessageSource, MessageType

from modules.dice_core.dice_parser import parse_and_roll, check_result
from modules.dice_core.character_store import (
    DEFAULT_CARDS,
    load_or_default, save, save_card, delete as del_char,
    set_skill, format_card,
    list_cards, get_active_card, set_active_card, delete_card,
    load_player, rename_card, load_card,
)
from modules.commands.utils import load_bot_setting, format_msg

logger = logging.getLogger(__name__)


# =================================================================
# 公共工具
# =================================================================

def parse_repeat(args: str) -> tuple[int, str]:
    """解析 n# 前缀: '3#3d6' -> (3, '3d6'), '3d6' -> (1, '3d6')"""
    m = re.match(r'\s*(\d+)\s*#\s*', args)
    if m:
        return int(m.group(1)), args[m.end():]
    return 1, args


def _get_group_id(msg: ParsedMessage) -> str:
    """获取群号 (私聊返回空字符串)"""
    if msg.raw.source == MessageSource.GROUP:
        return msg.raw.target_id
    return ""


def _get_uid(msg: ParsedMessage) -> str:
    return msg.raw.sender_id


def _get_sender_name(msg: ParsedMessage) -> str:
    return msg.raw.sender_name or msg.raw.sender_id


# =================================================================
# .r — 骰子投掷
# =================================================================

class RDiceCommand(Command):
    name = "r"
    description = "骰子投掷 (3d6, d100, 3d6+2, 3#3d6)"
    group = "骰子"
    usage = ".r <骰子表达式> [DC]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        repeat, rest = parse_repeat(args)
        actual_args = rest if repeat > 1 else args
        if not actual_args.strip():
            return self._sub_help()

        results = []
        for i in range(repeat):
            r = self._roll_once(actual_args)
            if r:
                label = f"[#{i+1}] " if repeat > 1 else ""
                results.append(f"{label}{r}")
        if not results:
            return None

        template = load_bot_setting("dice_core", "r_message", "{}")
        return format_msg(template, "\n".join(results))

    @staticmethod
    def _roll_once(args: str) -> str | None:
        args = args.strip()
        dc = 0
        expr = args

        # 优先解析 /DC 格式: 3d6/12
        if '/' in args:
            parts = args.rsplit('/', 1)
            try:
                dc = int(parts[1].strip())
                expr = parts[0].strip()
            except ValueError:
                pass

        # 回退：空格分隔 DC: 3d6 12
        if dc == 0:
            parts = expr.split(None, 1)
            if len(parts) > 1:
                try:
                    dc = int(parts[1])
                    expr = parts[0]
                except ValueError:
                    pass

        formatted, total, values = parse_and_roll(expr)
        if not values:
            return f"无效表达式: {expr}"

        lines = [f"🎲 {args.strip()}: {formatted} = {total}"]
        if dc > 0:
            result = check_result(total, dc)
            lines.append(f"vs {dc} → {result}")
        return "\n".join(lines)

    def _sub_help(self) -> str:
        return (
            ".r 子命令:\n"
            "  .r XdY       - 掷 X 个 Y 面骰\n"
            "  .r XdY+Z     - 掷骰 +Z\n"
            "  .r XdY#N     - 重复 N 次\n"
            "  .r XdY DC    - 带难度判定 (d100 COC 规则)"
        )


# =================================================================
# .rh — 暗骰 (私聊发送结果)
# =================================================================

class RhCommand(Command):
    name = "rh"
    description = "暗骰 (结果私聊)"
    group = "骰子"
    usage = ".rh <骰子表达式>"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        repeat, rest = parse_repeat(args)
        actual_args = rest if repeat > 1 else args
        if not actual_args.strip():
            return "用法: .rh <骰子表达式>\n示例: .rh 3d6  /  3#d100"

        results = []
        for _ in range(repeat):
            formatted, total, values = parse_and_roll(actual_args.strip())
            if not values:
                return f"无效表达式: {actual_args.strip()}"
            result_text = f"🎲 暗骰 {actual_args.strip()}: {formatted} = {total}"
            results.append(result_text)

            # 私聊发送结果
            try:
                await ctx.napcat_bridge._send_text("private", msg.raw.sender_id, result_text)
            except Exception:
                pass

        # 旁观者转发
        extra = ""
        if msg.raw.source == MessageSource.GROUP:
            group_id = msg.raw.target_id
            observers = ctx.state_manager.state.observers.get(group_id, set())
            if observers:
                combined = "\n".join(results)
                sender_name = _get_sender_name(msg)
                for obs_qq in observers:
                    if obs_qq != msg.raw.sender_id:
                        try:
                            await ctx.napcat_bridge._send_text(
                                "private", obs_qq,
                                f"👁️ [旁观] 群 {group_id} — {sender_name}:\n{combined}"
                            )
                        except Exception:
                            pass
                extra = f"，已转发 {len(observers)} 位旁观者"

        return f"🎲 已暗骰 (结果已私聊{extra})"


# =================================================================
# .ra — d100 技能/属性检定 (COC 规则)
# =================================================================

class RaCommand(Command):
    name = "ra"
    description = "d100 检定 (COC规则)"
    group = "骰子"
    usage = ".ra [技能名][/阈值]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        repeat, rest = parse_repeat(args)
        actual_args = rest if repeat > 1 else args
        skill_name = actual_args.strip()
        if not skill_name:
            return "用法: .ra [技能名或属性名][/阈值]\n示例: .ra 侦查  /  .ra 侦查/50  /  3#ra 侦查"

        # 解析 /阈值 后缀
        dc_override = 0
        if '/' in skill_name:
            parts = skill_name.rsplit('/', 1)
            skill_name = parts[0].strip()
            try:
                dc_override = int(parts[1].strip())
            except ValueError:
                pass

        uid = _get_uid(msg)
        group_id = _get_group_id(msg)
        char = load_or_default(uid, group_id=group_id)
        value = dc_override or char.get("skills", {}).get(skill_name) or char.get("attributes", {}).get(skill_name, 0)
        if not value and not dc_override:
            return f"📋 角色卡中未找到「{skill_name}」，请先用 .st 录入"

        results = []
        for i in range(repeat):
            roll = random.randint(1, 100)
            check = check_result(roll, value)
            player = char.get("name", _get_sender_name(msg))
            label = f"[#{i+1}] " if repeat > 1 else ""
            threshold = f"/{value}" if dc_override else ""
            results.append(
                f"{label}{player} 进行「{skill_name}{threshold}」检定：\n"
                f"1d100 = {roll}/{value}，{check}"
            )

        template = load_bot_setting("dice_core", "ra_message", "{}")
        return format_msg(template, "\n".join(results))


# =================================================================
# .show — 展示角色卡
# =================================================================

class ShowCommand(Command):
    name = "show"
    description = "展示角色卡"
    group = "骰子"
    usage = ".show"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        uid = _get_uid(msg)
        group_id = _get_group_id(msg)

        char = load_or_default(uid, group_id=group_id)
        if not char.get("skills") and not char.get("attributes"):
            return "📋 暂无角色卡。使用 .st [技能名] [值] 录入。"

        result = format_card(char, char.get("system", "ark"))
        template = load_bot_setting("dice_core", "show_message", "{}")
        return format_msg(template, result)


# =================================================================
# .del — 删除数据
# =================================================================

class DelCommand(Command):
    name = "del"
    description = "删除角色卡/技能"
    group = "骰子"
    usage = ".del card / .del [技能名]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        uid = _get_uid(msg)
        group_id = _get_group_id(msg)
        action = args.strip().lower()

        if action in ("card", "角色卡", "all"):
            if del_char(uid):
                result = "角色卡已删除"
                template = load_bot_setting("dice_core", "del_card_message", "{}")
                return format_msg(template, result)
            return "📋 没有可删除的角色卡"

        if action:
            char = load_or_default(uid, group_id=group_id)
            if action in char.get("skills", {}):
                del char["skills"][action]
                save(uid, char)
                return f"✅ 技能「{action}」已删除"
            if action in char.get("attributes", {}):
                return "❌ 属性不可删除，使用 .st [属性名] 0 归零"

        return (
            ".del 子命令:\n"
            "  .del card    - 删除整张角色卡\n"
            "  .del [技能名] - 删除指定技能"
        )


# =================================================================
# .pc — 角色卡管理（多卡切换）
# =================================================================

class PcCommand(Command):
    name = "pc"
    description = "角色卡管理 (list/switch/new/del)"
    group = "骰子"
    usage = ".pc [switch/new/del] [名称]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        uid = _get_uid(msg)
        action = args.strip()

        if not action:
            active = get_active_card(uid)
            cards = list_cards(uid)
            lines = [f"当前角色卡: {active}", f"共 {len(cards)} 张角色卡:"]
            for c in cards:
                mark = " ← 当前" if c == active else ""
                lines.append(f"  {c}{mark}")
            return "\n".join(lines)

        parts = action.split(None, 1)
        sub = parts[0].lower()
        name = parts[1] if len(parts) > 1 else ""

        if sub in ("switch", "切换", "use"):
            if not name:
                return "用法: .pc switch <角色卡名>"
            if name not in list_cards(uid):
                return f"角色卡「{name}」不存在"
            set_active_card(uid, name)
            return f"✅ 已切换到角色卡「{name}」"

        elif sub in ("del", "删除"):
            if not name:
                return "用法: .pc del <角色卡名>"
            if name not in list_cards(uid):
                return f"角色卡「{name}」不存在"
            if name == get_active_card(uid):
                return "❌ 不能删除当前活跃角色卡，请先切换到其他卡"
            delete_card(uid, name)
            return f"✅ 角色卡「{name}」已删除"

        elif sub in ("new", "创建", "add"):
            if not name:
                return "用法: .pc new <角色卡名>"
            if name in list_cards(uid):
                return f"角色卡「{name}」已存在"
            cards = list_cards(uid)
            if len(cards) >= 5:
                return "❌ 最多 5 张角色卡。请先 .pc del 删除不需要的卡。"
            # 修复: 使用 deepcopy 防止默认模板被修改
            char = copy.deepcopy(DEFAULT_CARDS["ark"])
            char["system"] = "ark"
            save_card(uid, name, char)
            return f"✅ 角色卡「{name}」已创建"

        else:
            # 直接给名字 = 切换
            if action in list_cards(uid):
                set_active_card(uid, action)
                return f"✅ 已切换到角色卡「{action}」"
            return (
                f"未知子命令: .pc {args}\n\n"
                ".pc 子命令:\n"
                "  .pc              - 查看角色卡列表\n"
                "  .pc <名称>       - 切换角色卡\n"
                "  .pc switch <名称> - 切换\n"
                "  .pc new <名称>    - 创建新卡\n"
                "  .pc del <名称>    - 删除"
            )


# =================================================================
# .nn — 重命名角色卡
# =================================================================

class NnCommand(Command):
    name = "nn"
    description = "重命名角色卡"
    group = "骰子"
    usage = ".nn <新名称> / .nn <旧名称> <新名称>"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        uid = _get_uid(msg)
        parts = args.split()

        if len(parts) == 1:
            new_name = parts[0]
            old_name = get_active_card(uid)
            if old_name == new_name:
                return "❌ 新旧名称相同"
            if new_name in list_cards(uid):
                return f"❌ 角色卡「{new_name}」已存在"
            if rename_card(uid, old_name, new_name):
                return f"✅ 角色卡「{old_name}」已重命名为「{new_name}」"
            return "❌ 重命名失败"

        elif len(parts) >= 2:
            old_name, new_name = parts[0], parts[1]
            if old_name not in list_cards(uid):
                return f"❌ 角色卡「{old_name}」不存在"
            if new_name in list_cards(uid):
                return f"❌ 角色卡「{new_name}」已存在"
            if rename_card(uid, old_name, new_name):
                result = f"角色卡「{old_name}」已重命名为「{new_name}」"
                template = load_bot_setting("dice_core", "nn_message", "{}")
                return format_msg(template, result)
            return "❌ 重命名失败"

        else:
            return (
                "用法:\n"
                "  .nn <新名称>              - 重命名当前角色卡\n"
                "  .nn <旧名称> <新名称>      - 重命名指定角色卡"
            )


# =================================================================
# .st — 设置属性/技能
# =================================================================

class StCommand(Command):
    name = "st"
    description = "设置属性/技能值"
    group = "骰子"
    usage = ".st [名称] [数值]"
    reminder = "使用 .st 录入属性/技能；支持紧凑格式: .st 力量5敏捷3"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        uid = _get_uid(msg)
        group_id = _get_group_id(msg)
        args = args.strip()

        if not args:
            return (
                ".st 子命令:\n"
                "  .st [名称] [数值]  - 设置属性或技能\n"
                "  .st [名称]          - 查询值\n"
                "也支持: .st 力量5敏捷3智力7..."
            )

        # 优先尝试紧凑格式 (如: .st 生理耐受5反应机动6物理强度8)
        parsed = _parse_compact(args)
        if parsed and len(parsed) >= 2:
            _ensure_new_card(uid)
            for k, v in parsed.items():
                set_skill(uid, k, v, group_id=group_id)
            char = load_or_default(uid, group_id=group_id)
            player_name = char.get("name") or _get_sender_name(msg)
            template = load_bot_setting(
                "dice_core", "st_message",
                "角色卡已经设置好了~~~欢迎加入~~~"
            )
            return format_msg(template, player_name=player_name)

        # 单键查询/设置
        parts = args.split(None, 1)
        name = parts[0]

        if len(parts) < 2:
            char = load_or_default(uid, group_id=group_id)
            val = char.get("skills", {}).get(name) or char.get("attributes", {}).get(name)
            return f"📋 {name} = {val}" if val is not None else f"📋 {name} 未设置"

        try:
            value = int(parts[1])
        except ValueError:
            return f"无效数值: {parts[1]}"

        _ensure_new_card(uid)
        set_skill(uid, name, value, group_id=group_id)
        char = load_or_default(uid, group_id=group_id)
        player_name = char.get("name") or _get_sender_name(msg)
        template = load_bot_setting(
            "dice_core", "st_message",
            "角色卡已经设置好了~~~欢迎加入~~~"
        )
        return format_msg(template, player_name=player_name)


# =================================================================
# 工具函数
# =================================================================

def _parse_compact(text: str) -> dict[str, int]:
    """解析紧凑格式字符串 '力量5敏捷3智力7' -> {力量:5, 敏捷:3, 智力:7}"""
    result = {}
    pattern = re.compile(r'([^\d\s]+?)\s*(-?\d+(?:\.\d+)?)')
    for m in pattern.finditer(text):
        key = m.group(1).strip()
        try:
            val = int(float(m.group(2)))
        except ValueError:
            continue
        result[key] = val
    return result


def _ensure_new_card(uid: str):
    """如果当前活跃卡已有数据，自动创建新卡并切换。最多 5 张，超出覆盖最旧的。"""
    active = get_active_card(uid)
    char = load_card(uid, active)
    if char and (char.get("attributes") or char.get("skills")):
        cards = list_cards(uid)

        MAX_CARDS = 5
        if len(cards) >= MAX_CARDS:
            for c in cards:
                if c != active:
                    delete_card(uid, c)
                    break

        n = 2
        while f"默认{n}" in list_cards(uid):
            n += 1
        new_name = f"默认{n}"
        blank = copy.deepcopy(DEFAULT_CARDS["ark"])
        blank["system"] = "ark"
        save_card(uid, new_name, blank)
        set_active_card(uid, new_name)
