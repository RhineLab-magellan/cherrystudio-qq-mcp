"""
内置命令实现

包含核心管理命令:
- .help - 帮助信息
- .bot - 机器人开关
- .order - 指令白名单
- .model - 模型切换
- .ob - 旁观者模式
- .dismiss - 退群
"""

import json
import logging
from pathlib import Path
from modules.command_module import Command, CommandContext
from modules.commands.utils import load_bot_setting, format_msg
from protocols.messages import ParsedMessage, MessageSource

logger = logging.getLogger(__name__)


class HelpCommand(Command):
    """显示帮助信息 — 支持标准结构输出"""
    name = "help"
    description = "显示所有可用命令及其说明"
    group = "系统"
    usage = ".help [命令名]"

    # ---- 分组显示顺序 ----
    _GROUP_ORDER = ["系统", "会话管理", "群管理", "骰子", "行于泰拉", "日志"]

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        query = args.strip().lstrip(".")

        # ---- 单命令详细帮助 ----
        if query and ctx.command_registry:
            cmd = ctx.command_registry.get(query)
            if cmd:
                return self._cmd_detail(cmd)
            else:
                return f"未找到命令: .{query}\n\n{self._full_help(ctx)}"

        # ---- 全量帮助 ----
        if ctx.command_registry:
            return self._full_help(ctx)

        # 回退
        return "📖 命令注册表不可用，请稍后重试。"

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _full_help(self, ctx: CommandContext) -> str:
        """
        标准结构全量帮助:

        ----
        [通用自定义提示语]
        -模块：作用介绍-
        子命令： 作用介绍 ：使用规范
        ----
        """
        commands = ctx.command_registry.list_all()

        # 1. 通用自定义提示语
        greeting = load_bot_setting("help", "help_greeting", "")

        # 2. 按 group 分组
        groups: dict[str, list[Command]] = {}
        for cmd in commands:
            groups.setdefault(cmd.group, []).append(cmd)

        # 排序: 按 _GROUP_ORDER 定义的顺序，未定义的排末尾
        def _sort_key(g: str) -> int:
            try:
                return self._GROUP_ORDER.index(g)
            except ValueError:
                return 999

        lines: list[str] = ["----"]

        if greeting:
            lines.append(greeting)

        for group_name in sorted(groups.keys(), key=_sort_key):
            cmds = groups[group_name]
            lines.append(f"\n-{group_name}：{self._group_desc(group_name, cmds)}-")
            for cmd in cmds:
                usage_spec = cmd.usage or f".{cmd.name}"
                lines.append(f"  .{cmd.name}：{cmd.description}：{usage_spec}")

        lines.append("\n----")
        lines.append("输入 .help <命令名> 查看详细帮助")
        return "\n".join(lines)

    def _cmd_detail(self, cmd: Command) -> str:
        """
        单命令详细帮助:

        ----
        命令：.名称
        模块：分组
        描述：description
        使用：usage

        [reminder]

        [sub_help if available]
        ----
        """
        lines = [
            "----",
            f"命令：.{cmd.name}",
            f"模块：{cmd.group}",
            f"描述：{cmd.description}",
        ]
        if cmd.usage:
            lines.append(f"使用：{cmd.usage}")

        if cmd.reminder:
            lines.append(f"\n{cmd.reminder}")

        # 子命令帮助 (如果命令有 _sub_help 方法)
        if hasattr(cmd, "_sub_help") and callable(cmd._sub_help):
            lines.append("")
            lines.append(cmd._sub_help())

        lines.append("----")
        return "\n".join(lines)

    @staticmethod
    def _group_desc(group: str, cmds: list[Command]) -> str:
        """生成分组描述"""
        descs = {
            "系统": "基础功能与帮助",
            "会话管理": "Agent 切换、模型偏好、会话控制",
            "群管理": "群聊开关、旁观者、欢迎语、退群",
            "骰子": "骰子投掷、角色卡管理、COC 检定",
            "行于泰拉": "Ark TRPG 技能检定、人物作成、名片",
            "日志": "群聊日志记录与导出",
        }
        return descs.get(group, "、".join(c.description for c in cmds[:2]))


