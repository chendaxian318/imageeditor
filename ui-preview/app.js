const themes = [
  { id: "slate", layout: "pro-three", layoutName: "三栏专业工作台", name: "岩灰工作室", desc: "图库、画布、参数同时在场", keywords: "冷静 / 精准 / 长时间工作", note: "三栏并行呈现图库、画布与参数，强调画布本身，适合作为默认专业版方向。", colors: ["#8bb7ff", "#171a21"] },
  { id: "nordic", layout: "nordic-filmstrip", layoutName: "左侧调整 + 底部胶片带", name: "北欧纸感", desc: "浏览与调整动线更自然", keywords: "自然 / 轻盈 / 低压", note: "参数放在左侧，缩略图改为底部横向胶片带，中央画布获得更宽的浏览空间。", colors: ["#416f5d", "#f8f6f0"] },
  { id: "obsidian", layout: "focus-dock", layoutName: "垂直工具坞 + 专注画布", name: "黑曜专业版", desc: "隐藏图库，突出高频专业操作", keywords: "极暗 / 高效 / 专注", note: "工具栏收为左侧垂直工具坞，图库默认隐藏，仅保留画布和精密参数面板。", colors: ["#d6ff4b", "#0d0f11"] },
  { id: "aurora", layout: "glass-overlay", layoutName: "双侧悬浮玻璃面板", name: "极光玻璃", desc: "画布通铺，面板悬浮其上", keywords: "未来 / 通透 / 氛围", note: "画布铺满工作区，图库与参数变为可收纳的悬浮玻璃卡片，弱化传统软件框架。", colors: ["#a993ff", "#10183e"] },
  { id: "bauhaus", layout: "reverse-lab", layoutName: "参数左置 + 图库右置", name: "包豪斯信号", desc: "通过对向分区形成操作节奏", keywords: "几何 / 原色 / 识别度", note: "调整区移到左侧，图库变成右侧窄栏，形成与默认方案完全相反的操作路径。", colors: ["#e43c2f", "#f2ecdd"] },
  { id: "swiss", layout: "swiss-filmstrip", layoutName: "主画布 + 底部资产索引", name: "瑞士网格", desc: "用横向索引提升批量挑片效率", keywords: "网格 / 理性 / 信息密度", note: "右侧保留参数，底部使用紧凑横向资产索引，适合大量照片的筛选和统一调色。", colors: ["#ed2d2d", "#ffffff"] },
  { id: "terminal", layout: "terminal-console", layoutName: "画布 + 参数控制台", name: "复古终端", desc: "命令式双栏，不展示视觉滤镜", keywords: "极客 / 复古 / 命令感", note: "移除图库和滤镜缩略图，只呈现图像结果与参数控制台，强调键盘驱动的极客工作流。", colors: ["#55ff94", "#06170f"] },
  { id: "clay", layout: "clay-cards", layoutName: "单画布 + 浮动调节卡", name: "柔和黏土", desc: "核心功能收进柔软悬浮卡片", keywords: "柔软 / 亲和 / 触感", note: "图库默认收起，滤镜和参数以右侧浮动卡出现，减少第一次使用时的视觉负担。", colors: ["#c87568", "#eeeae7"] },
  { id: "brutal", layout: "brutal-reverse", layoutName: "左右反转创作台", name: "新粗野主义", desc: "参数在左，素材在右", keywords: "大胆 / 年轻 / 反精致", note: "用粗暴直接的左右反转布局区分素材与创作参数，面向快速内容生产。", colors: ["#ff4c8b", "#f1db55"] },
  { id: "ink", layout: "zen-drawer", layoutName: "沉浸画布 + 底部抽屉", name: "禅意水墨", desc: "默认只看作品，需要时展开调整", keywords: "东方 / 安静 / 留白", note: "隐藏图库，参数面板变成底部横向抽屉，让画面在绝大多数时间保持完整和安静。", colors: ["#bb493f", "#f3f0e7"] },
  { id: "sunset", layout: "creator-overlay", layoutName: "素材栏 + 创作者悬浮面板", name: "日落创作台", desc: "左侧选图，右侧快速套用风格", keywords: "温暖 / 创意 / 亲密", note: "保留素材栏，常用风格和参数在画布右侧悬浮，适合内容创作者快速出片。", colors: ["#ff8b63", "#2c162b"] },
  { id: "forest", layout: "forest-dock", layoutName: "纵向工具坞 + 双侧资源", name: "森林实验室", desc: "高频工具沿左边缘纵向排列", keywords: "深绿 / 稳定 / 自然", note: "把全局命令收为垂直工具坞，同时保留图库与参数，适合宽屏上的重度工作流。", colors: ["#83c88f", "#111d18"] },
  { id: "lavender", layout: "flow-bottom", layoutName: "图库左置 + 横向调整台", name: "薰衣草流", desc: "滑杆在底部横向展开", keywords: "优雅 / 轻松 / 友好", note: "顶部专注选图与预览，参数滑杆横向铺在底部，更接近轻量美化应用的使用习惯。", colors: ["#7557c7", "#f8f5fd"] },
  { id: "copper", layout: "workshop-reverse", layoutName: "工坊式反向三栏", name: "铜色工坊", desc: "先参数、后画布、再底片", keywords: "质感 / 工具 / 成熟", note: "按照参数—画布—底片的顺序组织三栏，像传统暗房工作台，适合摄影师。", colors: ["#d88b52", "#251b15"] },
  { id: "film", layout: "darkroom-filmstrip", layoutName: "暗房画布 + 接触印样带", name: "单色胶片", desc: "底部胶片带模拟暗房挑片", keywords: "胶片 / 纯粹 / 影像", note: "中央暗房画布配底部接触印样带，参数固定在右侧，强化连续挑片与冲印感。", colors: ["#f3f3f0", "#1b1b1b"] },
  { id: "chrome", layout: "classic-suite", layoutName: "经典桌面软件套件", name: "千禧铬光", desc: "标题栏、命令栏和功能区层层展开", keywords: "复古未来 / 金属 / 数字", note: "保留完整标题栏与双层命令感，三栏紧密排布，复现经典桌面创作软件的信息密度。", colors: ["#315cff", "#dbe4f7"] },
  { id: "material", layout: "material-dock", layoutName: "底部胶囊命令坞", name: "个性化圆润", desc: "四模式常驻，命令靠近底部操作区", keywords: "圆润 / 系统化 / 易用", note: "看图、转图、改图和生图拥有常驻一级入口；看图时隐藏参数面板，并保留收藏工具和底部胶囊命令。", colors: ["#67507a", "#f7f2fa"] },
  { id: "blueprint", layout: "blueprint-reverse", layoutName: "参数先行工程视图", name: "蓝图工程", desc: "尺寸与参数获得最高优先级", keywords: "工程 / 参数 / 精密", note: "左侧参数面板优先，右侧资源栏用于数据集切换，符合工程与批处理思维。", colors: ["#66c5ff", "#0b2541"] },
  { id: "editorial", layout: "editorial-contact", layoutName: "顶部接触印样 + 左侧编辑", name: "编辑工坊", desc: "像编辑台一样先选片再精修", keywords: "编辑感 / 高级 / 作品导向", note: "图库成为顶部横向接触印样，左侧为编辑参数，画布像杂志版面一样占据主体。", colors: ["#1c5145", "#fbfaf7"] },
  { id: "fluent", layout: "fluent-rail", layoutName: "视窗折叠资源轨", name: "流畅视窗", desc: "资源栏收窄，保持熟悉的原生结构", keywords: "原生 / 熟悉 / 稳妥", note: "图库压缩成图标式资源轨，仍保留系统用户熟悉的顶部命令栏与右侧属性面板。", colors: ["#0067c0", "#eef5fb"] }
];

