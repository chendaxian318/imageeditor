# v0.9.0 兼容矩阵

每个单元格必须链接到安装日志、测试结果和诊断报告；“计划”不等于通过。Windows 11 测试同时覆盖全新安装与从 Windows 10 保留应用升级后的核心功能，符合微软的兼容测试建议。

| 系统/环境 | 安装/升级/卸载 | 查看与拖放 | 编辑/导出 | 批处理 | DPI/多屏 | 状态 |
|---|---|---|---|---|---|---|
| Windows 10 22H2 x64，当前用户 | 实机 | 实机 | 自动+实机 | 1,000 项 | 100/125/150/200% | 本机部分通过 |
| Windows 11 23H2 x64，当前用户 | 待干净 VM | 待干净 VM | 自动+实机 | 1,000 项 | 100/125/150/200% | 发布门禁 |
| Windows 11 24H2 x64，当前用户 | 待干净 VM | 待干净 VM | 自动+实机 | 1,000 项 | 双屏/负坐标 | 发布门禁 |
| Windows 11 x64，标准用户、无网络 | 待干净 VM | 待验证 | 待验证 | 待验证 | 100% | 发布门禁 |
| Windows 11 x64，从 v0.8.0 覆盖升级 | 待干净 VM | 设置/最近文件迁移 | 项目恢复 | 队列恢复 | 系统主题 | 发布门禁 |

格式集合：JPEG、PNG/APNG、WebP、AVIF、BMP、TIFF、GIF；HEIF/HEIC 只在安装可选插件的独立单元格验收。异常集合：损坏、截断、超大尺寸、只读、权限不足、源文件移动/删除、磁盘写入中断、更新下载中断、哈希/签名不匹配。

干净 VM 每轮必须执行 `scripts/test_install_cycles.ps1` 的 3 次安装/升级/保留卸载/清除卸载，并归档控制台输出。Windows App Certification Kit 可作为额外静态和启动检查，但不能替代本项目的传统桌面安装器与文件关联测试。参考：[Windows 11 测试建议](https://learn.microsoft.com/en-us/windows/deployment/update/plan-determine-app-readiness)、[Windows App Certification Kit](https://learn.microsoft.com/en-us/windows/uwp/debug-test-perf/windows-app-certification-kit)。
