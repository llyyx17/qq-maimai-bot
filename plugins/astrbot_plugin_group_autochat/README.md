# astrbot_plugin_group_autochat —— 群聊自主插话

让 AstrBot 的机器人在群里**不用 @ 也能接话**：命中关键词就搭一句，或者群聊正热闹时按概率冒个泡。

## 为什么要写插件

AstrBot 的唤醒判定（`pipeline/waking_check/stage.py`）只有五个入口：

1. 被 @ / @全体
2. 被引用
3. 以 `wake_prefix` 开头
4. 私聊
5. **某个已经装了过滤器（filter）的插件 handler 命中**

前四个都是“被人叫”，第五个才是“自己判断”。所以这个插件注册了一个不带 @ 条件的群消息 handler：
它先按规则判断该不该说话，该说的时候把 `event.is_at_or_wake_command` 置为 `True`——
这正是 `pipeline/process_stage/stage.py` 里决定「要不要调 LLM」的那个开关，剩下的交给 AstrBot 正常流程。

## 两条触发路径

| 路径 | 触发条件 | 默认参数 |
| --- | --- | --- |
| 关键词 | 消息里出现配置的关键词（不区分大小写） | 概率 0.9，冷却 60 秒 |
| 随机冒泡 | 群里有热度（窗口内消息数够）+ 概率命中 | 概率 0.08，冷却 240 秒，热度 ≥3 条/5 分钟 |

**两条路径各自一份额度，互不占用**：关键词 20 次/小时（真正起限制的是 60 秒冷却——被叫到就该应），
随机冒泡 3 次/小时。之前是共用一份，结果是关键词把额度吃光之后它就一整个小时不冒泡了。

**关键词默认里故意没有「舞萌」「maimai」**——这俩在这个群天天出现，当关键词等于每条都触发。
「查分」「段位」「机厅」「拼车」「rating」后来也陆续删了：它们是群里的日常话题词，不是“在叫它”，
留着只会让插话变密。默认只剩「有人在叫它」：豪鱼/小鲸鱼/鲸鱼/臭鱼/鱼哥。

## 它怎么知道群里在聊什么

没被 @ 的消息**不会进 AstrBot 的会话历史**（waking_check 会直接把事件 stop 掉），
所以只靠 LLM 自己的上下文，它插话时是瞎的。

插件因此在内存里维护一份「每群最近 40 条」的滚动缓冲，真插话时用 `on_llm_request` 钩子把最近
10 条拼进 `system_prompt`：

```
【群聊近况】（下面这些消息是群里刚说的，你并没有被 @，是系统允许你自主插一句）
王少：这歌定数多少
龙少：13.7 吧
（直接自然地接话或吐槽即可；不要解释你为什么说话，也不要说“我看到大家在聊…”这种话。）
```

最后那句是防呆：不加的话模型很容易复述「看到大家在聊机厅，我也来一句」。

## 配置（WebUI 插件配置页 / `data/config/astrbot_plugin_group_autochat_config.json`）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | true | 总开关 |
| `groups` | [] | 空 = 白名单里的所有群；填了只在这些群生效 |
| `keywords` | 豪鱼/小鲸鱼/鲸鱼/臭鱼/鱼哥 | 命中即进入关键词路径 |
| `keyword_probability` | 0.9 | 命中后说的概率 |
| `keyword_max_per_hour` | 20 | 关键词额度（独立） |
| `random_probability` | 0.08 | 随机冒泡概率 |
| `random_max_per_hour` | 3 | 随机冒泡额度（独立） |
| `keyword_cooldown_seconds` | 60 | 关键词冷却 |
| `random_cooldown_seconds` | 240 | 随机冒泡冷却 |
| `activity_window_seconds` / `activity_min_messages` | 300 / 3 | 热度门槛 |
| `quiet_hours` | [] | 例如 `["02:00-08:00"]`，按**本地时间**，支持跨零点 |
| `ignore_prefixes` | `/ # ／ ! ！` | 这些开头的不算计（多半是命令） |
| `context_messages` | 10 | 注入多少条群聊 |
| `debug` | false | 打开后每条群消息都写一行「不插话（原因）」到 INFO 日志 |

调参建议：先开 `debug` 跑一晚上，看日志里 `[autochat] ... 不插话：...` 的原因分布，再动概率。
常见原因是「群里太冷（1/3 条）」和「刚说过话，冷却中」。

## 文件

```
main.py            接线：group handler + on_llm_request
decision.py        纯判定逻辑（不依赖 AstrBot，可单测）
test_decision.py   20 项单测
_conf_schema.json  WebUI 里的配置表单
metadata.yaml      插件元信息
```

## 测试

```powershell
cd astrbot_plugin_group_autochat
python -m unittest test_decision -v
```

覆盖：关键词命中/冷却/大小写、群白名单、自己发的消息、命令前缀、安静时段（含跨零点）、
冷场不冒泡、热场冒泡、每小时上限、上下文注入格式与长度。

> 跨零点那条单测抓到过一个真 bug：我最初用「epoch 分钟数 % 1440」算小时，
> 那是 UTC 的分钟数，机器在 +08:00 时会整体偏 8 小时，安静时段等于永远不生效。

## 已知限制与副作用

- **每条群消息都会走一遍这个 handler**（因为 filter 必须通过才算激活）。
  代价是几次字典操作；但副作用是消息不再在 waking_check 就被 stop，
  会继续经过限流/内容安全阶段。这个群的消息量远达不到 `rate_limit`（30 条/60 秒）的门槛。
- 白名单仍然有效：非白名单群的消息虽然也会进 handler，但 `WhitelistCheckStage` 会拦掉，不会回复。
- 状态存在进程内存里，重启后冷却和热度清零（重启后可能会比平时更容易冒一句）。
- 只处理群消息；私聊本来就是有问必答，不需要这个插件。
