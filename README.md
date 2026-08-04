# 轻量图片编辑器

一个可安装在 Windows 上运行的桌面图片编辑器，支持：

- 默认简洁查看模式，编辑工具栏和参数面板按需展开
- 浅色、深色和跟随系统主题；颜色、间距、焦点样式由统一设计令牌管理
- Windows Per-Monitor V2 DPI 感知，支持 100%、125%、150%、200% 缩放和多显示器工作区约束
- 完整键盘工作流：F6/Shift+F6 切换主要区域，F1 查看快捷键，菜单和核心操作均可不使用鼠标完成
- 画布、工具栏、参数面板和交互控件提供可访问名称、清晰焦点样式及键盘焦点顺序
- 简体中文和 English 使用外置 JSON 资源，可在设置中即时切换，无需重新安装
- 首次启动引导、可操作的空状态，以及加载失败后的重试、另选图片和文件夹定位
- 分类设置页覆盖外观、语言、性能、缓存、保存、元数据、隐私、更新和文件关联
- 离线状态下所有核心功能完整可用；程序不会在后台上传图片或自动发起网络请求
- 单实例运行；连续打开图片时复用已有窗口并转发全部路径
- 后台解码、相邻图片预取和后台预览缩放，损坏文件不会阻塞目录浏览
- 全屏、沉浸式查看、幻灯片、播放间隔与循环设置
- 拖放打开、多路径浏览、最近目录、适应窗口、实际尺寸和窗口位置记忆
- 重命名、复制、移动、资源管理器定位，以及只进入 Windows 回收站的安全删除
- 图片属性：格式、尺寸、颜色模式、文件大小、日期和基础 EXIF
- 统一文档状态与操作日志；所有编辑、撤销、自动保存和导出从同一 `Document` 读取
- 非破坏命令时间线；几何、裁剪、颜色、标注、尺寸和导出按固定阶段回放，原图像素不被改写
- 可保存 `.lieproj` 编辑项目；项目使用版本化 JSON，原图保持外部引用并验证 SHA-256
- 精细旋转、自动拉直、直方图、色阶、RGB 分通道曲线、色温、色调、阴影、高光和降噪
- 历史面板可跳转到任意步骤，支持编辑前后分屏比较；预览、导出和复制结果共用同一渲染器
- 可编辑标注对象：文字、箭头、直线、矩形、椭圆、自由画笔和自动序号
- 隐私与局部效果：马赛克、模糊和纯色遮挡；对象可选择、移动、四角缩放并修改颜色、填充、描边和透明度
- 自定义裁剪比例、四角透视校正、画布四边扩展和背景色
- 常用操作收藏、自定义快捷键、最近编辑项目、最近导出位置及启动工作区恢复
- 自动校正 8 种 EXIF Orientation，导出时移除方向标记以避免重复旋转
- 按格式保留 ICC、EXIF、DPI 与 XMP，预览时将有效嵌入色彩配置转换到 sRGB 显示空间
- 识别 GIF、APNG 和动画 WebP；未经明确确认不会把动画静默覆盖为单帧
- GIF、APNG 和动画 WebP 可直接播放，并可独立导出为 GIF、APNG 或动画 WebP
- AVIF 可直接打开和导出；HEIF/HEIC 通过单独的可选解码插件启用，不增加基础安装体积
- 原子保存、明确的同名冲突处理与可选 `.bak` 覆盖备份
- 每 2 秒自动保存未完成会话；异常退出后可恢复或安全放弃，即使源文件随后被删除也可恢复
- 监测源文件被外部修改、重命名或删除，并阻止覆盖已变化的源文件
- 对解压炸弹、异常尺寸、超量动画帧、截断文件和未知格式设置解码边界
- 导出预设：社交媒体、头像、邮件、原质量、自定义；无编辑且同格式时“原质量”执行字节级复制
- 格式转换：JPEG、PNG、WEBP、AVIF、BMP、TIFF
- 压缩：JPEG / WEBP 质量调节，可填写目标 KB 自动压缩到接近指定大小，PNG 优化压缩
- 裁剪：拖动预览区创建裁剪框，拖动框内移动，拖四角或四边调整大小
- 应用裁剪：Enter 将当前裁剪写入非破坏历史，之后仍可继续编辑或回到裁剪前
- 裁剪比例：自由、1:1、4:3、3:4、16:9、9:16，并支持居中裁剪
- 调整尺寸：输入宽高可精确调整；只填宽或只填高会按比例缩小；也可填写最长边，避免批量处理时拉伸变形
- 常用尺寸：原图、50%、25%、最长边 1920
- 旋转翻转：左转、右转、水平翻转、垂直翻转
- 快速调色：自动优化、亮度、对比度、饱和度、锐化、黑白
- 文字水印：可填写水印内容，选择左上、右上、左下、右下或居中位置，并调节透明度和字号；单张导出和批量处理都会自动应用
- 预览体验：适应/固定比例缩放、Ctrl+加号/减号缩放、Ctrl+0 适应视图、Ctrl+1 实际大小、右键或中键拖动画面平移、双击回到适应视图、裁剪三分线、预计输出尺寸、文件大小和相对原图的节省空间
- 对比预览：勾选或按住空格快速查看原图
- 撤销重做：Ctrl+Z 撤销一步，Ctrl+Y 重做一步；历史面板支持跨会话保存和任意节点跳转
- 防误操作：有未保存修改时，打开新图、粘贴新图或关闭程序前会确认
- 打开体验：打开文件、从剪贴板粘贴图片、命令行传入图片、最近打开列表、鼠标滚轮切换缩放
- 文件夹浏览：打开一张图片后可用上一张/下一张、第一张/最后一张、左右方向键或 PageUp/PageDown 切换同文件夹图片，按 Windows 习惯自然排序（image2 在 image10 前）
- 保存体验：保存为、Ctrl+E 快速导出、复制编辑结果到剪贴板、可在保存后自动定位到导出的文件
- 命名体验：可填写输出文件前缀和后缀，快速导出和批量处理会自动套用到文件名，便于区分源图和处理结果
- Windows 集成：标准安装器提供“已安装的应用”卸载入口、可选快捷方式、图片右键菜单、`.lieproj` 项目关联，并注册为 JPEG、PNG、APNG、WEBP、AVIF、HEIF/HEIC、BMP、TIFF、GIF 的候选默认应用
- 快捷键：Ctrl+O 打开、F11 全屏、Ctrl+F11 沉浸模式、F5 幻灯片、Alt+Enter 属性、F2 重命名、Delete 移入回收站、Ctrl+V 粘贴、Ctrl+S 保存、Ctrl+E 快速导出、Ctrl+Shift+C 复制结果、方向键或 PageUp/PageDown 切换图片、Home/End 跳到首尾、Ctrl+加号/减号缩放、Ctrl+0 适应、Ctrl+1 实际大小、Ctrl+R 右转、Ctrl+Z 撤销、Ctrl+Y 重做、Enter 应用裁剪
- 批量处理：版本化任务队列支持并发数、内存上限、取消、异常恢复、失败重试和再次运行；预设可导入导出，重命名模板支持名称、序号和日期变量，可组合元数据清理、尺寸、水印与格式转换，并导出 CSV/JSON 错误日志

