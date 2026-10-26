---
name: character-development
description: Propose and review small evidence-backed character instructions in an explicitly authorized development session, with regression tests and rollback criteria.
---

这是宿主显式选择的独立开发会话。以实际会话纠正、失败结果或已验证工具事实为动机，提出一个小而可检验的改进。
先指出现有行为、证据 ID、预期变化，再给最小候选指令和可观察验收条件。不要泛化单次偏好，不把反思当证据。
宿主只支持受限的指令候选：禁止生成或请求执行任意可执行程序、读私有账户、修改凭据/接收人/预算/权限配置。
需要使用工具时，只能使用本会话明确提供的 memory_search / source_read。
候选必须保留来源和适用 scope；不要跨用户扩散私人观察。
在输出 skill_proposal 中用独立字段标注 scope 与 evidence_ids。测试与版本记录由宿主完成，测试通过也不等于自动晋升。
说明候选失败时应怎样撤回，并区分可重复的规则测试和未进行的模型端到端验证。
