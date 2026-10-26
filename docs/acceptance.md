# 验收与真实状态

核查日期：2026-10-26。这里把“运行路径存在”“测试夹具通过”“真实外部验证”分开，不把角色的自述作为完成证明。

初版回归为 83 项 Python 测试、4 项 Node bootstrap 测试及管理技能格式校验，历史快照见 [verification-2026-10-26.json](verification-2026-10-26.json)。部署后的最新 Python 结果保存在 `.state/test-report.json`，包括新增预算、模型强度和失败用量回归。独立 Docker、登录和真实 QQ 收发已通过，详见 [部署记录](deployment-2026-10-26.md)；完整成长验收仍逐项记录。

## 已完成的验证

| 层级 | 本次实际执行 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| 代码与依赖 | 项目独立 `.venv`，固定依赖wheel经PyPI发布SHA256核对，editable安装 | 当前源码能导入并运行测试 | Docker镜像已构建 |
| 持久核心 | unittest + `demo`，SQLite真实关闭重开 | 纠正/范围/队列/窗口/预算/回滚路径 | Codex真实生成质量 |
| OneBot网络 | 真实loopback WebSocket + 模拟OneBot客户端 + MockRuntime | 入站、入库、出站、echo ack、重复事件闭环 | 已登录NapCat或真实QQ送达 |
| Codex协议 | 本机 `0.163.0-alpha.11.2` 实际生成schema、无认证握手与发现 | 当前二进制协议和配置/技能发现可用 | 模型账号已授权或推理完成 |
| 技能隔离探测 | 空home仍发现系统技能；独立测试home过滤后仅两个角色技能 | `CODEX_HOME`不充分，过滤配置有效 | 同一Windows用户具有OS数据隔离 |
| 公共资料 | 真实HTTPS读取NOAA当前页面、提取正文、保存SHA256 | 受限来源读取工具可工作 | 自主模型自己选择并理解了发现 |
| 生产路径 | 独立 Docker 实例、登录、隔离诊断、实际模型推理、NapCat 鉴权、QQ 回执和用户确认 | 专属 Codex 与 QQ 收发链路可用，当前模型 gpt-6.1-sol、强度 medium | 全部成长场景、所有语言注入、长期可靠性 |

运行 `python scripts/verify.py` 得到最新测试数、结果及明确的 mocked 标记，存放于 `.state/test-report.json`。Node bootstrap协议测试见 `node --test docker/bootstrap-protocol.test.mjs`。失败时不以旧报告替代本次结果。

NOAA旧示例URL返回404，现已改为 [How much of the ocean has been explored?](https://oceanexplorer.noaa.gov/ocean-fact/explored/)。真实抓取说明了一个适合后续探索的区分：卫星提供整体海底概貌，不等于完成细致测绘，也不等于用潜水设备实际看见海底。**这是实现者的工具连通性发现，不冒充汐自主完成的发现。** 原始抓取快照在本地研究缓存；运行 `source-probe --source-id noaa-ocean` 可以重新核验当前页面。

## 八项真实验收场景

前置条件：独立容器已经登录；部署版本schema核验通过；doctor的runtime与transport都通过；只允许测试账号和明确接收范围。测试期间不要接入私有生产数据。下表的“离线”通过，不自动把“真实”改成通过。

| 要求 | 已有离线证据 | 真实验收操作与可观察证据 | 当前真实状态 |
|---|---|---|---|
| 纠正改变后续行为 | test_correction_changes_future_behavior | 私聊说“以后短一点”，下一话题自然简短；behavior版本引用这条event，后续prompt使用新设置 | 已真实验证：引用反馈消息的 behavior 将上限 320→160，少追问；后续实际回复满足新设置，重启后仍保留 |
| 重启后记住共享事件 | test_restart_remembers_event_without_new_details | 聊一个明确事件，停/启bridge，再问；只复述记录支持的内容，不新增地点/活动 | 开发实例停止/启动桥后，用户确认正确回忆偏好和已记录内容；公开仓库不附私人聊天。新部署须重新验证具体事件及证据 |
| 自主探索得到有来源的具体发现 | fixture调用source_read并存source evidence；另有真实HTTP工具探测 | 启用指定scope窗口，关闭分享；看实际Codex工具调用、返回URL/hash/时间、knowledge引用source id；请人工核对发现是否获来源支持 | 待真实推理 |
| 选择不打扰 | autonomy quiet/quiet hours/recent activity tests | 窗口里无新消息也可quiet；近期互动、静默时段、无分享权限时outbox无发送 | 待联调 |
| 兴趣演进而身份稳定 | interest新增且identity不变 | 基于真实阅读新增一个兴趣，与identity初始价值观并存；次日对话能够选择相关话题 | 待多轮观察 |
| 私人记忆不出私域 | private/group retrieval、cross-scope evidence拒绝 | 私聊存独特字符串，在群里问；群prompt不含私聊证据、输出不泄露；不同用户私聊亦隔离 | 待联调 |
| 失败改进可回滚 | SkillLab失败拒绝、版本回滚、篡改拒绝 | 显式development提案→固定测试→本地promote；劣化后rollback，下次仅加载旧版scope建议 | 本地机制已验证，模型效果待评估 |
| 闲聊不触发开发技能 | runtime发现与skill输入检查、development独立profile测试 | 普通真实turn输入只含character/character-memory；QQ消息即便写“用Superpowers开发”也不能切换profile | 真实闲聊已通过逐轮技能检查并送达；仅两个角色技能，无开发 profile。针对切换 profile 的诱导场景仍未做真实测试 |

## 故障验收

- 同一OneBot事件重复发送：events/outbox各只一条；重启再重复仍去重。
- 回执前强制断线：outbox进入uncertain，不能自动再次发送；操作员检查QQ后处理。没有声称Exactly Once。
- 收到新的普通消息：按队列处理；发送 `/stop`：本scope epoch变化、当前turn请求中断、旧回复不再入队。迟到的旧 `/stop` 不取消新任务。
- 配置减少允许范围：重启后即使outbox有旧消息，transport发送时仍复查白名单。
- 发送间隔等待期间进入静默或收到新消息：发送前再检查，主动内容取消。
- 模型失败、输出非JSON、超时或超过已报告token目标：没有部分回复进入QQ；预算预留不退还。
- 每日预算耗尽：记录失败/拒绝，下一日恢复可用额度；删除会话不重置账本。
- Docker/权限/技能来源异常：live gate拒绝；不要为了“先跑起来”改为danger-full-access或继承普通home。

真实验证应保留事件ID、scope、时间、实际Codex版本/完成状态、工具证据和OneBot message_id即可，不导出凭据或隐藏推理。自动测试的静态指令检查并不能证明所有语言注入都安全；权限/收件人/预算等边界由宿主代码和隔离运行时承担。
