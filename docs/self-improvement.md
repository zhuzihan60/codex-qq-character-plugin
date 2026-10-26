# 自我改进的实际范围

当前改进的是可检索记忆、行为建议和工具使用方式，不是训练或修改 Codex 模型权重。汐的初始身份和不可自行变更的运行边界由持有者维护；日常经验可以改变局部习惯、兴趣权重与知识，但不能改收件范围、凭证、预算和系统规则。

## 三个层级

| 层级 | 可变化的内容 | 执行者与约束 |
|---|---|---|
| L1 经验适应 | 有原始事件支持的记忆、纠正、偏好、兴趣、低风险交谈设置 | 成长核心按当前会话 scope 和证据校验；将来回合重新检索 |
| L2 指令实验 | 当前会话的表达习惯、已批准工具的使用建议 | 模型只提出候选；固定宿主检查器测试；通过后由可信管理入口提升；每版可回滚 |
| L3 运行边界 | 权限、收件人、账号、凭证处理、花费上限、安全控制 | 模型无修改能力，必须由持有者在可信运行配置中管理 |

本次 L2 最小实现是**带版本的低优先级行为建议**。它不把模型生成的 Python 或 Shell 当成可执行工具，不自动写入用户既有 skills，不把候选内容提升为系统指令。以后开发小工具时，应在专用开发 runtime 测试，并经独立审查后把固定接口交给桥接器；普通 QQ 输入不会开启这个流程。此版本不声称已实现任意新工具的自动安全部署。

## SkillLab 的保存与生效

`src/qq_character/skills.py` 的 `SkillLab(root)` 在专用目录保存：

- `candidates/<id>.json`：版本号、scope、原始证据 ID、动机、建议正文、时间、SHA-256 与检查器版本。
- `checks/<id>.json`：固定检查器的逐项结果、对应候选 SHA、测试时间；明确标记没有证明语义安全与证据真实性。
- `active.json`：每个 scope 的 active/previous 指针和提升/回滚历史。通过临时文件、fsync、原子替换写入。

`propose()` 只保存候选；`test()` 不执行候选程序；`promote()` 只接受已经通过相同检查器且 SHA 未改变的候选。`active_instructions(scope)` 每次重新读取、复核候选，并仅对完全匹配的会话返回建议。它不返回动机、原始事件文本或开发任务日志；别的私聊和群聊得到空字符串。首次提升也能回滚到没有建议的状态。

真实 scope 由宿主从事件确定，例如 `onebot:123:private:456`；不能从候选正文或 QQ 用户自称获得。候选中只保存证据引用 ID；来源是否存在、是否属于该 scope、是否为原始事件，应由调用它的成长核心验证。SkillLab 自身不连接记忆数据库，因此不能宣称已经验证了这些事实。

## 可测试的候选格式

建议正文以这两行开始；它们表达局部适用性，不授予任何权限：

```text
scope: current_conversation
evidence: source_events_only
对方明确纠正之后，按最近的原始纠正调整称呼。证据不足时保留不确定性；问题已经解决就自然收尾，不必总是追加提问。
```

可信管理进程使用的 Python 接口示例：

```python
from qq_character.skills import SkillLab

lab = SkillLab("character-data/skill-lab")
scope = "onebot:123:private:456"
candidate = lab.propose(
    "scope: current_conversation\n"
    "evidence: source_events_only\n"
    "对方明确纠正之后，按最近的原始纠正调整称呼。证据不足时保留不确定性；"
    "问题已经解决就自然收尾，不必总是追加提问。",
    motivation="用户已明确指出过度追问的问题",
    evidence_ids=["原始纠正事件ID"],
    scope=scope,
)
report = lab.test(candidate)
if report["passed"]:
    result = lab.promote(candidate)
advice = lab.active_instructions(scope)
# 如后续表现退步：
lab.rollback(scope)
```

应用时的固定说明应写明“下列是当前会话的可撤回建议；与本轮请求或操作边界冲突时忽略”，不要将其作为替换 developer/system 指令的载体。没有有意义的经验时不生成候选；用户沉默本身不是失败或厌恶证据。

## 检查能证明什么

当前检查包括：正文长度、必需的 scope/evidence 行、非空动机与证据引用、不可执行指令类型、禁用边界变更/代码标记、候选摘要完整性、固定攻击样例的检查器回归。宿主测试器与样例不由候选定义。

