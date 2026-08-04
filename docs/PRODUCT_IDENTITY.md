# 产品身份

本文件记录 v0.1.0 起必须保持稳定的产品标识。代码中的唯一来源为
`light_image_editor/identity.py`，版本号的唯一来源为
`light_image_editor/version.py`。

| 项目 | 值 |
|---|---|
| 中文产品名 | 轻量图片编辑器 |
| 英文产品名 | Light Image Editor |
| 内部名称 | LightImageEditor |
| 发布者 | chendaxian318 |
| 应用 ID | io.github.chendaxian318.imageeditor |
| 可执行文件 | ImageEditor.exe |
| 注册表应用名 | LightImageEditor |
| 文件类型 ProgID | LightImageEditor.Image |
| 当前版本 | 1.0.0 |
| 用户数据目录 | `%LOCALAPPDATA%\LightImageEditor`（迁移目标） |

## 稳定性规则

- 版本号只在 `light_image_editor/version.py` 修改，构建脚本、PE 资源、安装器和发布文件名均从这里读取。
- `APP_ID`、`REGISTRY_NAME` 和 `PROG_ID` 发布后不得随意变更；如必须变更，需要提供注册表、设置和文件关联迁移。
- 发布者名称必须与后续代码签名证书或 Microsoft Store 发布者身份一致。
- 当前可执行文件名为兼容既有安装和关联保留为 `ImageEditor.exe`。

## 许可证状态

当前源码来源包含 `https://gitee.com/cswl2020/pic`，所提供源码中没有上游许可证文件。
在权利人明确书面授权或补充上游许可证之前，本仓库不得被视为获得开源许可，不能擅自以 MIT、Apache-2.0 等许可证公开再分发。
