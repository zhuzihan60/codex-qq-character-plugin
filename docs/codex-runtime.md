# Codex 运行时与隔离验证

本模块调用真实的 `codex app-server`，没有把 OpenAI 通用聊天接口伪装成 Codex。
桥接器拥有记忆、调度、发送与预算；每个模型动作使用新的 ephemeral thread，凭检索结果延续经验。
不恢复整个 QQ 会话的 Codex 历史，因此反思和开发任务日志不会隐式混入下一条 QQ 回复。

## 已经实际验证的部分

核查日期：2026-10-26。以下前六项记录最初的宿主协议探测，版本为 `codex-cli 0.163.0-alpha.11.2`。正式独立 Docker 容器后来已部署 `codex-cli 0.164.0`，完成独立设备授权、真实推理和 OneBot 鉴权；最新证据见 `docs/deployment-2026-10-26.md`。宿主探测与正式容器是两个不同环境。

- 已运行该二进制的 `app-server generate-json-schema --experimental`，据此核对请求和响应字段。
- 已在全新、无凭据的角色 home 中实际完成 `initialize → initialized`，以及 `config/read`、`skills/list`、`mcpServerStatus/list`、`plugin/list`。
- 第一次发现仍有系统配置层，以及自动安装的 5 个系统技能：imagegen、openai-docs、review-agent、skill-creator、skill-installer。
- 仅在这个临时角色 home 内，通过发现的精确技能路径禁用无关技能、加入角色/记忆技能后，第二次发现列表只含 `character` 和 `character-memory`，MCP 和已启用插件为空。
- 两次检查的 `live_ready` 都是 false：同一 Windows 用户的新目录仍不是操作系统隔离。
- 宿主探测没有读取、复制或使用正常 Codex 的 auth 文件；该历史阶段没有执行 `turn/start`、模型推理或 QQ 真实收发。

本地诊断结果在 `research/official/local-diagnostic.json`、`local-filtered-diagnostic.json`；生成的协议 schema 在 `research/official/schema/`。这些研究缓存不纳入发布源码。
系统配置路径出现在有效配置来源元数据中，不意味着读取了用户配置内容或凭据。

## 接口与协议

```python
runtime = Runtime(config["runtime"])
report = runtime.diagnose()                 # 不推理，不登录，不发送 QQ 消息
dev_report = runtime.diagnose(mode="development") # 核查独立开发 profile
result = runtime.run(
    prompt, scope="qq:private:123", mode="chat",
    timeout_s=75, token_budget=24000,
    tool_handler=lambda name, args: host_scoped_tool(name, args),
)
# result = {"output": {...}, "usage_tokens": 1234, "thread_id": "..."}
runtime.interrupt("qq:private:123")          # 宿主授权的取消接口
```

支持 `chat`、`autonomy`、`reflection`、`development`。development 必须配置独立的容器、home、cwd 和凭据/state volume；不可借 QQ 文本切换。

当前部署明确配置 `runtime.model="gpt-6.1-sol"`、`runtime.reasoning_effort="medium"`。0.164.0 的 `thread/start` 没有顶层 effort 字段，因此通过 `config.model_reasoning_effort` 建立线程并检查返回的 `reasoningEffort`；每次 `turn/start` 再显式传入 `effort`。对话与反思均使用中等强度。
`scripts/runtime-model-info.py` 或 `scripts/manage-bridge.ps1 doctor` 可无推理查询实际模型、强度及对应模型目录的支持档位。当前真实查询返回 `gpt-6.1-sol / medium`，目录默认是 `low`；不把配置文件值当作实际生效证明。

诊断与实际推理入口都会通过 `account/read`（`refreshToken:false`）检查专属运行时的账户状态，
只保留 `authentication` 中的是否已检查、是否已认证、认证类型及是否要求 OpenAI 认证；不输出邮箱、账户 ID 或令牌。
账户为空、未知认证类型或查询失败时 `live_ready=false`；隔离与发现审计成功的结果仍保留，无认证的 `local-probe` 仍可核查协议。
这项检查不会登录、刷新令牌或读取 auth 文件，只证明 App Server 报告存在已知账户；远端令牌有效性、模型权限与额度仍须用有预算的真实推理验证。

