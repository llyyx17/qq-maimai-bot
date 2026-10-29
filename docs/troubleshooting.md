# 踩坑记录

都是实际撞过的，按「症状 → 原因 → 处理」写。

## 1. 机器人能生成回复，但 QQ 里一条都收不到

**症状**：AstrBot 日志有 `Prepare to send - ...`，但群里没消息；日志里有 `IndexError: list index out of range`。

**原因**：`platform_settings.segmented_reply.interval` 只填了一个数（例如 `"1.2"`）。
`RespondStage` 在发送前算随机间隔：

```python
return random.uniform(self.interval[0], self.interval[1])
```

配置被解析成 `[1.2]`，取 `[1]` 就越界，整条回复在发送前被丢掉。

**处理**：改成「最小,最大」两个值：

```json
"segmented_reply": { "interval": "1.2, 2.6" }
```

改完必须重启 AstrBot。日志出现 `Segmented-reply interval: [1.2, 2.6]` 才算生效。

> 这个坑的迷惑性在于：模型侧完全正常（traceback 里能看到生成的原文），
> 只有发送那一步静默失败，看起来像「机器人不理人」。

## 2. 查分插件启动就报 `插件初始化失败: No such file or directory`

**症状**：`... static\mai\pic\b50_score_basic.png` 找不到，插件整个不初始化。

**原因**：查分插件的**静态资源是单独分发的资源包**，不随代码一起发。只 clone 代码的话
`static/` 是空的。

**处理**：下载资源包，解压后把 `static/` 覆盖到：

```
<AstrBot>/core/data/plugin_data/astrbot_plugin_maimaidx/static/
```

没有装 7-Zip 也能解压——Windows 自带的 `tar.exe`（libarchive）支持 7z：

```powershell
tar -xf "Resource CN1.55.7z" -C .\extract
```

## 3. 白名单填了 QQ 号却还是不理人

**原因**：AstrBot 的白名单要的是**会话 ID**，不是 QQ 号。

**处理**：

```
snowluma:FriendMessage:1000000001
snowluma:GroupMessage:123456789
```

日志里出现 `Session ID ... is not in the session allowlist` 就是这个。

## 4. PowerShell 脚本莫名语法错误

**症状**：`Unexpected token '...' in expression or statement`，报错行看着完全正常。

**原因**：Windows PowerShell 5.1 读取**无 BOM 的 .ps1 时按 ANSI（GBK）解码**，
文件里的中文注释会变成乱码，把引号和括号咬坏。

**处理**：带中文的 `.ps1` 必须存成 **UTF-8 with BOM**；或者干脆只写 ASCII。

## 5. Node 写某个盘符报 `EPERM`

**症状**：`fs.writeFileSync` / `copyFileSync` 一律 `EPERM: operation not permitted`，
但同一个目录 PowerShell 写得好好的。

**处理**：这类目录用 PowerShell 做文件操作；需要改配置时用「读出来 → 改 → 用 PowerShell 写回」，
不要硬刚 Node 的权限。

## 6. SnowLuma 起两个实例后 OneBot 时断时续

**原因**：两个进程抢 5099，第二个会漂到 5100，QQ 侧的 hook 被反复卸载/重连。

**处理**：启动脚本要做幂等判断，先检测端口再启动。不要手动开第二次。

## 7. QQ 重启/登出后机器人就不动了

**症状**：SnowLuma 日志出现 `SnowLuma unloaded from PID=...`。

**原因**：注入的 hook 随 QQ 进程消失。

**处理**：重跑一次启动脚本（它会重启 SnowLuma 重新注入）。所以启动脚本不要只启动一次就完事，
要能反复执行。

## 8. 群里 @ 才有反应，不 @ 就当没看见

**原因**：AstrBot 的唤醒判定只有四个入口：被 @、被引用、`wake_prefix` 开头、私聊。
其余消息在 `WakingCheckStage` 就被 stop 掉了。

**处理**：装本仓库的 `astrbot_plugin_group_autochat`，它利用第五个入口（插件 handler 命中）
把 `event.is_at_or_wake_command` 置真，并按关键词/热度决定要不要说话。

注意：没被 @ 的消息不进会话历史，插件自己维护「群聊近况」并通过 `on_llm_request` 注入。
