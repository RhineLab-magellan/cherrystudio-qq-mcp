"""
配置文件 Pydantic 验证模型

用于验证 config.json 的结构和类型。
在 server.py 加载配置时调用 model_validate() 进行校验。

设计原则:
  - 仅用于验证层，不替换内部数据结构
  - ConfigDict(extra="allow") 允许未知字段 (向后兼容)
  - 所有字段提供合理默认值
"""

from pydantic import BaseModel, Field, ConfigDict


class NapCatConfig(BaseModel):
    """NapCat 互联桥配置"""
    model_config = ConfigDict(extra="allow")

    ws_host: str = Field(default="127.0.0.1", description="WebSocket 主机地址")
    ws_port: int = Field(default=3001, ge=1, le=65535, description="WebSocket 端口")
    access_token: str = Field(default="", description="访问令牌")
    ws_max_reconnect: int = Field(default=0, ge=0, description="最大重连次数 (0=无限)")


class CherryStudioConfig(BaseModel):
    """CherryStudio 模块配置"""
    model_config = ConfigDict(extra="allow")

    mcp_server_name: str = Field(default="QQ Bridge", description="MCP 服务器名称")
    http_api_base: str = Field(default="http://127.0.0.1:23333", description="HTTP API 基地址")
    api_key: str = Field(default="", description="API 密钥")
    mcp_server_path: str = Field(default="", description="MCP 服务器脚本路径")
    timeout: int = Field(default=120, ge=10, le=600, description="请求超时 (秒)")


class LLMProviderConfig(BaseModel):
    """LLM Provider 配置"""
    model_config = ConfigDict(extra="allow")

    name: str = Field(default="", description="Provider 名称")
    base_url: str = Field(default="", description="API 基地址")
    api_key: str = Field(default="", description="API 密钥")
    models: list[str] = Field(default_factory=list, description="可用模型列表")


class AgentConfig(BaseModel):
    """Agent 配置"""
    model_config = ConfigDict(extra="allow")

    name: str = Field(default="assistant", description="Agent 名称")
    description: str = Field(default="默认助手", description="Agent 描述")
    model: str = Field(default="", description="使用的模型")


class AgentsConfig(BaseModel):
    """Agents 配置组"""
    model_config = ConfigDict(extra="allow")

    default_agent: str = Field(default="assistant", description="默认 Agent 名称")
    agents: list[AgentConfig] = Field(default_factory=list, description="Agent 列表")


class BridgeSettings(BaseModel):
    """桥接设置"""
    model_config = ConfigDict(extra="allow")

    max_reply_chain_depth: int = Field(default=10, ge=1, le=50, description="最大回复链深度")
    cooldown_seconds: int = Field(default=3, ge=0, le=60, description="冷却时间 (秒)")
    session_timeout_minutes: int = Field(default=30, ge=1, le=1440, description="会话超时 (分钟)")
    enable_command_module: bool = Field(default=True, description="启用命令模块")
    enable_cherrystudio_module: bool = Field(default=True, description="启用 CherryStudio 模块")
    message_buffer_size: int = Field(default=200, ge=10, le=5000, description="消息缓冲区大小")


class AutoReplyConfig(BaseModel):
    """自动回复配置 (兼容旧项目)"""
    model_config = ConfigDict(extra="allow")

    doc_threshold: int = Field(default=1000, ge=100, description="文档阈值 (字符数)")


class BridgeConfig(BaseModel):
    """
    完整配置文件模型

    对应 config.json 的顶层结构。
    """
    model_config = ConfigDict(extra="allow")

    napcat: NapCatConfig = Field(default_factory=NapCatConfig)
    cherrystudio: CherryStudioConfig = Field(default_factory=CherryStudioConfig)
    llm_providers: list[LLMProviderConfig] = Field(default_factory=list)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    settings: BridgeSettings = Field(default_factory=BridgeSettings)
    auto_reply: AutoReplyConfig = Field(default_factory=AutoReplyConfig)
    bridge: BridgeSettings = Field(default_factory=BridgeSettings, description="桥接设置 (与 settings 合并)")


def validate_config(raw_dict: dict) -> BridgeConfig:
    """
    验证配置字典并返回 BridgeConfig 模型

    Args:
        raw_dict: 从 config.json 加载的原始字典

    Returns:
        验证后的 BridgeConfig 实例

    Raises:
        pydantic.ValidationError: 配置验证失败时抛出
    """
    return BridgeConfig.model_validate(raw_dict)
