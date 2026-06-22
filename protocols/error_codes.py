"""
错误码定义
定义所有模块使用的标准错误码体系

错误码格式: BRG-XXXX
- BRG: Bridge 前缀
- XXXX: 4位数字错误码

错误码范围分配:
- 1000-1999: NapCat 互联桥相关
- 2000-2999: 消息互联桥相关
- 3000-3999: 命令模块相关
- 4000-4999: CherryStudio 模块相关
- 5000-5999: Server 模块相关
- 6000-6999: 骰子核心 (dice_core) 相关
- 7000-7999: 行于泰拉 (ark_trpg) 相关
- 8000-8999: 日志系统 (log) 相关
- 9000-9999: 通用错误
"""

from enum import Enum


class ErrorCode(Enum):
    """
    标准错误码枚举

    每个错误码包含:
    - code: 错误码字符串 (如 "BRG-1001")
    - message: 错误描述 (用于日志)
    - user_text: 用户可见的自定义文本前缀
    """

    # ========== NapCat 互联桥错误 (1000-1999) ==========
    NAPCAT_CONNECTION_FAILED = ("BRG-1001", "NapCat WebSocket 连接失败", "连接失败")
    NAPCAT_AUTH_FAILED = ("BRG-1002", "NapCat 认证失败 (Access Token 无效)", "认证失败")
    NAPCAT_SEND_FAILED = ("BRG-1003", "发送消息到 NapCat 失败", "发送失败")
    NAPCAT_DISCONNECTED = ("BRG-1004", "NapCat 连接意外断开", "连接断开")
    NAPCAT_TIMEOUT = ("BRG-1005", "NapCat API 调用超时", "请求超时")
    NAPCAT_INVALID_RESPONSE = ("BRG-1006", "NapCat 返回无效响应", "响应异常")

    # ========== 消息互联桥错误 (2000-2999) ==========
    MESSAGE_PARSE_FAILED = ("BRG-2001", "消息解析失败", "消息解析失败")
    MESSAGE_ROUTING_FAILED = ("BRG-2002", "消息路由失败", "路由失败")
    MODULE_DISABLED = ("BRG-2003", "目标模块已禁用", "功能不可用")
    FILTER_REJECTED = ("BRG-2004", "消息被过滤规则拒绝", "消息被拦截")
    RESPONSE_MERGE_FAILED = ("BRG-2005", "多模块响应合并失败", "响应处理失败")

    # ========== 命令模块错误 (3000-3999) ==========
    COMMAND_NOT_FOUND = ("BRG-3001", "命令不存在", "未知命令")
    COMMAND_EXECUTION_FAILED = ("BRG-3002", "命令执行失败", "命令执行失败")
    COMMAND_PERMISSION_DENIED = ("BRG-3003", "命令权限不足", "权限不足")
    COMMAND_INVALID_ARGS = ("BRG-3004", "命令参数无效", "参数错误")
    COMMAND_TIMEOUT = ("BRG-3005", "命令执行超时", "执行超时")
    COMMAND_SESSION_EXPIRED = ("BRG-3006", "会话已过期", "会话过期")

    # ========== CherryStudio 模块错误 (4000-4999) ==========
    CHERRY_STUDIO_CONNECTION_FAILED = (
        "BRG-4001", "CherryStudio 连接失败", "AI服务连接失败")
    CHERRY_STUDIO_API_ERROR = ("BRG-4002", "CherryStudio API 调用失败", "AI服务异常")
    AGENT_NOT_FOUND = ("BRG-4003", "指定的 Agent 不存在", "Agent不存在")
    LLM_PROVIDER_FAILED = (
        "BRG-4004", "LLM Provider 调用失败 (所有回退尝试均失败)", "AI处理失败")
    SESSION_CREATE_FAILED = ("BRG-4005", "创建会话失败", "会话创建失败")
    VISION_PROCESSING_FAILED = ("BRG-4006", "图片识别处理失败", "图片处理失败")
    FILE_PROCESSING_FAILED = ("BRG-4007", "文件解析处理失败", "文件处理失败")
    MCP_RESPONSE_TIMEOUT = ("BRG-4008", "MCP 响应超时，已切换到 HTTP API", "响应超时")
    CHERRY_SESSION_EXPIRED = ("BRG-4009", "CherryStudio 会话过期或停滞", "会话已过期")
    SSE_RETRY_EXHAUSTED = (
        "BRG-4010", "SSE 连接重试耗尽 (1次重试后仍失败)", "AI服务连接不稳定")
    AGENT_ID_UNRESOLVED = (
        "BRG-4011", "Agent ID 解析失败 (显示名无法映射到内部 ID)", "Agent配置异常")
    HTTP_SESSION_NOT_INITIALIZED = (
        "BRG-4012", "HTTPClient._session 为 None (未初始化或已关闭)", "AI服务未就绪")
    SESSION_CREATE_COOLDOWN = (
        "BRG-4013", "会话创建冷却中 (上次创建失败后的保护期)", "AI服务恢复中")
    AGENT_API_HEALTH_FAILED = (
        "BRG-4014", "CherryStudio /health 健康检查失败", "AI服务不可用")
    SESSION_REBUILD_FAILED = (
        "BRG-4015", "会话重建失败 (删除旧会话或创建新会话失败)", "会话重建失败")
    SSE_TOTAL_TIMEOUT = (
        "BRG-4016", "SSE 请求总超时 (停滞检测轮次耗尽，AI 未完成响应)", "AI响应超时")
    SSE_HEADER_TIMEOUT = (
        "BRG-4017", "SSE 请求头部等待超时 (Agent 服务在规定时间内未开始返回数据)", "AI服务未响应")

    # ========== Server 模块错误 (5000-5999) ==========
    SERVER_INIT_FAILED = ("BRG-5001", "服务器初始化失败", "启动失败")
    MCP_REGISTER_FAILED = ("BRG-5002", "MCP 工具注册失败", "工具注册失败")
    CONFIG_LOAD_FAILED = ("BRG-5003", "配置文件加载失败", "配置加载失败")
    SINGLETON_CHECK_FAILED = ("BRG-5004", "检测到重复运行的实例", "重复运行")
    SHUTDOWN_TIMEOUT = ("BRG-5005", "优雅关闭超时", "关闭超时")

    # ========== 骰子核心错误 (6000-6999) ==========
    DICE_INVALID_EXPR = ("BRG-6001", "骰子表达式无效", "无效的骰子表达式")
    DICE_CARD_NOT_FOUND = ("BRG-6002", "角色卡不存在", "角色卡不存在")
    DICE_CARD_LIMIT = ("BRG-6003", "角色卡数量已达上限 (5张)", "角色卡已满")
    DICE_SKILL_NOT_FOUND = ("BRG-6004", "技能或属性未在角色卡中找到", "技能未录入")
    DICE_SAVE_FAILED = ("BRG-6005", "角色卡保存失败", "保存失败")
    DICE_LOAD_FAILED = ("BRG-6006", "角色卡加载失败", "加载失败")

    # ========== 行于泰拉错误 (7000-7999) ==========
    ARK_INVALID_FORMAT = ("BRG-7001", "检定格式错误", "格式错误")
    ARK_SKILL_VALUE_ZERO = ("BRG-7002", "技能值+奖励骰 <=0", "技能值无效")
    ARK_CARD_SET_FAILED = ("BRG-7003", "群名片设置失败", "名片设置失败")

    # ========== 日志系统错误 (8000-8999) ==========
    LOG_NOT_FOUND = ("BRG-8001", "日志不存在", "日志不存在")
    LOG_ALREADY_EXISTS = ("BRG-8002", "日志名称已存在", "日志已存在")
    LOG_NO_ACTIVE = ("BRG-8003", "没有活跃的日志", "无活跃日志")
    LOG_WRITE_FAILED = ("BRG-8004", "日志写入失败", "写入失败")
    LOG_DELETE_FAILED = ("BRG-8005", "日志删除失败", "删除失败")

    # ========== 通用错误 (9000-9999) ==========
    UNKNOWN_ERROR = ("BRG-9001", "未知错误", "系统错误")
    INTERNAL_ERROR = ("BRG-9002", "内部服务器错误", "内部错误")
    RATE_LIMITED = ("BRG-9003", "请求频率限制", "操作过于频繁")
    SERVICE_UNAVAILABLE = ("BRG-9004", "服务暂时不可用", "服务不可用")

    def __init__(self, code: str, message: str, user_text: str):
        self.code = code
        self.message = message
        self.user_text = user_text

    @classmethod
    def from_code(cls, code: str) -> "ErrorCode | None":
        """从错误码字符串获取 ErrorCode 枚举"""
        for error in cls:
            if error.code == code:
                return error
        return None


