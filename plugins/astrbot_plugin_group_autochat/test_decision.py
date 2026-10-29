"""自主插话判定逻辑的单测（不需要 AstrBot 运行时）。

跑法：
    cd astrbot_plugin_group_autochat
    python -m unittest test_decision -v
"""

from __future__ import annotations

import time
import unittest

from decision import (
    DEFAULTS,
    GroupState,
    add_message,
    build_context_block,
    decide,
    in_quiet_hours,
    mark_reply,
    match_keyword,
)


def cfg(**overrides) -> dict:
    out = dict(DEFAULTS)
    out.update(overrides)
    return out


def base_call(text: str, state: GroupState | None = None, now: float | None = None, **overrides):
    state = state or GroupState()
    rng = overrides.pop("rng", lambda: 0.0)
    return decide(
        text=text,
        sender_id="10001",
        self_id="1000000002",
        group="123456789",
        config=cfg(**overrides),
        state=state,
        now=now if now is not None else time.time(),
        rng=rng,
    )


class KeywordTests(unittest.TestCase):
    def test_keyword_hits(self):
        result = base_call("豪鱼你在吗")
        self.assertTrue(result["reply"])
        self.assertEqual(result["mode"], "keyword")
        self.assertEqual(result["matched"], "豪鱼")

    def test_keyword_is_case_insensitive(self):
        # 默认关键词全是中文，这里显式塞一个英文词来验证大小写不敏感
        self.assertTrue(base_call("RATING多少", keywords=["rating"])["reply"])

    def test_keyword_cooldown_blocks_second(self):
        state = GroupState()
        now = time.time()
        first = base_call("豪鱼在吗", state=state, now=now)
        self.assertTrue(first["reply"])
        state.last_keyword_at = now
        second = base_call("豪鱼再说一句", state=state, now=now + 5)
        self.assertFalse(second["reply"])
        self.assertIn("冷却", second["reason"])

    def test_keyword_after_cooldown_passes(self):
        state = GroupState()
        now = time.time()
        state.last_keyword_at = now - 120
        self.assertTrue(base_call("豪鱼在吗", state=state, now=now)["reply"])


class FilterTests(unittest.TestCase):
    def test_ignores_self_message(self):
        result = decide(
            text="豪鱼",
            sender_id="1000000002",
            self_id="1000000002",
            group="123456789",
            config=cfg(),
            state=GroupState(),
            now=time.time(),
        )
        self.assertFalse(result["reply"])

    def test_ignores_command_prefix(self):
        self.assertFalse(base_call("/help")["reply"])
        self.assertIn("命令", base_call("/help")["reason"])

    def test_group_allowlist(self):
        result = base_call("豪鱼在吗", groups=["111111"])
        self.assertFalse(result["reply"])
        self.assertIn("不在启用列表", result["reason"])

    def test_disabled(self):
        self.assertFalse(base_call("豪鱼", enabled=False)["reply"])

    def test_quiet_hours_cross_midnight(self):
        # 2026-01-01 03:00 本地时间
        ts = time.mktime((2026, 1, 1, 3, 0, 0, 0, 0, -1))
        self.assertTrue(in_quiet_hours(ts, ["02:00-08:00"]))
        self.assertFalse(in_quiet_hours(ts, ["09:00-18:00"]))
        self.assertTrue(in_quiet_hours(ts, ["23:00-04:00"]))
        self.assertFalse(in_quiet_hours(ts, []))
        result = base_call("豪鱼", quiet_hours=["02:00-08:00"], now=ts)
        self.assertFalse(result["reply"])
        self.assertIn("安静", result["reason"])