class BotCommand(Command):
    """控制机器人在群聊的回复状态"""
    name = "bot"
    description = "开启或关闭机器人在本群的自动回复"
    group = "会话管理"
    usage = ".bot [on/off/status/orderwhite]"
    reminder = "使用 .bot on/off 开关机器人；.bot orderwhite 切换免@模式"

    # 默认消息 (移植自旧项目 BotSettingConfig 默认值)
    DEFAULT_ON_MSG = "✅ 已恢复正常回复。"
    DEFAULT_OFF_MSG = "⛔ 已开启指令模式，仅响应 .开头的命令。"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        args_lower = args.strip().lower()
        is_group = msg.raw.source == MessageSource.GROUP
        target_id = msg.raw.target_id

        # ---- 空参: 显示版本/欢迎信息 (私聊群聊均可) ----
        if not args_lower:
            return self._build_greeting(ctx)

        # ---- help 子命令 (私聊群聊均可) ----
        if args_lower in ("help", "?"):
            return self._sub_help()

        # ---- on/off/status/orderwhite 需要群上下文 ----
        if not is_group:
            return ".bot on/off/status/orderwhite 仅在群聊中有效。\n私聊中请使用 .bot 查看版本信息，或 .bot help 查看帮助。"

        if args_lower == "on":
            await ctx.state_manager.remove_from_blacklist(target_id)
            template = load_bot_setting("BuiltInOrder", "bot_on_message", self.DEFAULT_ON_MSG)
            return template

        elif args_lower == "off":
            await ctx.state_manager.add_to_blacklist(target_id)
            template = load_bot_setting("BuiltInOrder", "bot_off_message", self.DEFAULT_OFF_MSG)
            return template

        elif args_lower == "status":
            is_blocked = ctx.state_manager.is_in_blacklist(target_id)
            status = "已关闭" if is_blocked else "已开启"
            return f"📊 当前状态: {status}"

        elif args_lower in ("orderwhite", "orderWhite"):
            if ctx.state_manager.is_in_whitelist(target_id):
                await ctx.state_manager.remove_from_whitelist(target_id)
                status_text = "已关闭本群的免@功能"
            else:
                await ctx.state_manager.add_to_whitelist(target_id)
                status_text = "已开启本群的免@功能"
            template = load_bot_setting("BuiltInOrder", "bot_orderwhite_message", "")
            if template:
                return format_msg(template, status_text)
            return f"✅ {status_text}"

        else:
            return f"未知子命令: .bot {args}\n\n{self._sub_help()}\n\n输入 .help 查看完整命令列表。"

    def _sub_help(self) -> str:
        return (
            ".bot 子命令:\n"
            "  .bot              - 显示版本信息与欢迎语\n"
            "  .bot on           - 恢复正常回复 (群聊)\n"
            "  .bot off          - 仅响应 .xxx 指令，不参与聊天 (群聊)\n"
            "  .bot status       - 查看当前状态 (群聊)\n"
            "  .bot orderwhite   - 切换本群免@模式 (群聊)\n"
            "  .bot help         - 显示此帮助"
        )

    @staticmethod
    def _build_greeting(ctx: CommandContext) -> str:
        """
        构建欢迎消息: 自定义问候 + 版本号 + reminder 列表 + 引导

        移植自旧系统 auto_reply.build_greeting()
        """
        VERSION = "QQ-MCP Bridge v3.0.0 by RhineLab-magellan"

        custom = load_bot_setting("内置模块", "custom_greeting", "").strip()

        parts: list[str] = []
        if custom:
            parts.append(custom)
        parts.append(VERSION)

        # 收集各命令的 reminder
        if ctx.command_registry:
            reminders = [c.reminder for c in ctx.command_registry.list_all() if c.reminder]
            if reminders:
                parts.append("---")
                parts.extend(reminders)

        parts.append("---")
        parts.append("输入 .help 查看完整命令列表")

        return "\n".join(parts)


