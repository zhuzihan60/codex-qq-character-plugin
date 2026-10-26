# Windows 10/11 全新部署：从空记忆开始

这份指南用于另一台电脑。只从 GitHub 获取代码，重新建立独立环境和账号授权。不要迁移旧电脑的 `.state`、`.runtime`、`.venv`、`config/local.json`、Codex auth 或 Docker 数据卷。首次运行时 SQLite 自动创建空记忆。

## 先在新的 Codex 聊天里使用

在新电脑的 Codex 中选择一个空项目目录，将下面这段发给新聊天：

```text
请从 https://github.com/zhuzihan60/codex-qq-character-plugin 部署这个 Codex 插件及其 QQ 常驻桥接服务。
新电脑是 Windows 10/11，从空记忆开始，角色名为汐。先读 README.md、docs/windows-fresh-install.md 和插件说明。
先检查系统、Docker Desktop Linux 容器、端口和同名容器/卷，不影响现有 AstrBot/NapCat，不导入旧聊天或现有 Codex 凭据和私人技能目录。
一步一步部署；需登录、扫码、填写 QQ 数字 ID 时再告诉我。凭据仅在本机输入，不打印 Token。
保持每天最多 100 条回复、推理 medium、自主活动关闭、先仅允许我的指定 QQ 私聊。群号另行明确授权。
核验真实有效模型、技能/插件/MCP 和隔离。离线测试、实际 Codex 推理、QQ 送达分别报告。
安装插件不代表桥接服务已经启动；最终需要真实 QQ 收发、偏好纠正和重启记忆验证。
```

## 1. 准备环境与代码