const icons = {
  folder: '<path d="M3 5.5h5l1.6 2H21v10.5H3z"/><path d="M3 8h18"/>',
  save: '<path d="M5 3h12l3 3v15H4V3z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>',
  export: '<path d="M12 15V3m0 0L7.5 7.5M12 3l4.5 4.5"/><path d="M5 12v9h14v-9"/>',
  undo: '<path d="M9 7H4v-5"/><path d="M4.5 7A9 9 0 1 1 5 18"/>',
  redo: '<path d="M15 7h5v-5"/><path d="M19.5 7A9 9 0 1 0 19 18"/>',
  crop: '<path d="M6 2v15a2 2 0 0 0 2 2h14M2 6h15a2 2 0 0 1 2 2v14"/>',
  rotate: '<path d="M20 11a8 8 0 1 1-2.35-5.65L20 8"/><path d="M20 3v5h-5"/>',
  compare: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16M8 8h1M15 8h1"/>',
  fullscreen: '<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  pointer: '<path d="m5 3 13 9-7 1-3 7z"/>',
  hand: '<path d="M7 12V7a2 2 0 0 1 4 0v3-5a2 2 0 0 1 4 0v5-3a2 2 0 0 1 4 0v7c0 5-3 7-7 7H9c-2 0-4-2-5-4l-2-4a2 2 0 0 1 3-2z"/>',
  magnify: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5M10.5 7v7M7 10.5h7"/>'
};

document.querySelectorAll("[data-icon]").forEach((node) => {
  const name = node.dataset.icon;
  node.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
});

const rail = document.getElementById("themeRail");
const themeIndex = document.getElementById("themeIndex");
const themeName = document.getElementById("themeName");
const themeDescription = document.getElementById("themeDescription");
const layoutName = document.getElementById("layoutName");
const themeKeywords = document.getElementById("themeKeywords");
const themeNote = document.getElementById("themeNote");
let current = 0;

themes.forEach((theme, index) => {
  const button = document.createElement("button");
  button.className = "theme-chip";
  button.setAttribute("aria-pressed", "false");
  button.innerHTML = `<i style="--chip-a:${theme.colors[0]};--chip-b:${theme.colors[1]}"></i><span>${String(index + 1).padStart(2, "0")} ${theme.name}</span>`;
  button.addEventListener("click", () => setTheme(index));
  rail.appendChild(button);
});

