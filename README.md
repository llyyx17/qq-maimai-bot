# QQ 舞萌查分机器人（自建）

一个跑在自己电脑上的 QQ 群机器人：接入舞萌 DX 查分数据，日常在群里接话、查分、看排名。

不是框架、不是 SaaS，是**一个人维护的小项目**——把「怎么搭起来、踩过哪些坑」写清楚，方便以后重装或者别人抄作业。

## 它做什么

| 能力 | 说明 |
| --- | --- |
| B50 / 单曲成绩查询 | 通过落雪查分器（Lxns）或水鱼查分器（Diving-Fish）查询 |
| 群内排名 | 群成员自愿加入后生成排名图 |
| 群聊自主插话 | 不被 @ 也能接话：叫它外号会应，群里热闹时偶尔主动冒泡（自研插件） |
| 别名 / 曲目信息 | 曲名别名、定数、封面等 |

## 架构

```
QQ 客户端（机器人账号已登录）
   │  SnowLuma 注入原生组件，提供 OneBot v11
   ▼
SnowLuma ── OneBot HTTP(3000) / WS 服务端(3001)
   │
   │  （SnowLuma 主动反连出去）
   ▼  ws://127.0.0.1:6199/ws
AstrBot ── aiocqhttp 适配器（WebUI 6185）
   │
   ├─ astrbot_plugin_maimaidx   查分 / 排名 / 曲目信息
   └─ astrbot_plugin_group_autochat  群聊自主插话（本仓库自带）
   │
   ▼
LLM（OpenAI 兼容接口，自备 key）
```

关键点：SnowLuma 是 WS **服务端**，AstrBot 的 aiocqhttp 也是服务端，所以链路方向是
**SnowLuma 反向连到 AstrBot**（`networks.wsClients` → `6199/ws`）。这一点很容易搞反。

## 组件与版本

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| AstrBot | 4.29.x | 对话框架，插件宿主 |
| SnowLuma | 2.2.0 | QQ 侧 OneBot 网关注入 |
| astrbot_plugin_maimaidx | 1.5.7 | 查分插件（第三方） |
| astrbot_plugin_group_autochat | 1.0.0 | 群聊自主插话（本仓库） |
| Node.js | 24.x | SnowLuma 运行时 |

## 目录

```
docs/            部署步骤、踩坑记录
plugins/         自研插件源码（可直接放进 AstrBot 的 data/plugins/）
config/          配置模板（全部脱敏，占位符形式）
```

## 快速开始

见 [`docs/setup.md`](docs/setup.md)。大致顺序：

1. 登录 QQ 机器人账号
2. 启动 SnowLuma，确认 OneBot 的 3001 端口起来
3. 启动 AstrBot，配置 aiocqhttp 平台（6199）与 LLM
4. 装插件：`astrbot_plugin_maimaidx`（查分）+ 本仓库的 `group_autochat`
5. 查分还需要自备**查分器开发者 Token**（见 `docs/setup.md` 的「数据源」一节）

## 群聊自主插话（本仓库的主要代码）

AstrBot 默认只在「被 @ / 被引用 / `wake_prefix` / 私聊」时才调 LLM，群里不叫它就不说话。
这个插件利用了唤醒判定的第五个入口——**插件 handler 的 filter 命中**：

```python
# 判定通过时，打开 LLM 调用开关
event.is_at_or_wake_command = True
```

另外它自己维护每个群最近 40 条消息，插话时通过 `on_llm_request` 注入「群聊近况」——
因为没被 @ 的消息不会进 AstrBot 的会话历史，不补的话模型插话时是瞎的。

详见 [`plugins/astrbot_plugin_group_autochat/README.md`](plugins/astrbot_plugin_group_autochat/README.md)。

## 安全与隐私

- 仓库里**没有任何**真实凭据、QQ 号、群号、服务器路径，配置全部是占位符。
- 机器人只在白名单群/私聊里工作；查分数据仅在本群展示，不落库、不转发。
- 使用 SnowLuma 属于第三方注入方案，与腾讯无关，账号存在被风控的风险——是否使用请自行判断。

## License

MIT，见 [LICENSE](LICENSE)。