class OrderCommand(Command):
    """会话与 Agent 管理 + 免@白名单"""
    name = "order"
    description = "会话/Agent 管理 (切换/列表/重建/状态)"
    group = "会话管理"
    usage = ".order <子命令> [参数]"
    reminder = "使用 .order 切换 <Agent> 切换 Agent；.order 重建会话 重置上下文"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        parts = args.strip().split(None, 1)
        action = parts[0] if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        # ---- Agent 管理命令 ----

        if action in ("切换", "switch"):
            return await self._switch_agent(msg, ctx, sub_args)

        if action in ("列表", "listagents", "agents"):
            return self._list_agents(ctx)

        if action in ("重建", "rebuild", "reset", "重建会话"):
            return await self._rebuild_session(msg, ctx)

        if action in ("状态", "status"):
            return await self._status(msg, ctx)

        # ---- 免@白名单命令 ----

        if action == "list":
            whitelist = ctx.state_manager.state.order_whitelist
            if not whitelist:
                return "📋 当前没有免@群"
            groups = "\n".join(f"  - {gid}" for gid in sorted(whitelist))
            return f"📋 免@群列表:\n{groups}"

        if action == "add":
            if not sub_args:
                if msg.raw.source == MessageSource.GROUP:
                    group_id = msg.raw.target_id
                else:
                    return "请指定群号: .order add <群号>"
            else:
                group_id = sub_args.strip()
            await ctx.state_manager.add_to_whitelist(group_id)
            return f"✅ 已将群 {group_id} 添加到免@白名单"

        if action == "remove":
            if not sub_args:
                return "请指定群号: .order remove <群号>"
            group_id = sub_args.strip()
            await ctx.state_manager.remove_from_whitelist(group_id)
            return f"✅ 已将群 {group_id} 从免@白名单移除"

        if action in ("help", "帮助", "?"):
            return self._sub_help()

        if action:
            return f"未知子命令: .order {args}\n\n{self._sub_help()}\n\n输入 .help 查看完整命令列表。"

        return self._sub_help()

    def _sub_help(self) -> str:
        return (
            ".order 子命令:\n"
            "  .order 切换 <名称>     - 切换到指定 Agent\n"
            "  .order 列表            - 查看所有可用 Agent\n"
            "  .order 重建会话        - 删除当前会话，下次对话开启新上下文\n"
            "  .order status          - 查看当前会话状态\n"
            "  .order list            - 查看免@群列表\n"
            "  .order add/remove [群号] - 管理免@白名单\n"
            "  .order help            - 显示此帮助"
        )

    # ------------------------------------------------------------------
    # Agent 管理子命令实现
    # ------------------------------------------------------------------

    async def _switch_agent(
        self, msg: ParsedMessage, ctx: CommandContext, name: str
    ) -> str:
        """切换到指定 Agent"""
        cs = ctx.cherrystudio_module
        if not cs:
            return "CherryStudio 模块未就绪"

        name = name.strip()
        if not name:
            # 列出可用 Agent
            return self._list_agents(ctx)

        discovered = getattr(cs, "discovered_agents", {})
        if not discovered:
            return "当前没有可用的 Agent (尚未完成自动发现)"

        if name not in discovered:
            available = "、".join(discovered.keys())
            return f"未找到 Agent「{name}」。\n可用: {available}"

        # 1. 停止当前会话处理器 (触发清理，保留远程会话供复用)
        session_key = msg.session_key
        if session_key in cs.session_handlers:
            await cs.rebuild_session(session_key)

        # 2. 设置新 Agent 为当前会话的活跃 Agent
        await ctx.state_manager.set_active_agent(session_key, name)

        # ---- B3 修复: 使 ConversationStore 内存缓存失效 ----
        # 避免下次 load_session 返回旧 Agent 目录下的缓存数据
        # ---- B4 修复: 更新 mapping.json 指向新 Agent ----
        if cs.conversation_store:
            cs.conversation_store.invalidate_session(session_key)
            cs.conversation_store.mapping[session_key] = name
            cs.conversation_store._save_mapping()
            logger.info(
                f"[.order] Session mapping updated: {session_key} -> {name}")

        return f"✅ 已切换到 Agent「{name}」(已持久化，重启后仍生效)。下次消息将使用新 Agent。"

    def _list_agents(self, ctx: CommandContext) -> str:
        """列出所有可用 Agent"""
        cs = ctx.cherrystudio_module
        if not cs:
            return "CherryStudio 模块未就绪"

        discovered = getattr(cs, "discovered_agents", {})
        default_agent = getattr(cs, "agent_id", "default")

        if not discovered:
            # 回退: 显示配置的默认 Agent
            return (
                f"可用 Agent:\n"
                f"  1. {default_agent} ← 当前默认\n\n"
                f"(尚未完成自动发现，仅显示配置的默认 Agent)"
            )

        lines = ["可用 Agent:"]
        for i, (name, cfg) in enumerate(discovered.items(), 1):
            marker = " ← 当前默认" if cfg.get("agent_id", "") == default_agent else ""
            lines.append(f"  {i}. {name}{marker}")
        lines.append("\n切换指令: .order 切换 <名称>")
        return "\n".join(lines)

    async def _rebuild_session(
        self, msg: ParsedMessage, ctx: CommandContext
    ) -> str:
        """重建当前会话"""
        cs = ctx.cherrystudio_module
        if not cs:
            return "CherryStudio 模块未就绪"

        session_key = msg.session_key
        if session_key in cs.session_handlers:
            await cs.rebuild_session(session_key)
            return f"✅ 会话已重建。下次消息将开启全新会话。"
        else:
            return "当前没有活跃会话，无需重建。"

    async def _status(
        self, msg: ParsedMessage, ctx: CommandContext
    ) -> str:
        """显示当前会话状态 (含 Agent、模型偏好、处理器状态)"""
        cs = ctx.cherrystudio_module
        session_key = msg.session_key

        # 从 StateManager 获取当前绑定的 Agent
        active_agent = await ctx.state_manager.get_active_agent(session_key)

        # 从 StateManager 获取持久化的模型偏好
        saved_model = await ctx.state_manager.get_saved_model(session_key)

        # 从 CherryStudioModule 获取会话处理器状态
        handler_exists = False
        session_id = None
        agent_name = active_agent or "default"
        if cs:
            handler = cs.session_handlers.get(session_key)
            if handler and handler.session_data:
                handler_exists = True
                session_id = handler.session_data.session_id
                agent_name = handler.session_data.agent_name

        # 构建状态信息
        lines = [
            f"📊 会话状态: {session_key}",
            f"当前 Agent: {agent_name}",
            f"模型偏好: {saved_model or '默认'}",
            f"会话处理器: {'活跃' if handler_exists else '空闲'}",
            f"远程会话: {session_id[:24] + '...' if session_id else '无'}",
        ]

        # 补充 discovered_agents 数量
        if cs:
            n_agents = len(getattr(cs, "discovered_agents", {}))
            lines.append(f"可用 Agent 数: {n_agents}")

        return "\n".join(lines)


