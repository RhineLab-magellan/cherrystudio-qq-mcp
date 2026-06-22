"""技能 -> 属性映射表

移植自旧项目 Plugins/ark_trpg/skills.py
纯数据模块，无项目依赖。
"""

# 每个技能对应的基础属性
SKILL_TO_ATTR: dict[str, str] = {}
_RAW_MAP = {
    "精神意志": "欺诈 乔装 潜行 调查 觉察 追踪",
    "个人魅力": "声乐 艺术 心理 游说 取悦 威吓",
    "反应机动": "妙手 急救 驾驶 舞蹈 短兵 暗器 射击 身法",
    "物理强度": "长兵 刀剑 钝器 格斗 软兵 拳术 盾术",
    "经验智慧": "兵械操作 生物驯养 战术规划 支援技术 自然学 医药学 源石学 社会学 政法学 经管学 机械工程 电子工程 农林渔牧 手工工艺",
    "源石技艺适应性": "动能 光明 暗影 火焰 电能 气流 控水 土石 冰霜 躯体 植物 恢复 传心感知",
}

for attr, skills_str in _RAW_MAP.items():
    for skill in skills_str.split():
        SKILL_TO_ATTR[skill] = attr

# 基础属性列表
BASE_ATTRS = list(_RAW_MAP.keys())


def find_attr(skill_name: str) -> str | None:
    """查找技能对应的基础属性"""
    return SKILL_TO_ATTR.get(skill_name)


def is_attr(name: str) -> bool:
    """判断是否为基础属性名"""
    return name in BASE_ATTRS
