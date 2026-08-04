# 轻量图片编辑器 1.0.0

一款面向 Windows 10/11 x64 的本地图片查看与非破坏编辑工具，可注册为图片格式的候选默认应用。

## 下载选择

- `LightImageEditor-Setup-x64-1.0.0.exe`：推荐的当前用户安装包，支持升级、卸载、快捷方式、右键菜单和默认应用注册。
- `LightImageEditor-Portable-x64-1.0.0.exe`：免安装便携版，不写入默认应用注册。
- `LightImageEditor-Rollback-x64-0.9.0.exe`：仅在 1.0.0 出现阻断性问题时使用的签名回滚包。
- `SHA256SUMS.txt`：所有发布文件校验值；`SBOM.cdx.json`：软件物料清单。

安装后进入“设置 → 应用 → 默认应用”，选择“轻量图片编辑器”，再确认需要关联的图片扩展名。Windows 要求该选择由用户在系统界面完成，程序不会静默抢占现有默认设置。

升级会先请求正在运行的程序保存并安全退出；有未保存编辑时可以保存、放弃或取消升级。卸载时可选择保留或清除设置、最近记录、恢复会话、日志和缓存。

## 获取帮助

- [使用和默认应用帮助](../DEFAULT_APP_HELP.md)
- [隐私说明](../../PRIVACY.md)
- [第三方许可证](../../THIRD_PARTY_LICENSES.md)
- [反馈问题](https://github.com/chendaxian318/imageeditor/issues/new/choose)
- [查看已知问题](../beta/KNOWN_ISSUES.md)
