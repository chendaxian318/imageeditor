# Changelog

## 2.0.0 候选版 — Material You UI（2026-08-07）

- 新增 PSD、PSDT 与 PDF 查看支持：PSD/PSDT 由专用解析器生成只读合成预览，并在缓存预览异常时尝试从图层树重建；PDF 使用有页数、页面尺寸与像素上限的 PDFium 后台渲染，支持整份文档纵向连续滚动并按视口加载页面。三种格式都可进入快速标注，完成后导出 PNG/JPEG/WebP 副本，不覆盖 Photoshop 图层文件或源 PDF。安装器同步注册 `.psd`、`.psdt`、`.pdf` 候选关联，并为 PSD/PSDT 添加明确的右键查看入口。

- 产品对外名称统一为 `CSImage`，新安装默认路径改为 `D:\CSImage`，安装包与便携包同步使用 CSImage 文件名；旧版 AppId、文件关联和用户数据标识继续保留，确保覆盖升级与设置迁移兼容。
- Windows 安装程序在首次安装、覆盖安装和升级安装中始终显示目录选择页；“浏览”改为按需展开固定本地磁盘的图形化文件夹窗口，避免 Windows Shell 枚举断开的网络盘造成卡顿，同时保留完整路径编辑和新建文件夹。
- 看图器移除编辑栏中重复的“仅复制”动作，右上角窗口控制区统一为深色圆角胶囊样式，并使用双矩形最大化图标。
- PySide6 看图器的轻标注扩展为截图式快速编辑栏：新增独立“选择”模式以及直线、椭圆、画笔、文字、自动序号、马赛克、模糊和隐私遮挡，绘制工具始终创建新对象并支持重叠绘制，画完自动进入选择状态；再次点击对象可拖动位置，矩形类对象提供四角控制点，直线与箭头提供起点、位于曲线上的中间弯曲点和终点，拖动线上中间点可生成二次贝塞尔曲线且不显示离线辅助三角形，箭头方向跟随末端切线；选中后工具栏同步并实时修改当前对象的颜色和大小，空选择时禁用属性控件；创建、移动、缩放、属性、文字和删除统一进入多步撤销，Delete、Esc 和 Ctrl+Z 按当前编辑上下文处理；文字在图片上原位输入，Enter 确认、Esc 取消，选择状态双击已有文字可继续修改，切换工具、复制或替换前提交待确认文字；文字字号独立使用 8～96 px、默认 20 px，并按当前画面可见大小换算到原图；马赛克和模糊在拖动时实时显示真实效果，并与复制和替换结果保持一致。
- PySide6 看图器在未打开图片时新增居中空状态卡片，提供“打开图片”主按钮与拖放提示，并隐藏无意义的切图箭头、文件信息和图库入口。
- 缩放比例按钮改为选择器，提供适合窗口及 25% 到 200% 的常用预设，选择后立即缩放并关闭弹层。
- 看图命令坞将“适合窗口”替换为实时缩放百分比，点击比例恢复 100%，并恢复独立全屏按钮。
- 关闭当前看图界面的“更多”功能入口，底部仅保留打开、缩放、适合窗口和旋转。
- 将看图缩放命令中含义模糊的“适应”统一改为“适合窗口”，明确表示完整显示整张图片。
- 看图底部命令坞进一步缩为打开、缩放、适应、旋转和“更多”六个控件；上一张/下一张移到画布两侧并仅在多图目录显示，全屏保留在“更多”和 F11，减少底部视觉拥挤。
- 看图底部新增持续可见的文件基础属性（文件名、格式、像素尺寸、文件大小和目录位置），并增加带图片数量的“图库”胶囊按钮；点击即可展开或收起当前文件夹的全部图片缩略图。
- 默认桌面收敛为纯看图界面：隐藏模式导航、参数面板、画布工具和常驻图库，只保留打开、切图、缩放、旋转、全屏与“更多”；转图、改图和生图实现继续保留，但不在当前看图入口中展示。

