# Windows 独立实例管理

在本项目目录运行以下命令。脚本使用固定的 `qq-character-*` 实例名称；首次部署前确认它们和 `6098/6198` 端口没有冲突。已有 AstrBot/NapCat 的实例不属于本项目。换电脑先读 [全新部署指南](windows-fresh-install.md)。

## 当前结构

- Codex：`qq-character-runtime`，独立登录卷，非 root、只读根目录；模型不能读取宿主文件。
- QQ 客户端：`qq-character-napcat`，独立三个数据卷；WebUI 仅发布 `127.0.0.1:6098`。QQ 进程以 UID 10002 运行，初始化需要 root，权限边界与 Codex 容器不同。
- 桥：宿主当前 Windows 用户的 Python 进程，监听 `127.0.0.1:6198/onebot`，连接 Docker 的 Codex App Server。
- NapCat 到 `host.docker.internal:6198` 的 loopback 连通性已实测通过。此结果不代表 QQ 已登录或 WebSocket 已鉴权。
- 私聊和群聊允许范围由 `config/local.json` 管理，公开模板默认均为空。群聊仅真实 @汐 的文本消息触发，自主活动默认关闭。加入群聊前需持有者明确配置允许群号。

## 首次初始化

`docker/napcat.compose.yaml` 固定 NapCat 镜像版本。首次下载后运行：

```powershell
.\scripts\initialize-napcat.ps1
```

该脚本拒绝覆盖同名容器或已有专属卷。它生成两个独立随机 Token，将当前用户 DPAPI 加密副本放在 `.state/secrets/`，通过短暂种子文件写入新容器卷，最后删除种子文件。Token 不出现在命令参数或容器环境变量中。初始化失败时先检查容器和卷，不能直接删除已有卷来重试。

登录二维码需要可选依赖：`.venv\Scripts\python.exe -m pip install -e '.[deployment]'`。二维码只在本地编码，不调用第三方二维码服务。

```powershell
.\scripts\manage-napcat.ps1 status
.\scripts\manage-napcat.ps1 qr
.\.venv\Scripts\python.exe scripts/render-napcat-qr.py
Start-Process .state/napcat-login.html
```

请用汐专用测试 QQ 扫码，不能使用旧 AstrBot 的账号或主号。过期后使用 `manage-napcat.ps1 qr-refresh`，再运行渲染脚本。WebUI 登录每分钟限次；等待扫码时使用一次 `status -WaitSeconds 60`，不要频繁重新认证。

登录成功必须同时满足 `isLogin=true`、`coreReady=true`，并核对返回的 `uin` 是测试账号。将其配置到 `transport.bot_id` 后继续检查，不以“已扫码”代替登录成功。

## 接入检查与运行

```powershell
.\scripts\manage-napcat.ps1 verify-config
.\.venv\Scripts\python.exe scripts/onebot-preflight.py
.\scripts\manage-bridge.ps1 doctor
.\scripts\manage-bridge.ps1 start
.\scripts\manage-bridge.ps1 status
```

`verify-config` 只显示 Token 是否匹配、目标 URL 是否正确等摘要。`onebot-preflight.py` 最多等待 45 秒，核验真实连接的路径、Bearer Token、Bot ID、Universal 标头，不发送 QQ 消息。它独占桥的数据库锁；期间收到的允许范围内事件进入正式持久队列，随后交给桥处理。

`doctor` 在独立 Codex 内核验隔离、有效配置、技能发现和登录，并检查桥配置；它不执行模型推理。`start` 隐藏运行桥，`status` 核验进程归属；进程存活仍不等于 QQ 收发成功。

真实端到端验收必须由允许的主号向测试账号发送消息，并确认数据库收件事件、实际模型结果和 OneBot 发送确认。不得把离线模拟或端口监听当成真实验收。

## 群聊接入

先由操作员在 `transport.allowed_groups` 填入用户明确指定的群号，保留旧配置备份，等待正在处理的事件完成后只重启桥。仅加入 QQ 群不会自动扩展该白名单。汐的独立测试账号需要由群成员邀请入群。

- 允许群内任意成员真正 @汐 并附带文本；普通聊天、仅文字写出“@汐”、@全体成员不触发，也不入库。
- 私聊、不同群的上下文与记忆按 scope 分开；同群成员的个人偏好按发送者区分，群内公开事件和知识属于该群。
- 群聊与私聊共用每天 100 条总回复额度，不因增加一个群而新增额度。自主活动和主动分享仍关闭。
- 群内 `@汐 /stop` 取消当前群的待办和未发回复，不影响私聊或其他群；不永久关闭这个群。
- 首次测试必须核对实际群事件、`send_group_msg` 对应回执及用户收到消息，不能仅凭白名单或已建立连接宣布群聊已通过。

## 停止与恢复

```powershell
.\scripts\manage-bridge.ps1 stop
```

停止只针对核验过的本项目 PID、创建时间、解释器与命令行。Windows 下停止是进程终止；下一次启动会恢复队列，已提交但未确认的发送标记为不确定，不盲目重发。

需要停止 QQ 客户端时，使用配置中 `runtime.docker_command` 指向的 Docker 运行：

```powershell
docker compose -f docker/napcat.compose.yaml stop
```

不使用 `down -v`，保留独立登录状态、角色数据库和回滚证据。Docker CLI 尚未加入当前终端 PATH 时，使用其绝对路径。Docker 容器设有 `unless-stopped`，但仍依赖 Docker Desktop 运行；当前桥管理脚本尚未注册登录自启任务。

## 诊断和记录

- `.state/deployment-2026-10-26.json`：部署检查点。
- `.state/live-smoke.json`：真实 Codex 推理记录，无 QQ 发送。
- `.state/test-report.json`：离线测试，模型和 QQ 客户端均为模拟。
- `.state/onebot-preflight.json`：仅在真实反向连接验证后生成。
- `.state/bridge-process.json`、`.state/logs/`：桥进程与本机日志，不注入角色提示或发送到 QQ。

NapCat 自身启动日志含 WebUI Token，不要直接把 `docker logs`、完整配置、DPAPI 文件或二维码 URL 发到聊天。对外共享诊断只使用脚本输出的摘要。`.state`、`.runtime`、`config/local.json` 均不进入源码分发。
