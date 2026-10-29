"""群聊自主插话的判定逻辑。

刻意做成纯函数 + 显式状态：不 import AstrBot，能直接 `python -m unittest` 跑，
这样「该不该说话」这种规则可以反复调，不用真的在群里试。

两条触发路径：
  keyword —— 消息里出现配置的关键词（有人叫外号、聊到特定话题）
  random  —— 纯概率冒泡，但**必须先满足群里有热度**（最近窗口内消息数够多），
             否则人少的时候它会对着空气自言自语。
两条都受「同一群冷却」和「每小时上限」约束。
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field

DEFAULTS: dict = {
    "enabled": True,
    # 空列表 = 白名单里的所有群都适用（AstrBot 的白名单阶段已经先过滤过一遍了）
    "groups": [],
    # 注意：不要放「舞萌」「maimai」这种全群天天在说的词，那等于每条都触发。
    # 这里放的是「有人在叫它」或「明确在聊某个具体话题」的信号。
    "keywords": ["豪鱼", "小鲸鱼", "鲸鱼", "臭鱼", "鱼哥"],
    "keyword_probability": 0.9,
    "random_probability": 0.08,
    "keyword_cooldown_seconds": 60,
    "random_cooldown_seconds": 240,
    # 两条路径各自一份额度：关键词是「有人在叫它」，该接就接；随机冒泡才需要压。
    # max_replies_per_hour 是旧的共用额度，已废弃（配置里留着也不会再读）。
    "keyword_max_per_hour": 20,
    "random_max_per_hour": 3,
    # 随机冒泡的热度门槛：最近 window 秒内至少有 min_messages 条消息
    "activity_window_seconds": 300,
    "activity_min_messages": 3,
    # 例如 ["02:00-08:00"]；留空 = 全天都适用
    "quiet_hours": [],
    "ignore_prefixes": ["/", "#", "／", "!", "！"],
    # 注入给模型的「群聊近况」保留多少条
    "context_messages": 10,
    # true 时把「为什么没插话」写进 debug 日志
    "debug": False,
}


@dataclass
class GroupState:
    """一个群的运行状态（进程内内存，重启即清零）。"""

    recent: list[tuple[str, str, float]] = field(default_factory=list)  # (说话人, 内容, 时间)
    keyword_times: list[float] = field(default_factory=list)  # 关键词插话的时间戳
    random_times: list[float] = field(default_factory=list)  # 随机冒泡的时间戳
    last_reply_at: float = 0.0  # 任何一次插话（随机冒泡的冷却看这个）
    last_keyword_at: float = 0.0  # 上一次关键词插话（关键词冷却看这个）


def cfg_get(config, key, default=None):
    """AstrBotConfig 可能是 dict，也可能是带 .get 的对象，统一读法。"""
    if config is None:
        return DEFAULTS.get(key, default)
    if hasattr(config, "get"):
        value = config.get(key, default)
    else:  # pragma: no cover - 只有配置对象形态变化时才会走到
        value = getattr(config, key, default)
    return default if value is None else value


def merged_config(config) -> dict:
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        out[key] = cfg_get(config, key, DEFAULTS[key])
    return out


def _to_minutes(hhmm: str) -> int | None:
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(hhmm).strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


def in_quiet_hours(now_ts: float, ranges) -> bool:
    """支持跨零点的区间，例如 "23:00-02:00"。按**本机本地时间**判断。

    （一开始用 epoch 分钟数取模，那是 UTC 的分钟数，服务器在 +08:00 时会整体偏 8 小时，
    表现成「安静时段完全没生效」——这种时区错误只能靠单测抓。）
    """
    if not ranges:
        return False
    local = time.localtime(now_ts)
    minutes = local.tm_hour * 60 + local.tm_min
    for item in ranges:
        if "-" not in str(item):
            continue
        start_s, end_s = str(item).split("-", 1)
        start, end = _to_minutes(start_s), _to_minutes(end_s)
        if start is None or end is None or start == end:
            continue
        if start < end:
            if start <= minutes < end:
                return True
        elif minutes >= start or minutes < end:  # 跨零点
            return True
    return False


def match_keyword(text: str, keywords) -> str | None:
    lowered = text.lower()
    for kw in keywords or []:
        kw = str(kw).strip()
        if kw and kw.lower() in lowered:
            return kw
    return None


def add_message(state: GroupState, sender: str, text: str, now: float, keep: int = 40) -> None:
    if not text:
        return
    state.recent.append((str(sender or "?")[:24], str(text).replace("\n", " ")[:200], now))
    if len(state.recent) > keep:
        del state.recent[: len(state.recent) - keep]


def prune(state: GroupState, now: float, window_seconds: float) -> None:
    cutoff = now - max(60.0, window_seconds)
    state.recent = [row for row in state.recent if row[2] >= cutoff]
    state.keyword_times = [t for t in state.keyword_times if now - t < 3600]
    state.random_times = [t for t in state.random_times if now - t < 3600]


def mark_reply(state: GroupState, now: float, mode: str = "random") -> None:
    """记一次插话。两条路径各自记账，额度互不占用。"""
    state.last_reply_at = now
    if mode == "keyword":
        state.last_keyword_at = now
        state.keyword_times.append(now)
    else:
        state.random_times.append(now)


def activity_count(state: GroupState, now: float, window_seconds: float) -> int:
    return sum(1 for _, _, ts in state.recent if now - ts <= window_seconds)


def decide(
    *,
    text: str,
    sender_id: str,
    self_id: str,
    group: str,
    config,
    state: GroupState,
    now: float,
    rng=random.random,
) -> dict:
    """返回 {"reply": bool, "mode": "keyword"|"random"|None, "reason": str, ...}"""
    cfg = {**DEFAULTS, **(config or {})} if isinstance(config, dict) else merged_config(config)
    text = (text or "").strip()

    if not cfg["enabled"]:
        return {"reply": False, "mode": None, "reason": "插件已关闭"}
    groups = [str(g) for g in (cfg.get("groups") or [])]
    if groups and str(group) not in groups:
        return {"reply": False, "mode": None, "reason": f"群 {group} 不在启用列表"}
    if not text:
        return {"reply": False, "mode": None, "reason": "空消息（图片/语音等）"}
    if str(sender_id) == str(self_id):
        return {"reply": False, "mode": None, "reason": "自己发的"}
    for prefix in cfg.get("ignore_prefixes") or []:
        if prefix and text.startswith(prefix):
            return {"reply": False, "mode": None, "reason": "命令前缀，交给指令系统"}
    if in_quiet_hours(now, cfg.get("quiet_hours")):
        return {"reply": False, "mode": None, "reason": "安静时段"}

    prune(state, now, float(cfg.get("activity_window_seconds") or 300))

    hit = match_keyword(text, cfg.get("keywords"))
    if hit:
        # 关键词额度是独立的：有人在叫它就该应，只受冷却和（很宽松的）小时上限约束，
        # 不会被随机冒泡的额度挤掉。
        if len(state.keyword_times) >= int(cfg.get("keyword_max_per_hour") or 20):
            return {"reply": False, "mode": "keyword", "reason": "本小时关键词插话到上限", "matched": hit}
        cooldown = float(cfg.get("keyword_cooldown_seconds") or 60)
        if state.last_keyword_at and now - state.last_keyword_at < cooldown:
            return {"reply": False, "mode": "keyword", "reason": f"命中「{hit}」但还在冷却", "matched": hit}
        if rng() >= float(cfg.get("keyword_probability") or 0.9):
            return {"reply": False, "mode": "keyword", "reason": f"命中「{hit}」但这次随机没中", "matched": hit}
        return {"reply": True, "mode": "keyword", "reason": f"命中关键词「{hit}」", "matched": hit}

    window = float(cfg.get("activity_window_seconds") or 300)
    need = int(cfg.get("activity_min_messages") or 3)
    active = activity_count(state, now, window)
    # 随机冒泡用自己那份额度，跟关键词说了多少句无关
    if len(state.random_times) >= int(cfg.get("random_max_per_hour") or 3):
        return {"reply": False, "mode": "random", "reason": "本小时随机冒泡到上限", "activity": active}
    if active < need:
        return {"reply": False, "mode": "random", "reason": f"群里太冷（{active}/{need} 条），不冒泡", "activity": active}
    cooldown = float(cfg.get("random_cooldown_seconds") or 240)
    if state.last_reply_at and now - state.last_reply_at < cooldown:
        return {"reply": False, "mode": "random", "reason": "刚说过话，随机冒泡冷却中", "activity": active}
    if rng() >= float(cfg.get("random_probability") or 0.08):
        return {"reply": False, "mode": "random", "reason": "这次随机没中", "activity": active}
    return {"reply": True, "mode": "random", "reason": f"群里有热度（{active} 条/{int(window)}s），随机冒泡", "activity": active}


def build_context_block(state: GroupState, limit: int = 10, max_chars: int = 1200) -> str:
    """把最近群聊整理成一段给模型看的上下文。"""
    rows = state.recent[-max(1, int(limit)) :]
    if not rows:
        return ""
    lines = []
    used = 0
    for sender, text, _ in rows:
        line = f"{sender}：{text}"
        if used + len(line) > max_chars:
            break
        used += len(line) + 1
        lines.append(line)
    if not lines:
        return ""
    return (
        "【群聊近况】（下面这些消息是群里刚说的，你并没有被 @，是系统允许你自主插一句）\n"
        + "\n".join(lines)
        + "\n（直接自然地接话或吐槽即可；不要解释你为什么说话，也不要说“我看到大家在聊…”这种话。）"
    )
