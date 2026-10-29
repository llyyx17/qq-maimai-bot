"""群聊自主插话：不用 @ 也能在群里接话。

原理（AstrBot 管线）：一条群消息只有在下列任一情况才算“被唤醒”——被 @、@全体、
被引用、以 wake_prefix 开头、私聊、或者**某个插件的 handler filter 通过**。
最后一条是这里利用的口子：

1. 本插件注册一个不带 @ 条件的 handler，于是每条群消息都会激活它；
2. 判定通过时把 event.is_at_or_wake_command 设为 True —— 这是 ProcessStage 里
   真正决定「要不要调 LLM」的那个开关；
3. 再用 on_llm_request 把最近的群聊拼进 system_prompt，让它知道群里在聊什么
   （没被 @ 的消息不会进 AstrBot 的会话历史，所以这块得自己补）。

注意：WakingCheckStage 排在白名单之前，所以这里看到的是**所有**群消息；
真正拦住非白名单群的是后面的 WhitelistCheckStage，本插件不绕过它。
"""

from __future__ import annotations

import time

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

from .decision import (
    GroupState,
    add_message,
    build_context_block,
    decide,
    mark_reply,
    merged_config,
)

PLUGIN_ID = "astrbot_plugin_group_autochat"


@register(PLUGIN_ID, "operator", "群聊自主插话：关键词 / 随机冒泡，不需要 @", "1.0.0")
class GroupAutoChat(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.states: dict[str, GroupState] = {}
        # 启动时把生效配置打进日志：调过参数之后能直接从日志确认「改的到底生效没」，
        # 不用去翻 JSON 猜 AstrBot 有没有重新加载。
        try:
            cfg = merged_config(config)
            logger.info(
                f"[autochat] 已启用：关键词额度 {cfg['keyword_max_per_hour']}/小时、"
                f"随机冒泡额度 {cfg['random_max_per_hour']}/小时，"
                f"关键词 {cfg['keywords']}，随机冒泡概率 {cfg['random_probability']}"
            )
        except Exception as e:  # pragma: no cover - 配置异常不该阻止插件加载
            logger.warning(f"[autochat] 读取配置失败：{e}")

    def _cfg(self) -> dict:
        return merged_config(self.config)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """每条群消息都会进来：先记账，再决定要不要插话。"""
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return

        group = str(event.get_group_id() or "")
        state = self.states.setdefault(group, GroupState())
        text = (event.message_str or "").strip()
        now = time.time()

        # 不管回不回，先把这条记进「群聊近况」，插话时要用
        sender = event.get_sender_name() or event.get_sender_id() or "?"
        add_message(state, sender, text, now)

        # 已经被 @ / wake_prefix 唤醒的，走 AstrBot 原来的流程，这里不重复处理
        if event.is_at_or_wake_command:
            return

        result = decide(
            text=text,
            sender_id=event.get_sender_id(),
            self_id=event.get_self_id(),
            group=group,
            config=cfg,
            state=state,
            now=now,
        )

        if not result["reply"]:
            # 用 info 而不是 debug：AstrBot 默认日志等级是 INFO，logger.debug 会被直接吞掉，
            # 那样「打开 debug 看看为什么不说话」就变成打开了一个永远空的开关。
            if cfg.get("debug"):
                logger.info(f"[autochat] {group} 不插话（{result['mode'] or '-'}）：{result['reason']}")
            return

        # 关键一步：打开 LLM 调用开关，剩下的交给 AstrBot 正常流程
        event.is_at_or_wake_command = True
        event.is_wake = True
        event.set_extra("autochat_wake", True)
        event.set_extra("autochat_reason", result["reason"])
        mark_reply(state, now, result["mode"])
        logger.info(f"[autochat] {group} 自主插话（{result['mode']}）：{result['reason']}")

    @filter.on_llm_request()
    async def inject_group_context(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """只在自主插话时注入群聊近况；正常 @ 回复不改变原有行为。"""
        if not event.get_extra("autochat_wake"):
            return
        group = str(event.get_group_id() or "")
        state = self.states.get(group)
        if state is None:
            return
        cfg = self._cfg()
        block = build_context_block(state, limit=int(cfg.get("context_messages") or 10))
        if not block:
            return
        base = (req.system_prompt or "").rstrip()
        req.system_prompt = f"{base}\n\n{block}".strip() if base else block
        logger.debug(f"[autochat] {group} 已注入群聊近况（{len(block)} 字）")

    async def terminate(self) -> None:
        self.states.clear()
