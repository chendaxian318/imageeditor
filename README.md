# 轻量图片编辑器

一个可安装在 Windows 上运行的桌面图片编辑器，支持：

- 默认简洁查看模式，编辑工具栏和参数面板按需展开
- 单实例运行；连续打开图片时复用已有窗口并转发全部路径
- 后台解码、相邻图片预取和后台预览缩放，损坏文件不会阻塞目录浏览
- 全屏、沉浸式查看、幻灯片、播放间隔与循环设置
- 拖放打开、多路径浏览、最近目录、适应窗口、实际尺寸和窗口位置记忆
- 重命名、复制、移动、资源管理器定位，以及只进入 Windows 回收站的安全删除
- 图片属性：格式、尺寸、颜色模式、文件大小、日期和基础 EXIF
- 格式转换：JPEG、PNG、WEBP、BMP、TIFF
- 压缩：JPEG / WEBP 质量调节，可填写目标 KB 自动压缩到接近指定大小，PNG 优化压缩
- 裁剪：拖动预览区创建裁剪框，拖动框内移动，拖四角或四边调整大小
- 应用裁剪：Enter 将当前裁剪真正应用到画面，之后可继续编辑裁剪后的图片
- 裁剪比例：自由、1:1、4:3、3:4、16:9、9:16，并支持居中裁剪
- 调整尺寸：输入宽高可精确调整；只填宽或只填高会按比例缩小；也可填写最长边，避免批量处理时拉伸变形
- 常用尺寸：原图、50%、25%、最长边 1920
- 旋转翻转：左转、右转、水平翻转、垂直翻转
- 快速调色：自动优化、亮度、对比度、饱和度、锐化、黑白
- 文字水印：可填写水印内容，选择左上、右上、左下、右下或居中位置，并调节透明度和字号；单张导出和批量处理都会自动应用
- 预览体验：适应/固定比例缩放、Ctrl+加号/减号缩放、Ctrl+0 适应视图、Ctrl+1 实际大小、右键或中键拖动画面平移、双击回到适应视图、裁剪三分线、预计输出尺寸、文件大小和相对原图的节省空间
- 对比预览：勾选或按住空格快速查看原图
- 撤销重做：Ctrl+Z 撤销一步，Ctrl+Y 重做一步，覆盖裁剪、尺寸、旋转翻转和调色
- 防误操作：有未保存修改时，打开新图、粘贴新图或关闭程序前会确认
- 打开体验：打开文件、从剪贴板粘贴图片、命令行传入图片、最近打开列表、鼠标滚轮切换缩放
- 文件夹浏览：打开一张图片后可用上一张/下一张、第一张/最后一张、左右方向键或 PageUp/PageDown 切换同文件夹图片，按 Windows 习惯自然排序（image2 在 image10 前）
- 保存体验：保存为、Ctrl+E 快速导出、复制编辑结果到剪贴板、可在保存后自动定位到导出的文件
- 命名体验：可填写输出文件前缀和后缀，快速导出和批量处理会自动套用到文件名，便于区分源图和处理结果
- Windows 集成：标准安装器提供“已安装的应用”卸载入口、可选快捷方式、图片右键菜单，并注册为 JPEG、PNG、WEBP、BMP、TIFF 的候选默认应用
- 快捷键：Ctrl+O 打开、F11 全屏、Ctrl+F11 沉浸模式、F5 幻灯片、Alt+Enter 属性、F2 重命名、Delete 移入回收站、Ctrl+V 粘贴、Ctrl+S 保存、Ctrl+E 快速导出、Ctrl+Shift+C 复制结果、方向键或 PageUp/PageDown 切换图片、Home/End 跳到首尾、Ctrl+加号/减号缩放、Ctrl+0 适应、Ctrl+1 实际大小、Ctrl+R 右转、Ctrl+Z 撤销、Ctrl+Y 重做、Enter 应用裁剪
- 批量处理：可选择多个文件，也可选择整个文件夹递归处理；文件夹模式会保留子目录结构，自动避让同名输出，进度窗口显示当前文件和完成进度，可取消剩余任务，完成后显示节省空间并可直接打开输出文件夹

## 构建

构建环境固定为 Windows x64 与 Python 3.12.10。第一次运行会创建 `.venv`，安装锁定依赖，依次执行格式检查、静态检查、类型检查和测试，然后构建安装用 `onedir`、便携 `onefile` 和标准安装器。构建过程不会升级 pip，依赖缺失、版本漂移、存在额外包或任一外部命令失败都会立即停止。标准安装器使用固定版本并校验哈希与数字签名的 Inno Setup 编译器。

在 PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

构建完成后产物位于 `release`：

- `LightImageEditor/`：供标准安装器使用的 `onedir` 应用目录
- `LightImageEditor-Portable-x64-0.2.0.exe`：便携 `onefile` 版本
- `LightImageEditor-Setup-x64-0.2.0.exe`：当前用户标准安装器，支持覆盖升级和“已安装的应用”卸载
- `LICENSE`、`NOTICE.md`、`THIRD_PARTY_LICENSES.md`：随发布物和安装程序提供的权属及第三方许可说明
- `SHA256SUMS.txt`：本次构建产物的 SHA-256 校验值

安装程序可在完成后直接打开 Windows“默认应用”设置。选择“轻量图片编辑器”，再为希望使用的图片格式确认默认关联即可。Windows 10/11 要求默认应用由用户在系统设置中确认，安装程序不会静默覆盖现有选择。覆盖升级或卸载时，安装器会通过本地 IPC 请求主程序保存或安全退出；用户取消时操作随之取消，不会强制终止进程。

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
- `light_image_editor/files.py`：文件与 Windows Shell 服务
- `light_image_editor/single_instance.py`：命名互斥量与本地 IPC
- `light_image_editor/navigation.py`：多路径与目录浏览顺序
- `light_image_editor/image_loader.py`：后台解码与相邻图片预取
- `light_image_editor/preview.py`：后台预览渲染
- `light_image_editor/metadata.py`：图片属性与基础 EXIF
- `image_ops.py`：图像处理和导出核心
- `packaging/windows/`：受版本管理的 PyInstaller 与 Inno Setup 配方
- `benchmarks/`：启动、大图、切图和批处理基准
- `docs/release/`：来源、历史校验值和发布合规记录

完整版本规划见 [DEVELOPMENT_ROADMAP.md](DEVELOPMENT_ROADMAP.md)。
本版本变更与验收结果见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证状态

导入的上游源码没有附带明确许可证。本仓库当前不授予开源使用或再分发许可；公开或商业发布前必须确认原始代码权属并取得书面修改与再分发授权。详见 [LICENSE](LICENSE)、[NOTICE.md](NOTICE.md) 和 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
