# 轻量图片编辑器

一个可安装在 Windows 上运行的桌面图片编辑器，支持：

- 改图模式的透视文字检测现除空间分离双平面外，还能保守分离低色差双平面，以及三种色轴可区分、有限局部遮挡的三个普通印刷体平面：互斥 RGB 色轴分层分别恢复四角、主色和阅读顺序；smoke/standard 门禁量化两两区域/墨迹遮挡、三层覆盖、候选范围、主色误差、确定性和 P95。完全同色样本保持单一合并候选，不猜测深度分层或不可见文字。

- Material You 工作台：常驻“看图 / 转图 / 改图 / 生图”一级导航、左侧资源区、中央画布、模式化右侧面板和底部胶囊命令 Dock
- 看图模式提供上一张、下一张、适应、1:1、不同文件双图比较、动画进度与倍速，以及“去转图 / 去改图”交接入口；模块切换保留当前资源、修订和编辑子页状态
- 135 项机器可读功能台账覆盖四模块与全局能力；底部“全部功能”或 Ctrl+K 可按中文功能、模块、入口和快捷键搜索并执行真实回调，菜单、收藏工具、模块入口和统一任务托盘继续保留
- 生图模式采用左侧参数、右侧候选的双栏工作区，内置完全离线的模拟 Provider；支持负面提示词、最多四张参考图及逐张内容/风格/构图角色与强度、固定种子、5 种中文风格预设和基于所选候选的可追踪变体，并提供服务状态、逐次上传/费用确认及显式失败重试；候选支持持久收藏、仅看收藏和安全移除，移除时仍保留受管修订与任务追溯；参考列表与历史可跨重启恢复，候选保持真实 Asset/Revision 身份发送到看图、改图或转图
- 转图模式提供独立真实队列、文件/文件夹入队、启用/排除、配方导入导出、输出目录、开始/取消和逐项状态；中央预览与原裁切、尺寸、格式控件继续保留
- 转图配方采用版本化严格 JSON，并以同一有界执行器驱动单张与批量的裁切、旋转、翻转、尺寸、文字水印和安全导出
- SQLite 素材库持久保存外部/托管资源、SHA-256、来源记录与父修订树；文件移动、内容变更和应用重启后仍保持真实资源身份
- AI 平台统一使用异步任务管理、一次性上传/费用授权和 Windows DPAPI 凭据存储；AI 故障不会阻塞看图与转图
- 顶部统一任务托盘集中显示转图批次、智能改图和生图任务，支持进度、详情、前往、取消、显式重试及完成项清理；历史跨重启恢复，未结束任务安全标记为未知且不会自动重放
- 智能任务记录支持安全重启恢复：中断任务显示为状态未知且不会自动重放，取消可压住晚到成功回包；改图结果入库会验证请求、服务、源修订和操作血缘，并原子拒绝损坏或超限内容
- M5 智能改图提供独立 smoke/standard 质量门禁，量化检查 2×/4× 尺寸与 Alpha、普通背景去除、主体贴边与缓慢渐变、半透明 Alpha 保留、保留/删除画笔、纯色/自定义图片背景替换、规则/渐变背景物体擦除、离线多主体像素蒙版、异色相接/可见重叠颜色分离、同色可见中等宽接触形状分离、加入/移出画笔精修及蒙版外隔离、横排/竖排/透视与手动弧度改文字区域隔离、-90°～+90°普通印刷体十度粗搜与一度精修、受控单行二次基线弧度检测、空间分离双透视、低色差遮挡双透视与可区分色轴遮挡三透视平面候选、同色遮挡安全合并、水平普通印刷体纯色/描边/偏移投影/线性渐变/单色扩散发光分类、主色/辅助色/强度估计与可编辑替换、平滑渐变/低频纹理、周期性高频纹理/正交亮度结构及受控多尺度照片式随机纹理/密集枝状边缘背景的纯色/发光分析、小字号/上下边界/双行/有限局部遮挡排版适应、JPEG 4:2:0 质量 95/75/50 单次压缩鲁棒性、多随机种子首选候选正确性与无字累计零误报、真实字体 Top-1 推荐、像素确定性和 P95 性能
- M6 生图提供可注入 Provider 的独立 smoke/standard 技术质量门禁，验证真实 PNG、固定种子复现、候选区分、最多四张参考图权重与指纹、变体血缘、来源完整性及 P95 性能；提示词语义和审美质量留给生产 Provider 专项验收
- M7 v2.0 发布就绪审计采用失败关闭策略：15 个门禁逐项校验证据文件、状态和关闭条件；四模式发布级长稳已通过，生产 Provider、隐私费用、实机兼容、安装升级、签名扫描或许可未完成时仍不会生成“可发布”结论
- 四模式标准长稳门禁覆盖 20,000 次看图、20,000 次模式切换、1,000 个转换及损坏输入隔离、改图/生图成功与取消各 50 次、任务和托管资源跨重启恢复，并量化阶段 RSS 趋势
- v2.0.0 安装候选预检严格核对版本、发布文档、锁定工具链、构建安全、中文低权限安装器、升级/卸载数据策略、签名、回滚和候选产物；当前版本或证据不匹配时保持失败状态
- 构建脚本按主版本选择发布证据：1.x 保持既有生产校验，2.x 自动选择对应发布说明、安装预检和 v2 发布门禁；Inno 安装器不再固定携带旧版说明
- Windows 兼容性探针记录真实系统/架构/DPI API，并在四档模拟缩放下检查四模式布局、中文字体、抗锯齿圆角和窗口裁剪；本机结果不会替代 Windows 11 或物理混合 DPI 验收
- DPI 变化时主窗口会重新约束到当前显示器工作区；自动门禁以 1,000 次虚拟移窗覆盖左侧/上方负坐标、200% DPI 与小工作区，避免保存或恢复出屏窗口
- 外部兼容性证据导入器冻结五个 Windows 10/11 场景，并校验系统、账户、网络、显示器、DPI、包类型、安装循环、相对证据路径和 SHA-256；缺失实机证据不会被模板状态替代
- 生产 Provider 选型门禁冻结改图与生图的能力、上传、区域、最长 30 天留存、训练用途、费用和沙盒文件哈希；最终 Provider/模型必须由产品负责人明确批准，空模板、隐私越界或伪造证据均保持发布阻塞
- v2 构建会在发布审计前重新运行 Provider 选型校验器，并将本次生成状态作为三个 AI 门禁的强依赖；生产及生产候选构建不能使用阻塞豁免，避免通过手改发布状态绕过真实沙盒与人工批准
- 外部实机兼容性和安装升级同样是构建时强依赖：v2 构建会重新校验五场景兼容性证据并生成安装预检状态，再把本次三类状态传入总审计；仓库旧报告或手工 `passed` 不能替代本次构建证据
- v2 供应链门禁逐文件绑定应用、便携包、安装包和回滚包的真实 SHA-256、大小、批准证书指纹、可信时间戳、Defender 结果及人工多引擎复核；构建会重新生成状态，缺少最终字节或仅修改汇总布尔值不能通过
- v2 许可门禁分别验证第一方商业再分发权、第三方依赖再分发审查和 Inno Setup 商业许可；每项授权范围、有效期、人工批准与文件哈希均为构建强依赖，现有“不授予权利”的 LICENSE 不会被当成发布授权
- 改图模式新增“智能”页、2×/4× 本地高清、离线确定性去背景与改文字基线；去背景使用整圈边框主色家族估计和背景色差自适应，能处理受控主体贴边、缓慢渐变并保留原始半透明 Alpha，结果仍可用保留主体/删除背景画笔继续细化；物体擦除可离线识别最多 5 个主要主体、逐个切换并在画布校正边框后复用局部修复闭环；改字可在后台离线建议横排至竖排普通印刷体区域、分离空间上互不遮挡的多个透视文字平面并按阅读顺序切换、保守识别受控单行弧形普通印刷体并同步弧度、对水平普通印刷体保守分类纯色/描边/偏移投影/线性渐变/单色扩散发光，并自动回填可校正的主色、辅助色和描边/投影/发光强度；局部稳健二次 RGB 背景面可分离受控平滑渐变与低频纹理，局部中值背景和全局色向端点可处理周期性高频纹理及正交亮度结构，照片式局部化路径可在受控多尺度随机纹理与密集枝状边缘中分离纯色和发光文字。照片式场景还会依据全画面空间覆盖、重复实心主色证据和候选紧致度抑制连续自然边缘，并以尺度自适应纯色核心支持小字号、上下边界、双行及有限局部遮挡；JPEG 压缩路径以空间变化、全局亮度差和有色环带区别字形、压缩块及发光，门禁覆盖单次 4:2:0 质量 95/75/50。多随机种子门禁要求正确文字排在首位且对应无字背景累计零候选。用户还可校正普通旋转/透视裁切及受控二次基线弧形 OCR 输入、识别内容并推荐本机相似字体，再执行背景修复和横排/竖排/透视中文排版或 -100%～+100% 手动单行弧度排版，所有结果均作为托管子修订保存；真实照片语义内容、复杂自然对象、强遮挡下的生产级字效复刻与 OCR、真实照片弧形识别语料、多层/内发光/非均匀发光、纹理/三维/非线性变形高级艺术字及艺术字体识别仍未完成
- 改文字在 Windows 10/11 优先使用系统中文 OCR，失败后自动回退可选 Tesseract：自动候选先完成旋转、竖排、透视或受控弧形几何校正，校正来源会进入识别结果并在界面如实显示；系统空结果会在同一个 30 秒总预算内用有界灰度对比度增强重试。界面区分系统无可信度、增强结果与 Tesseract 可信度；未安装、缺语言包、失败、无结果或换图竞态均安全回退手工输入，图片不会上传
- 浅色、深色和跟随系统主题；背景、表面容器、强调色、画布、间距和焦点样式由 Material 设计令牌统一管理
- Windows Per-Monitor V2 DPI 感知，支持 100%、125%、150%、200% 缩放和多显示器工作区约束
- 完整键盘工作流：F6/Shift+F6 切换主要区域，F1 查看快捷键，菜单和核心操作均可不使用鼠标完成
- 画布、工具栏、参数面板和交互控件提供可访问名称、清晰焦点样式及键盘焦点顺序
- 界面固定为简体中文，中文文案使用外置 UTF-8 JSON 资源统一维护
- 首次启动引导、可操作的空状态，以及加载失败后的重试、另选图片和文件夹定位
- 分类设置页覆盖外观、性能、缓存、保存、元数据、隐私、更新和文件关联
- 离线状态下所有核心功能完整可用；程序不会在后台上传图片或自动发起网络请求
- 稳定版与测试版双更新通道；只在用户主动检查时联网，清单签名、安装包大小、SHA-256 和 Windows 发布者签名必须全部通过
- 保留上一份已验证安装包，可由用户主动回滚；任何下载中断、篡改或签名不匹配都会安全失败
- 本地轮转日志、异常退出记录和脱敏问题报告 ZIP；匿名崩溃上报默认关闭，发送前实行“设置同意 + 本次确认”双重许可
- RC 性能与质量门禁覆盖启动、首图、切图、缩放、导出、批处理、长稳内存、异常注入和发布物可追溯性
- 单实例运行；连续打开图片时复用已有窗口并转发全部路径
- 后台解码、相邻图片预取和后台预览缩放，损坏文件不会阻塞目录浏览
- 当前目录缩略图侧栏：高亮当前图片、点击切图、自动滚动定位；采用可见行虚拟化与后台解码，大目录不会一次性加载全部预览
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
- 应用裁剪：框选时画布下方显示“应用裁剪 / 取消”操作条，也可按 Enter 应用、按 Esc 取消；写入非破坏历史后仍可继续编辑或回到裁剪前
- 图标提示：底部 Dock、画布工具和动画控制中的纯图标按钮，鼠标悬停后显示中文功能名
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
- 快捷键：Ctrl+O 打开、Ctrl+B 显示/隐藏缩略图侧栏、F11 全屏、Ctrl+F11 沉浸模式、F5 幻灯片、Alt+Enter 属性、F2 重命名、Delete 移入回收站、Ctrl+V 粘贴、Ctrl+S 保存、Ctrl+E 快速导出、Ctrl+Shift+C 复制结果、方向键或 PageUp/PageDown 切换图片、Home/End 跳到首尾、Ctrl+加号/减号缩放、Ctrl+0 适应、Ctrl+1 实际大小、Ctrl+R 右转、Ctrl+Z 撤销、Ctrl+Y 重做、Enter 应用裁剪
- 批量处理：版本化任务队列支持并发数、内存上限、取消、异常恢复、失败重试和再次运行；预设可导入导出，重命名模板支持名称、序号和日期变量，可组合元数据清理、尺寸、水印与格式转换，并导出 CSV/JSON 错误日志