- 参考 WPS 图片、360 看图与微软照片的渐进披露方式，大幅简化四模式默认界面：收藏工具条不再永久占用第二行，看图顶部仅保留“打开文件夹”，底部命令坞会按模式切换；看图只显示打开、切图、缩放、旋转、全屏和更多，转图只显示打开、导出、裁剪、旋转和更多，改图才显示保存、撤销、重做与原图对比，生图使用独立工作区而不显示无关命令坞。全部原功能继续保留在对应模块及“全部功能”搜索中。
- 转图模式新增格式专属高级参数：WebP 无损编码、PNG 2～256 色调色板，以及 TIFF 不压缩、LZW、Deflate、PackBits 压缩；静态图与动画统一走真实编码链路，配方升级到 schema v2 并兼容导入 v1。高级控件会随输出格式启用，WebP 无损与目标 KB 互斥，透明 APNG 调色板量化在当前不安全场景下明确拒绝而非静默损坏。
- 受控局部遮挡透视文字从双层扩展到三种可区分色轴的三平面：检测器从最多四个可靠 RGB 色轴中选择两两区域 IoU 为 1%～65%、主色距离至少 12 的最大兼容组，并保留同一可信分层组内的三个高重叠候选；完全同色样本继续安全合并，不推断深度或不可见像素。M5 standard 三层样本输出 3 个独立四角并保持阅读顺序，两两区域 IoU 为 10.7729%～54.2675%、字形遮挡率 1.8024%～25.6759%、覆盖率均为 100%、候选内文字占比 71.5116%～71.9483%、主色误差均为 1，P95 469.462 ms；同色负样本只有 1 个透视候选，P95 452.058 ms。文字检测总体 P95 687.684 ms，smoke/standard 零违规，相关 24 项专项回归及 534 项全量测试通过。
- 修复转图裁剪缺少确认入口的交互断点：进入裁剪后在画布下方显示“拖动框选 / 应用裁剪 Enter / 取消 Esc”操作条，有效选区创建后才启用应用按钮；取消只清除本次框选，不删除已写入配方的裁剪。全部纯图标 Material 按钮现在会在悬停 450 ms 后显示中文功能提示，覆盖底部 Dock、画布工具和动画控制。相关 UI、转图配方与队列回归共 87 项通过。
- 受控局部遮挡双透视文字进一步覆盖低色差不同色轴：将量化色轴合并阈值收紧到余弦相似度 0.998，每个前景像素只归属最佳色轴且最佳/次佳差至少 0.0006，最终两层主色仍须至少相差 12，防止同色抗锯齿被误拆。可靠实心主色与发光分析字芯颜色相差超过 24 时回退纯色，避免重叠暗色文字被误判为白色发光。新单元与 M5 standard 使用最大通道差 24、色轴相似度 0.996597 的两层文字；输出 2 个独立四角、覆盖率均为 100%、主色误差 1/2、尺度方向 0.019336/-0.073992，P95 296.927 ms，文字检测总体 P95 770.186 ms。smoke/standard 零违规，530 项全量测试通过；完全同色、重度遮挡、三层以上与不可见像素恢复仍未覆盖。
- 受控透视文字检测新增不同色轴局部遮挡双平面闭环：当 0° 检测把两个相交文字行合成单一宽高候选时，按各字芯相对背景的 RGB 色轴分离抗锯齿像素；只有两层都具备足够实心像素、水平跨度和密度、都能独立拟合凸透视四角，且候选 IoU 位于 8%～65% 时才替代原合并候选。主色从色轴投影最强 10% 像素估计，小尺寸强透视允许单边近水平但仍保留明显尺度变化。M5 standard 的双色中英文混排样本预期区域 IoU 10.7729%、真实字形遮挡率 6.2584%，输出 2 个独立四角并保持阅读顺序；两层覆盖率均为 100%，候选内文字占比 71.5116%/71.9483%，尺度方向 0.019344/-0.073992，主色误差 1/2，P95 308.905 ms，结果可复现。smoke/standard 零违规，529 项全量测试通过；同色、低色差、重度遮挡、三层以上和不可见文字恢复仍未覆盖。
- 受控照片式文字检测新增 JPEG 压缩鲁棒性：仅对至少 75% 空间分块仍有局部变化的 JPEG 启用高对比水平带分析，避免质量 50 的 8×8 压缩块冒充恒定字形核心；文字相对中位亮度至少相差 70、横跨至少 18% 画宽且密度至少 12%，主色从最强四分位像素估计。亮色字芯周围的持续有色环带用于反推发光色与半径，主色不一致的遮挡物回退普通字；同色多行允许压缩色漂后保持阅读顺序。0° 已稳定检出两个分离高置信区域且至少一个带透视四角时，仅继续局部角度精修，双透视 P95 为 206.011 ms。M5 standard 覆盖 JPEG 4:2:0 质量 95/75/50 的普通、发光、小字号、双行和局部遮挡共 15 个文字案例，以及 12 个跨质量/种子无字负样本；文字全部首选正确，最低重叠率 92.3217%，最差主色/发光色误差 15/5、半径误差 0 px，文字最慢 P95 63.803 ms，负样本累计 0 候选，总体文字检测 P95 702.737 ms，零违规。528 项全量测试通过；不外推到重复转码、质量低于 50、其他色度抽样或真实强遮挡。
- 受控照片式复杂背景文字检测新增排版适应与局部遮挡基线：以量化 RGB 颜色簇和高度集中的实心字形核心补充灰度定位，使用整图上限与候选区域占比共同形成尺度自适应阈值，并在严格证据存在时跳过冗余的 49 角度扫描；同主色多行候选按阅读顺序输出。M5 standard 新增小字号、顶部、底部、双行和椭圆/斜线局部遮挡 5 类案例，各重复 3 次；全部首选正确且可复现，双行 2/2 命中，主色误差 0，遮挡最低重叠率 92.3217%、其余 100%，最慢 P95 265.039 ms。原 8 个跨种子文字继续全部首选正确，4 个无字背景累计 0 候选；文字检测总体 P95 727.840 ms，零违规。527 项全量测试通过；该基线不代表 JPEG 重压缩、真实场景强遮挡或生产语料。
- 受控照片式复杂背景文字检测从单样本扩展为多场景首选候选与零误报基线：全局照片式判定新增 4×6 分块空间覆盖，只有边缘密度至少 2.5%、有效行覆盖至少 55% 且活跃分块至少 55% 时才启用后置过滤，避免把纯色背景上的 ±30° 旋转文字误判为照片纹理。照片式候选会内部保留最多 12 项，按置信度、横纵比和区域紧致度重新排序，再要求估计主色在分析图中至少占 0.8%、在候选框中至少占 7.5%，压制枝状连续边缘。单元矩阵覆盖 8 个随机种子的普通字、发光字和无字背景；M5 standard 选择 4 个代表性种子形成 8 个文字案例与 4 个负样本，各重复 3 次，8 个首选候选全部正确、最低重叠率 94.9814%、主色误差 0、发光色最大误差 3、半径最大误差 4 px，4 个无字背景共 0 候选；普通字/发光字最慢 P95 973.511/916.941 ms，无字最慢 P95 539.650 ms，总检测 P95 914.143 ms，零违规。526 项全量测试通过。该矩阵仍是确定性合成背景，不代表真实照片语义内容或生产语料。
- 受控照片式复杂背景文字检测新增专用局部化路径：只有原始边缘密度较高且有效边缘覆盖至少 55% 行时，才启用 11×11 局部对比度过滤、行密度 P80 自适应阈值、20% 列占用阈值和受限纵向安全边距，避免随机纹理与枝状边缘把整幅背景误连成文字行，同时保留纯色、平滑纹理和周期结构纹理的原路径。M5 standard 新增确定性多尺度随机纹理、八组半透明枝状自然边缘、纯色/发光文字及同背景无字负样本；两类均正确分类、区域重叠率 100%、主色误差 0，发光光色误差 1、半径误差 1 px，无字样本 0 候选，P95 为 951.251/867.106 ms，总检测 P95 920.644 ms；照片式专项采用 1,200 ms 抗抖动上限，文字检测整体继续保持 1,000 ms 上限，零违规；525 项全量测试通过。该合成基线不代表真实照片语义内容、强遮挡或生产级 OCR/字效复刻。
- 受控结构纹理背景文字分析新增局部纹理路径：候选周边的二次背景面残差 P75 达标时启用 15×15 中值背景，抑制周期性高频纹理和跨越文字的水平/垂直亮度阶跃；纯色字以高实心比例及显著数量优势保守确认，发光则继续用全局色向端点反推光色并按主文字核心校准半径。高置信主方向会过滤明显偏弱的纹理角度精修种子，仍保留最多四个真实高置信方向。M5 standard 纯色/发光两类均正确分类、区域重叠率 100%、主色误差 0，发光光色误差 3、半径误差 0 px，P95 693.673/732.529 ms，总检测 P95 747.067 ms，零违规且未放宽 1,000 ms 门槛；524 项全量测试通过。随机照片纹理、密集自然边缘、语义内容背景和强遮挡仍需生产级能力。
- 受控非均匀背景文字分析新增局部稳健二次 RGB 表面：以候选周边拟合 `x/y/xy/x²/y²` 背景并用中位残差/MAD 排除文字污染，文字颜色和发光改为相对逐像素局部背景分析；近似均匀边框保留快速路径。新增平滑光照渐变加低频纹理的纯色/发光门禁，两类均正确分类、区域重叠率 100%；纯色主色误差 0，发光主色误差 0、光色误差 8、半径误差 1 px，P95 分别为 353.158/357.630 ms，总检测 P95 764.822 ms，523 项全量测试通过。高频纹理、结构边缘和真实照片内容背景仍需生产级能力。
- 受控复杂字效新增单色扩散发光闭环：严格字效契约、离线 Provider、修订来源、撤销/重做和 Material You 均支持 `glow`，以辅助色和 1～64 px 半径生成高斯扩散光层，再绘制清晰主文字；普通、旋转、透视和逐字弧形排版共用同一真实渲染路径。检测器依据至少两层同色向半透明衰减层区别发光与描边/抗锯齿，反推光色并估计半径。M5 standard 发光替换形成 4,241 个主文字像素、10,602 个辅助色像素和 2,564 个边界外光晕像素，区域外保持率 100%，P95 34.521 ms；检测主色/光色误差均为 0、半径误差 1 px、P95 312.799 ms。多层、内发光、非均匀发光、复杂背景、纹理和三维霓虹仍未覆盖。
- 受控复杂字效新增原图参数估计：文字区域建议会为纯色、描边、右下投影和线性渐变返回严格可选的主色、辅助色及 1～64 px 强度，Material You 切换候选时自动回填并保留用户校正入口。分析框加入小幅安全边距，颜色缩放改用面积平均以避免 Lanczos 色层振铃；投影距离按外侧边界而非被主文字覆盖后的可见重心估计。M5 standard 八个旋转普通文字主色最大通道误差 15，四类字效主色误差均为 0、辅助色最大误差 2、强度最大误差 1 px，0 条违规；1440×480 的 10 px 描边缩放回归仍回填 10 px。真实照片复杂纹理、遮挡和高级艺术字仍需生产级能力。
- 受控复杂字效从分类扩展为真实替换闭环：文字草稿、离线 Provider、撤销/重做和修订来源共同记录普通/描边/投影/线性渐变、辅助色及 0～64 px 强度；Material You 新增字效、辅助色和强度控件，自动候选同步样式，四种样式共用旋转、透视和弧形排版。M5 standard 四类 Provider 样本区域外保持率均为 100%，来源与像素可复现，P95 分别为 27.251 / 29.861 / 30.371 / 26.635 ms；本条先建立用户指定参数的渲染闭环，自动估计见后续记录，发光、纹理、三维和非线性变形艺术字仍未实现。
- 离线文字区域建议新增受控复杂字效分类：在几何定位后对旋转候选先行扶正，再依据文字核心颜色层数、主色空间关系和渐变相关性，保守区分水平普通印刷体的纯色、描边、偏移投影与线性渐变；Material You 状态显示对应中文标签，并由后续字效替换闭环同步为可编辑样式。M5 standard 四类样本均正确检出、区域重叠率 100% 且可复现，P95 分别为 323.499 / 306.682 / 389.358 / 310.849 ms；发光、纹理、三维、非线性变形艺术字和艺术字体识别仍未实现。
- 本地 OCR 新增受控单行弧形文字几何校直：Windows 系统 OCR、Tesseract 与后备链共享严格 -1～+1 弧度契约，透视四角与弧度互斥；旋转校正后按二次基线标定构造反向分段网格，同时抵消纵向位移和局部切线旋转。识别结果记录 `curve` 校正来源，Material You 状态明确显示正在校直及已校直。M5 standard 的 +45%、+75%、-45%、-75% 四类中文样本输入偏差 3.973201～14.224884 px，校直后最大残差 3.061924 px、最差残差比例 30.7499%，像素可复现，整体 P95 12.587 ms；真实照片弧形识别准确率和艺术字仍待生产语料验收。
- 离线文字区域建议新增受控单行弧形普通印刷体检测：在旋转校正后以独立字形包络和 12 段后备包络拟合二次基线，只有横向覆盖、残差、弯曲量与线性偏移同时达标才同步弧度；透视四角与自动弧度互斥，手工框选会清除旧弧度。M5 standard 的 0%、+25%、+45%、+75%、-45%、-75% 六类中文样本全部检出，最低重叠率 99.3151%、最大弧度误差 7.133706%，结果可复现，单类最慢 P95 568.816 ms；原有八个直线旋转样本均保持弧度 0，OCR 前几何校直见同版本后续闭环。
- 改文字新增 -100%～+100% 手动单行弧度排版：弧度进入严格请求、修订来源、撤销/重做和再次生成链路，离线渲染按二次曲线逐字定位并随局部切线旋转；多行或越界输入失败关闭。M5 standard 的 +80% 样本生成 5,715 个有效文字像素，中心上拱 29.482505 px，选区外保持率 100%，结果可复现，P95 为 48.295 ms；自动检测和 OCR 前几何校直均已形成受控闭环，复杂艺术字仍未实现。
- 改文字新增空间分离双透视平面基线：质量筛选后把多个四边形候选按上到下、左到右稳定排序，Material You 状态明确显示“透视文字平面 1/2”，切换后对应四角会继续传入 OCR 和替换链路。M5 standard 的两个反向透视平面均独立检出，区域重叠率 100%、尺度方向差分别为 0.033513/-0.060915、结果可复现，P95 为 727.314 ms；不宣称覆盖重叠或遮挡平面。
- Windows 系统中文 OCR 增加受控复杂背景基线：修正零文字行被 PowerShell 序列化为 `null` 时误判协议损坏的问题，并在系统返回空结果时于原 30 秒总预算内执行最高 3×、最长边 2,400 px、总像素不超过 8,000 万的灰度自适应对比度增强重试；UI 明确标记增强结果。Windows 10 standard 门禁扩展到横排、旋转、竖排、透视、渐变、图案和低对比小字 7 类共 21 次，全部精确识别且可复现，低对比样本稳定走增强路径、P95 为 1,449.404 ms。
- Rebuilt the main Tkinter workbench around the selected Material You concept: lightweight top navigation, left library, central canvas, right editing panel and a centred bottom command dock.
- Added Material light/dark colour roles, surface containers, active-tool styling, rounded thumbnail selection and a custom capsule command surface.
- Kept the existing editing, annotation, export, keyboard, accessibility and Chinese-language workflows intact while making the editing panel visible by default for new profiles.
- Added an isolated visual-preview launcher and Material-specific UI regression coverage.
- Removed the English catalog, runtime language selector and persisted locale setting; the application now ships as Simplified Chinese only.
- 增加“看图 / 转图 / 改图 / 生图”四模式外壳、状态保持路由与资源修订交接；看图模式提供独立查看控制和明确的跨模式入口。
- 将功能台账扩展为 135 项四模式与全局能力，并把收藏工具、智能选区及候选切换、蒙版加入/移出/撤销/重置、框内文字识别、字体推荐、背景替换、物体擦除、候选整理、“全部功能”面板和统一任务托盘接入规范模式归属及真实功能回调。
- v2 安装预检新增真实候选完整性验证：解析 PE 头并确认应用/便携版为 AMD64，校验 CycloneDX 1.6 的 2.0.0 应用身份，并重新计算 `SHA256SUMS.txt`。安装生命周期不再用同版本覆盖冒充升级，而是执行 1.1.3→2.0.0、目标版修复安装、保留数据卸载和清除数据卸载三轮闭环，输出绑定两个安装器 SHA-256 的机器可读证据；无真实候选时继续失败关闭。
- 离线改文字扩展到受控近竖排和竖排普通印刷体：80°候选自动同步角度，精确90°保留 +90°/-90°两个阅读方向并在 UI 中明确切换；本地 OCR 先旋转成长横向裁切，替换渲染使用转置排版面避免窄竖框裁字。M5 standard 的 0°、±30°、80°、90°五样本全部检出，最低重叠率 96.463%；竖排替换产生 3,026 个有效红色文字像素、纵向长宽比 9.46875且选区外像素 100% 不变。
- 普通印刷体旋转检测从少数固定角度升级为 -90°～+90°全范围两阶段搜索：10°粗搜选择最多4个互异方向，再以1°步长局部精修。新增17°、58°、-83°非粗搜样本，M5 standard 八角度全部检出，最大角度误差1°、最低区域重叠率95.9002%、检测P95为573.398 ms，精确竖排正反方向继续同时保留。
- 智能选区新增受控异色相接/可见重叠对象分离：始终保留完整连通主体作为第一顺位安全候选，并在颜色距离、面积、紧致度与质心间距达标时提供明确标记的“颜色分离”子候选；子候选复用像素蒙版、画笔精修和擦除流程。M5 standard 新样本稳定返回 1 个合并候选与 2 个互斥子候选，结果可复现，智能选区总体 P95 为 102.534 ms；同色深度重叠、被遮挡像素恢复与生产语义分割仍不在本基线范围。
- 智能选区新增同色细颈相接形状分离：使用可见轮廓的局部厚度峰、细颈证据和确定性测地扩张生成互斥子蒙版，同时继续把完整主体放在第一顺位；“形状分离”候选复用现有蒙版精修与擦除流程。单圆和单圆角矩形不误分，M5 standard 新样本稳定返回 1 个合并候选与 2 个形状候选，六类智能选区总体 P95 为 135.844 ms；深度重叠、遮挡和不可见像素恢复仍不在范围内。
- 同色形状分离扩展到受控中等宽接触：峰值抑制同时约束峰高与间距，鞍深阈值可连续处理直径约 96 像素对象最多约 29 像素可见重叠；更深重叠失败关闭为完整候选。真实 UI 样本升级后仍只修改选中对象。M5 standard 扩展为 7 类样本，新场景返回 2 个互斥形状候选，IoU 85.5439%，总体 P95 为 124.032 ms。
- 主体像素蒙版新增本地画笔精修：加入蒙版可补回漏选区域，移出蒙版可保护误选像素，笔划支持顺序重放、逐条撤销和恢复当前候选的自动蒙版；切换候选、手工框选、取消或换图会隔离旧笔划。最终请求绑定精修后蒙版并记录笔划数量，修订血缘只保留蒙版 SHA-256。M5 standard 受控样本的手工移出像素保持率、蒙版外保持率及未选主体保持率均为 100%，P95 为 21.986 ms。
- 智能选区候选新增有界压缩的像素级二值蒙版：画布以 Material You 强调色半透明叠层预览，保持自动框时 Provider 只修复蒙版内像素；拖动边框会明确清除蒙版并回退矩形修复。修订血缘只记录蒙版 SHA-256，不保存原始载荷。M5 standard 双主体样本的所选像素变化率、蒙版外保持率及未选主体保持率均为 100%，结果可复现；本轮包含画笔精修后的 P95 为 21.986 ms。
- 智能选区从单一最大主体扩展为最多 5 个按像素面积稳定排序的离线候选；“下一个主体”可循环切换并更新同一画布校正框。选择第二个对象执行修复时，第一对象和源文件保持不变。M5 新增双主体真实样本，稳定返回 2 个候选；四类样本覆盖率均为 100%，最低 IoU 82.84%，本轮总体 P95 为 45.375 ms。
- 物体擦除新增完全离线的智能选区：复用去背景的整圈边框主色分析，对不透明图按自适应对比度提取显著主体，对透明图直接使用 Alpha 轮廓；结果作为可拖动校正的画布边框，不在用户执行修复前创建修订。分析支持取消、换图隔离和平坦图片手工回退。
- 改文字新增完全离线的相似字体推荐：依据框选原文字像素、已识别或填写的原文字及旋转角度，对本机中文候选字体进行归一化轮廓比较和稳定排序；后台运行、可取消、换图隔离，推荐只回填字体选择，用户确认前不创建新修订。M5 standard 中微软雅黑与宋体两个真实渲染样本 Top-1 全命中，结果可复现，本轮整体 P95 为 40.633 ms。
- 改图模式新增物体擦除与局部修复受控基线：画布框选和边框校正、0～64 px 羽化、四周边界双向插值、异步任务、不可变子修订、取消/换图隔离、结果对比和转图交接形成闭环；M5 门禁量化区域误差、Alpha、区域外隔离、确定性和 P95。
- 改图透明结果新增纯色与本地自定义图片背景替换：异步合成不透明 PNG，以透明修订创建不可变分支，记录底色和背景 SHA-256，支持取消、换图隔离及转图交接；M5 门禁新增真实像素、Alpha、确定性、哈希和 P95 验证。
- 增加版本化转图配方、严格 JSON、按步骤顺序执行器，以及离线模拟的生图任务排队、取消和候选交接。
- 增加真正的转图队列工作区、配方导入导出、任务状态、开始/取消和 1,000 文件性能可靠性门禁，同时保留原裁切、尺寸、格式和中央预览入口。
- 看图模式增加不同文件双图比较、动画帧进度和 0.5×～2× 倍速；正式查看门禁覆盖 24 MP 首图、预取切图、500 图目录和内存增长。
- 完成看图模式 10%～800% 连续锚点缩放和直接打开文件夹；完成转图独立草稿、检查点恢复、失败重试、结果定位、动画保留/展平与透明背景策略。
- 增加 24/50 MP、混合格式、透明图、动画和 100%～200% DPI 的转图保真门禁，并将 1,000 行队列选中/切换优化为索引和局部更新。
- 增加 SQLite 持久素材库、内容寻址托管文件、真实 SHA-256、父修订树与重启身份恢复；转图可立即入队并在后台登记完成后原位补齐资源身份。
- 增加持久异步 JobManager、Provider 授权/费用门禁和 DPAPI 凭据存储；离线生图现生成真实 PNG，安全解码后作为托管修订进入资源库。
- 改图模式新增“智能”页和 2×/4× 本地高清；保留真实源像素与 Alpha，结果作为源素材的托管子修订保存，并可与原图并排对比、放弃候选或发送到转图。
- 改图模式接入离线确定性去背景基线：四角背景采样、可调容差和边缘羽化生成透明 PNG，并复用高清流程的后台任务、托管子修订、结果放弃和转图交接能力。
- 去背景结果新增保留主体与删除背景边缘画笔：画布显示有序遮罩笔划，应用后在真实 RGBA 像素上更新 Alpha，并以当前透明结果为父节点创建可继续细化、可发送到转图的新修订。
- 智能任务稳定性门禁新增重启恢复、未知状态不自动重放、取消与晚到成功回包竞态，以及请求/Provider/源修订/操作血缘校验；损坏、超限、错误 MIME 和跨资产结果均在入库前原子拒绝。
- 新增 M5 智能改图独立 smoke/standard 质量门禁：使用真实 PNG 量化 2×/4× RGBA 与 Alpha、三种纯色背景分离、保留/删除笔划、改文字区域内外像素、确定性和 P95 性能，并保存机器可读原始结果。
- 改图模式接入手工校正改文字基线：画布框选原文字后采样修复背景，以中文字体重新排版；支持字号、颜色、对齐、旋转、参数撤销/重做和二次生成，并在换图时取消旧智能任务，避免结果串图。
- 生图模式从单张表单卡片扩展为独立候选工作台：新增两列缩略图网格、候选选择、持久提示词/候选历史，以及打开查看、发送到改图和发送到转图；重启恢复与跨模式交接均保留真实 AssetId/RevisionId。
- 生图模式新增负面提示词和单参考图：可使用当前素材或异步登记本地图片，设置内容/风格/构图角色与强度；请求指纹绑定参考 RevisionId 和 SHA-256，离线 Provider 读取真实像素生成候选，并跨重启恢复精确参考身份。
- 生图模式新增固定种子与摄影质感、插画、水彩、水墨、像素艺术中文风格预设；两者参与能力协商、请求指纹和来源记录，相同参数与固定种子的独立任务可复现相同像素。
- 生图页重排为左侧参数、右侧候选的响应式双栏；新增 Provider 目录、服务状态和费用元数据，联网参考图上传与付费执行分别逐次确认，失败或状态未知任务仅允许用户显式以新请求身份重试，并保留限流与内容策略拒绝语义。
- 生图候选新增“基于所选生成变体”：变体能力需由 Provider 明确声明，来源绑定所选 RevisionId/SHA-256，继承提示词、负面提示词与风格并派生固定种子；新结果使用独立 Asset/Revision，原候选继续保留。
- 生图参考图从单张扩展为最多四张：新增圆角工作区内的参考列表、添加当前图/本地图片、逐张移除和清空；每张独立保存内容/风格/构图角色及强度，完整参数进入请求指纹、上传授权、来源记录、离线像素合成和跨重启恢复，同时保持旧单参考图设置兼容。
- 新增 M6 生图独立 smoke/standard 技术质量门禁，可显式注入任意 AIProvider 与声明模型；使用真实 PNG 量化固定种子复现、同批候选唯一性、非退化输出、四参考图权重像素、逐项指纹与来源、候选变体血缘及 P95 性能，并明确不以离线结果冒充提示词语义或生产模型审美质量。
- 生图候选新增持久收藏、星标和“仅看收藏”，收藏可独立于最近 12 项历史跨重启恢复；“删除候选”会清理工作区、收藏、选择与交接状态，但保留受管修订和任务历史用于来源追溯。
- 新增独立的 Material You 统一任务托盘：聚合转图批次、智能改图和生图 Job，显示状态、进度与详情，并将前往、取消、重试、清理完成路由到原有控制器；本地历史可跨重启恢复，未结束任务安全收敛为未知且不自动重放。
- 启动 M7 发布硬化：新增 15 项 v2.0 四模式发布门禁和失败关闭校验器；已通过项必须引用仓库内真实证据，未选生产 Provider、隐私费用、兼容性、安装升级、签名扫描及许可会继续明确阻止发布。
- 新增四模式 smoke/standard 发布级长稳门禁：连续验证真实图片加载、Asset/Revision 模式交接、批量转换和损坏输入隔离、改图/生图成功与取消、Provider 故障隔离、任务及托管资源重启恢复和 RSS 趋势；标准档已关闭 V2-STABILITY。
- 新增 v2.0.0 安装候选失败关闭预检：核对源码版本、目标发布文档、锁定工具链、PyInstaller/Inno Setup、SBOM/校验和、升级退出、保留/清除用户数据、生产签名、回滚包和候选产物；当前 1.1.3 开发树不会被误报为 v2 安装就绪。
- 构建脚本改为按主版本选择发布证据与校验器，并向 Inno Setup 注入对应版本发布说明；补齐 v2.0.0 候选发布说明和下载页，同时保持 1.x 原有生产签名与回滚流程兼容。
- 新增 Windows 兼容性实机探针：记录系统构建、架构、DPI API 和显示信息，在 100%～200% 模拟缩放下逐模式验证 Material 布局、统一任务托盘、中文字体、抗锯齿圆角及窗口裁剪；Windows 10 当前单元通过，Windows 11 和物理混合 DPI 继续保持外部门禁。
- 主窗口在检测到 DPI 变化后会重新约束到当前显示器工作区；兼容性探针新增 1,000 次虚拟多屏移窗，覆盖左侧/上方负坐标、右侧 200% DPI 和 800×600 小工作区，防止窗口保存或恢复到屏幕外。
- 新增 v2 外部兼容性证据契约、待填写模板和严格导入器：冻结五个 Windows 10/11 场景，校验实机条件、安装版/便携版、三轮生命周期及三类证据文件 SHA-256；缺失、重复、越界或哈希不符全部失败关闭。
- 新增 v2 生产 Provider 选型契约、待填写模板和严格校验器：分别冻结智能改图与生图能力，校验官方 HTTPS 资料、处理区域、留存、训练用途、费用上限和五类沙盒证据 SHA-256；缺候选、隐私越界、伪造证据或未经人工批准全部失败关闭，系统不会自动选择付费服务商。
- v2 总发布审计新增 Provider 状态强依赖，三个 AI 门禁无法用手工 `passed` 绕过未通过的选型状态；构建流程会先用注入的真实证据重新生成 Provider 状态，生产及生产候选构建不允许阻塞豁免，再继续安装预检与总发布审计。
- v2 兼容性与安装升级门禁新增同等强依赖：构建会按 Provider、五场景外部兼容性、安装预检、总审计的固定顺序重新生成状态；总审计要求对应状态结构、版本、阻塞计数和通过结果一致，旧报告或单独修改门禁状态无法放行。
- 新增 v2 供应链安全契约、扫描模板、多引擎复核模板和严格校验器：对应用主程序、便携包、安装包与回滚包重算哈希和大小，交叉验证批准发布者指纹、可信时间戳、Defender 病毒库/逐文件结果及人工多引擎记录；构建生成状态成为 `V2-SIGN-MALWARE` 强依赖。
- 新增 v2 许可授权契约、待填写模板和严格校验器：独立验证第一方权属/商业再分发、第三方依赖再分发审查及 Inno Setup 商业许可，要求完整范围、有效期、人工批准和哈希文件；构建生成状态成为 `V2-LICENSE` 强依赖，当前无权利授予的 LICENSE 不会被误用。
- 改图“替换图片文字”新增可选本地框内内容识别：安全调用本机 Tesseract 中文/英文语言包，解析逐行文字和置信度并填入可编辑输入框；未安装、语言缺失、失败、空结果和换图晚到结果全部回退现有手工流程，且不上传图片。
- 改文字新增完全离线的文字行区域建议：后台分析局部对比度和连通行，提供最多 5 个可切换候选并映射到原图画布继续手工校正；检测失败回退手工框选，换图作废旧结果，不上传图片。
- 离线文字区域建议先扩展为横排及 -45°～+45° 受控角度普通印刷体检测，随后继续覆盖80°近竖排及±90°方向候选；候选角度自动同步到替换排版，本地 Tesseract 识别会先反向校正旋转裁切，M5 质量门禁现包含横排、±30°、80°和90°真实像素样本。
- 离线去背景从四角平均升级为整圈边框主色家族估计，并按背景自身色差自适应容差；主体覆盖整条边和两个角时仍可分离，原始半透明 Alpha 继续参与合成。M5 门禁新增缓慢渐变背景下的不透明及 Alpha=128 贴边主体样本。
- 恢复可见的收藏工具栏，并将当前用户界面中的工作台、色域和离线服务说明统一为中文。
- 底部“全部功能”不再弹出传统菜单栏，而是打开支持 Ctrl+K、中文搜索、模块筛选、键盘选择和真实回调执行的 Material You 命令面板；135 项功能全部可检索，禁用能力可见但不会误执行。

