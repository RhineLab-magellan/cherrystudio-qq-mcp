"""
Ark TRPG 命令 (.rk .rkb .rkp .sck .ark .sn)

移植自旧项目 Plugins/ark_trpg/commands.py
适配新架构 Command / CommandContext / ParsedMessage 接口。

修复:
  - .sck: will 变量未定义 (NameError) -> 从角色卡读取 "精神意志" 属性
  - .sck: result_msg 在 total <= will 时未定义 -> 补全逻辑
  - .sn:  load_or_default() 后接 ( 导致语法错误 (SyntaxError)
           整文件无法导入，6 个命令全部静默失效 -> 修复为正确的赋值语句
"""

import random
import re
import logging

from modules.command_module import Command, CommandContext
from protocols.messages import ParsedMessage, MessageSource

from modules.dice_core.dice_parser import check_critical_d6 as check_critical
from modules.dice_core.character_store import load_or_default, save
from modules.ark_trpg.skills import find_attr, is_attr, BASE_ATTRS
from modules.commands.dice import parse_repeat, _get_group_id, _get_uid, _get_sender_name
from modules.commands.utils import load_bot_setting, format_msg

logger = logging.getLogger(__name__)


HELP_TEXT = """=== ArkTRPG 骰子插件 ===
【行于泰拉 · 投掷帮助】
录入：.st [技能or属性名] [值]  (使用通用 .st 指令)
示例：.st 刀剑 7  /  .st 生理耐受 5

技能检定：.rk [骰面] [技能名] [技能值]/[难度]
示例：.rk 6 觉察 9/12  (6d6+觉察属性，DC=12)
· 技能值支持 "X+Y" 格式，Y 为固定加值
· 不输入技能值时从角色卡读取
· 不输入难度时 DC=0
· 不输入骰面时默认 d6

奖励骰/惩罚骰：.rkb N ... / .rkp N ...
示例：.rkb 2 觉察 7/12

自控检定：.sck [骰数]   (默认1d10 vs 精神意志)
人物作成：.ark [次数]    (默认1次)
录入技能：.st [名称] [值]  (使用通用 .st 指令)
查看帮助：.rk help"""


# =================================================================
# .rk / .rkb / .rkp — 技能检定
# =================================================================

class RkCommand(Command):
    name = "rk"
    description = "行于泰拉技能检定"
    group = "行于泰拉"
    usage = ".rk [骰面] [技能名] [技能值]/[难度]"
    reminder = "录入: .st 技能名 值; 检定: .rk 骰面 技能名 技能值/难度"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        repeat, rest = parse_repeat(args)
        actual_args = rest if repeat > 1 else args
        results = []
        for i in range(repeat):
            r = await _rk_handle(actual_args, msg, 0, ctx)
            if r:
                label = f"[#{i+1}]\n" if repeat > 1 else ""
                results.append(f"{label}{r}")
        return "\n".join(results) if results else None


class RkbCommand(Command):
    name = "rkb"
    description = "行于泰拉技能检定 (奖励骰)"
    group = "行于泰拉"
    usage = ".rkb [N] [骰面] [技能名] [技能值]/[难度]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        repeat, rest = parse_repeat(args)
        actual_args = rest if repeat > 1 else args
        bonus = 1
        m = re.match(r"(\d+)", actual_args.strip())
        if m:
            bonus = int(m.group(1))
            actual_args = actual_args[m.end():].strip()
        results = []
        for i in range(repeat):
            r = await _rk_handle(actual_args, msg, bonus, ctx)
            if r:
                label = f"[#{i+1}]\n" if repeat > 1 else ""
                results.append(f"{label}{r}")
        return "\n".join(results) if results else None