function setTheme(index) {
  current = (index + themes.length) % themes.length;
  const theme = themes[current];
  document.body.dataset.theme = theme.id;
  document.body.dataset.layout = theme.layout;
  themeIndex.textContent = `${String(current + 1).padStart(2, "0")} / ${themes.length}`;
  themeName.textContent = theme.name;
  themeDescription.textContent = theme.desc;
  layoutName.textContent = theme.layoutName;
  themeKeywords.textContent = theme.keywords;
  themeNote.textContent = theme.note;
  document.querySelectorAll(".theme-chip").forEach((chip, i) => {
    const selected = i === current;
    chip.classList.toggle("active", selected);
    chip.setAttribute("aria-pressed", String(selected));
  });
  document.querySelectorAll(".theme-chip")[current].scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  localStorage.setItem("lie-ui-theme", theme.id);
}

document.getElementById("previousTheme").addEventListener("click", () => setTheme(current - 1));
document.getElementById("nextTheme").addEventListener("click", () => setTheme(current + 1));
document.getElementById("toggleRail").addEventListener("click", (event) => {
  rail.classList.toggle("open");
  const expanded = rail.classList.contains("open");
  event.currentTarget.textContent = expanded ? "收起风格" : "全部风格";
  event.currentTarget.setAttribute("aria-expanded", String(expanded));
});
document.addEventListener("keydown", (event) => {
  if (event.target.matches("input")) return;
  if (event.key === "ArrowLeft") setTheme(current - 1);
  if (event.key === "ArrowRight") setTheme(current + 1);
});

document.querySelectorAll(".thumbnail").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".thumbnail").forEach((item) => item.classList.remove("selected"));
  button.classList.add("selected");
  document.querySelector(".window-title em").textContent = button.dataset.file;
}));
document.querySelectorAll(".quick-filters button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".quick-filters button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  const mode = button.querySelector(".filter-preview").classList[1] || "natural";
  document.querySelector(".photo-frame svg").style.filter = mode === "mono" ? "grayscale(1) contrast(1.08)" : mode === "warm" ? "sepia(.2) saturate(1.1)" : mode === "vivid" ? "saturate(1.25) contrast(1.04)" : "";
}));
document.querySelectorAll('.slider-row input').forEach((input) => input.addEventListener('input', () => {
  const n = Number(input.value);
  input.nextElementSibling.textContent = `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n)}`;
  const pct = (n + 100) / 2;
  input.style.background = `linear-gradient(90deg,var(--surface-3) 0 50%,var(--accent) 50% ${pct}%,var(--surface-3) ${pct}%)`;
}));

let zoom = 68;
function applyZoom(delta) {
  zoom = Math.min(110, Math.max(40, zoom + delta));
  document.getElementById("zoomValue").textContent = `${zoom}%`;
  document.getElementById("photoFrame").style.transform = `scale(${zoom / 74})`;
}
document.getElementById("zoomOut").addEventListener("click", () => applyZoom(-6));
document.getElementById("zoomIn").addEventListener("click", () => applyZoom(6));

const modes = {
  viewer: { summary: "浏览与预览 · 本地", title: "资源库", meta: "示例图片 · 5 张", inspector: "adjust" },
  convert: { summary: "裁切、尺寸与格式 · 本地", title: "转换队列", meta: "待转换 · 5 张", inspector: "convert" },
  edit: { summary: "调色、标注与智能改图", title: "资源与版本", meta: "当前图片 · 3 个修订", inspector: "adjust" },
  generate: { summary: "智能生成 · 按需启用", title: "参考资源", meta: "示例图片 · 5 张", inspector: "adjust" }
};

function setMode(mode) {
  const next = modes[mode] ? mode : "viewer";
  const definition = modes[next];
  document.body.dataset.mode = next;
  document.getElementById("modeSummary").textContent = definition.summary;
  document.getElementById("assetPanelTitle").textContent = definition.title;
  document.getElementById("assetPanelMeta").textContent = definition.meta;
  document.querySelectorAll("[data-mode-button]").forEach((button) => {
    const selected = button.dataset.modeButton === next;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  document.querySelectorAll("[data-inspector]").forEach((button) => {
    const selected = button.dataset.inspector === definition.inspector;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  localStorage.setItem("lie-ui-mode", next);
}

document.querySelectorAll("[data-mode-button]").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.modeButton));
});
document.querySelectorAll("[data-mode-jump]").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.modeJump));
});
document.querySelectorAll("[data-inspector]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.inspector === "convert") setMode("convert");
    if (["adjust", "annotation", "history"].includes(button.dataset.inspector)) setMode("edit");
    document.querySelectorAll("[data-inspector]").forEach((item) => {
      item.classList.remove("active");
      item.setAttribute("aria-selected", "false");
    });
    button.classList.add("active");
    button.setAttribute("aria-selected", "true");
  });
});

const saved = themes.findIndex((theme) => theme.id === localStorage.getItem("lie-ui-theme"));
setTheme(saved >= 0 ? saved : 16);
setMode(localStorage.getItem("lie-ui-mode") || "viewer");