协议顺序为握手、能力诊断、新建线程、启动一轮、接收完成事件。最终 JSON 用 `outputSchema` 约束并在本地再次检查。
仅 `agentMessage.phase=final_answer` 可进入结果；旧版没有 phase 时只在轮次完成后取最后一条完整 agentMessage。
commentary、reasoning、计划、命令输出、工具进度均不转发。失败或中断不交付部分回复。
超时/达到已上报 token 预算时请求 `turn/interrupt`，随后关闭本轮进程。
因为杀死宿主的 `docker exec` 客户端不保证容器内子进程退出，每个容器内 App Server 额外由 GNU `timeout` 看守：
在本轮 `timeout_s + 5` 秒发送 TERM，再过 5 秒强制 KILL。即使桥接器断开，也保留这个进程生命周期上限。
Dockerfile 构建时检查 `timeout` 存在。这个上限与 token 精确扣费上限是不同概念。

字段采用本机生成的 schema，而非照抄网上示例：`thread/start.sandbox="read-only"`，
`turn/start.sandboxPolicy={"type":"readOnly","networkAccess":false}`。
动态工具需要 `experimentalApi=true`，这是已明确标注的实验性接口。[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)

**预算边界：**模型用量事件通常有滞后，interrupt 也不是服务端精确扣费截止。不能承诺刚好停在 24000 token。预算包括输入上下文与工具数据，不只是最终回复。首次无动态工具冒烟耗用 6257 tokens，完整提示加一次记忆检索实测 14145 tokens，说明旧 8000 目标不足以覆盖完整流程。当前目标经真实测量调整为 24000，普通问候无需无意义的记忆检索。
宿主应在发起调用前保守预留预算；没有收到完整 usage 时不能当作免费。时间和轮次有独立上限。
`max_tool_calls` 只限制桥接器处理的宿主动态工具次数，不是整个 Codex 内置工具集的总次数上限。
不启用 Codex goals 自动续跑，以免桥接器之外产生无限续轮。

## 隔离不是一个布尔开关