## 构建

构建环境固定为 Windows x64 与 Python 3.12.10。第一次运行会创建 `.venv`，安装锁定依赖，依次执行格式检查、静态检查、类型检查和测试，然后构建安装用 `onedir`、便携 `onefile` 和标准安装器。构建过程不会升级 pip，依赖缺失、版本漂移、存在额外包或任一外部命令失败都会立即停止。标准安装器使用固定版本并校验哈希与数字签名的 Inno Setup 编译器。

在 PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

构建完成后产物位于 `release`：

- `LightImageEditor/`：供标准安装器使用的 `onedir` 应用目录
- `LightImageEditor-Portable-x64-0.7.0.exe`：便携 `onefile` 版本
- `LightImageEditor-Setup-x64-0.7.0.exe`：当前用户标准安装器，支持覆盖升级和“已安装的应用”卸载
- `LICENSE`、`NOTICE.md`、`PRIVACY.md`、`THIRD_PARTY_LICENSES.md`：随发布物和安装程序提供的权属、隐私及第三方许可说明
- `SHA256SUMS.txt`：本次构建产物的 SHA-256 校验值

安装程序可在完成后直接打开 Windows“默认应用”设置。选择“轻量图片编辑器”，再为希望使用的图片格式确认默认关联即可。Windows 10/11 要求默认应用由用户在系统设置中确认，安装程序不会静默覆盖现有选择。覆盖升级或卸载时，安装器会通过本地 IPC 请求主程序保存或安全退出；用户取消时操作随之取消，不会强制终止进程。