class RkpCommand(Command):
    name = "rkp"
    description = "行于泰拉技能检定 (惩罚骰)"
    group = "行于泰拉"
    usage = ".rkp [N] [骰面] [技能名] [技能值]/[难度]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        repeat, rest = parse_repeat(args)
        actual_args = rest if repeat > 1 else args
        penalty = 1
        m = re.match(r"(\d+)", actual_args.strip())
        if m:
            penalty = int(m.group(1))
            actual_args = actual_args[m.end():].strip()
        results = []
        for i in range(repeat):
            r = await _rk_handle(actual_args, msg, -penalty, ctx)
            if r:
                label = f"[#{i+1}]\n" if repeat > 1 else ""
                results.append(f"{label}{r}")
        return "\n".join(results) if results else None


async def _rk_handle(args: str, msg: ParsedMessage, bonus: int, ctx: CommandContext) -> str | None:
    """统一的 .rk 处理逻辑"""
    if args.strip().lower() in ("help", "帮助"):
        return HELP_TEXT
    result = _do_rk(msg, args.strip(), bonus)
    key = "rk_message" if bonus == 0 else ("rkb_message" if bonus > 0 else "rkp_message")
    player_name = ""
    uid = _get_uid(msg)
    group_id = _get_group_id(msg)
    char = load_or_default(uid, system="ark", group_id=group_id)
    player_name = char.get("name") or _get_sender_name(msg)
    return format_msg(
        load_bot_setting("arktrpg", key, "{}"),
        result,
        player_name=player_name,
    )


def _do_rk(msg: ParsedMessage, args: str, bonus: int) -> str:
    """.rk 核心检定"""
    match = re.match(r"(\d*)\s*([^\d\s/]+)\s*(\d+(?:\+\d+)?)?\s*(?:/\s*(\d+))?", args.strip())
    if not match:
        return f"格式错误。\n{HELP_TEXT}"

    face_str, skill_name, value_str, dc_str = match.groups()
    face = int(face_str) if face_str else 6
    dc = int(dc_str) if dc_str else 0

    uid = _get_uid(msg)
    group_id = _get_group_id(msg)
    char = load_or_default(uid, system="ark", group_id=group_id)
    manual_bonus = 0
    if value_str:
        if "+" in value_str:
            parts = value_str.split("+")
            skill_value = int(parts[0]) if parts[0] else 0
            manual_bonus = int(parts[1]) if len(parts) > 1 else 0
        else:
            skill_value = int(value_str)
    else:
        skill_value = char.get("skills", {}).get(skill_name) or char.get("attributes", {}).get(skill_name, 0)

    attr_bonus = 0
    if manual_bonus > 0:
        attr_bonus = manual_bonus
    elif not is_attr(skill_name):
        attr = find_attr(skill_name)
        if attr:
            attr_bonus = char.get("attributes", {}).get(attr, 0)

    dice_count = skill_value + bonus
    if dice_count <= 0:
        return "❌ 技能值+奖励骰必须 >0"

    values = [random.randint(1, face) for _ in range(dice_count)]
    roll_sum = sum(values)
    total = roll_sum + attr_bonus

    player_name = char.get("name") or _get_sender_name(msg)
    prefix = f"{player_name} 进行 {skill_name} 检定"

    result = "成功" if total >= dc else "失败"
    dice_str = "+".join(str(v) for v in values)
    bonus_label = f"+{bonus}" if bonus >= 0 else str(bonus)
    lines = [
        f"{prefix}：",
        f"({skill_value}{bonus_label})d{face}+{attr_bonus} = {dice_str}+{attr_bonus} = {total}/{dc}，{result}",
    ]

    crit_success, crit_fail = check_critical(values, face)
    if crit_success:
        lines.append("🎉 大成功！（至少半数最大值）")
    elif crit_fail:
        lines.append("💀 大失败！（至少半数最小值）")

    return "\n".join(lines)


# =================================================================
# .sck — 自控检定
# =================================================================

