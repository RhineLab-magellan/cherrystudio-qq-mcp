"""通用持久化工具 — 每个模块独立的 JSON 文件"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("store")

STORE_DIR = Path(__file__).parent


def load_list(filename: str) -> list[str]:
    """加载字符串列表，文件不存在返回空列表"""
    path = STORE_DIR / filename
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []
    except Exception as e:
        logger.warning(f"加载 {filename} 失败: {e}")
        return []


def save_list(filename: str, data: list[str]):
    """保存字符串列表"""
    path = STORE_DIR / filename
    try:
        data = sorted(set(data))
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"保存 {filename} 失败: {e}")
