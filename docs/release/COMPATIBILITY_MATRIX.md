# v2.0 候选兼容矩阵

每个单元格必须链接到安装日志、测试结果和诊断报告；“计划”不等于通过。Windows 11 测试同时覆盖全新安装与从 Windows 10 保留应用升级后的核心功能，符合微软的兼容测试建议。

| 系统/环境 | 安装/升级/卸载 | 查看与拖放 | 编辑/导出 | 批处理 | DPI/多屏 | 状态 |
|---|---|---|---|---|---|---|
| Windows 10 22H2 x64，当前用户 | v2 安装待候选包 | 自动实机探针 | 自动实机探针 | 1,000 项 | 100/125/150/200% 模拟缩放通过 | UI/DPI 本机通过，安装待验证 |
| Windows 11 23H2 x64，当前用户 | 待干净 VM | 待干净 VM | 自动+实机 | 1,000 项 | 100/125/150/200% | 发布门禁 |
| Windows 11 24H2 x64，当前用户 | 待干净 VM | 待干净 VM | 自动+实机 | 1,000 项 | 双屏/负坐标 | 发布门禁 |
| Windows 11 x64，标准用户、无网络 | 待干净 VM | 待验证 | 待验证 | 待验证 | 100% | 发布门禁 |
| Windows 11 x64，从 v0.8.0 覆盖升级 | 待干净 VM | 设置/最近文件迁移 | 项目恢复 | 队列恢复 | 系统主题 | 发布门禁 |

格式集合：JPEG、PNG/APNG、WebP、AVIF、BMP、TIFF、GIF；HEIF/HEIC 只在安装可选插件的独立单元格验收。异常集合：损坏、截断、超大尺寸、只读、权限不足、源文件移动/删除、磁盘写入中断、更新下载中断、哈希/签名不匹配。

## 当前自动化实机证据

2026-08-05 在 Windows 10 22H2 build 19045、AMD64、2560×1440、实际 96 DPI 主显示器运行 `benchmarks/run_windows_compatibility_probe.py`。探针确认 64 位进程、Per-Monitor V2 与系统 DPI API 可用；看图、转图、改图、生图在 96/120/144/192 DPI 的 Tk 模拟缩放下，顶部模式栏、统一任务托盘、中央工作区、改图面板和底部 Dock 均保持窗口内布局。四档中文字体均可测量，超采样圆角存在过渡 Alpha，窗口裁剪圆角直径按比例缩放。另执行 1,000 次虚拟多屏移窗，覆盖左侧/上方负坐标、右侧 200% DPI 和小工作区；生产窗口约束使用相同规则，所有样本均完整落入目标工作区。原始结果见 `m7-windows10-compatibility.json`。

该证据不是四台不同物理 DPI 显示器测试，也没有执行 v2 安装包生命周期；因此 Windows 10 安装、Windows 11、离线标准用户、双屏负坐标和真实混合 DPI 移窗仍保持发布门禁。

## 外部证据导入

必测场景已冻结在 `V2_COMPATIBILITY_CONTRACT.json`，包括 Windows 10 22H2 当前用户、Windows 11 23H2 当前用户、Windows 11 24H2 混合 DPI 多屏、Windows 11 24H2 离线标准用户，以及从 1.1.3 覆盖升级五项。实验室从 `V2_COMPATIBILITY_EVIDENCE.template.json` 开始填写，每项必须归档安装生命周期日志、诊断报告和测试结果，并记录相对路径与 SHA-256。

`scripts/verify_v2_compatibility_evidence.py` 会拒绝缺失或重复场景、错误系统/账户/网络/显示条件、DPI 或包类型覆盖不足、少于三轮安装生命周期、非 2.0.0 包、路径越界、缺失文件和哈希不符。当前 `v2-compatibility-evidence-status.json` 如实显示五个场景均未导入。

干净 VM 必须执行 `scripts/test_install_cycles.ps1` 的 3 轮 1.1.3→2.0.0 真升级、目标版修复安装、保留数据卸载和清除数据卸载，并归档控制台输出及绑定两个安装器 SHA-256 的 `V2_INSTALL_LIFECYCLE.json`。Windows App Certification Kit 可作为额外静态和启动检查，但不能替代本项目的传统桌面安装器与文件关联测试。参考：[Windows 11 测试建议](https://learn.microsoft.com/en-us/windows/deployment/update/plan-determine-app-readiness)、[Windows App Certification Kit](https://learn.microsoft.com/en-us/windows/uwp/debug-test-perf/windows-app-certification-kit)。