class ModelCommand(Command):
    """管理模型偏好 (持久化到 SharedState.saved_models)"""
    name = "model"
    description = "查看或切换LLM模型 (change/reset 需管理员)"
    group = "会话管理"
    usage = ".model list/change/status/reset"
    reminder = "使用 .model list 查看模型；.model change <名称> 切换"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        parts = args.strip().split(None, 1)
        if not parts or parts[0] in ("help", "?"):
            return self._sub_help()

        action = parts[0].lower()
        session_key = msg.session_key
        admin_qq = ctx.config.get("admin_qq", "")
        is_admin = str(msg.raw.sender_id) == str(admin_qq)

        if action == "list":
            # 从 config 的 llm_providers 动态获取模型列表
            providers = ctx.config.get("llm_providers", [])
            if not providers:
                providers = ctx.config.get("llm", [])
            if providers and isinstance(providers, list):
                lines = ["📊 可用模型:"]
                idx = 1
                for p in providers:
                    if isinstance(p, dict):
                        prov_name = p.get("name", "unknown")
                        models = p.get("models", [])
                        if models:
                            for m in models:
                                lines.append(f"  {idx}. {m} [{prov_name}]")
                                idx += 1
                        else:
                            # 兼容无 models 数组的旧配置
                            name = p.get("model", prov_name)
                            lines.append(f"  {idx}. {name} [{prov_name}]")
                            idx += 1
                    else:
                        lines.append(f"  {idx}. {p}")
                        idx += 1
                lines.append("\n使用 .model change <模型名> 切换")
                lines.append("使用 .model reset 恢复默认模型")
                return "\n".join(lines)
            return (
                "📊 当前未配置模型列表 (config.json 中无 llm_providers)。\n"
                "使用 .model change <模型名> 手动指定\n"
                "使用 .model reset 恢复默认模型"
            )

        elif action == "change":
            if not is_admin:
                return "⛔ 权限不足。.model change 仅限管理员使用。"
            if len(parts) < 2:
                return "请指定模型名称: .model change <模型名>"
            model_name = parts[1].strip()
            await ctx.state_manager.set_saved_model(session_key, model_name)
            return f"✅ 已切换到模型: {model_name} (已持久化，重启后仍生效)"

        elif action == "status":
            current_model = await ctx.state_manager.get_saved_model(session_key)
            if current_model:
                return f"📊 当前模型: {current_model} (持久化偏好)"
            else:
                return "📊 当前使用默认模型 (未设置偏好)"

        elif action == "reset":
            if not is_admin:
                return "⛔ 权限不足。.model reset 仅限管理员使用。"
            await ctx.state_manager.remove_saved_model(session_key)
            return "✅ 已清除模型偏好，将使用默认模型"

        else:
            return f"未知子命令: .model {args}\n\n{self._sub_help()}\n\n输入 .help 查看完整命令列表。"

    def _sub_help(self) -> str:
        return (
            ".model 子命令:\n"
            "  .model list            - 查看可用模型列表\n"
            "  .model change <模型名>  - 切换当前会话模型 (管理员)\n"
            "  .model status          - 查看当前模型偏好\n"
            "  .model reset           - 清除模型偏好 (管理员)\n"
            "  .model help            - 显示此帮助"
        )