如需 HEIF/HEIC，可在开发环境中单独执行 `python -m pip install -r requirements-heif.lock`。该插件不会进入基础依赖或默认安装包；启用后应用会在启动时自动发现并注册解码器。

## 开发与验证

产品版本的唯一来源是 `light_image_editor/version.py`，产品名、发布者、应用 ID、注册表名和文件 ProgID 的唯一来源是 `light_image_editor/identity.py`。

```powershell
# 完整单元测试
.\.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py" -v

# 格式、静态检查和类型检查
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy image_editor.py image_ops.py installer.py light_image_editor scripts benchmarks

# 快速性能基准冒烟
.\.venv\Scripts\python.exe benchmarks\run_benchmarks.py --profile smoke

# 打包程序连续 20 次激活验收
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_single_instance.ps1

# 三轮全新安装、覆盖升级和卸载验收（会改动当前用户安装状态）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_install_cycles.ps1
```

核心结构：

- `image_editor.py`：向后兼容的脚本入口
- `light_image_editor/app.py`：应用创建和命令行入口
- `light_image_editor/ui/`：Tkinter 界面
- `light_image_editor/settings.py`：配置持久化
- `light_image_editor/i18n.py`：外置语言资源加载和运行时界面切换
- `light_image_editor/theme.py`：明暗主题、系统主题跟随和统一设计令牌
- `light_image_editor/dpi.py`：Per-Monitor DPI 感知与多显示器窗口约束
- `light_image_editor/accessibility.py`：可访问名称和主要焦点区域导航
- `light_image_editor/privacy.py`：离线优先和禁止后台上传策略
- `light_image_editor/files.py`：文件与 Windows Shell 服务
- `light_image_editor/single_instance.py`：命名互斥量与本地 IPC
- `light_image_editor/navigation.py`：多路径与目录浏览顺序
- `light_image_editor/image_loader.py`：后台解码与相邻图片预取
- `light_image_editor/formats.py`：AVIF/可选 HEIF 能力探测、动画解码和原子动画导出
- `light_image_editor/batch.py`：版本化批处理预设、任务清单、内存预算、恢复和日志
- `light_image_editor/document.py`：统一文档、操作日志、元数据与源文件指纹
- `light_image_editor/session.py`：事务式自动保存与异常恢复
- `light_image_editor/presets.py`：内置导出预设
- `light_image_editor/preview.py`：后台预览渲染与 ICC 显示转换
- `light_image_editor/metadata.py`：图片属性与基础 EXIF
- `image_ops.py`：图像处理和导出核心
- `packaging/windows/`：受版本管理的 PyInstaller 与 Inno Setup 配方
- `benchmarks/`：启动、大图、切图和批处理基准
- `docs/release/`：来源、历史校验值和发布合规记录

完整版本规划见 [DEVELOPMENT_ROADMAP.md](DEVELOPMENT_ROADMAP.md)。
本版本变更与验收结果见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证状态

导入的上游源码没有附带明确许可证。本仓库当前不授予开源使用或再分发许可；公开或商业发布前必须确认原始代码权属并取得书面修改与再分发授权。详见 [LICENSE](LICENSE)、[NOTICE.md](NOTICE.md) 和 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