## 1.1.3 — 2026-08-04

- Fixed the zoom and other combo-box drop-downs reporting callback errors when Tcl maps
  their internal popdown windows.
- Added a real Tk regression test that opens the zoom list, selects Fit and verifies that
  the view resets without any callback exception.

## 1.1.2 — 2026-08-04

- Added throttled, non-committing preview timelines so annotations follow the pointer while they are created, moved or resized.
- Kept drag interactions as a single undoable command on mouse release and discarded stale transient frames safely.
- Improved the immediate Canvas guide with real arrowheads and annotation-relative stroke widths.
- Preserved arrow direction when resizing arrows that were originally drawn in reverse directions.

## 1.1.1 — 2026-08-04

- Reworked split comparison to show complete before/after images in equal panels without aspect-ratio distortion.
- Made Original/50%/25% size presets idempotent and based on the rotated/cropped content before its resize stage.
- Fixed crashes from a one-point freehand preview and from preview images bound to the wrong Tk interpreter.
- Fixed empty-state batch startup, quick-export dirty-state loss, stale file actions after a failed load, and thumbnail synchronization after project/clipboard/session activation.
- Prevented global paste/apply/compare shortcuts from stealing input in text fields.
- Hardened corrupted settings, crash-record acknowledgement, temporary image lifetimes, preview callback shutdown and benchmark service cleanup.
- Refreshed the final SHA-256 manifest after release-gate generation so every generated release report is covered.
- Completed the v1.1.1 functional audit: Ruff, Mypy, 144 tests, performance smoke and quick soak all pass.