## 构建

构建环境固定为 Windows x64 与 Python 3.12.10。第一次运行会创建 `.venv`，安装锁定依赖，依次执行格式检查、静态检查、类型检查和测试，然后构建安装用 `onedir`、便携 `onefile` 和标准安装器。构建过程不会升级 pip，依赖缺失、版本漂移、存在额外包或任一外部命令失败都会立即停止。标准安装器使用固定版本并校验哈希与数字签名的 Inno Setup 编译器。

在 PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

构建完成后产物位于 `release`：

- `LightImageEditor/`：供标准安装器使用的 `onedir` 应用目录
- `LightImageEditor-Portable-x64-1.0.0.exe`：便携 `onefile` 版本
- `LightImageEditor-Setup-x64-1.0.0.exe`：当前用户标准安装器，支持覆盖升级和“已安装的应用”卸载
- `LightImageEditor-Rollback-x64-0.9.0.exe`：生产构建附带的上一版本签名回滚包
- `LICENSE`、`NOTICE.md`、`PRIVACY.md`、`THIRD_PARTY_LICENSES.md`：随发布物和安装程序提供的权属、隐私及第三方许可说明
- `SBOM.cdx.json`：CycloneDX 1.6 软件物料清单
- `RELEASE_NOTES-v1.0.0.md`、`RELEASE_GATE_STATUS.json`：正式版说明与机器可读发布门禁
- `BUILD_PROVENANCE.json`、`LOCAL_SECURITY_SCAN.json`、`diagnostics/`：提交到产物的追溯、签名/杀软和诊断资料
- `SHA256SUMS.txt`：本次构建产物的 SHA-256 校验值

