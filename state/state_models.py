"""
SharedState Pydantic 验证模型

用于验证 shared_state.json 的结构和类型。
在 StateManager 加载/保存状态时调用 model_validate() 进行校验。

设计原则:
  - 仅用于验证层，不替换 SharedState dataclass
  - ConfigDict(extra="allow") 允许未知字段 (向后兼容旧版本状态文件)
  - 序列化格式与现有 to_dict() / from_dict() 保持一致
"""

from pydantic import BaseModel, Field, ConfigDict


class SharedStateModel(BaseModel):
    """
    SharedState 的 Pydantic 验证模型

    字段与 state/manager.py 中的 SharedState dataclass 一一对应。
    """
    model_config = ConfigDict(extra="allow")

    # 旁观者列表 (群号 -> 用户ID列表)
    observers: dict[str, list[str]] = Field(
        default_factory=dict,
        description="旁观者映射 (群号 -> 用户ID列表)",
    )

    # 开启旁观者的群
    ob_groups: list[str] = Field(
        default_factory=list,
        description="开启旁观者的群列表",
    )

    # .bot off 的群 (机器人黑名单)
    bot_blacklist: list[str] = Field(
        default_factory=list,
        description="机器人黑名单群列表",
    )

    # 免 @ 的群 (指令白名单)
    order_whitelist: list[str] = Field(
        default_factory=list,
        description="免 @ 指令白名单群列表",
    )

    # 模型偏好 (会话键 -> 模型名称)
    saved_models: dict[str, str] = Field(
        default_factory=dict,
        description="保存的模型偏好映射",
    )

    # 当前活跃的 Agent (会话键 -> Agent名称)
    active_agents: dict[str, str] = Field(
        default_factory=dict,
        description="活跃 Agent 映射",
    )

    # 模块启用状态
    modules_enabled: dict[str, bool] = Field(
        default_factory=lambda: {"command": True, "cherrystudio": True},
        description="模块启用状态",
    )

    # 日志黑名单
    log_blacklist: list[str] = Field(
        default_factory=list,
        description="日志黑名单群列表",
    )

    # 欢迎配置
    welcome_config: dict[str, dict] = Field(
        default_factory=dict,
        description="群聊欢迎配置",
    )


def validate_state(raw_dict: dict) -> SharedStateModel:
    """
    验证状态字典并返回 SharedStateModel 模型

    Args:
        raw_dict: 从 shared_state.json 加载的原始字典

    Returns:
        验证后的 SharedStateModel 实例

    Raises:
        pydantic.ValidationError: 状态验证失败时抛出
    """
    return SharedStateModel.model_validate(raw_dict)