## 1.1.0 — 2026-08-04

- Added a left thumbnail sidebar for the current directory or explicit multi-file browsing sequence.
- Kept thumbnail decoding off the Tk thread and virtualized the list so only visible rows plus a small buffer are rendered.
- Added current-image highlighting, click-to-open with unsaved-edit protection, automatic scroll tracking and synchronization after rename, move or recycle-bin deletion.
- Added a persistent visibility preference, Ctrl+B toggle, dark/light theme colours, bilingual labels and an accessible region name.

## 1.0.0 — pending production gates

- Added a fail-closed production build mode that requires a timestamped Authenticode publisher, protected update-manifest key and signed previous-version rollback installer.
- Added a two-stage draft-to-public release process so external malware review validates the exact bytes that are ultimately published.
- Added stable-channel rollback metadata, release-page content, Windows default-app help, a 72-hour monitoring plan and an emergency hotfix runbook.
- Public `v1.0.0` remains blocked until the machine-readable rights, Beta, seven-day stability, clean-VM, signing, malware and rollback gates are closed with evidence.

## 0.9.0-rc.1 — 2026-08-04

### Release-candidate quality

- Froze features and added machine-readable RC gates, a seven-day stability ledger, Windows 10/11 compatibility matrix and disposable-profile lifecycle requirements.
- Added repeatable startup, first-image, switching, zoom, export, batch and RSS measurements plus a same-machine 15% regression gate against the accepted v0.8.0 baseline.
- Added configurable 20,000-switch and 1,000-item batch soak testing with bounded-memory and injected damaged/missing-file checks.
- Released stale preview frames and owned intermediate images deterministically to reduce long-session memory retention.
- Added local Defender scanning, final Authenticode inspection, source-to-artifact provenance, PyInstaller diagnostic evidence and fail-closed RC verification.
- Completed the user manual and updated privacy, release, licence and third-party inventory references for 0.9.0.

