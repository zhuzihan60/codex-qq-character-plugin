# Third-party provenance

运行时直接复用 `vendor/qq-codex-companion/src/qq_codex_companion/onebot.py` 和其 `config.py` 中的数据类型。来源为 need-to-find-name/qq-codex-companion，固定 commit `58c74119bce9932238479c96cebf0b987909245b`，MIT 许可。原版权与完整许可保留在 `vendor/qq-codex-companion/LICENSE`；来源清单见同目录 `UPSTREAM.md`。

其他参考仓库只做源码研究和行为比较，没有把无明确许可证项目的代码加入本运行链。研究快照不在发布包中；其作者的许可和权利仍适用。

Python依赖固定在 `pyproject.toml`，各wheel保留其原始metadata及许可文件。Codex CLI是独立安装的官方运行时，Docker模板固定公开包版本；Codex的开源许可与实际模型/账号服务权限是不同事项。本项目不提供模型服务凭据。
