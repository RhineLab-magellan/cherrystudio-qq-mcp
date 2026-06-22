"""共享角色卡存储 — 按 QQ ID 独立，跨群通用，支持多角色卡

移植自旧项目 Plugins/dice_core/character_store.py
修复:
  - DATA_DIR: 改为项目根目录/data/ (而非模块内部)
  - DEFAULT_CARDS 浅拷贝: 改用 copy.deepcopy 防止模板被污染
"""

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger("dice-core")

# 角色卡数据存放在项目根目录下的 data/ 中
DATA_DIR = Path(__file__).parent.parent.parent / "data"

DEFAULT_CARDS = {
    "ark": {
        "name": "", "hp": 10, "hp_max": 10, "sp": 10, "sp_max": 10,
        "skills": {}, "attributes": {},
    },
    "coc": {
        "name": "佚名调查员", "age": 20,
        "attributes": {
            "str": 0, "con": 0, "siz": 0, "dex": 0,
            "app": 0, "int": 0, "pow": 0, "edu": 0, "luc": 0,
        },
        "san": 0, "san_max": 99, "hp": 0, "hp_max": 0, "skills": {},
    },
}


def _card_path(uid: str, card_name: str) -> Path:
    return DATA_DIR / uid / "cards" / f"{card_name}.json"


def _player_path(uid: str) -> Path:
    return DATA_DIR / uid / "player.json"


# -- PlayerData --

def load_player(uid: str) -> dict:
    path = _player_path(uid)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Load player data failed {uid}: {e}")
    return {"name": "", "active_card": "默认"}


def save_player(uid: str, data: dict):
    path = _player_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_active_card(uid: str) -> str:
    return load_player(uid).get("active_card", "默认")


def set_active_card(uid: str, card_name: str):
    data = load_player(uid)
    data["active_card"] = card_name
    save_player(uid, data)


# -- 角色卡 CRUD --

def list_cards(uid: str) -> list[str]:
    d = DATA_DIR / uid / "cards"
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def load_card(uid: str, card_name: str | None = None) -> dict | None:
    name = card_name or get_active_card(uid)
    path = _card_path(uid, name)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Load character card failed {uid}/{name}: {e}")
    return None


def _group_path(group_id: str, uid: str) -> Path:
    return DATA_DIR / group_id / f"{uid}.json"


def load_group_data(group_id: str, uid: str) -> dict | None:
    """从群组目录加载角色数据（旧格式兼容）"""
    path = _group_path(group_id, uid)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("skills") or data.get("attributes"):
                return data
    except Exception as e:
        logger.warning(f"Load group character data failed {group_id}/{uid}: {e}")
    return None


def load_or_default(uid: str, card_name: str | None = None, system: str = "ark",
                    group_id: str = "") -> dict:
    name = card_name or get_active_card(uid)
    char = load_card(uid, name)
    if char is not None and (char.get("skills") or char.get("attributes")):
        return char

    # 回退：从群组旧格式数据加载
    if group_id and char is not None:
        group_char = load_group_data(group_id, uid)
        if group_char:
            for key in ("name", "hp", "hp_max", "sp", "sp_max",
                        "skills", "attributes", "system"):
                if key in group_char and (key not in char or not char.get(key)):
                    char[key] = group_char[key]
            save_card(uid, name, char)
            logger.info(f"Recovered character card from group data: {uid}/{name} (group {group_id})")
            return char

    if char is not None:
        return char

    # 修复: 使用 deepcopy 防止默认模板被修改
    default = copy.deepcopy(DEFAULT_CARDS.get(system, DEFAULT_CARDS["ark"]))
    default["system"] = system
    save_card(uid, name, default)
    return default


def save_card(uid: str, card_name: str, char: dict):
    path = _card_path(uid, card_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(char, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_card(uid: str, card_name: str) -> bool:
    path = _card_path(uid, card_name)
    if path.exists():
        try:
            path.unlink()
            return True
        except Exception as e:
            logger.warning(f"Delete character card failed: {e}")
    return False


def delete(uid: str, card_name: str | None = None) -> bool:
    return delete_card(uid, card_name or get_active_card(uid))


def rename_card(uid: str, old_name: str, new_name: str) -> bool:
    """重命名角色卡。如果旧名是活跃卡，更新活跃卡指向"""
    old = _card_path(uid, old_name)
    new = _card_path(uid, new_name)
    if old.exists() and not new.exists():
        try:
            old.rename(new)
            if get_active_card(uid) == old_name:
                set_active_card(uid, new_name)
            return True
        except Exception as e:
            logger.warning(f"Rename character card failed: {e}")
    return False


def save(uid: str, char: dict, card_name: str | None = None):
    save_card(uid, card_name or get_active_card(uid), char)


# -- 便捷读写 --

def set_skill(uid: str, name: str, value, card_name: str | None = None,
              group_id: str = "") -> str:
    char = load_or_default(uid, card_name, group_id=group_id)
    cname = card_name or get_active_card(uid)
    if name in char.get("attributes", {}):
        char["attributes"][name] = value
    else:
        char.setdefault("skills", {})[name] = value
    save_card(uid, cname, char)
    return f"{name} = {value}"


# -- 显示 --

def format_card(char: dict, system: str = "ark") -> str:
    lines = [f"📋 {char.get('name', '未命名')}"]
    if system == "ark":
        lines.append(
            f"HP: {char.get('hp', '?')}/{char.get('hp_max', '?')}  "
            f"SP: {char.get('sp', '?')}/{char.get('sp_max', '?')}"
        )
        if char.get("attributes"):
            lines.append("── 属性 ──")
            _append_grouped(lines, char["attributes"])
    elif system == "coc":
        a = char.get("attributes", {})
        lines.append(
            f"STR:{a.get('str', '?')} CON:{a.get('con', '?')} "
            f"SIZ:{a.get('siz', '?')} DEX:{a.get('dex', '?')} APP:{a.get('app', '?')}"
        )
        lines.append(
            f"INT:{a.get('int', '?')} POW:{a.get('pow', '?')} "
            f"EDU:{a.get('edu', '?')} LUC:{a.get('luc', '?')}"
        )
        lines.append(
            f"SAN:{char.get('san', '?')}/{char.get('san_max', '?')} "
            f"HP:{char.get('hp', '?')}/{char.get('hp_max', '?')}"
        )
    if char.get("skills"):
        lines.append("── 技能 ──")
        _append_grouped(lines, char["skills"], max_per_line=6)
    return "\n".join(lines)


def _append_grouped(lines: list, items: dict, max_per_line: int = 5):
    kv = list(items.items())
    for i in range(0, len(kv), max_per_line):
        chunk = kv[i:i + max_per_line]
        lines.append("  " + " | ".join(f"{k}:{v}" for k, v in chunk))