### Remaining external gates

- Public release remains blocked by publisher code signing, first-party rights confirmation, the 20-user Beta minimum, seven consecutive P0/P1-free days, Windows 10/11 clean-VM evidence and approved multi-engine false-positive review.

## 0.8.0 — 2026-08-04

### Beta release system

- Added stable and Beta channels with user-initiated checks only; signed manifests, declared size, SHA-256 and trusted Windows Authenticode publisher verification all fail closed.
- Added atomic update downloads, a cache of the previous verified installer and an explicit rollback action.
- Added versioned settings migration that preserves recent files and unknown fields, with a pre-migration backup.
- Added rotating local diagnostics, crash recovery records, privacy redaction and user-initiated ZIP report export; anonymous upload remains off by default and requires both saved consent and per-report confirmation.
- Added uninstall-time choice to preserve or clear settings, sessions, logs, update cache and batch state.
- Added deterministic CycloneDX 1.6 SBOM generation, release SHA-256 coverage, Beta release notes, known issues, feedback template and P0/P1 ownership/closure register.
- Added release signing gates and dual-channel manifest generation when the Authenticode tool, certificate thumbprint and protected manifest private key are configured.

### Validation

- 113 automated tests pass, including signature tampering, channel mismatch, interrupted download, hash mismatch, publisher rejection, rollback, redaction, dual-consent and legacy migration cases.
- Unsigned local builds carry `SIGNING_REQUIRED.txt` and cannot produce update-channel manifests; public Beta remains blocked until the final binaries have a trusted publisher signature.