class SckCommand(Command):
    name = "sck"
    description = "自控检定 (d10 vs 精神意志)"
    group = "行于泰拉"
    usage = ".sck [骰数]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        try:
            count = int(args.strip()) if args.strip() else 1
        except ValueError:
            count = 1

        uid = _get_uid(msg)
        group_id = _get_group_id(msg)
        char = load_or_default(uid, system="ark", group_id=group_id)

        values = [random.randint(1, 10) for _ in range(count)]
        total = sum(values)
        dice_str = "+".join(str(v) for v in values)
        player = char.get("name") or _get_sender_name(msg)

        # 修复: 从角色卡读取 "精神意志" 属性 (旧代码 will 变量未定义)
        will = char.get("attributes", {}).get("精神意志", 0)

        if total > will:
            result_msg = (
                f"{player} 进行自控检定：\n"
                f"{count}d10 = {dice_str} = {total}/{will}，失败"
            )
        else:
            # 修复: 补全成功分支 (旧代码 result_msg 未定义)
            result_msg = (
                f"{player} 进行自控检定：\n"
                f"{count}d10 = {dice_str} = {total}/{will}，成功"
            )

        template = load_bot_setting("arktrpg", "sck_message", "{}")
        return format_msg(template, result_msg, player_name=player)


# =================================================================
# .ark — 人物作成
# =================================================================

class ArkCommand(Command):
    name = "ark"
    description = "泰拉人作成 (掷7属性)"
    group = "行于泰拉"
    usage = ".ark [次数]"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        try:
            count = int(args.strip()) if args.strip() else 1
        except ValueError:
            count = 1
        if count > 10:
            return "最多 10 次"

        uid = _get_uid(msg)
        group_id = _get_group_id(msg)
        char = load_or_default(uid, system="ark", group_id=group_id)
        player = char.get("name") or _get_sender_name(msg)

        lines = [f"泰拉人作成 ×{count}："]
        for i in range(count):
            attrs = {}
            for attr in BASE_ATTRS:
                v1, v2 = random.randint(1, 4), random.randint(1, 4)
                attrs[attr] = v1 + v2

            economy = sum(random.randint(1, 6) for _ in range(4))
            social_dice = attrs.get("个人魅力", 0)
            social = sum(random.randint(1, 6) for _ in range(social_dice))

            total_without = sum(attrs.values())
            total_with = total_without + economy + social

            card = (
                f"\n--- 第{i + 1}组 ---\n"
                + "  ".join(f"{k}:{v}" for k, v in attrs.items())
                + f"\n  经济评级: {economy}  社交点数: {social}"
                + f"\n  总计(含经济社交): {total_with}"
            )
            lines.append(card)

        result = "\n".join(lines)
        template = load_bot_setting("arktrpg", "ark_message", "{}")
        return format_msg(template, result, player_name=player)


# =================================================================
# .sn — 名片设置
# =================================================================

class SnCommand(Command):
    name = "sn"
    description = "设置名片模板"
    group = "行于泰拉"
    usage = ".sn rk"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        action = args.strip().lower()

        if action in ("rk", "arktrpg"):
            gid = msg.raw.target_id if msg.raw.source == MessageSource.GROUP else msg.raw.sender_id
            uid = _get_uid(msg)

            # 修复: 旧代码 load_or_default() 后接 ( 导致语法错误
            char = load_or_default(uid, system="ark", group_id=gid)
            card = (
                f"{char.get('name', _get_sender_name(msg))} "
                f"HP{char.get('hp', 0)}/{char.get('hp_max', 0)} "
                f"SP{char.get('sp', 0)}/{char.get('sp_max', 0)}"
            )

            try:
                await ctx.napcat_bridge.set_group_card(gid, uid, card)
                result = f"✅ 名片已更新: {card}"
                template = load_bot_setting("arktrpg", "sn_rk_message", "{}")
                return format_msg(template, result)
            except Exception as e:
                return f"❌ 设置名片失败: {e}"

        elif action in ("help", "?"):
            return ".sn rk  — 设置行于泰拉自动名片 (HP/HP上限 SP/SP上限)"

        else:
            return "未知子命令。用法: .sn rk"
