# dsh-bili-whale 当前实现审计与 QQ/Codex 移植建议

核查日期：2026-10-26（Asia/Shanghai）。本报告通过 GitHub 官方连接器读取仓库元数据、main 分支、完整文件树和下列源文件；未运行第三方项目或登录 B 站，因而“已实现”表示源码存在对应路径，不表示已替作者验证线上效果。

## 快照、许可与维护

| 项目 | 核查结果 |
|---|---|
| 仓库 | [Alterssss/dsh-bili-whale](https://github.com/Alterssss/dsh-bili-whale) |
| 固定源码快照 | [`8b0009f8ce2ebdc421afcb2e727b19ac18912ec6`](https://github.com/Alterssss/dsh-bili-whale/commit/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6)（核查时 main） |
| 包版本 | 0.4.6（[package.json](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/package.json)） |
| 许可 | MIT，Copyright (c) 2026 ALTERSSSS；复用源码需保留版权及许可文本（[LICENSE](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/LICENSE)） |
| 仓库创建 | 2026-10-18 01:17:20 +08:00 |
| main 最新提交 | 2026-10-25 18:14:41 +08:00，文档整合提交 |
| 最近推送 | GitHub 元数据为 2026-10-25 18:17:11 +08:00 |
| 维护判断 | 未归档；近期有扫码登录与社区缺陷修复。项目非常年轻，活跃不等于稳定性已有长期证据 |
| 宿主兼容 | DSH/Cordis 插件，Node 18+；不是 Codex 插件，也不是 Codex SDK/App Server 适配器 |

元数据出处：[仓库 API](https://api.github.com/repos/Alterssss/dsh-bili-whale)、[main 分支 API](https://api.github.com/repos/Alterssss/dsh-bili-whale/branches/main)。本地只读研究摘存放在 `research/whale/source/`，保留原 MIT LICENSE；这些文件没有加入本项目运行链。

## 已实现的体验循环

其有价值的部分不是一种人格，而是一个有限、可恢复、带反馈回流的活动循环：

1. 宿主常驻定时器检查作息，并从持久化计划恢复。
2. 时间到达后，把一次具体活动交给固定的 DSH 会话。
3. 会话读取人格及日记，取候选，先低成本筛选，再深入少量内容。
4. 在评分、配额与渠道限制下分享，或者跳过分享。
5. 写叙事日记、结构化事件和发言账；收集真实评论、点赞、私信反馈。
6. 下次候选排序读取反馈产生的主题词，改变推荐结果。

其中 1、2、5 的部分记录与 6 有程序路径；理解内容、写日记、判断值得分享、概括“偏好校准”主要由模型受提示词驱动。

### 调度与重启

[tools/lib/night-plan.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/lib/night-plan.mjs) 使用本地日历的绝对时间戳；窗口有日期键，跨午夜不重复获得同一窗口的机会。随机时间及 wake/skip 结果进入状态，重启不重抽。完成才消耗机会；skip 也消耗一次；过期窗口不会被补发。支持多个窗口，次数总和硬上限 12，默认 20:00–24:00、一次、30% 跳过、至少 10 分钟间隔。测试文件确实覆盖跨午夜、重启恢复、计划稳定、配置更新不消耗次数和过期计划（[planner tests](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tests/planner.test.mjs#L9-L79)），本轮没有运行它们。

[lib/scheduler.js](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/lib/scheduler.js#L239-L285) 每 60 秒 tick，状态写入临时文件再 rename；同进程单次投递未结束时保持占用，不把超时等同于失败重发。投递后有 8 分钟冷却，另靠 DSH 会话文件 mtime 推断是否仍在工作。这是一种宿主相关的忙闲近似，不应移植到 Codex；Codex 侧应根据真实 turn 状态/完成事件管理占用。

[lib/session-delivery.js](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/lib/session-delivery.js) 用单队列统一选择会话、注入人格、轮换和确认。人格按 hash 可减少重复注入；重启首次重新注入并提示读两本日记。私信使用 receipt 构造稳定 requestId，并保留待确认投递。普通定时唤醒的 requestId 则由当前时间生成，且在 prompt 返回后才完成计划。因此不能据此宣称“任意崩溃点严格 exactly-once”：例如宿主接受消息但状态写回前退出，恢复后仍可能再投一次。QQ 实现要补充事件唯一键、事务任务状态和发送 outbox；结果不明的发送需核查，不能盲重试。

### 唤醒提示与节制

[wakePrompt](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/lib/scheduler.js#L113-L156) 是明确的活动清单：检查依赖、读双日记、筛最多 5 条、深读 2–3 条、评分排序、只考虑第一名分享、写日记、扫反馈、记录结束。它确实把“做过什么”与下一次行动接在一起，并限制候选数量；没有通用的模型 token/费用硬预算。

[share-policy.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/lib/share-policy.mjs) 以代码校验分享开关、理由最短长度、0–20 分、默认最低 13 分、rank 必须等于 1。流量保护层有跨进程锁、读写计数、间隔、相同操作指纹、未知结果拦截及风控暂停（[traffic.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/lib/traffic.mjs#L1-L120)）。这些约束比“请节制”的提示强，但如果模型拥有同账户完整文件和 shell 权限，不能把它们视作不可改写的安全边界。

“选择安静”需要区分两个机制：概率 skip 只是随机不浏览；模型可以评分不足而不分享。但 skip 默认仍要求写两本日记并可能推送，长期未回应也仍提示发送心情。因此它并未完整满足本项目所要求的“没有值得说的事，就完全不打扰”。

### 日记、记忆与真实证据

[diary.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/commands/diary.mjs#L1-L54) 区分：

- `whale-diary.md`：给人读的叙事，含看过的表格与想法池。
- `whale-diary-private.md`：通过唤醒提示让模型写的私人叙事、偏好校准。
- `whale-log.jsonl`：程序事件流，字段是 at/kind/target/note。
- `quota.json`：对外发言记录与配额依据。
- `feedback.json`：反应记录与时间。

这是较好的“叙事和账本分工”，但不等于用户要求的六类记忆：没有统一 provenance、confidence、scope、纠正/取代关系、遗忘和私聊/群隔离检索层。会话启动让模型读整个双日记，没有相关性检索预算；“私人日记”是不同文件和叙事用途，不是经验证的访问控制。

尤其不应照搬 [silenceLines](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/lib/scheduler.js#L69-L94) 的“以日记为准补记真实反馈”。模型自己写的叙事不能反过来证明用户实际说过什么。QQ 版本应要求指向原始 QQ event_id、发送回执或工具结果；只有这些记录才能支持耐久记忆更新。缺原始证据时只能标为待核实，不能补造成真实互动。

### 偏好校准：有实际回流，但范围有限

[feed.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/commands/feed.mjs#L35-L45) 首先读配置 `preferences.like`；只在未配置时从旧日记“爱看”字段兜底。因此“私人日记记录偏好校准”本身不必然改变下一次程序排序。

实际可观察的学习路径在 [feed.mjs 171–269 行](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/commands/feed.mjs#L171-L269)：从被赞视频标题/评论、角色收藏的标题/理由抽拉丁词与中文 3–4 字片段；候选命中每项 +2.5，最多 +7.5。总分为：

`log10(1 + 弹幕数 / max(30, 时长秒)) × 3 + 偏好标签命中数 × 4 + 反馈主题词加分`

排序排除已看、被赞、已收藏对象，增加多样性与最低质量筛选。这是具体、可测的检索/排序适应，不是模型训练，也不是完整价值观或关系的演化。

其学习信号仍有局限：点赞某条评论未必等于喜欢整个主题；角色自己的收藏和用户反馈被混入同一个主题词集合；未见每个推断的置信度、作用域、矛盾纠正及衰减证据。QQ 版本应分开“用户喜欢”和“角色兴趣”，以可追溯的小步权重更新避免一次反应改变整个身份。

### 反馈处理

[quota.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/commands/quota.mjs#L192-L319) 从结构化收件箱按 owner mid 精确识别回复；点赞按 like_time 水位去重，回复按事件 ID/时间去重；手工 `react --note` 供跨端补录。[feedback.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/lib/feedback.mjs) 排除未来/无效时间并限制保留 ID 数量；[dm-feedback.mjs](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/lib/dm-feedback.mjs) 将已收到的 owner 私信时间纳入最近反馈。

没有反馈会降低分享额度（默认第 3 天开始、第 7 天归零）；这只是发送频率政策，不能证明用户讨厌某个话题。[VISION.md](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/VISION.md) 把沉默、小脾气、归属与陪伴写成作者希望达到的关系，并明确承认当前连续性依赖少数文字、记忆不连续、情感模糊。后半部分应视为愿景而非意识、真实情绪或自主成长已经成立的证据。本项目不采用其中要求用户耐心照顾、用“你不理我”的心情维系互动的设计。

## 文档与当前代码存在的差异

这也说明不能直接把 README 的“都已做到”当成验收结果：

| 声明/注释 | 当前快照中的代码 |
|---|---|
| README 人格每次只注入前 3000 字 | scheduler personaBlock 实际 `slice(0, 20000)`（[源码](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/lib/scheduler.js#L320-L339)） |
| README 某段称请求保护值固定、600 ms | `riskSettings()` 读配置，默认 1500 ms，最低 500 ms；多项预算可修改（[源码](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/lib/traffic.mjs#L7-L25)） |
| README/工具默认评论监听关、私信监听开 | `tools/lib/config.mjs` 是此默认；宿主 `lib/index.js` DEFAULTS 仍是评论开、私信关，oncePerPerson 也不同（[宿主](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/lib/index.js#L73-L97)，[工具](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/lib/config.mjs#L25-L55)） |
| 日记“不可关” | 唤醒提示确实要求写；diary CLI 仍在 `diary.enabled === false` 时直接退出（[源码](https://github.com/Alterssss/dsh-bili-whale/blob/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6/tools/commands/diary.mjs#L22-L23)） |
| 固定会话即“跨天不会忘” | 上下文和双日记增加连续性，但没有检索正确率、事件事实性或跨作用域隐私验收 |

这些是静态核查结果，不把注释里“实测通过”当作本轮的真机验证。

## 在 QQ + 真实 Codex 中保留与替换

| 保留其机制 | 本项目需要的替换或增强 |
|---|---|
| 每天少量自主活动、持久计划、允许跳过 | 任务进入 SQLite，事件 ID/活动 occurrence 唯一；有硬时间、token、费用和频率预算；no-op 可以结束且零 QQ 消息 |
| 先便宜筛选、再深入阅读 | 许可范围内 search/read 工具，记录 URL、检索时间、实际读取摘要及来源结果 ID；没有读取成功就不写成经历 |
| 双日记与事件账本分工 | 原始事件不可伪造；六类记忆按 scope 检索；反思是衍生摘要，不能成为独立事实证据；叙事日记可关 |
| 真实反馈改变下次选题 | 证据支持的 preference/interest/behavior patch；小步更新、conflict/supersedes、置信度；一次纠正即有可观察后果 |
| 共用队列、确认和持久水位 | transport 与运行时分离，QQ inbox/outbox 管 ordering、去重、reply window、重连；各私聊/群独立 scope/thread |
| 流量预算、未知结果停止自动重发 | 由角色进程无法改写的 bridge policy/账户隔离执行；工具权限和收件人白名单不能由模型修改 |
| 专用会话、人格注入 | 专用 Codex runtime 的真实 instructions/skills/plugins/MCP discovery 检查；不使用 DSH 的 ctx.sessionController |
| 少量从体验中改善行为 | 级别 1 为可回滚记忆/低风险设置；级别 2 在独立开发区提案、测试、版本化、晋升；级别 3 权限边界只能由用户/管理员改变 |

不能直接复用 DSH 的 `ctx.sessionController.create/prompt`、Cordis UI 注入、`requestRejection`、DSH 会话文件格式，或 README 中的 `danger-full-access` 安装建议。适合作为行为设计主参考，暂不适合作为 QQ/Codex 的运行基础。可复用其纯计划算法/测试思路与流量保护设计，但必须保持 MIT attribution，并重写调用边界。

## 最小完整成长闭环验收建议

最小可行版本不需要先做视频理解、社交平台公开评论、复杂人格评分或向量数据库。只需一个带可替换 QQ transport 的常驻 bridge、一个隔离 Codex 会话入口、一份 SQLite 事件/记忆/任务库、受限 search/read 和一个版本化改进工作区。

每次事件取相关记忆，真实 Codex 选择 reply/quiet/explore/reflect；bridge 执行允许的动作并存回执。反思提出有 evidence_id 的小 patch，校验后影响下一次 prompt、检索或低风险表达参数。调度产生同样的事件，quiet 结果不产生 outbox。验收必须把“fixture/mocked Codex 输出测试”和“真实 Codex + 真实 QQ 接收发送 + 重启”分开。当前对参考仓库的研究只完成静态源码核查，未宣称其线上体验、B 站登录或模型行为已验证。
