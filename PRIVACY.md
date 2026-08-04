# Privacy / 隐私说明

Light Image Editor processes image pixels, metadata, projects, presets and batch jobs locally
on the user's Windows computer. Core viewing and editing features work without a network
connection.

轻量图片编辑器在用户自己的 Windows 电脑上处理图片像素、元数据、编辑项目、预设和批处理任务。看图与编辑核心功能在断网状态下仍可完整使用。

The application does not upload images in the background, perform telemetry, show ads, or
silently contact an update service. Version 0.8.0 contacts the selected update channel only
after the user clicks Check for updates. Windows manages default-app associations and the user
must confirm any change in system settings.

本应用不会在后台上传图片，不进行遥测，不展示广告，也不会静默连接更新服务。v0.8.0 只会在用户点击检查更新后连接所选更新通道。默认应用关联由 Windows 管理，任何变更均需用户在系统设置中确认。

Local settings, recent paths, recovery sessions, logs and batch manifests remain in the current
user profile. Diagnostic ZIP export is user initiated and excludes images while redacting paths,
emails, IP addresses and secret-like values. Anonymous crash reporting is off by default and
requires saved consent plus confirmation for each report. Uninstall asks whether to preserve or
remove local data; silent uninstall preserves it unless `/CLEARUSERDATA` is explicitly supplied.

本地设置、最近路径、恢复会话、日志和批处理清单保存在当前用户配置目录中。诊断 ZIP 仅由用户主动导出，不包含图片，并会遮蔽路径、邮箱、IP 和密钥样式内容。匿名崩溃上报默认关闭，必须同时具备设置同意和本次发送确认。卸载会询问保留或清除本地数据；静默卸载默认保留，只有显式传入 `/CLEARUSERDATA` 才会清除。