## 0.7.0 — 2026-08-04

### Experience and accessibility

- Added light, dark and system-following themes backed by shared colour, spacing, focus and state tokens.
- Enabled Windows Per-Monitor V2 DPI awareness, live Tk scaling refresh and monitor-work-area geometry clamping for 100%, 125%, 150% and 200% DPI configurations.
- Added F6/Shift+F6 region navigation, visible focus styling, accessible names for the canvas/toolbars/options/status areas and keyboard-safe handling that no longer steals navigation keys from text inputs.
- Added a searchable keyboard-reference window, first-use guide, keyboard-operable empty state and actionable retry/choose/reveal recovery for failed image loads.
- Fixed a native `WM_DROPFILES` crash by queuing dropped paths in the Win32 callback and dispatching them later from the Tk event loop instead of calling Tk while its main loop has released the Python GIL.

### Localisation and settings

- Moved Simplified Chinese and English UI resources to external UTF-8 JSON catalogs and added live language switching without reinstalling or restarting.
- Added an appearance/language, performance/cache, saving/metadata, privacy, update and file-association settings center.
- Kept the entire core application offline-capable, prohibited background uploads and automatic network requests, and shipped a bilingual privacy statement.
- Added both locale catalogs to the installed `onedir` and portable `onefile` PyInstaller recipes.