class BridgeError(Exception):
    """
    桥接系统基础异常类

    所有模块抛出的异常都应继承此类，
    包含标准化的错误码和详细信息。
    """

    def __init__(
        self,
        error_code: ErrorCode | str,
        detail: str = "",
        custom_text: str | None = None,
        original_exception: Exception | None = None,
    ):
        """
        初始化桥接异常

        Args:
            error_code: 错误码 (ErrorCode 枚举或字符串)
            detail: 详细错误信息 (用于日志)
            custom_text: 自定义用户提示文本 (覆盖默认 user_text)
            original_exception: 原始异常 (用于异常链)
        """
        if isinstance(error_code, ErrorCode):
            self.error_code = error_code.code
            self._error_enum = error_code
        else:
            self.error_code = error_code
            self._error_enum = ErrorCode.from_code(error_code)

        self.detail = detail
        self.custom_text = custom_text
        self.original_exception = original_exception

        # 构建异常消息 (用于日志)
        if self._error_enum:
            message = f"[{self.error_code}] {self._error_enum.message}"
            if detail:
                message += f" - {detail}"
        else:
            message = f"[{self.error_code}] {detail or '未知错误'}"

        super().__init__(message)

    @property
    def user_message(self) -> str:
        """
        获取展示给用户的消息

        格式: "[自定义文本]+[错误码]"
        """
        if self._error_enum:
            text = self.custom_text or self._error_enum.user_text
        else:
            text = self.custom_text or "处理失败"
        return f"{text} [{self.error_code}]"

    def to_dict(self) -> dict:
        """转换为字典 (用于日志记录)"""
        return {
            "error_code": self.error_code,
            "detail": self.detail,
            "custom_text": self.custom_text,
            "user_message": self.user_message,
            "original_exception": str(self.original_exception) if self.original_exception else None,
        }
