# v1.0 正式发布操作手册

## 先决条件

1. `V1_RELEASE_GATES.json` 中除 `V1-MALWARE` 外全部为 `passed`，`V1_PRIORITY_MATRIX.json` 中所有 P0 均由静态状态或最终签名证据关闭，并附可审计证据。
2. `RC_STABILITY_LOG.json` 至少包含连续 7 天且无新增/开放 P0/P1 的记录。
3. 创建并推送指向冻结提交的 annotated tag `v1.0.0`。
4. GitHub Actions secrets 已配置：`WINDOWS_CODE_SIGNING_PFX_BASE64`、`WINDOWS_CODE_SIGNING_PFX_PASSWORD`、`UPDATE_MANIFEST_PRIVATE_KEY_BASE64`。
5. 代码签名证书的发布者与产品身份一致，时间戳服务可用；私钥不得写入仓库或构建日志。

功能冻结后不得直接加入 P1/P2。确有必要时，优先级矩阵中的新增项必须设置 `introduced_after_freeze: true`，并记录 `scope_exchange.removed_id`、新增与移除工作量；移除工作量不得小于新增工作量。

## 阶段一：生成签名草稿

手动运行 `Build signed production draft`，输入 `v1.0.0` 和上一版本标签。流水线从上一标签重新构建并签署回滚安装包，再构建 1.0.0、执行测试/性能/长稳门禁、生成更新清单、SHA-256、SBOM 和溯源，并创建不可公开下载链接之外的 GitHub Draft Release。此阶段不会发布为 Latest。

下载草稿中的四个最终对象进行批准的多引擎检查。不得重新构建或替换被扫描文件。基于模板生成 `MULTI_ENGINE_REVIEW.json`，每个哈希必须对应草稿中的实际 EXE；审核人将该文件上传到同一 Draft Release。

## 阶段二：公开同一批字节

手动运行 `Publish externally reviewed production release`。流水线下载草稿资产、展开 onedir、递归复核 `SHA256SUMS.txt`，重新验证 Authenticode 与 Defender，并将多引擎记录逐项绑定到实际哈希。只有生产门禁全部通过才执行 `gh release edit --draft=false --latest`。

发布后立即启动 `POST_RELEASE_MONITORING.md` 的 72 小时记录。出现 P0 或供应链异常时执行 `HOTFIX_RUNBOOK.md`，不得覆盖已公开资产或复用版本号。

## 官方依据

- Microsoft SignTool 与 SHA-256/RFC 3161 时间戳：<https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool>
- Windows 默认应用由用户在系统界面确认：<https://learn.microsoft.com/en-us/windows/apps/develop/windows-integration/default-apps-platform>
- GitHub artifact attestations：<https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations>
- GitHub Draft Release 发布：<https://cli.github.com/manual/gh_release_create>