### Validation

- Added catalog parity and reversible-switch tests, four DPI-scale and negative-coordinate multi-monitor geometry tests, explicit offline-policy checks, packaged-resource checks and a real Tk settings/accessibility smoke test.
- Added a real synthetic `WM_DROPFILES` regression test with a Unicode path; the process remains alive and the path reaches the Tk callback.
- Verified the English main window, menus, status values and keyboard-help surface contain no remaining Chinese fragments after a live switch.

## 0.6.0 — 2026-08-04

### Formats and animation

- Enabled AVIF open, convert and export through the AVIF codec already present in the locked Pillow 11.3.0 Windows wheel.
- Added runtime discovery for the optional `pillow-heif` HEIF/HEIC decoder with a separate lock file; the base installer remains free of that dependency.
- Added background multi-frame GIF/APNG/WebP playback and an explicit animated export workflow for GIF, APNG and WebP.
- Registered AVIF, APNG and optional HEIF/HEIC extensions as Windows candidate default-app formats.

### Persistent batch processing

- Added a versioned batch manifest with pending, running, succeeded, failed and cancelled states; interrupted running tasks recover as pending.
- Added configurable concurrency and a weighted memory budget, cancellation, failed-item retry, whole-job rerun and atomic progress checkpoints.
- Added versioned import/export presets, rename templates with name/stem/extension/index/date variables, directory-tree preservation and explicit error/overwrite/unique conflict policies.
- Added metadata cleaning, resize, watermark and format-conversion recipes, same-file overwrite protection, CSV/JSON error reports, completion notifications and output-folder actions.
- Isolated read-only, damaged, oversized and same-name inputs so one task never blocks the remaining queue.

### Validation

- 98 automated tests pass, including real AVIF round trips, three animation formats, preset schema gates, cancellation/recovery/retry and a 1,003-file mixed-format stress test.
- The stress test completed 1,001 valid inputs while isolating one damaged and one oversized input; active image reservations remained within a configured 32 MB budget.
- A real Tk smoke test played a three-frame animation and completed six UI-launched AVIF batch tasks without cross-thread Tk calls.

## 0.5.0 — 2026-08-04