这些规则是可重复的最小门槛，**不是自然语言语义安全证明，也不是聊天质量提升证明**。哈希用于识别版本与过期测试，不是针对能任意写宿主磁盘者的数字签名。真正的权限隔离依赖运行环境、宿主工具授权和只读/可写边界；维护者不能把关键词检测当成该边界。

对于“这个建议让对话更好”的评价，还应对后续真实事件记录可观察结果，例如纠正后是否仍使用旧称呼、是否仍对已结束话题机械追问。结果不好则回滚，不把生成的“我进步了”反思作为成功证据。

## 已完成的本地验收

2026-10-26，本机 Python 3.12 运行 `tests/test_skills.py`，13 个测试通过。覆盖：未测试不生效、失败候选不可提升、两个有效版本间回滚、首次版本回滚为空、重启保持、私聊/群聊隔离、缺证据/缺 scope/过长拒绝、测试后篡改拒绝、active 篡改时返回空、候选 ID 路径逃逸拒绝、整个 scope 遗忘时连同候选/检查/历史删除且不影响其他 scope。

这是**真实运行的本地单元测试，使用临时磁盘数据和固定输入，没有调用 Codex 或发送 QQ 消息**。它证明版本与 scope 的实现行为，不等于模型在长期相处中已取得可量化进步。

## 持有者显式启动开发会话

普通 QQ 消息没有 `develop` 路由。持有者在本地停止 bridge 后，可用：

```powershell
.\.venv\Scripts\python.exe -m qq_character develop --scope onebot:123:private:456 --task-file .\development-task.txt
```

scope 必须已经存在，并且仍在私聊/群聊允许列表中。UTF-8 任务文件最多 8000 字符。命令仅提取该 scope 最多 8 条相关记忆和最多 8 条原始事件/资料证据；开发会话的输出不会入 QQ outbox。它使用同一全局日 Token/调用次数预算，超时与每回合 Token 预算仍生效。失败调用不会退还预留额度以允许无限重试。

先将 `config/development.example.json` 的对象放入 `config/local.json` 的 `runtime.development` 字段。该配置必须使用独立容器 `qq-character-development`、独立 home `/home/character/.codex-dev`、独立 cwd `/workspace/development` 与独立认证卷 `qq-character-dev-auth`；不能复用日常聊天容器、私人工程目录或正常开发 CODEX_HOME。部署模板与单独登录步骤见 [Codex runtime 文档](codex-runtime.md) 的独立开发容器部分及 `docker/compose.dev.yaml`。容器创建后运行 `python docker/seal-runtime.py config/local.json --development` 固定实际批准镜像 ID，不复制用户原有登录文件。诊断必须实际通过才调用模型。

开发 profile 的 `allowed_skills` 初始只批准 `character-development`，包含相关的计划、测试设计与审查提示。运行时按实际发现结果显式提供批准 skill。现有 Superpowers 等技能可以由持有者先审查，再复制所需子集到专用开发镜像/环境并加入批准列表；**不自动导入用户技能目录**。要求 Shell、联网安装或额外工具的完整开发技能不一定兼容本次只读开发 profile，不能因为“发现了技能名称”就宣称其全部流程可执行。

模型给出的候选还须在第三行列出本次提供的原始证据 ID，例如：

```text
scope: current_conversation
evidence: source_events_only
evidence_ids: ["onebot:真实事件ID"]
对方已经明确要求减少无目的追问。下一次这类话题回答完成后自然结束；只有缺失的信息确实影响回答时再问。
```

宿主检查引用属于当前已给证据后，去掉这一元数据行，保存候选并执行固定检查器。输出仅含简短状态、候选 ID 和检查结果，**不自动 promote**。持有者可用 `skills test --id ...`、`skills promote --id ...`、`skills rollback --scope ...` 管理版本。此路径使用真实 Codex runtime 时仍只能提议和审查指令/工具设计，固定宿主测试器负责当前支持的指令检查；它不执行模型生成的 Python/Shell，也不声称已完成任意新工具的集成测试。

`tests/test_cli_develop.py` 的 6 个离线回归通过：指定 scope 外的资料不进入 prompt，伪造或跨 scope 证据不能保存候选，模型返回的 QQ 回复/记忆/行为副作用不会被应用，未批准或不存在的 scope 不启动 runtime，诊断失败不推理，失败调用保留预算并阻止无限重试，超长任务文件被拒绝。这些测试使用明确的 `FakeRuntime`，并未执行真实 Codex 开发推理；工作站未安装 Docker，独立开发容器实际运行仍待部署验证。
