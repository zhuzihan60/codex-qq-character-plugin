# QQ Character · Codex 管理插件

此目录是可本地安装的 Codex skills 插件包，使用官方 portable `plugin.json`，另带 `.codex-plugin/plugin.json` 兼容入口。提供部署诊断、指定范围记忆管理、开发候选测试与回滚流程。它不提供账号登录，不会自己常驻，不是 QQ 消息接收服务器。

实际 bridge 源码在项目根目录 `src/qq_character`；需要保留整个源码 checkout 并完成 editable 安装。插件调用已存在的本地 CLI，不能只复制插件目录就期待 QQ 在线。

## 本地注册

本仓库已附 `.agents/plugins/marketplace.json`，marketplace 名为 `codex-qq-character-plugin`，插件名为 `qq-character`。保持完整 checkout，`source.path` 指向 `./plugins/qq-character`。不需要覆盖个人 marketplace。

在支持该能力的 Codex 客户端中打开此项目，在插件界面刷新并安装本地来源；或在本 checkout 的终端执行 `codex plugin marketplace add .` 后打开插件界面。开启新聊天验证 `manage-character` 被发现。命令需要宿主安装 Codex CLI；独立角色容器的 CLI 不用于管理宿主插件。参见 [官方插件打包说明](https://developers.openai.com/plugins/build/plugins)。本次发布仅验证包结构，未自动安装到现有环境；若客户端不显示本地来源，应检查版本支持，不将源码存在当成安装成功。

换电脑先看 [Windows 全新部署](../../docs/windows-fresh-install.md)。安装后的技能可能位于插件缓存中，执行管理命令时工作目录必须是你克隆的完整仓库根目录，不能在插件缓存目录启动桥接器。

它应留在持有者的管理环境；聊天角色容器不会加载本管理插件。普通 QQ 内容即使提到“部署”“开发”，也没有能力调用本地管理 CLI。

## 运行检查

在项目根目录：

```powershell
.\.venv\Scripts\python.exe -m qq_character --help
.\.venv\Scripts\python.exe -m qq_character demo
.\.venv\Scripts\python.exe -m qq_character doctor
```

`doctor` 是无推理诊断；未完成独立环境或 QQ 配置时应阻断。更多部署与常驻说明见项目根 README。不要让插件把测试日志或私聊导出发到 QQ。