class RandomBubbleTests(unittest.TestCase):
    def _hot_state(self, now: float, count: int = 5) -> GroupState:
        state = GroupState()
        for i in range(count):
            add_message(state, f"群友{i}", "随便聊聊", now - i * 10)
        return state

    def test_cold_group_does_not_bubble(self):
        now = time.time()
        state = GroupState()
        add_message(state, "甲", "嗯", now - 5)
        result = base_call("今天吃啥", state=state, now=now, rng=lambda: 0.0)
        self.assertFalse(result["reply"])
        self.assertIn("太冷", result["reason"])

    def test_hot_group_bubbles_on_lucky_roll(self):
        now = time.time()
        state = self._hot_state(now)
        result = base_call("今天吃啥", state=state, now=now, rng=lambda: 0.0)
        self.assertTrue(result["reply"])
        self.assertEqual(result["mode"], "random")

    def test_hot_group_stays_quiet_on_unlucky_roll(self):
        now = time.time()
        state = self._hot_state(now)
        result = base_call("今天吃啥", state=state, now=now, rng=lambda: 0.99)
        self.assertFalse(result["reply"])
        self.assertIn("随机没中", result["reason"])

    def test_random_cooldown(self):
        now = time.time()
        state = self._hot_state(now)
        state.last_reply_at = now - 30
        result = base_call("今天吃啥", state=state, now=now, rng=lambda: 0.0)
        self.assertFalse(result["reply"])
        self.assertIn("冷却", result["reason"])

    def test_random_cap(self):
        now = time.time()
        state = self._hot_state(now)
        state.random_times = [now - 60 * i for i in range(3)]
        result = base_call("今天吃啥", state=state, now=now, rng=lambda: 0.0)
        self.assertFalse(result["reply"])
        self.assertIn("随机冒泡到上限", result["reason"])

    def test_keyword_cap(self):
        now = time.time()
        state = self._hot_state(now)
        state.keyword_times = [now - 60 * i for i in range(20)]
        result = base_call("豪鱼在吗", state=state, now=now, rng=lambda: 0.0)
        self.assertFalse(result["reply"])
        self.assertIn("关键词插话到上限", result["reason"])

    def test_keyword_budget_does_not_eat_random_budget(self):
        # 关键词额度用满，随机冒泡照样能发：两份额度互不相干
        now = time.time()
        state = self._hot_state(now)
        state.keyword_times = [now - 60 * i for i in range(20)]
        result = base_call("今天吃啥", state=state, now=now, rng=lambda: 0.0)
        self.assertTrue(result["reply"])
        self.assertEqual(result["mode"], "random")

    def test_random_budget_does_not_block_keyword(self):
        # 随机额度用满，被人叫到还是要应
        now = time.time()
        state = self._hot_state(now)
        state.random_times = [now - 60 * i for i in range(3)]
        result = base_call("豪鱼在吗", state=state, now=now, rng=lambda: 0.0)
        self.assertTrue(result["reply"])
        self.assertEqual(result["mode"], "keyword")

    def test_mark_reply_bills_the_right_bucket(self):
        state = GroupState()
        now = time.time()
        mark_reply(state, now, "keyword")
        self.assertEqual(len(state.keyword_times), 1)
        self.assertEqual(len(state.random_times), 0)
        mark_reply(state, now + 1, "random")
        self.assertEqual(len(state.random_times), 1)
        self.assertEqual(state.last_reply_at, now + 1)


class ContextTests(unittest.TestCase):
    def test_context_block_lists_recent_messages(self):
        now = time.time()
        state = GroupState()
        add_message(state, "王少", "这歌定数多少", now - 20)
        add_message(state, "龙少", "13.7 吧", now - 10)
        block = build_context_block(state, limit=10)
        self.assertIn("群聊近况", block)
        self.assertIn("王少：这歌定数多少", block)
        self.assertIn("龙少：13.7 吧", block)
        self.assertIn("不要解释", block)

    def test_context_block_respects_limit_and_chars(self):
        now = time.time()
        state = GroupState()
        for i in range(30):
            add_message(state, f"群友{i}", "很长的消息" * 10, now - i)
        block = build_context_block(state, limit=5, max_chars=100)
        self.assertLessEqual(len(block), 400)

    def test_empty_state_gives_empty_block(self):
        self.assertEqual(build_context_block(GroupState()), "")


class MiscTests(unittest.TestCase):
    def test_match_keyword_returns_matched_word(self):
        self.assertEqual(match_keyword("有人在吗 豪鱼", ["豪鱼"]), "豪鱼")
        self.assertIsNone(match_keyword("完全无关", ["豪鱼"]))

    def test_empty_text_does_not_reply(self):
        self.assertFalse(base_call("")["reply"])

    def test_unknown_extra_keys_are_tolerated(self):
        # 配置里多出别的键不应该炸
        result = base_call("豪鱼", **{"some_future_option": True})
        self.assertTrue(result["reply"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