安装程序可在完成后直接打开 Windows“默认应用”设置。选择“轻量图片编辑器”，再为希望使用的图片格式确认默认关联即可。Windows 10/11 要求默认应用由用户在系统设置中确认，安装程序不会静默覆盖现有选择。覆盖升级或卸载时，安装器会通过本地 IPC 请求主程序保存或安全退出；用户取消时操作随之取消，不会强制终止进程。卸载时可选择保留或清除设置、最近记录、恢复会话、日志和缓存；静默卸载默认保留，传入 `/CLEARUSERDATA` 才会清除。

公开发布还需要配置 `IMAGEEDITOR_SIGNTOOL`、`IMAGEEDITOR_AUTHENTICODE_THUMBPRINT` 和仓库外的 `IMAGEEDITOR_UPDATE_PRIVATE_KEY`。构建会签署并复验最终 EXE 后生成两个通道的签名清单；未配置时只生成带 `SIGNING_REQUIRED.txt` 的本地测试构建，不得上传到更新通道。更新安全模型与发布步骤见 `docs/release/UPDATE_SECURITY.md`。

v1.0 正式发布采用签名草稿与公开发布两个阶段。草稿阶段要求上一版本签名回滚包并生成 GitHub artifact attestation；公开阶段下载同一批资产，绑定多引擎审核哈希并重新验证签名、Defender、SHA-256、SBOM、标签、提交和全部发布门禁。完整步骤见 `docs/release/PRODUCTION_RELEASE_RUNBOOK.md`，未通过 `RELEASE_GATE_STATUS.json` 时禁止创建公开稳定版。

