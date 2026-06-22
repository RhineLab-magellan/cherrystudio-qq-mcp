"""骰子表达式解析器 — 支持 DnD/COC 标准骰子语法

移植自旧项目 Plugins/dice_core/dice_parser.py
纯工具模块，无项目依赖。
"""

import random
import re


def parse_and_roll(expr: str) -> tuple[str, int, list[int]]:
    """解析骰子表达式并掷骰。

    支持格式:
      XdY      -> 掷 X 个 Y 面骰
      XdY+Z    -> 掷 X 个 Y 面骰，结果 +Z
      XdY#N    -> 掷 X 个 Y 面骰，重复 N 次
      d100     -> 掷 1 个 100 面骰

    返回: (格式化字符串, 总和, [所有骰子值])
    """
    expr = expr.strip()
    repeat = 1

    # 重复掷骰: XdY#N
    repeat_match = re.search(r'#(\d+)$', expr)
    if repeat_match:
        repeat = int(repeat_match.group(1))
        expr = expr[:repeat_match.start()]

    # 加值: XdY+Z
    bonus = 0
    bonus_match = re.search(r'\+(\d+)$', expr)
    if bonus_match:
        bonus = int(bonus_match.group(1))
        expr = expr[:bonus_match.start()]

    # 解析 XdY
    dice_match = re.match(r'(\d*)d(\d+)', expr)
    if not dice_match:
        dice_match = re.match(r'd(\d+)', expr)
    if not dice_match:
        return ("无效表达式", 0, [])

    count = int(dice_match.group(1)) if dice_match.group(1) else 1
    face = int(dice_match.group(2))

    results = []
    for _ in range(repeat):
        values = [random.randint(1, face) for _ in range(count)]
        results.extend(values)

    total = sum(results) + bonus
    dice_str = "+".join(str(v) for v in results)
    if bonus > 0:
        formatted = f"({dice_str})+{bonus}"
    else:
        formatted = dice_str

    return (formatted, total, results)


def check_result(roll: int, dc: int) -> str:
    """COC 风格判定结果"""
    if roll <= 5:
        return "大成功"
    if roll >= 96:
        return "大失败"
    if dc > 0 and roll <= dc // 5:
        return "极难成功"
    if dc > 0 and roll <= dc // 2:
        return "困难成功"
    if dc > 0 and roll <= dc:
        return "成功"
    return "失败"


def check_critical_d6(values: list[int], face: int) -> tuple[bool, bool]:
    """行于泰拉风格：至少半数骰子为最大值/最小值"""
    total = len(values)
    half = (total + 1) // 2
    max_count = sum(1 for v in values if v == face)
    min_count = sum(1 for v in values if v == 1)
    return max_count >= half, min_count >= half