### Editable annotations and privacy tools

- Added non-destructive text, arrow, line, rectangle, ellipse, freehand and numbered-marker objects.
- Added local mosaic, Gaussian blur and solid-redaction objects; effects remain bounded to their selected regions and undo restores the original pixels.
- Added topmost object selection, move, four-corner resize, colour, fill, stroke, opacity, text, font-size and effect-strength controls.
- Persisted every annotation object and update command in `.lieproj` projects and abnormal-exit sessions so objects remain editable across launches.

### High-frequency workflows

- Added free-form `width:height` crop ratios, interactive four-corner perspective correction and four-sided canvas expansion with a configurable background.
- Added configurable favourite actions and keyboard shortcuts with duplicate and syntax validation.
- Added recent project and export-location menus, remembered export destinations and optional startup restoration of the latest project workspace.
- Added DPI-aware selection handles and hit regions; verified 16-pixel targets at 200% DPI.

### Technical decision gate

- Tkinter remains the UI framework through v1.0: 100 vector objects render at a 41.3 ms median and 10 local effects at a 118.3 ms median on the development machine, while 200% DPI create/move interactions pass.
- A framework migration remains a separate future project if later accessibility or canvas-performance evidence fails the documented gates.

### Validation

- 90 automated tests pass, including project editability, local-effect boundaries, undo restoration, custom crop validation, 200% DPI hit sizing, perspective/canvas rendering and a 100-object performance gate.
- Real Tk smoke tests pass object creation, selection, move, style update, undo/redo and 200% DPI canvas interaction.

## 0.4.0 — 2026-08-04

### Non-destructive editing core

- Added a serializable command timeline with deterministic stage ordering: geometry, crop, colour, annotation, size and export.
- Added stable undo/redo for 100+ operations, branch truncation, slider-command merging and direct jumps from the history panel.
- Added one renderer shared by preview, export, size estimation and clipboard copy so full-resolution preview/export pixels stay aligned.
- Added fine rotation with optional automatic edge crop, projection-based automatic straighten, histogram analysis and before/after split comparison.
- Added levels, per-channel RGB curves, colour temperature, tint, shadows, highlights, bounded sharpening and edge-preserving denoise.

### Persistent projects

- Added versioned `.lieproj` JSON projects with external source references, relative-path fallback, source SHA-256 validation and atomic writes.
- Added schema migration for legacy document state, tolerant unknown top-level fields and safe read-only opening for unsupported future commands.
- Added timeline-aware crash recovery and project-file registration in the Windows installer.

### Validation

- 79 automated tests pass, including 100-operation undo/redo, project round trips and migration, unknown-field compatibility, auto-straighten confidence, histogram counts and preview/export pixel identity.

## 0.3.0 — 2026-08-04

### Fidelity and document model

- Added a single `Document` model for edit state, crop/resize/export state, operation history, dirty tracking, source metadata and source identity.
- Normalized all eight EXIF Orientation values on decode and removed Orientation on export to prevent repeated rotation.
- Preserved ICC, EXIF, DPI and XMP where the target Pillow format supports them; added ICC-to-sRGB conversion for display previews without mutating source pixels or profiles.
- Added byte-exact original-quality passthrough for unchanged same-format files, including animated APNG and WebP.
- Removed implicit default resizing so an unchanged export is not needlessly resampled.

### Safe editing and recovery

- Added same-directory atomic replacement, explicit conflict policies and optional timestamp-preserving `.bak` backups before overwrite.
- Added transparent-to-opaque and animated-to-single-frame confirmation gates.
- Added GIF/APNG/WebP animation detection and visible frame-count labels.
- Added transactional two-file autosave generations and startup recovery, including recovery after source deletion.
- Added source fingerprints and live detection of external modification, rename and deletion; stale-source overwrite is blocked.
- Added decode limits for decompression bombs, pixel/dimension limits, animation frame count, truncation and unsupported formats.
- Added social-media, avatar, email, original-quality and custom export presets.
- Added GIF to the Windows candidate default-app registrations.

### Validation

- 68 automated tests pass, including eight orientation golden cases, sRGB and Display P3 preview conversion, ICC/EXIF/DPI/XMP round trips, GIF/APNG/WebP animation guards, interrupted atomic writes, source conflicts, crash recovery and malformed input boundaries.
- A real Tk UI smoke test passed asynchronous open, `Document` binding, dirty tracking and autosave.

## 0.2.0 — 2026-08-03

### Default viewer

- Added per-user single-instance coordination using a named mutex and authenticated local pipe.
- Added multi-path activation, deterministic natural folder order, previous/next navigation and adjacent-image prefetch.
- Added a default compact viewer mode with an optional editor panel.
- Added fullscreen, immersive viewing, configurable slideshow and loop playback.
- Added native Windows drag-and-drop, recent directories, fit/actual-size viewing and window geometry persistence.
- Added background decoding and preview rendering; damaged or unsupported files no longer block navigation.
- Added file rename, copy, move, Explorer reveal and Recycle Bin-only deletion.
- Added format, dimensions, colour mode, file size, dates and basic EXIF properties.

### Installation and release

- Changed the installed application to PyInstaller `onedir`; retained a portable `onefile` build.
- Replaced the legacy custom installer with a standard per-user Inno Setup installer and Apps & Features uninstall entry.
- Added optional shortcuts, an image context menu and Windows candidate-default-app registration.
- Added IPC-coordinated upgrade/uninstall exit with save, discard and cancel handling; removed forced process termination.
- Added deterministic product/version metadata, recursive SHA-256 manifests and pinned installer compiler verification.

### Validation

- 49 automated tests pass, including 500-file ordering, 20 activation redirects and real unsaved-upgrade cancellation.
- Packaged EXE validation: 20 launches produced one main process and displayed the final activation.
- Installer validation: three fresh installs, three overwrite upgrades and three uninstalls completed without application file or registry residue; the final documentation rebuild also passed one complete cycle.
- Standard benchmark on the development machine: startup median 597 ms, 48 MP first-visible preview median 1,443 ms, switch median 117 ms (three rounds).

### Release blockers

- Imported upstream source still lacks a verified redistribution licence; public or commercial distribution remains blocked.
- The current Inno Setup 7 compiler is licensed for non-commercial use; commercial builds require a separate Inno Setup licence.
- Executables are not code-signed. Obtain a trusted signing certificate and timestamp release artifacts before public distribution.