class ObCommand(Command):
    """旁观者模式管理"""
    name = "ob"
    description = "管理旁观者模式 (群聊)"
    group = "群管理"
    usage = ".ob [join/exit/list/clr/on/off]"
    reminder = "使用 .ob join 加入旁观，发言不计入日志；.ob on/off 开关旁观模式"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        if msg.raw.source != MessageSource.GROUP:
            return ".ob 仅在群聊中有效。"

        group_id = msg.raw.target_id
        user_id = msg.raw.sender_id
        parts = args.strip().split()
        action = parts[0].lower() if parts else ""

        # ---- 空参 / join: 默认加入旁观 (移植自旧系统) ----
        if not action or action == "join":
            ob_groups = set(ctx.state_manager.state.ob_groups)
            ob_groups.add(group_id)
            observers = dict(ctx.state_manager.state.observers)
            if group_id not in observers:
                observers[group_id] = set()
            observers[group_id].add(user_id)
            await ctx.state_manager.update_state({
                "ob_groups": ob_groups,
                "observers": observers,
            })
            default_reply = f"✅ {user_id} 已加入旁观者模式，您将收到本群的所有消息日志"
            template = load_bot_setting("ob", "ob_join_message", "")
            if template:
                return format_msg(template, default_reply, player_name=user_id)
            return default_reply

        elif action == "on":
            ob_groups = set(ctx.state_manager.state.ob_groups)
            ob_groups.add(group_id)
            await ctx.state_manager.update_state({"ob_groups": ob_groups})
            return "✅ 已开启本群的旁观者模式"

        elif action == "off":
            ob_groups = set(ctx.state_manager.state.ob_groups)
            ob_groups.discard(group_id)
            await ctx.state_manager.update_state({"ob_groups": ob_groups})
            return "✅ 已关闭本群的旁观者模式"

        elif action == "clr":
            observers = dict(ctx.state_manager.state.observers)
            count = len(observers.pop(group_id, set()))
            await ctx.state_manager.update_state({"observers": observers})
            return f"✅ 已清除本群 {count} 位旁观者"

        elif action == "exit":
            observers = dict(ctx.state_manager.state.observers)
            if group_id in observers:
                observers[group_id].discard(user_id)
            await ctx.state_manager.update_state({"observers": observers})
            return f"✅ {user_id} 已退出旁观者模式"

        elif action == "list":
            observers = ctx.state_manager.state.observers.get(group_id, set())
            if not observers:
                list_text = "📋 当前群没有旁观者"
            else:
                users = "\n".join(f"  - {uid}" for uid in sorted(observers))
                list_text = f"📋 旁观者列表:\n{users}"
            template = load_bot_setting("ob", "ob_list_message", "")
            if template:
                return format_msg(template, list_text)
            return list_text

        elif action in ("help", "?"):
            return self._sub_help()

        else:
            return f"未知子命令: .ob {args}\n\n{self._sub_help()}\n\n输入 .help 查看完整命令列表。"

    def _sub_help(self) -> str:
        return (
            ".ob 子命令:\n"
            "  .ob / .ob join  - 加入旁观 (默认)\n"
            "  .ob exit        - 退出旁观\n"
            "  .ob list        - 查看旁观者\n"
            "  .ob clr         - 清除所有旁观者\n"
            "  .ob on/off      - 开关旁观模式\n"
            "  .ob help        - 显示此帮助"
        )


