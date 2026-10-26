# 基础项目与 QQ 接入核查（2026-10-26）

本次通过 GitHub 连接器读取仓库元数据、默认分支提交、完整目录树、下列关键源码与许可证；未执行这些项目的安装脚本。结论是：**复用 MIT 的 qq-codex-companion 的 OneBot 传输模块，另加持久成长核心和经过诊断的专用 Codex 运行环境**。它是最小可审查起点，不是现成满足全部验收条件的成品。

| 项目 | 本次固定默认分支 SHA | 最近默认分支提交（UTC） | 许可证与维护信号 | 结论 |
|---|---|---|---|---|
| [qq-codex-companion](https://github.com/need-to-find-name/qq-codex-companion) | `58c74119bce9932238479c96cebf0b987909245b` | 2026-10-15 10:21 | MIT；仅两次公开提交；无 release；该 SHA 的上游 CI 成功 | 选用轻量传输基础，补足持久化和隔离 |
| [codex-qq-bot](https://github.com/gl813788-byte/codex-qq-bot) | `d1254dad48a0481fb5d74622afb35464d1cfc06c` | 2026-10-24 15:24 | 元数据 license=null，树中未发现 LICENSE/COPYING，package.json 未声明；v1.2.0 发布于 10-04；近期多个合并提交 | 功能最接近，但本次不将其源码纳入产品 |
| [MaiBot](https://github.com/Mai-with-u/MaiBot) | `062d45cf8bd4272ac57d7ec03fcf405e5a458211` | 2026-10-23 17:46 | 根 LICENSE GPLv3；v1.3.15 于 10-23 发布，持续更新 | 社交行为与记忆有价值；替换其模型编排成本较大 |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | `832fc6d98ac25facd54b07424c16067435c118e6` | 2026-10-25 11:51 | 根 LICENSE AGPLv3；v4.29.3 于 10-24 发布，平台适配与测试广 | 多平台运维成熟，完整引入会增加双重编排 |
| [codex_qq](https://github.com/pinkllo/codex_qq) | `bc1821cb257e3aba17ae198ffd7ed15496657cd9` | 2026-05-22 16:54 | license=null，树中未发现许可证；无 release；最近提交停留在 5 月 | 定位是远程 Codex 工作台，不选作角色核心 |

维护信号说明：提交日期来自默认分支 commit，不把 `pushed_at`、星数或存在测试当作运行可靠性的证明。五个仓库当时均未归档。许可证是本次可见状态，不代表已完成完整依赖许可证审计。元数据和目录证据位于 `research/foundations/*/snapshot.json`。

## qq-codex-companion：直接保留什么，必须改变什么

实际已有：[OneBot 反向 WebSocket](https://github.com/need-to-find-name/qq-codex-companion/blob/58c74119bce9932238479c96cebf0b987909245b/src/qq_codex_companion/onebot.py) 的 Token 校验、替换旧连接、echo 请求/响应关联、文本/图片动作；[App Server](https://github.com/need-to-find-name/qq-codex-companion/blob/58c74119bce9932238479c96cebf0b987909245b/src/qq_codex_companion/codex_client.py) 的 initialize、thread/start/resume、turn/start、结构化回复；[状态文件](https://github.com/need-to-find-name/qq-codex-companion/blob/58c74119bce9932238479c96cebf0b987909245b/src/qq_codex_companion/state.py) 保存 thread、下次主动时刻和近期主动次数。

[main.py](https://github.com/need-to-find-name/qq-codex-companion/blob/58c74119bce9932238479c96cebf0b987909245b/src/qq_codex_companion/main.py) 确实实现了时间窗、空闲、概率、日次数、收到新消息取消主动发送、连续消息 debounce。不是只在 README 描述。但是普通主动回合要求至少一条消息，还禁止工具使用；自拍和“日常快照”提示词鼓励虚构生活氛围。这不等于用户要求的探索—证据—反思循环，本项目不沿用这种主动提示词。

[长期记忆](https://github.com/need-to-find-name/qq-codex-companion/blob/58c74119bce9932238479c96cebf0b987909245b/src/qq_codex_companion/memory.py) 有分类、时间、TTL、关键词召回、去重、遗忘和历史；但数据项没有用户/群 scope，也没有可核对来源事件和置信度。全局同一 store 供多白名单账号使用，不能直接扩展为多人隐私记忆。新核心使用 scope 与证据约束的存储，不能只增加提示词。

本次固定源码子集保留于 `vendor/qq-codex-companion`，带原始 MIT LICENSE、来源和 SHA；运行桥复用 `OneBotReverseServer` 网络层。覆盖事件处理器的原因：

- 上游仅接收私聊；消息 ID 去重仅存内存 500 项，重启丢失。
- 上游回调只传 conversation/user/text/images，丢弃持久去重需要的原始 ID 和时间。
- 上游发送动作没有跨进程 outbox；网络超时不能区分未发送和已发送但回执丢失。
- App Server 子进程继承整个 `os.environ`，默认使用用户 CODEX_HOME；只设只读 workspace 不能证明既有技能、MCP、用户数据隔离。
- Codex 回合用全局锁；超时等待本身没有完整中断/送达恢复语义。

上游 pyproject 要求 Python >=3.14；OneBot 模块本身只用兼容 3.12 的 Python 语法和 websockets 15 API。因此本项目按所需模块单独验证，而不是假称完整上游包已支持 3.12。上游 MCP 依赖、图片处理、天气、角色 prompt 不会因 vendor 存在自动启用。

## codex-qq-bot：值得学习，但暂不复制

[App Server turn](https://github.com/gl813788-byte/codex-qq-bot/blob/d1254dad48a0481fb5d74622afb35464d1cfc06c/src/codex-app-server-turn.js) 已有 resume、steer、interrupt、动态工具、结构化结果、权限参数；并非单纯 OpenAI API 聊天壳。[主动兴趣模块](https://github.com/gl813788-byte/codex-qq-bot/blob/d1254dad48a0481fb5d74622afb35464d1cfc06c/src/qq-enhancer/proactive-interest.js) 有模型判定、最小间隔、in-flight 排斥和话题过期；[自我画像](https://github.com/gl813788-byte/codex-qq-bot/blob/d1254dad48a0481fb5d74622afb35464d1cfc06c/src/qq-self-persona.js) 根据样本生成带权重兴趣、交谈风格和记忆摘要，并将结果重新注入后续 prompt。

其最近提交还涉及跨会话检索、头像记忆、风格复盘、日志性能，已经超出终端转发。不能把它笼统称为“没有主动性”。但当前设计允许部分跨群历史共享，必须重审用户需要的私域边界；画像再由摘要生成仍需避免把模型自述当成独立证据。

[环境模块](https://github.com/gl813788-byte/codex-qq-bot/blob/d1254dad48a0481fb5d74622afb35464d1cfc06c/src/codex-child-env.js) 有环境白名单版本，但仍接收指定/现有 CODEX_HOME，并支持读取已有 profile；这不是“已经隔离所有用户技能”的证据。App Server 启动使用 `--stdio`，必须和本机 Codex 版本核对。README 推荐 Windows 用 WSL，安装入口用 bash/zsh，不等于 Windows 原生部署。

未找到明确许可证是本次不复制实现的决定因素；若作者补充可用许可，可以重新评估将成长核心作为其插件/服务接入，减少平台功能维护成本。

## MaiBot：社交调度与证据记忆是实际能力

当前 [turn scheduler](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/maisaka/turn_trigger/scheduler.py) 包括动态回复 gate、私聊快速响应、等待期间新消息、idle backoff；不是每条消息必答。[memory_flow_service](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/services/memory_flow_service.py) 提取用户事实并保留原始消息证据；[person_fact_verifier](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/services/person_fact_verifier.py) 校验消息、用户、会话、引用片段和受支持谓词。这是本项目“反思不能为自身作证”的有益参考。

其主运行路径通过自己的 LLMService/model_client 编排，已检查的 [Responses client](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/llm_models/model_client/openai_responses_client.py) 是模型 API 协议适配，不能当作 Codex runtime。树和关键 provider 路径未见内建 Codex App Server 接入；不排除外部插件。本项目不把“兼容 OpenAI 模型”误称为“使用实际 Codex”。QQ 消息层依赖 maim-message 与独立适配器，换成 Codex还要处理工具和上下文生命周期。GPL 及嵌套目录各自的 LICENSE 需要在衍生发布前继续核对。

## AstrBot：适合平台扩展阶段

实际已有 aiocqhttp/OneBot 与 qqofficial/Webhook 适配器、会话管理、MCP、插件和技能管理；[CronJobManager](https://github.com/AstrBotDevs/AstrBot/blob/832fc6d98ac25facd54b07424c16067435c118e6/astrbot/core/cron/manager.py) 从数据库恢复计划，支持主动 agent job；[skill_manager](https://github.com/AstrBotDevs/AstrBot/blob/832fc6d98ac25facd54b07424c16067435c118e6/astrbot/core/skills/skill_manager.py) 管理本地、workspace 和 sandbox 技能，不是空壳。

其 [tool_loop_agent_runner](https://github.com/AstrBotDevs/AstrBot/blob/832fc6d98ac25facd54b07424c16067435c118e6/astrbot/core/agent/runners/tool_loop_agent_runner.py) 自己运行 provider/tool loop；[Responses provider](https://github.com/AstrBotDevs/AstrBot/blob/832fc6d98ac25facd54b07424c16067435c118e6/astrbot/core/provider/sources/openai_responses_source.py) 仍是 API adapter。本次未验证内建 Codex App Server provider。适合未来将成长服务接到已有多平台部署，当前为一个 QQ 角色全量引入会增加配置、存储和双层 agent loop。AGPL 与其发布模式也需保留相应许可处理。

## pinkllo/codex_qq：定位不同

[README](https://github.com/pinkllo/codex_qq/blob/bc1821cb257e3aba17ae198ffd7ed15496657cd9/README.md) 明确围绕代码查看、修改、审查、调试、Codex 历史、Web 面板和 Cloudflare 链接。[codex-client.js](https://github.com/pinkllo/codex_qq/blob/bc1821cb257e3aba17ae198ffd7ed15496657cd9/src/codex-client.js) 调用 `codex exec`/`exec resume`、继承 process.env、解析 session ID 和最终文件；没有这里要求的体验成长存储。默认工作区示例为 /root、sandbox workspace-write。这是可用方向的远程工作台，但不符合本次角色目标；未找到明确许可证。

## 官方 QQ Bot 与 NapCat/OneBot：身份与能力不能混用

| 项目 | 官方 QQ Bot | NapCat + OneBot 11 |
|---|---|---|
| 身份 | 开放平台创建的机器人应用，AppID/AppSecret/Access Token | 已登录 NTQQ 的独立 QQ 账号，通过客户端能力操作 |
| 会话标识 | user_openid、member_openid、group_openid；不可当 QQ 数字号 | OneBot self_id/user_id/group_id 等账号/群标识 |
| 收消息 | 依已开通事件与权限；当前已存在全量群消息模式，但要开启“接收所有消息” | 客户端账号所在会话上报事件，桥接器必须额外做允许范围过滤 |
| 主动分享 | 当前文档存在主动及召回能力，取决于该应用实际权限、用户开关、窗口与额度 | 可调用发送消息动作；仍受账号状态、好友/群关系和平台风控影响 |
| 运行依赖 | 凭证、事件服务、官方 API | NTQQ/NapCat 登录态、常驻 OneBot、常驻桥接器 |
| 本次选择 | 保留可替换接口，未实现并验证真实官方适配器 | 按用户选择落地；独立小号，回环监听与 Token |

官方身份与接入凭证见 [启动接入](https://bot.q.qq.com/wiki/develop/api-v2/)；全量群消息及 OpenID 字段见 [当前群事件文档](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)。不能声称官方机器人永远只收 @ 消息。NapCat 的实现明确调用 QQ 客户端 Node 模块，再通过 HTTP/WebSocket 暴露 OneBot；它不是腾讯官方 Bot API。[NapCat 架构说明](https://doc.napneko.icu/guide/napcat)

本次读到的官方规则：被动单聊 60 分钟最多 4 次，群聊 5 分钟最多 5 次；msg_id 与 msg_seq 需要记录，重试不能每次随意换序号。当前概述列出主动消息频控，并提供 is_wakeup 互动召回。**文档存在能力不证明用户的 App 已获权**；错误 40034105 明确表示无主动消息权限。后续官方适配器应默认为未验证主动能力，按诊断结果开启，不以过期被动消息自动降级群发。[当前消息概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)、[单聊接口](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)

官方资料本身也有冲突：GitHub 旧版 bot-docs 的发送页仍说 2025-04-21 起停止主动推送；当前网页已换成上述能力；当前单聊接口开头写 60 分钟，字段说明仍残留 5 分钟。不能用旧页或搜索摘要直接定论，应在真正接入时验证当前 App，保守设置可配置的有效期，并保留失败记录。[旧发送页](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/send-receive/send.md)

当前 changelog 还注明 2026-08-10 后 API 域名统一为 api.bot.qq.com；以后实现官方 transport 应按当前文档配置，不能复制旧硬编码域名。[官方变更记录](https://bot.q.qq.com/wiki/develop/api-v2/changelog.html)

NapCat 反向 WebSocket 由 NapCat 主动连接桥接器；桥接器发送 action/params/echo 并等待回执。建议只绑定回环，Token 在环境变量，不让模型看到或控制。OneBot echo 是请求关联，不是服务器“恰好一次发送”保证：发送成功后连接断开会留下结果不明状态；本项目应标记 uncertain 并停止自动重发，避免重复打扰。[NapCat 网络协议](https://doc.napneko.icu/onebot/network)、[NapCat 安全说明](https://doc.napneko.icu/other/security)

## 最小复用边界

保留已工作的 WebSocket/OneBot 协议基础；新的 SQLite 核心负责事件去重、按会话串行、作用域记忆、证据、持久日程、预算与 outbox；Codex App Server adapter 单独验证协议与有效配置；L2 指令实验保存在专用 lab，不能改 operating boundaries。插件负责安装诊断与管理，不替代必须常驻的 QQ/NapCat、桥接器和 runtime。

本研究只证明“读到了这些实现”。没有登录这些项目的 QQ 账号，没有验证它们的生产行为，也没有把仓库 CI 或本地 mocked tests 当成真实 QQ × Codex 端到端成功。