`docs/release/V1_PRIORITY_MATRIX.json` 是 v1.0 范围冻结的机器台账：任何 P0 未关闭都会阻止候选构建；冻结后新增 P1/P2 必须记录一项工作量相等或更大的换出范围，否则生产验证器失败。

正式发布候选还需运行标准性能比较和长稳测试：

```powershell
.\.venv\Scripts\python.exe benchmarks\run_benchmarks.py --profile standard --output "$env:TEMP\v10.json"
.\.venv\Scripts\python.exe benchmarks\compare_results.py --baseline .\benchmarks\baselines\v0.8.0-windows-x64-standard.json --current "$env:TEMP\v10.json"
.\.venv\Scripts\python.exe -m scripts.run_soak_tests --profile release --output "$env:TEMP\v10-soak.json"
```

完整操作说明见 `docs/USER_MANUAL.md`，兼容矩阵和正式发布门禁见 `docs/release/COMPATIBILITY_MATRIX.md` 与 `docs/release/V1_RELEASE_GATES.json`。

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

# 看图、转图与智能工具正式门禁
.\.venv\Scripts\python.exe benchmarks\run_viewer_gate.py --profile standard
.\.venv\Scripts\python.exe benchmarks\run_conversion_gate.py --profile standard
.\.venv\Scripts\python.exe benchmarks\run_smart_edit_gate.py --profile standard
.\.venv\Scripts\python.exe benchmarks\run_generation_gate.py --profile standard

# 打包程序连续 20 次激活验收
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_single_instance.ps1

# 三轮全新安装、覆盖升级和卸载验收（会改动当前用户安装状态）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_install_cycles.ps1 `
  -Installer .\release\LightImageEditor-Setup-x64-2.0.0.exe `
  -PreviousInstaller .\release\LightImageEditor-Rollback-x64-1.1.3.exe `
  -TargetVersion 2.0.0 -PreviousVersion 1.1.3
```

核心结构：

- `image_editor.py`：向后兼容的脚本入口
- `light_image_editor/app.py`：应用创建和命令行入口
- `light_image_editor/ui/`：Tkinter 界面
- `light_image_editor/settings.py`：配置持久化
- `light_image_editor/i18n.py`：简体中文外置文案资源加载
- `light_image_editor/shell/`：四模式路由、跨模式资源交接和完整功能台账
- `light_image_editor/domain/`：资源修订、智能任务请求、转图配方及顺序执行器
- `light_image_editor/providers/`：智能服务协议与完全离线的模拟服务
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
四模式最终产品目标与实施计划见 [docs/FOUR_MODE_DEVELOPMENT_PLAN.md](docs/FOUR_MODE_DEVELOPMENT_PLAN.md)。
本版本变更与验收结果见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证状态

导入的上游源码没有附带明确许可证。本仓库当前不授予开源使用或再分发许可；公开或商业发布前必须确认原始代码权属并取得书面修改与再分发授权。详见 [LICENSE](LICENSE)、[NOTICE.md](NOTICE.md) 和 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
