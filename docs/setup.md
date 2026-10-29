# 部署步骤

以下路径、号码、Token 全部是占位符，按自己环境替换。

## 0. 前置

- 一台常年开机的 Windows 机器（本项目在 Win10/11 上验证）
- 一个专门登录机器人的 QQ 账号（不要用主号）
- Node.js 18+（SnowLuma 需要）
- Python 3.10+（AstrBot 需要）
- 一个 OpenAI 兼容的 LLM 接口 + key

## 1. 登录 QQ

先让机器人账号在**桌面端 QQ** 上登录成功，并保持在线。
SnowLuma 是往「正在运行的 QQ 进程」里注入，QQ 没开就不会有 OneBot。

## 2. 启动 SnowLuma

```powershell
cd C:\path\to\SnowLuma
node index.mjs
```

- WebUI 默认 <http://127.0.0.1:5099>
- 首次启动要在 WebUI 里点同意 EULA（这一步只能人工做）
- 扫码登录机器人 QQ

确认 OneBot 起来后，应能访问：

- HTTP API：`http://127.0.0.1:3000`
- WS 服务端：`ws://127.0.0.1:3001`

## 3. 启动 AstrBot

```powershell
cd C:\path\to\AstrBot\core
python main.py
```

- WebUI 默认 <http://127.0.0.1:6185>
- 首次启动会生成 `data/cmd_config.json`

## 4. 配置平台（aiocqhttp，反连）

AstrBot 的 `aiocqhttp` 是 **WS 服务端**，监听 6199；让 SnowLuma 作为客户端连过来：

`data/cmd_config.json` 里 `platform` 段（示意）：

```json
{
  "id": "snowluma",
  "type": "aiocqhttp",
  "enable": true,
  "ws_reverse_host": "127.0.0.1",
  "ws_reverse_port": 6199,
  "ws_reverse_token": ""
}
```

再在 SnowLuma 的 OneBot 配置里加一个 wsClient：

```json
{
  "name": "astrbot",
  "url": "ws://127.0.0.1:6199/ws",
  "role": "Universal",
  "reconnectInterval": 5000
}
```

> 这一步最容易搞反：不是 AstrBot 去连 SnowLuma，是 **SnowLuma 连 AstrBot**。

两边都起来后，AstrBot 日志应出现：

```
aiocqhttp(OneBot v11) 适配器已连接。
```

## 5. 装插件

把插件目录放进 `<AstrBot>/core/data/plugins/`，重启 AstrBot。

```
data/plugins/
├─ astrbot_plugin_maimaidx/        # 查分（第三方，需自行获取）
└─ astrbot_plugin_group_autochat/  # 本仓库自带
```

## 6. 数据源（查分）

查分需要向查分器申请凭据，**二者选一即可**，也可以都配。

### 落雪（Lxns，推荐）

1. 注册 <https://maimai.lxns.net>，**完成邮箱验证**
2. 开发者面板 <https://maimai.lxns.net/developer> 申请成为开发者
3. 拿到开发者 API 密钥（形如 `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx=`）
4. 填到插件配置 `lxns.lxns_dev_token`
5. 每个群友：在落雪绑定自己的 QQ，并在**隐私设置里打开「允许读取谱面成绩」**

> 第 5 步是硬前提。只开「允许读取玩家信息」能查到昵称和 rating，但查不到 B50，
> 接口会返回 `403 allow third party fetch scores is disabled`。

想拿到精确达成率、头像、名牌，再建一个 OAuth 应用（权限选 `read_player`，
无回调地址填 `urn:ietf:wg:oauth:2.0:oob`），填 `lx_client_id` / `lx_client_secret`，
然后由群友发「绑定落雪」。

### 水鱼（Diving-Fish，备选）

需要 `divingfish.maimaidxtoken`（开发者 Token）或 OAuth 应用（`df_client_id` / `df_client_secret`）。
OAuth 走设备码流程：用户发「绑定水鱼」→ 打开链接授权 → 把确认码发回给机器人。

## 7. 白名单

`platform_settings.enable_id_white_list = true` 时，白名单里要填**会话 ID**，不是裸 QQ 号：

```
snowluma:FriendMessage:1000000001
snowluma:GroupMessage:123456789
123456789
```

填错的表现：日志刷 `Session ID ... is not in the session allowlist`，消息全被丢弃。

## 8. 验证

私聊机器人发一句「在吗」，应能收到回复；群里发「b50」应能出成绩图。

没反应就按 [`troubleshooting.md`](troubleshooting.md) 的顺序查。