class DismissCommand(Command):
    """让机器人退出指定群"""
    name = "dismiss"
    description = "让机器人退出指定群 (管理员)"
    group = "群管理"
    usage = ".dismiss <群号后四位>"
    reminder = "使用 .dismiss <群号后四位> 退群"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        # 权限检查: 仅管理员可执行
        admin_qq = ctx.config.get("admin_qq", "")
        if str(msg.raw.sender_id) != str(admin_qq):
            return "⛔ 仅管理员可执行退群操作"

        if not args.strip() or args.strip() in ("help", "?"):
            return self._sub_help()

        suffix = args.strip()

        # 验证群号后四位格式
        if not suffix.isdigit() or len(suffix) != 4:
            return f"请提供群号的末4位数字，例如: .dismiss 1234\n\n{self._sub_help()}"

        # 获取群列表并匹配末4位
        if not ctx.napcat_bridge:
            return "错误: NapCat 未连接"

        try:
            groups = await ctx.napcat_bridge.get_group_list()
        except Exception as e:
            return f"获取群列表失败: {e}"

        matched = []
        for g in groups:
            gid = str(g.get("group_id", ""))
            if gid.endswith(suffix):
                matched.append(g)

        if not matched:
            return f"未找到群号末4位为 {suffix} 的群"
        elif len(matched) > 1:
            names = "\n".join(
                f"  - {g.get('group_id', '')} ({g.get('group_name', '未知')})"
                for g in matched
            )
            return f"找到多个匹配群:\n{names}\n请使用更精确的后4位"

        target_group = matched[0]
        group_id = str(target_group.get("group_id", ""))
        group_name = target_group.get("group_name", "未知")

        # 调用退群 API
        try:
            await ctx.napcat_bridge.leave_group(group_id)
        except Exception as e:
            return f"退群失败: {e}"

        # 退群成功 (leave_group 无异常即为成功)
        # 退群后清理本地数据
        await ctx.state_manager.remove_from_blacklist(group_id)
        await ctx.state_manager.remove_from_whitelist(group_id)

        # 发送告别消息 (如果 BotSettingConfig 配置了)
        farewell = load_bot_setting("BuiltInOrder", "dismiss_message", "")
        if farewell and ctx.send_queue:
            try:
                from protocols.messages import OutgoingMessage, MessageType
                outgoing = OutgoingMessage(
                    target_source=MessageSource.GROUP,
                    target_id=group_id,
                    content=farewell,
                    message_type=MessageType.TEXT,
                    skip_doc=True,
                )
                await ctx.send_queue.put(outgoing)
            except Exception as e:
                logger.debug(f"Send group farewell message failed: {e}")

        return f"✅ 已退出群: {group_name} ({group_id})"

    def _sub_help(self) -> str:
        return (
            ".dismiss 使用说明 (管理员):\n"
            "  .dismiss <群号后四位>  - 让机器人退出匹配末4位的群\n"
            "  .dismiss help          - 显示此帮助\n"
            "\n退群后会自动清理该群的黑名单和白名单记录。"
        )