`isolated=true`、设置 `CODEX_HOME`、换 cwd 都不能通过 live gate。
本原型只实现可机械检查的 Docker 路径；独立 OS 账户可以作为未来部署方案，但目前不接受未经实现的账户隔离声明。
`CODEX_HOME` 用来分离配置和状态，还必须处理项目/系统配置与技能发现。[官方环境变量说明](https://learn.chatgpt.com/docs/config-file/environment-variables)

每轮推理前执行以下检查：

1. 直接 `docker inspect`，核验实际容器 image ID、非 root 数字 UID、只读根文件系统、cap_drop ALL、no-new-privileges。
2. 不允许 privileged、宿主 PID/IPC/network、设备、额外发布端口、宿主 bind mount、Docker socket。
3. 只接受配置中逐项列出的专属命名 volume；进一步 inspect volume，要求 local driver、无绑定宿主路径的 driver options、专属标签。
4. 以 inspect 返回的不可变容器 ID 执行，避免名称被替换后误连另一个容器。
5. App Server 回报的 codexHome 必须相符；读取有效配置层，检查允许的指令来源根。
6. 检查有效技能名和路径、完整 MCP 列表、已安装启用插件；未知或错误状态拒绝推理。
7. `thread/start` 返回的实际 approval、sandbox、ephemeral 与 instructionSources 再检查一次。

Docker daemon、已批准镜像及专属 volume 的创建者属于可信部署边界。这是独立文件系统/进程边界验证，
不是对任意恶意内核、Docker 管理员或受污染镜像的安全证明。不要把已有私人资料导入专属 volume。
容器不挂载桥接器数据库、QQ Token、宿主个人目录或已有技能库；只通过宿主工具获得当前范围的检索结果。
角色的专属 auth volume 对可信 Codex runtime 本身是可读的。read-only 限制写入，不能证明凭据不可读。
不复制宿主 auth、使用单独登录和不挂载私人目录，证明的是用户原有环境与角色环境的分离；不是对角色凭据建立了独立保密沙箱。

`plugin/list` 官方标注仍在开发中、不建议生产客户端依赖。因此这里将它作为**锁定版本原型的诊断依赖**，
不声称具备稳定生产协议兼容性。升级版本必须重跑 schema、诊断与真实验收；接口不支持时拒绝启动。
日常聊天不需要启用插件；管理插件安装在正常 Codex，不能自动带入角色运行时。[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)

## 工具和技能仍然可用

普通聊天保留角色与记忆技能，以及两个由桥接器提供并计数的实际动态工具：

每轮使用已审计的 skills/list 精确名称与路径，将两个技能作为 `turn/start.input` 的显式 `skill` 项注入正文。
因此关闭 shell 不会令模型只能看到描述却读不到技能内容。普通模式发现缺少任一角色技能、额外开发技能或重名技能都会拒绝推理。
development 使用自己独立环境发现并批准的技能项。[官方技能输入说明](https://learn.chatgpt.com/docs/app-server#skills)

| 工具 | 宿主边界 |
|---|---|
| `memory_search({query})` | 范围在宿主闭包中绑定，不接受模型提供 scope |
| `source_read({source_id})` | 只读取配置中批准的资料源；返回实际抓取内容、时间和 evidence_id |

动态工具请求必须匹配当前 thread/turn，不能夹带 namespace、scope、URL 或其他字段。
本原型关闭角色进程的通用 shell、apps、hooks、内置 memories、自动 goals 和不受宿主来源限制的内置 web search。
另外显式设定 `tools.update_plan.enabled=false` 和 `tools.experimental_request_user_input.enabled=false`，
避免仅关闭 shell 却仍向普通 QQ 对话暴露无关的计划/询问工具。当前最小开发 profile 同样关闭这两项。
本机 alpha 的 `config/read.config.tools` 类型只回报 web_search，因此诊断会核验回传的 sessionFlags 中这两项均为 false，
并标注证据为配置输入层；部署版本若直接回报有效字段，则检查该有效值。不能把这当作已枚举全部模型可见工具的证明。
Codex 其他内置能力可能仍存在，例如 apply_patch；其行为依赖实际只读 sandbox、审批拒绝、时间和 token 控制。
本实现不声称完整工具集只有两个，也不把只读 sandbox 解释为禁止读取专属 auth。
命令/写文件/权限/MCP审批一律拒绝。关闭的只是专属进程，用户已有技能（包括 Superpowers）完全不改。
相关 feature 设置和 `skills.config` 来自当前配置参考；网络代理并不统一约束 app/MCP/hosted 工具，不能把一个代理开关当作完整边界。[官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

开发模式用于提议、审查和生成小工具/指令，实际测试由桥接器的隔离 skill lab 执行。
以后加入 Superpowers 等开发技能时，应复制或安装到独立 development home，明确列入该 profile 的 allowed_skills，
保留其许可证，重跑发现诊断。角色不会自行安装到用户原有技能目录，也不能修改 allowlist。
当前可运行的最小开发 profile 只提供 `character-development` 指令提案/评审技能；不开放任意可执行代码工具。

## Docker 部署模板

本工作站现已安装 Docker，主角色容器已实际 build/run 并通过独立授权、隔离核验和真实推理。以下命令是可复用部署步骤；现有实例不应重复初始化或覆盖配置。独立开发容器尚未部署。
Dockerfile 固定 `@openai/codex@0.164.0`；2026-10-26 从 npm 官方包注册信息核实该版本存在、许可证 Apache-2.0。
本机 alpha 历史探测与公开版本的部署证据分开保存，当前 0.164.0 的真实结果见部署记录。安装方法可对照 [官方 Codex CLI](https://learn.chatgpt.com/docs/cli)。

在项目根目录的 PowerShell 运行：

两个 Compose 文件已分别固定项目名 `qq-character` 和 `qq-character-development`。
未通过 `-p` 或 `COMPOSE_PROJECT_NAME` 覆盖时，不会因文件位于 `docker/` 目录而复用其他项目的默认 `docker` 项目名或默认网络。
首次部署仍须确认下列固定容器和 volume 名没有被已有服务使用。

```powershell
docker compose -f docker/compose.yaml build
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml ps

# 仅在尚未创建本地配置时复制；之后编辑这份配置。
if (-not (Test-Path config/local.json)) { Copy-Item config/example.json config/local.json }
python docker/seal-runtime.py config/local.json
```

bootstrap 仅在角色专属 volume 中复制两个技能，并运行不含推理的 skills/list，按实际发现路径禁用其他技能。
不会读取 auth。重启会恢复受控角色配置与这两个种子技能，避免运行时修改变成权限持久化；角色经验保存在桥接器数据库。

在**专属容器内单独登录**，不要复制宿主 `~/.codex/auth.json`，不要把凭据发给聊天角色：

```powershell
docker exec -it --user 10001:10001 -e CODEX_HOME=/home/character/.codex qq-character-runtime codex login --device-auth
docker exec --user 10001:10001 -e CODEX_HOME=/home/character/.codex qq-character-runtime codex login status
docker exec --user 10001:10001 qq-character-runtime codex --version

# 为实际部署的版本生成协议，后续检查与本机参考是否兼容。
docker exec --user 10001:10001 qq-character-runtime codex app-server generate-json-schema --experimental --out /tmp/character-schema
docker cp qq-character-runtime:/tmp/character-schema research/official/deployed-schema
```

再执行项目 CLI 的 diagnose。必须看到实际 `protocol_handshake=true`、`discovery.ok=true`、`authentication.authenticated=true`、`live_ready=true`，
且 skills 只有两个角色技能，才进行已批准的模型/QQ 端到端测试。
`model` 留空表示使用专属 Codex 默认值；当前本地配置已固定为实际核验过的 `gpt-6.1-sol`，强度为 `medium`，不沿用正常开发环境的私有模型配置。

管理命令：

```powershell
docker compose -f docker/compose.yaml stop
docker compose -f docker/compose.yaml start
docker compose -f docker/compose.yaml logs --tail 40 runtime
```

不要用 `down -v` 作为普通停机命令，它会删除角色专属登录状态。
镜像升级后 image ID 不符会阻断；重新生成 schema、诊断、验收通过，再显式运行 seal-runtime。

### 独立开发容器（可选）

将 `config/development.example.json` 的独立 profile 合并到本地配置的 `runtime.development` 后，使用第二个容器：

```powershell
docker compose -f docker/compose.dev.yaml build
docker compose -f docker/compose.dev.yaml up -d
python docker/seal-runtime.py config/local.json --development
docker exec -it --user 10001:10001 -e CODEX_HOME=/home/character/.codex-dev qq-character-development codex login --device-auth
```

开发容器名为 `qq-character-development`；home 为 `/home/character/.codex-dev`，cwd 为 `/workspace/development`，
独立 volume 为 `qq-character-dev-auth`，唯一默认技能为 `character-development`。
两个容器可以使用同一个只读镜像，但不能共享角色凭据或状态 volume；数据库仍不挂载到容器。
受控 `QQ_CHARACTER_PROFILE=development` 只接受固定的部署值，由 bootstrap 选择固定路径与技能集合。
开发 CLI 从允许的现有 scope 检索证据，生成候选并交给宿主验证、测试和记录；不因这一步自动晋升。

## 测试边界

`tests/test_runtime.py` 是假协议事件的确定性单测，验证 JSON 提取、超时、token 中断、失败、跨线程工具拒绝、
审批拒绝、开发技能隔离与容器结构检查。它们不证明模型真的形成了偏好或成功调用了在线 Codex。
`node --test docker/bootstrap-protocol.test.mjs` 用假管道验证初始化进程退出、spawn 错误和超时均拒绝挂起请求，
超时强制终止子进程，bootstrap 以失败状态结束；这些测试同样不是 Docker 实测。
当前真实证据已包括专属 Docker 隔离、独立登录、App Server 推理、模型与强度核验、OneBot 鉴权和 QQ 发送回执，用户也确认收到回复。成长验收仍需逐项执行；模拟测试不能证明真实偏好适应或重启记忆已通过。证据与边界见 `docs/deployment-2026-10-26.md`。