需要 Git、Python 3.12 或兼容版本、Docker Desktop（WSL2 / Linux containers）、可用的 Codex 账号及独立 QQ 测试账号。Node.js 用于可选的 bootstrap 离线测试。Windows 具体版本应满足当前 Docker Desktop 的支持要求，安装前看 [Docker 官方 Windows 说明](https://docs.docker.com/desktop/setup/install/windows-install/)。

以下命令在新电脑的 PowerShell 中执行，每段成功后再继续。首次安装软件后重新打开终端。

```powershell
git clone https://github.com/zhuzihan60/codex-qq-character-plugin.git
Set-Location codex-qq-character-plugin
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[deployment]'
.\.venv\Scripts\python.exe scripts/verify.py
.\.venv\Scripts\python.exe -m qq_character demo
```

这两项验收不调用真实模型、不登录 QQ。不要把模拟回复作为上线证据。

## 2. 创建本机配置，确认资源没有冲突

```powershell
docker version
docker info --format '{{.OSType}}'
docker compose version
docker ps -a --format '{{.Names}}'
docker volume ls --format '{{.Name}}'
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 6098,6198 }
.\.venv\Scripts\python.exe -m qq_character init
```

Docker 应返回 `linux`。若已有 `qq-character-*` 容器/卷，或 `6098/6198` 已占用，停在这里检查所属实例，不能删除、覆盖或直接修改现有 AstrBot/NapCat。固定容器/卷名不是多实例模板；同机部署第二个汐需要同步修改脚本、Compose 和隔离允许列表。

将这台电脑的 Docker 可执行文件路径写入新建配置。下面只设置该字段，不读取或复制旧配置：

```powershell
$configPath = Join-Path (Get-Location).Path 'config/local.json'
$cfg = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$dockerExe = (Get-Command docker.exe -ErrorAction Stop).Source
$cfg.runtime | Add-Member -MemberType NoteProperty -Name docker_command -Value $dockerExe -Force
[IO.File]::WriteAllText($configPath, ($cfg | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
notepad config/local.json
```

填写 `transport.bot_id` 为汐的测试 QQ 号，`allowed_private` 为允许私聊者，例如 `["你的主号"]`，所有 ID 用字符串。暂时保持 `allowed_groups: []`、自主活动关闭。示例中的 `bot_id: "0"` 只是占位，不能用于上线。身份设定可编辑；默认 `gpt-6.1-sol`、`medium`、每日最多 100 条回复，是否可用还要用专属账号诊断。

## 3. 构建独立 Codex，重新授权

```powershell
docker compose -f docker/compose.yaml build
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml ps
.\.venv\Scripts\python.exe docker/seal-runtime.py config/local.json
docker exec -it --user 10001:10001 -e CODEX_HOME=/home/character/.codex qq-character-runtime codex login --device-auth
```

最后一条会显示本次官方验证地址和设备码。用外部浏览器完成账号登录和授权，不把密码、Token 发到聊天。若账号提示禁用设备码登录，按官方页面指引启用后重新执行；不要复用旧设备码。确认成功：

```powershell
docker exec --user 10001:10001 -e CODEX_HOME=/home/character/.codex qq-character-runtime codex login status
docker exec --user 10001:10001 qq-character-runtime codex --version
```

完整协议、有效指令/技能/MCP 发现与隔离门槛见 [Codex 运行时](codex-runtime.md)。`seal-runtime` 只固定本次镜像 ID，不代表登录、推理或安全检查已通过。

## 4. 建立独立 NapCat，扫码

以下脚本不迁移原账号会话；会创建专属卷和新的随机凭据，Windows DPAPI 加密凭据仅当前用户可用。若 PowerShell 阻止脚本，可对单次调用使用 `powershell -NoProfile -ExecutionPolicy Bypass -File <脚本路径>`，不用修改全局执行策略。

```powershell
.\scripts\initialize-napcat.ps1
.\scripts\manage-napcat.ps1 status
.\scripts\manage-napcat.ps1 qr
.\.venv\Scripts\python.exe scripts/render-napcat-qr.py
Start-Process .state/napcat-login.html
```

用独立 QQ 测试账号扫码并在手机确认；不要使用正在被旧 NapCat 使用的同一个账号。再次查询 `status`，应满足 `isLogin=true`、`coreReady=true`，核对 `uin` 与配置的 `bot_id` 一致。二维码过期时运行 `manage-napcat.ps1 qr-refresh` 后重新渲染。避免频繁请求 WebUI 登录；可用 `status -WaitSeconds 60` 等待。

## 5. 诊断、启动并做真实验收

```powershell
.\scripts\manage-napcat.ps1 verify-config
.\.venv\Scripts\python.exe scripts/docker-host-probe.py
.\.venv\Scripts\python.exe scripts/onebot-preflight.py
.\scripts\manage-bridge.ps1 doctor
```

前两个 probe 需要桥还没有启动。`onebot-preflight` 最多等 45 秒，收到真实反向 WebSocket 后结束；通过后才继续。Docker 容器通过 `host.docker.internal:6198` 连接宿主 loopback；若本机网络策略阻断，先诊断，不直接改成对外网开放。

`doctor` 必须返回 `bridge_ready=true`，运行时认证、隔离与发现均通过；聊天只发现 `character`、`character-memory` 两个技能。然后：

```powershell
.\scripts\manage-bridge.ps1 start
.\scripts\manage-bridge.ps1 status
```

用白名单主号私聊测试账号。依次验证收到自然回复、明确纠正聊天偏好、记住一个新命名的共同聊天事件；等待回复完成，只停止/启动桥，再问事件及偏好。真实收发还需检查入站、实际 Codex 完成状态、OneBot 回执和手机收到消息，不能只看进程存活。

不自动启用群聊或主动分享。群接入和停止/恢复见 [Windows 管理](windows-operation.md)。后台桥尚未安装系统自启任务；电脑睡眠、Docker Desktop 退出或用户注销都会影响可用性。登录后可先检查容器状态，再 `manage-bridge.ps1 start`。

## 6. 在 Codex 安装管理插件

本仓库包含 `.agents/plugins/marketplace.json`，注册的是 `qq-character` 插件。在支持本地 marketplace 的 Codex 客户端中打开此 checkout，刷新插件目录后安装；或按 [插件说明](../plugins/qq-character/README.md) 添加本地 marketplace。安装完成后开启新聊天，验证能发现 `manage-character`。

插件只管理本 checkout 的服务；不要装进汐的聊天运行时。它不能让关闭电脑后的 QQ 角色继续在线，也不会代替首次 Codex 设备授权或 QQ 扫码。

## 数据与故障边界

- 新电脑重新生成 Token、重新授权、重新扫码；不会继承旧记忆或偏好。
- 不向 GitHub 上传 `.state`、`.runtime`、`.env`、`config/local.json`、数据库、DPAPI、二维码或 auth 文件。NapCat 原始启动日志可能含 Token，只分享脚本脱敏摘要。
- `doctor` 失败先修复失败项，不绕过沙箱、镜像或技能允许列表；模型不可用时由持有者确认可用模型并重新诊断。
- 普通停机使用 `manage-bridge.ps1 stop` 和 Compose `stop`，不要使用会删除数据卷的 `down -v`。
- 该指南是发布源码的可重复操作说明；发布时没有在你的新电脑上执行。自主成长等未完成的真实验证见 [验收矩阵](acceptance.md)。