class SendCommand(Command):
    """管理员消息转发"""
    name = "send"
    description = "管理员消息转发到指定群或私聊"
    group = "系统"
    usage = ".send <类型> <ID> <消息>"
    reminder = "使用 .send <消息> 发送给管理员；.send <类型> <ID> <消息> 转发"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        # 权限检查: 仅管理员可执行
        admin_qq = ctx.config.get("admin_qq", "")
        if str(msg.raw.sender_id) != str(admin_qq):
            return "⛔ 仅管理员可使用消息转发"

        text = args.strip()
        if not text:
            return (
                ".send 子命令:\n"
                "  .send <消息>                        - 发送给管理员 (简化格式)\n"
                "  .send <group|private> <ID> <消息>   - 转发到指定目标 (完整格式)\n"
                "\n输入 .help 查看完整命令列表。"
            )

        parts = text.split(None, 2)

        # ---- 简化格式: .send <消息> → 发给 Master ----
        if len(parts) == 1 or (len(parts) >= 2 and parts[0].lower() not in ("group", "private")):
            master_qq = admin_qq
            if not master_qq:
                return "未配置 admin_qq，无法使用简化格式。请使用完整格式: .send <类型> <ID> <消息>"
            if not ctx.napcat_bridge:
                return "错误: NapCat 未连接"
            try:
                from protocols.messages import OutgoingMessage, MessageSource, MessageType
                outgoing = OutgoingMessage(
                    target_source=MessageSource.PRIVATE,
                    target_id=str(master_qq),
                    content=text,
                    message_type=MessageType.TEXT,
                    skip_doc=True,
                )
                await ctx.napcat_bridge.send_message(outgoing)
                return "✅ 已发送给管理员"
            except Exception as e:
                return f"发送失败: {e}"

        # ---- 完整格式: .send <group|private> <ID> <消息> ----
        target_type = parts[0].lower()
        target_id = parts[1]
        message = parts[2] if len(parts) > 2 else ""

        if not message:
            return "消息内容不能为空。用法: .send <类型> <ID> <消息>"

        if not ctx.napcat_bridge:
            return "错误: NapCat 未连接"

        try:
            from protocols.messages import OutgoingMessage, MessageSource, MessageType

            source = MessageSource.GROUP if target_type == "group" else MessageSource.PRIVATE
            outgoing = OutgoingMessage(
                target_source=source,
                target_id=target_id,
                content=message,
                message_type=MessageType.TEXT,
                skip_doc=True,
            )
            await ctx.napcat_bridge.send_message(outgoing)
            return f"✅ 消息已转发到 {target_type}:{target_id}"
        except Exception as e:
            return f"发送失败: {e}"


class MasterCommand(Command):
    """管理员专用命令"""
    name = "master"
    description = "管理员专用 (LLMReset/AllResetAgent/OnlyResetAgent)"
    group = "系统"
    usage = ".master <子命令>"
    reminder = "管理员: .master LLMReset/AllResetAgent/OnlyResetAgent"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        # 权限检查: 仅管理员可执行
        admin_qq = ctx.config.get("admin_qq", "")
        if str(msg.raw.sender_id) != str(admin_qq):
            return "⛔ 仅管理员可使用此命令"

        sub_cmd = args.strip()
        if not sub_cmd:
            return self._sub_help()

        sub_cmd_lower = sub_cmd.lower()

        if sub_cmd_lower == "llmreset":
            await ctx.state_manager.update_state({"active_agents": {}})
            return "✅ 已重置 LLM Provider 到默认配置"

        elif sub_cmd_lower == "allresetagent":
            await ctx.state_manager.update_state({
                "active_agents": {},
                "observers": {},
                "ob_groups": set(),
            })
            return "✅ 已重置所有会话数据"

        elif sub_cmd_lower == "onlyresetagent":
            await ctx.state_manager.update_state({"active_agents": {}})
            return "✅ 已清除活跃会话记录"

        elif sub_cmd_lower in ("help", "?"):
            return self._sub_help()

        else:
            return f"未知子命令: .master {sub_cmd}\n\n{self._sub_help()}\n\n输入 .help 查看完整命令列表。"

    def _sub_help(self) -> str:
        return (
            ".master 子命令 (管理员):\n"
            "  .master LLMReset         - 重置主 KEY (回退到默认 Provider)\n"
            "  .master AllResetAgent    - 删除所有会话数据\n"
            "  .master OnlyResetAgent   - 仅清除活跃会话记录\n"
            "  .master help             - 显示此帮助"
        )


class WelcomeCommand(Command):
    """管理新成员欢迎消息"""

    name = "welcome"
    description = "新成员欢迎设置 (群聊)"
    group = "群管理"
    usage = ".welcome open/close/set/status"
    reminder = "使用 .welcome open/close/set/status 管理新成员入群欢迎"

    DEFAULT_WELCOME = "欢迎新人！我是本群助手，发送 .help 查看可用命令～"

    async def handle(self, args: str, msg: ParsedMessage, ctx: CommandContext) -> str | None:
        if msg.raw.source != MessageSource.GROUP:
            return ".welcome 仅在群聊中有效。"

        group_id = msg.raw.target_id
        parts = args.strip().split(None, 1)
        action = parts[0].lower() if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if action in ("open", "开启", "on"):
            await ctx.state_manager.set_welcome(group_id, enabled=True)
            entry = ctx.state_manager.get_welcome(group_id)
            msg_preview = entry.get("message") or "(使用默认欢迎语)"
            return f"✅ 本群新成员欢迎已开启。\n当前欢迎语: {msg_preview}\n可用 {{at}} 代表新成员 @"

        elif action in ("close", "关闭", "off"):
            await ctx.state_manager.set_welcome(group_id, enabled=False)
            return "✅ 本群新成员欢迎已关闭。"

        elif action in ("set", "设置"):
            if not sub_args.strip():
                return "❌ 欢迎语不能为空。用法: .welcome set <消息>\n可用 {at} 代表新成员 @"
            await ctx.state_manager.set_welcome(group_id, message=sub_args.strip())
            entry = ctx.state_manager.get_welcome(group_id)
            status = "已开启" if entry.get("enabled") else "已关闭"
            return f"✅ 欢迎语已设置: {sub_args.strip()}\n当前状态: {status}（用 .welcome open 开启）"

        elif action in ("status", "状态"):
            entry = ctx.state_manager.get_welcome(group_id)
            status = "已开启" if entry.get("enabled") else "已关闭"
            welcome_msg = entry.get("message") or "(未设置，将使用默认欢迎语)"
            return f"📢 本群新成员欢迎: {status}\n欢迎语: {welcome_msg}\n可用 {{at}} 代表新成员 @"

        elif action in ("help", "帮助", "?", ""):
            return self._sub_help()

        else:
            return f"未知子命令: .welcome {args}\n\n{self._sub_help()}\n\n输入 .help 查看完整命令列表。"

    def _sub_help(self) -> str:
        return (
            ".welcome 子命令:\n"
            "  .welcome open        - 开启本群新成员欢迎\n"
            "  .welcome close       - 关闭本群新成员欢迎\n"
            "  .welcome set <消息>  - 设置欢迎语（可用 {at} 代表新成员 @）\n"
            "  .welcome status      - 查看当前欢迎设置\n"
            "  .welcome help        - 显示此帮助"
        )
