/* ==========================================================================
   docpix 前端逻辑
   --------------------------------------------------------------------------
   · 原生 ES module + 原生 fetch，零构建、零外部依赖、完全离线可用。
   · 结构：常量 → 工具函数 → state → api/toast → 表单引擎 → 各页面 → 任务轮询。
   · 安全：所有来自服务端/用户的文本一律走 textContent 或 esc() 转义，避免 XSS。
   ========================================================================== */

/* ------------------------------------------------------------- 常量 */

/** 后端缺失时用于兜底的图片扩展名。 */
const IMAGE_EXT_FALLBACK = [
  '.jpg', '.jpeg', '.png', '.webp', '.avif', '.heic', '.heif',
  '.bmp', '.gif', '.tif', '.tiff', '.ico',
];

/** 可作为纯文本内联预览的扩展名（不含点）。 */
const TEXT_PREVIEW_EXTS = new Set([
  'txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'xml', 'html', 'htm',
  'log', 'yaml', 'yml', 'rst', 'org', 'tex', 'srt', 'vtt',
]);

/** 后端无格式表时的兜底输出格式。 */
const IMAGE_OUTPUT_FALLBACK = ['jpg', 'png', 'webp', 'avif', 'heic', 'bmp', 'gif', 'tiff', 'ico'];
/** 后端 /api/system/formats 拿不到时的兜底 OCR 输出类型。 */
const OCR_OUTPUT_FALLBACK = ['pdf', 'pdfa', 'pdfa-1', 'pdfa-2', 'pdfa-3', 'txt'];

/** 水印 / 页码位置候选（以 datalist 提供，允许自由输入，避免与后端枚举不一致）。 */
const POSITIONS = [
  'top-left', 'top-center', 'top-right',
  'middle-left', 'center', 'middle-right',
  'bottom-left', 'bottom-center', 'bottom-right',
];

const MSG_FETCH_ENGINES = 'pwsh tools\\fetch_engines.ps1';
const MSG_FETCH_TESSERACT = 'pwsh tools\\fetch_tesseract.ps1';
const TITLE_NEED_TESSERACT = '未安装该语言包，请先在项目根目录运行 ' + MSG_FETCH_TESSERACT;

/** 状态 → 中文标签与样式类。 */
const STATUS_MAP = {
  pending: { label: '排队中', cls: 'pending' },
  running: { label: '处理中', cls: 'running' },
  done: { label: '完成', cls: 'done' },
  error: { label: '失败', cls: 'error' },
  canceled: { label: '已取消', cls: 'canceled' },
};

/** 操作键 → 中文名。 */
const OP_LABELS = {
  convert: '转换格式',
  resize: '调整尺寸',
  compress: '压缩',
  crop: '裁剪',
  rotate: '旋转',
  flip: '翻转',
  watermark_text: '文字水印',
  watermark_image: '图片水印',
  strip: '清除元数据',
  combine: '拼接',
  to_ico: '生成图标',
  merge: '合并',
  split: '拆分',
  extract_pages: '提取页面',
  delete_pages: '删除页面',
  reorder: '重排页面',
  encrypt: '加密',
  decrypt: '解密',
  page_numbers: '添加页码',
  pdf_to_images: 'PDF 转图片',
  images_to_pdf: '图片转 PDF',
  extract_text: '提取文本',
  metadata: '写入元数据',
  linearize: '线性化',
  repair: '修复',
  ocr: 'OCR 识别',
};

const KIND_LABELS = { image: '图片', pdf: 'PDF', convert: '转换', ocr: 'OCR' };

/* --------------------------------------------------------- 工具函数 */

/** 转义 HTML 特殊字符（仅用于必须拼字符串的场景）。 */
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/** querySelector 简写。 */
function $(sel) {
  return document.querySelector(sel);
}

/**
 * 创建元素。
 * @param {string} tag 标签名
 * @param {object} attrs 属性；class、text、html、on* 事件与 dataset 有特殊处理
 * @param {Array} children 子节点（字符串会成为文本节点）
 */
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = String(value);
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

/** 人类可读的文件大小。 */
function humanSize(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return '—';
  let value = Number(bytes);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  if (i === 0) return `${Math.round(value)} B`;
  return `${value.toFixed(value < 10 ? 2 : 1)} ${units[i]}`;
}

/** 人类可读的时长（秒 → 天/小时/分钟/秒）。 */
function humanDuration(seconds) {
  const sec = Math.max(0, Math.round(Number(seconds) || 0));
  if (sec >= 86400) return `${Math.round(sec / 86400)} 天`;
  if (sec >= 3600) return `${Math.round(sec / 3600)} 小时`;
  if (sec >= 60) return `${Math.round(sec / 60)} 分钟`;
  return `${sec} 秒`;
}

/** 文件名 → 含点小写扩展名，如 "/a/B.PDF" → ".pdf"。 */
function extWithDot(name) {
  const base = String(name || '').split(/[\\/]/).pop() || '';
  const idx = base.lastIndexOf('.');
  return idx > 0 ? base.slice(idx).toLowerCase() : '';
}

/** 文件名 → 不含点小写扩展名。 */
function extOf(name) {
  return extWithDot(name).replace(/^\./, '');
}

/** 只允许站内绝对路径，拒绝 // 、javascript: 等。 */
function isSafeUrl(url) {
  return typeof url === 'string' && /^\/(?!\/)/.test(url);
}

/**
 * 任务 id 缩写显示。
 * 后端 id 形如 ``ocr-20261002-142712-04862c``（kind-日期-时刻-随机），
 * 取前 8 字符会退化成 ``ocr-2026``——同类型任务全都一模一样，
 * 因此这里取「时刻 + 随机段」作为区分度足够的短号。
 */
function shortId(id) {
  const s = String(id || '');
  const parts = s.split('-');
  if (parts.length >= 3) return parts.slice(-2).join('-');
  return s.length > 10 ? s.slice(0, 8) : s;
}

/** 「图片 · 合并」这样的中文标签。 */
function labelFor(kind, op) {
  const kindLabel = KIND_LABELS[kind] || kind || '任务';
  const opLabel = OP_LABELS[op] || op || '';
  return opLabel ? `${kindLabel} · ${opLabel}` : kindLabel;
}

/* ------------------------------------------------------------- 全局状态 */

const state = {
  tab: 'image',
  system: null,
  formats: null,
  jobs: [],
  pollTimer: 0,
  jobsLoading: false,
  jobsPending: false,
  uid: 0,
  // 图片 / PDF / 转换 / OCR 四个上传区的数据（files 顺序即处理顺序）
  image: { files: [], op: 'convert', valuesByOp: {} },
  pdf: { files: [], op: 'merge', valuesByOp: {} },
  convert: { files: [], op: 'convert' },
  ocr: { files: [], languages: [], langReady: false },
};

/* -------------------------------------------------------- toast 与 api */

/**
 * 右上角提示条。
 * @param {string} message 文本（以 textContent 渲染，天然防 XSS）
 * @param {'info'|'ok'|'warn'|'error'} type 语义类型
 * @param {number} timeout 自动关闭毫秒数
 */
function toast(message, type = 'info', timeout = 5200) {
  const box = $('#toasts');
  if (!box) return null;
  const node = el('div', { class: `toast toast-${type}` });
  node.appendChild(el('div', { class: 'toast-msg', text: message }));
  const close = el('button', { class: 'toast-close', type: 'button', 'aria-label': '关闭', text: '×' });
  close.addEventListener('click', () => node.remove());
  node.appendChild(close);
  box.appendChild(node);
  setTimeout(() => {
    node.classList.add('toast-out');
    setTimeout(() => node.remove(), 240);
  }, timeout);
  return node;
}

/** 带 detail 提取的 API 错误。 */
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * fetch 封装：统一解 JSON、把后端 detail 转成中文错误并弹 toast。
 * @param {string} path 请求路径
 * @param {object} options method/body/headers/silent
 */
async function api(path, options = {}) {
  const { method = 'GET', body = null, headers = null, silent = false } = options;

  let res;
  try {
    res = await fetch(path, { method, body, headers: headers || undefined });
  } catch (err) {
    const message = '无法连接到后端服务，请确认 docpix 服务正在运行。';
    if (!silent) toast(message, 'error', 8000);
    throw new ApiError(message, 0);
  }

  if (!res.ok) {
    let detail = `请求失败（HTTP ${res.status}）`;
    try {
      const ctype = res.headers.get('content-type') || '';
      if (ctype.includes('json')) {
        const data = await res.json();
        if (data && data.detail !== undefined) {
          detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        }
      } else {
        const text = await res.text();
        if (text) detail = text.slice(0, 300);
      }
    } catch (err) { /* 忽略解析失败，保留默认文案 */ }
    if (!silent) toast(detail, 'error', 8000);
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return null;
  const ctype = res.headers.get('content-type') || '';
  if (ctype.includes('json')) return res.json();
  return res.text();
}

/* --------------------------------------------------------- 表单字段定义 */

const F = {
  text: (name, label, extra = {}) => ({ name, label, type: 'text', ...extra }),
  num: (name, label, extra = {}) => ({ name, label, type: 'number', ...extra }),
  select: (name, label, options, extra = {}) => ({ name, label, type: 'select', options, ...extra }),
  check: (name, label, extra = {}) => ({ name, label, type: 'check', ...extra }),
  /** 位置类字段：用 datalist 给候选，同时允许自由输入。 */
  pos: (name, label, extra = {}) => ({ name, label, type: 'datalist', options: POSITIONS, ...extra }),
};

const PAGES_FIELD = {
  name: 'pages', label: '页码范围', type: 'text', placeholder: '留空 = 全部，例如 1-3,5,7',
};

/** 图片操作定义（与后端 kind=image 的 op/params 严格对应）。 */
const IMAGE_OPS = {
  convert: {
    hint: '把所有已选图片统一转换为目标格式；quality 仅对有损格式生效。',
    fields: [
      F.select('format', '目标格式', { source: 'image_output' }, { required: true, value: 'png' }),
      F.num('quality', '质量 (1-100)', { min: 1, max: 100, value: 90 }),
    ],
  },
  resize: {
    hint: '按像素调整尺寸；mode 决定是否保持比例。',
    fields: [
      F.num('width', '宽度 (px)', { min: 1, placeholder: '留空 = 按高度等比' }),
      F.num('height', '高度 (px)', { min: 1, placeholder: '留空 = 按宽度等比' }),
      F.select('mode', '缩放模式', ['fit', 'fill', 'exact'], { value: 'fit' }),
    ],
  },
  compress: {
    hint: '重新编码以缩小体积；max_dim 限制最长边像素，0 表示不限制。',
    fields: [
      F.num('quality', '质量 (1-100)', { min: 1, max: 100, value: 80 }),
      F.num('max_dim', '最长边上限 (px)', { min: 0, value: 0 }),
    ],
  },
  crop: {
    hint: '按像素从四边裁掉指定宽度（单位 px）。',
    fields: [
      F.num('left', '左', { min: 0, value: 0 }),
      F.num('top', '上', { min: 0, value: 0 }),
      F.num('right', '右', { min: 0, value: 0 }),
      F.num('bottom', '下', { min: 0, value: 0 }),
    ],
  },
  rotate: {
    hint: '按角度旋转（顺时针为正）。',
    fields: [F.num('angle', '角度 (度)', { value: 90, step: 1 })],
  },
  flip: {
    hint: '水平或垂直翻转。',
    fields: [F.select('axis', '翻转方向', ['horizontal', 'vertical'], { value: 'horizontal' })],
  },
  watermark_text: {
    hint: '在图片上叠加文字水印。',
    fields: [
      F.text('text', '水印文字', { required: true, placeholder: '例如：内部资料' }),
      F.pos('position', '位置', { value: 'bottom-right' }),
      F.num('opacity', '不透明度 (0-1)', { min: 0, max: 1, step: 0.05, value: 0.5 }),
      F.num('font_size', '字号 (px)', { min: 1, value: 32 }),
      F.text('color', '颜色', { value: '#000000', placeholder: '#RRGGBB' }),
      F.num('angle', '旋转角度 (度)', { value: 0 }),
    ],
  },
  watermark_image: {
    hint: '用另一张已选图片作为水印：最后 1 个文件是水印图，其余为待处理底图。',
    fields: [
      F.pos('position', '位置', { value: 'bottom-right' }),
      F.num('scale', '相对缩放 (0-1)', { min: 0, max: 1, step: 0.05, value: 0.2 }),
      F.num('opacity', '不透明度 (0-1)', { min: 0, max: 1, step: 0.05, value: 0.6 }),
      F.num('margin_mm', '边距 (mm)', { min: 0, value: 5 }),
    ],
  },
  strip: {
    hint: '移除 EXIF / GPS / ICC 等元数据，不改变像素内容。',
    fields: [],
  },
  combine: {
    hint: '把多张图片拼接成一张；文件顺序即拼接顺序，可用列表中的 ↑ ↓ 调整。',
    fields: [
      F.select('direction', '拼接方向', ['horizontal', 'vertical'], { value: 'vertical' }),
      F.num('gap', '间隙 (px)', { min: 0, value: 0 }),
      F.text('background', '背景色', { value: '#ffffff', placeholder: '#RRGGBB' }),
    ],
  },
  to_ico: {
    hint: '生成 Windows 图标 .ico，内嵌多种尺寸；size 为最大边长。',
    fields: [F.num('size', '最大边长 (px)', { min: 16, max: 256, value: 256 })],
  },
};

/** PDF 操作定义（与后端 kind=pdf 的 op/params 严格对应）。 */
const PDF_OPS = {
  merge: {
    hint: '把所有已选 PDF 合并为一个；文件顺序即合并顺序，可用列表中的 ↑ ↓ 调整。',
    fields: [],
  },
  split: {
    hint: '按方式拆分 PDF：每页一个文件 / 指定范围 / 对半 / 每 n 页切分。',
    fields: [
      F.select('mode', '拆分方式', ['each', 'ranges', 'half', 'n'], { value: 'each' }),
      F.text('ranges', '范围', {
        placeholder: '仅 mode=ranges 时生效，例如 1-3,5',
        when: (v) => v.mode === 'ranges',
      }),
      F.num('n', '每份页数', {
        min: 1,
        value: 1,
        when: (v) => v.mode === 'n',
      }),
    ],
  },
  extract_pages: {
    hint: '只保留指定页面，生成新的 PDF。',
    fields: [F.text('pages', PAGES_FIELD.label, { required: true, placeholder: '例如 1-3,5,7' })],
  },
  delete_pages: {
    hint: '删除指定页面，生成新的 PDF。',
    fields: [F.text('pages', PAGES_FIELD.label, { required: true, placeholder: '例如 2,4-6' })],
  },
  reorder: {
    hint: '按给定顺序重排页面，必须包含全部页码。',
    fields: [F.text('order', '新页序', { required: true, placeholder: '例如 3,1,2' })],
  },
  rotate: {
    hint: '旋转指定页面或全部页面。',
    fields: [
      F.select('angle', '角度 (度)', ['90', '180', '270'], { value: '90' }),
      F.text('pages', '页码范围', { placeholder: '留空 = 全部，例如 1,3-5' }),
    ],
  },
  compress: {
    hint: '压缩 PDF 体积，级别越高压缩越激进、画质损失越大。',
    fields: [F.select('level', '压缩级别', ['low', 'medium', 'high', 'extreme'], { value: 'medium' })],
  },
  encrypt: {
    hint: '设置打开密码与权限密码，并限制打印/复制/修改。',
    fields: [
      F.text('user_password', '打开密码', { placeholder: '留空表示不设打开密码' }),
      F.text('owner_password', '权限密码', { placeholder: '留空则与打开密码相同' }),
      F.check('allow_print', '允许打印', { value: true }),
      F.check('allow_copy', '允许复制文本', { value: true }),
      F.check('allow_modify', '允许修改内容', { value: true }),
    ],
  },
  decrypt: {
    hint: '移除 PDF 的打开密码（需要提供当前密码）。',
    fields: [F.text('password', '当前密码', { required: true })],
  },
  page_numbers: {
    hint: '在页面上添加页码；template 可用 {page} / {total} 占位。',
    fields: [
      F.text('template', '页码模板', { value: '{page} / {total}' }),
      F.pos('position', '位置', { value: 'bottom-center' }),
      F.num('font_size', '字号', { min: 1, value: 12 }),
      F.num('margin_mm', '边距 (mm)', { min: 0, value: 10 }),
      F.num('start_at', '起始页码', { min: 1, value: 1 }),
    ],
  },
  watermark_text: {
    hint: '在 PDF 页面上叠加文字水印。',
    fields: [
      F.text('text', '水印文字', { required: true, placeholder: '例如：机密' }),
      F.pos('position', '位置', { value: 'center' }),
      F.num('font_size', '字号', { min: 1, value: 48 }),
      F.num('opacity', '不透明度 (0-1)', { min: 0, max: 1, step: 0.05, value: 0.25 }),
      F.text('color', '颜色', { value: '#888888', placeholder: '#RRGGBB' }),
      F.num('angle', '旋转角度 (度)', { value: 45 }),
    ],
  },
  watermark_image: {
    hint: '把最后 1 个已选图片作为水印，叠加到其余 PDF 上。',
    fields: [
      F.pos('position', '位置', { value: 'bottom-right' }),
      F.num('scale', '相对缩放 (0-1)', { min: 0, max: 1, step: 0.05, value: 0.25 }),
      F.num('opacity', '不透明度 (0-1)', { min: 0, max: 1, step: 0.05, value: 0.5 }),
      F.num('margin_mm', '边距 (mm)', { min: 0, value: 5 }),
    ],
  },
  pdf_to_images: {
    hint: '把 PDF 每页渲染成图片。',
    fields: [
      F.num('dpi', '分辨率 (DPI)', { min: 36, value: 150 }),
      F.select('fmt', '图片格式', { source: 'image_output' }, { value: 'png' }),
      F.text('pages', '页码范围', { placeholder: '留空 = 全部，例如 1-3' }),
      F.num('quality', '质量 (1-100)', { min: 1, max: 100, value: 90 }),
    ],
  },
  images_to_pdf: {
    hint: '把已选图片合并成一个 PDF；文件顺序即页面顺序。',
    fields: [
      F.select('page_size', '页面尺寸', ['fit', 'A4', 'A3', 'A5', 'Letter'], { value: 'fit' }),
      F.num('margin_mm', '边距 (mm)', { min: 0, value: 10 }),
      F.num('quality', '质量 (1-100)', { min: 1, max: 100, value: 90 }),
    ],
  },
  extract_text: {
    hint: '提取 PDF 中的文本层，输出纯文本。',
    fields: [F.text('pages', PAGES_FIELD.label, { placeholder: '留空 = 全部，例如 1-3' })],
  },
  metadata: {
    hint: '写入 PDF 文档元数据（留空的字段不改动）。',
    fields: [
      F.text('title', '标题'),
      F.text('author', '作者'),
      F.text('subject', '主题'),
      F.text('keywords', '关键词', { placeholder: '逗号分隔' }),
    ],
  },
  linearize: {
    hint: '线性化（Web 优化），让浏览器可边下载边打开。',
    fields: [],
  },
  repair: {
    hint: '尝试修复结构损坏的 PDF（可能丢失部分内容）。',
    fields: [],
  },
};

/** OCR 输出类型：候选项来自后端 /api/system/formats 的 ocr_output_types。 */
const OCR_OUTPUT_LABELS = {
  pdf: 'PDF（可搜索，保留原图）',
  pdfa: 'PDF/A（自动选级别）',
  'pdfa-1': 'PDF/A-1b',
  'pdfa-2': 'PDF/A-2b',
  'pdfa-3': 'PDF/A-3b',
  txt: 'TXT（纯文本，不含原图）',
};

/** OCR 高级选项。 */
const OCR_FIELDS = [
  F.select('output_type', '输出类型', { source: 'ocr_output_types' }, { value: 'pdf' }),
  F.select('optimize', '输出体积优化', ['0', '1', '2', '3'],
    { value: 1, numeric: true, hint: '0 = 不优化，1 = 默认，3 = 最激进（更慢）' }),
  F.check('deskew', '自动纠偏（deskew）'),
  F.check('force', '强制重新 OCR（force）'),
  F.check('skip_text', '跳过已有文本层（skip_text）'),
  F.check('rotate_pages', '自动旋转页面（rotate_pages）'),
];

const OP_DEFS = { image: IMAGE_OPS, pdf: PDF_OPS };

/* ----------------------------------------------------------- 表单引擎 */

/**
 * 解析字段候选项。
 * 支持两种写法：静态数组 options: ['a','b']，或取自格式表
 * options: { source: 'image_output' }（也接受字段级 source）。
 */
function resolveOptions(field) {
  let raw = [];
  let source = field.source;
  if (Array.isArray(field.options)) {
    raw = field.options;
  } else if (field.options && typeof field.options === 'object' && field.options.source) {
    source = field.options.source;
  }

  if (!raw.length && source) {
    const table = state.formats ? state.formats[source] : null;
    if (Array.isArray(table)) raw = table;
    else if (table && typeof table === 'object') {
      raw = Object.entries(table).map(([value, label]) => ({ value, label }));
    }
  }
  if (!raw.length && source === 'image_output') raw = IMAGE_OUTPUT_FALLBACK;
  if (!raw.length && source === 'ocr_output_types') raw = OCR_OUTPUT_FALLBACK;
  const options = raw.map((opt) => (typeof opt === 'string' ? { value: opt, label: opt } : opt));
  if (source === 'ocr_output_types') {
    return options.map((opt) => ({ ...opt, label: OCR_OUTPUT_LABELS[opt.value] || opt.label }));
  }
  return options;
}

/** 字段默认值集合。 */
function defaultValues(fields) {
  const values = {};
  for (const field of fields) {
    if (field.value !== undefined) values[field.name] = field.value;
    else if (field.type === 'check') values[field.name] = false;
  }
  return values;
}

/** 构造下拉框。 */
function buildSelect(field, current, id) {
  const select = el('select', { id, class: 'select' });
  const options = resolveOptions(field);
  if (!options.length) options.push({ value: '', label: '（暂无可用选项）' });

  let matched = false;
  for (const opt of options) {
    const option = el('option', { value: opt.value, text: opt.label });
    if (String(opt.value) === String(current)) { option.selected = true; matched = true; }
    select.appendChild(option);
  }
  // 保留后端/历史返回的自定义值，避免静默丢失
  if (!matched && current !== '' && current !== undefined && current !== null) {
    const option = el('option', { value: current, text: String(current) });
    option.selected = true;
    select.insertBefore(option, select.firstChild);
  }
  return select;
}

/** 构造单个字段行。 */
function buildFieldRow(field, values) {
  const id = `fld-${field.name}`;
  const raw = values ? values[field.name] : undefined;
  const current = raw !== undefined ? raw
    : (field.value !== undefined ? field.value : (field.type === 'check' ? false : ''));

  const row = el('div', { class: 'field-row', dataset: { field: field.name } });
  let control;

  if (field.type === 'check') {
    control = el('input', { id, type: 'checkbox' });
    control.checked = !!current;
    const label = el('label', { class: 'field check', for: id });
    label.appendChild(control);
    label.appendChild(el('span', { text: field.label }));
    row.appendChild(label);
    if (field.hint) row.appendChild(el('div', { class: 'field-hint', text: field.hint }));
    return row;
  }

  if (field.type === 'select') {
    control = buildSelect(field, current, id);
  } else if (field.type === 'datalist') {
    const listId = `${id}-list`;
    const list = el('datalist', { id: listId });
    for (const opt of resolveOptions(field)) list.appendChild(el('option', { value: opt.value }));
    control = el('input', {
      id, class: 'input', type: 'text', list: listId,
      value: current === '' ? '' : String(current),
      placeholder: field.placeholder || '可自由输入',
    });
    control.setAttribute('list', listId);
    row.appendChild(list);
  } else if (field.type === 'textarea') {
    control = el('textarea', { id, class: 'input', rows: field.rows || 3 });
    control.value = current === '' ? '' : String(current);
  } else if (field.type === 'number') {
    control = el('input', {
      id, class: 'input', type: 'number', inputmode: 'decimal',
      value: current === '' || current === undefined ? '' : String(current),
      placeholder: field.placeholder || '',
    });
    if (field.min !== undefined) control.setAttribute('min', String(field.min));
    if (field.max !== undefined) control.setAttribute('max', String(field.max));
    control.setAttribute('step', String(field.step !== undefined ? field.step : 'any'));
  } else {
    control = el('input', {
      id, class: 'input', type: 'text',
      value: current === '' ? '' : String(current),
      placeholder: field.placeholder || '',
    });
  }

  const label = el('label', { class: 'field', for: id });
  label.appendChild(el('span', {
    class: 'field-label',
    text: field.required ? `${field.label} *` : field.label,
  }));
  label.appendChild(control);
  row.appendChild(label);
  if (field.hint) row.appendChild(el('div', { class: 'field-hint', text: field.hint }));
  return row;
}

/** 读取表单当前值（隐藏字段跳过，空值不提交）。 */
function readFormValues(formEl, fields) {
  const out = {};
  for (const field of fields) {
    const row = formEl.querySelector(`[data-field="${field.name}"]`);
    if (!row || row.hidden) continue;
    const input = row.querySelector('input, select, textarea');
    if (!input) continue;

    if (field.type === 'check') { out[field.name] = !!input.checked; continue; }

    const value = input.value;
    if (value === '') continue;
    if (field.type === 'number' || field.numeric) {
      const num = Number(value);
      if (Number.isFinite(num)) out[field.name] = num;
      continue;
    }
    out[field.name] = value;
  }
  return out;
}

/** 依据 when 条件显示 / 隐藏字段行。 */
function applyFieldConditions(formEl, fields) {
  const values = readFormValues(formEl, fields);
  for (const field of fields) {
    if (typeof field.when !== 'function') continue;
    const row = formEl.querySelector(`[data-field="${field.name}"]`);
    if (row) row.hidden = !field.when(values);
  }
}

/** 绑定条件联动（每表单只绑一次）。 */
function bindFormConditions(formEl, fields) {
  formEl._fields = fields;
  if (formEl.dataset.bound === '1') return;
  formEl.dataset.bound = '1';
  const handler = (event) => applyFieldConditions(event.currentTarget, event.currentTarget._fields || []);
  formEl.addEventListener('input', handler);
  formEl.addEventListener('change', handler);
}

/** 渲染整个参数表单。 */
function renderParamsForm(formEl, fields, values) {
  formEl.textContent = '';
  if (!fields.length) {
    formEl.appendChild(el('p', { class: 'empty-note', text: '该操作无需额外参数，直接提交即可。' }));
    return;
  }
  for (const field of fields) formEl.appendChild(buildFieldRow(field, values));
  applyFieldConditions(formEl, fields);
}

/** 保存当前表单值，便于切换 op 后回来仍保留。 */
function saveOpValues(key) {
  const tool = state[key];
  const def = OP_DEFS[key] ? OP_DEFS[key][tool.op] : null;
  if (!def) return;
  const formEl = document.getElementById(`form-${key}`);
  if (!formEl || formEl.dataset.bound !== '1') return;
  tool.valuesByOp[tool.op] = readFormValues(formEl, def.fields);
}

/** 渲染某个上传页的「操作 + 参数」区域。 */
function renderOpSection(key) {
  const tool = state[key];
  const def = OP_DEFS[key] ? OP_DEFS[key][tool.op] : null;
  const hintEl = document.getElementById(`hint-${key}`);
  if (hintEl) hintEl.textContent = def ? def.hint : '';
  const formEl = document.getElementById(`form-${key}`);
  if (!formEl || !def) return;

  let values = tool.valuesByOp[tool.op];
  if (!values) {
    values = defaultValues(def.fields);
    tool.valuesByOp[tool.op] = values;
  }
  renderParamsForm(formEl, def.fields, values);
  bindFormConditions(formEl, def.fields);
}

/** 填充操作下拉框。 */
function buildOpSelect(key) {
  const select = document.getElementById(`op-${key}`);
  if (!select) return;
  select.textContent = '';
  for (const [op, def] of Object.entries(OP_DEFS[key])) {
    select.appendChild(el('option', { value: op, text: `${OP_LABELS[op] || op}（${op}）` }));
  }
  select.value = state[key].op;
}

/** 切换操作。 */
function setOp(key, op, initial = false) {
  const tool = state[key];
  if (!OP_DEFS[key] || !OP_DEFS[key][op]) return;
  if (!initial && tool.op) saveOpValues(key);
  tool.op = op;
  const select = document.getElementById(`op-${key}`);
  if (select && select.value !== op) select.value = op;
  renderOpSection(key);
}

/* --------------------------------------------------------- 上传文件管理 */

/**
 * 扩展名归一化为「带点 + 小写」。
 * 后端 /api/system/formats 的 image_input 是裸扩展名（"png"），
 * convert_matrix 的键也是裸扩展名，而本地兜底常量带点，这里统一。
 */
function normExt(ext) {
  const s = String(ext ?? '').trim().toLowerCase();
  if (!s) return '';
  return s.startsWith('.') ? s : `.${s}`;
}

/** 某个上传页允许的扩展名（返回 null 表示不限制）。 */
function allowedExts(key) {
  const formats = state.formats;
  const images = ((formats && formats.image_input) || IMAGE_EXT_FALLBACK).map(normExt);
  if (key === 'image') return images;
  if (key === 'pdf') return ['.pdf'];
  if (key === 'ocr') return ['.pdf', ...images];
  if (key === 'convert') {
    const keys = Object.keys((formats && formats.convert_matrix) || {});
    if (keys.length) return keys.map(normExt);
    return null;
  }
  return null;
}

/** 按扩展名筛选文件。 */
function filterAccepted(key, files) {
  const allowed = allowedExts(key);
  if (!allowed) return { accepted: files, rejected: [] };
  const set = new Set(allowed.map((e) => e.toLowerCase()));
  const accepted = [];
  const rejected = [];
  for (const file of files) (set.has(extWithDot(file.name)) ? accepted : rejected).push(file);
  return { accepted, rejected };
}

/** 创建文件条目（图片生成缩略图对象 URL）。 */
function makeFileItem(file) {
  state.uid += 1;
  const item = { id: `f${state.uid}`, file, url: null };
  const isImage = (file.type && file.type.startsWith('image/'))
    || IMAGE_EXT_FALLBACK.includes(extWithDot(file.name));
  if (isImage) {
    try { item.url = URL.createObjectURL(file); } catch (err) { item.url = null; }
  }
  return item;
}

/** 释放对象 URL。 */
function releaseItem(item) {
  if (item && item.url) {
    try { URL.revokeObjectURL(item.url); } catch (err) { /* 忽略 */ }
    item.url = null;
  }
}

/** 添加文件（含限额与扩展名校验）。 */
function addFiles(key, fileList) {
  const tool = state[key];
  const incoming = Array.from(fileList || []);
  if (!incoming.length) return;

  const { accepted, rejected } = filterAccepted(key, incoming);
  if (rejected.length) {
    const names = rejected.slice(0, 3).map((f) => f.name).join('、');
    toast(`已忽略 ${rejected.length} 个不支持的文件：${names}${rejected.length > 3 ? ' 等' : ''}`, 'error', 7000);
  }
  if (!accepted.length) return;

  const limits = (state.system && state.system.limits) || {};
  const total = tool.files.length + accepted.length;
  if (limits.max_batch_files && total > limits.max_batch_files) {
    toast(`单次最多处理 ${limits.max_batch_files} 个文件，当前会达到 ${total} 个，请先减少文件。`, 'error', 8000);
    return;
  }
  if (limits.max_upload_bytes) {
    for (const file of accepted) {
      if (file.size > limits.max_upload_bytes) {
        toast(`文件「${file.name}」为 ${humanSize(file.size)}，超过单文件上限 ${humanSize(limits.max_upload_bytes)}。`, 'error', 8000);
        return;
      }
    }
  }

  for (const file of accepted) tool.files.push(makeFileItem(file));
  renderFileList(key);
  if (key === 'convert') renderConvert();
}

/** 移除单个文件。 */
function removeFile(key, id) {
  const tool = state[key];
  const idx = tool.files.findIndex((item) => item.id === id);
  if (idx < 0) return;
  const [item] = tool.files.splice(idx, 1);
  releaseItem(item);
  renderFileList(key);
  if (key === 'convert') renderConvert();
}

/** 上移 / 下移（合并、拼接等依赖顺序的操作）。 */
function moveFile(key, id, delta) {
  const tool = state[key];
  const idx = tool.files.findIndex((item) => item.id === id);
  const target = idx + delta;
  if (idx < 0 || target < 0 || target >= tool.files.length) return;
  const [item] = tool.files.splice(idx, 1);
  tool.files.splice(target, 0, item);
  renderFileList(key);
}

/** 清空某个上传区。 */
function clearFiles(key) {
  const tool = state[key];
  tool.files.forEach(releaseItem);
  tool.files = [];
  renderFileList(key);
  if (key === 'convert') renderConvert();
}

/** 渲染文件列表。 */
function renderFileList(key) {
  const tool = state[key];
  const list = document.getElementById(`list-${key}`);
  const summary = document.getElementById(`summary-${key}`);
  if (!list) return;

  list.textContent = '';
  tool.files.forEach((item, index) => {
    const li = el('li', { class: 'file-item' });

    // 序号放在最左，便于确认处理顺序
    li.appendChild(el('span', { class: 'file-index', text: String(index + 1) }));

    const iconText = extOf(item.file.name) === 'pdf' ? '📕' : '📄';
    if (item.url) {
      const img = el('img', { class: 'thumb', alt: '' });
      // 浏览器无法解码的格式（HEIC / TIFF / ICO 等）退回类型图标，避免出现破图
      img.addEventListener('error', () => {
        img.replaceWith(el('div', { class: 'thumb thumb-icon', text: iconText }));
      });
      img.src = item.url;
      li.appendChild(img);
    } else {
      li.appendChild(el('div', { class: 'thumb thumb-icon', text: iconText }));
    }

    const info = el('div', { class: 'file-info' });
    info.appendChild(el('div', { class: 'file-name', text: item.file.name, title: item.file.name }));
    info.appendChild(el('div', {
      class: 'file-meta',
      text: `${extOf(item.file.name) || '未知格式'} · ${humanSize(item.file.size)}`,
    }));
    li.appendChild(info);

    const actions = el('div', { class: 'file-actions' });

    const up = el('button', { class: 'icon-btn', type: 'button', title: '上移', text: '↑' });
    up.disabled = index === 0;
    up.addEventListener('click', () => moveFile(key, item.id, -1));
    actions.appendChild(up);

    const down = el('button', { class: 'icon-btn', type: 'button', title: '下移', text: '↓' });
    down.disabled = index === tool.files.length - 1;
    down.addEventListener('click', () => moveFile(key, item.id, 1));
    actions.appendChild(down);

    const remove = el('button', { class: 'icon-btn remove', type: 'button', title: '移除', text: '✕' });
    remove.addEventListener('click', () => removeFile(key, item.id));
    actions.appendChild(remove);

    li.appendChild(actions);
    list.appendChild(li);
  });

  if (summary) {
    if (!tool.files.length) summary.textContent = '尚未选择文件';
    else {
      const bytes = tool.files.reduce((sum, item) => sum + (item.file.size || 0), 0);
      summary.textContent = `已选择 ${tool.files.length} 个文件 · 共 ${humanSize(bytes)}`;
    }
  }
}

/** 绑定拖拽区与选择框。 */
function wireDropzone(key) {
  const zone = document.getElementById(`dz-${key}`);
  const input = document.getElementById(`input-${key}`);
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());
  zone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      input.click();
    }
  });
  input.addEventListener('change', () => {
    addFiles(key, input.files);
    input.value = '';
  });
  zone.addEventListener('dragover', (event) => {
    event.preventDefault();
    zone.classList.add('over');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('over');
    if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
      addFiles(key, event.dataTransfer.files);
    }
  });
}

/** 设置某个 input 的 accept 属性。 */
function setAccept(id, exts) {
  const input = document.getElementById(id);
  if (input && exts && exts.length) input.setAttribute('accept', exts.join(','));
}

/** 依据后端格式表刷新 accept 与图片页说明。 */
function updateAccepts() {
  const images = allowedExts('image');
  setAccept('input-image', images);
  setAccept('input-pdf', ['.pdf']);
  setAccept('input-ocr', allowedExts('ocr'));
  setAccept('input-convert', allowedExts('convert'));

  const imageSub = document.getElementById('dz-image-sub');
  if (imageSub && images && images.length) {
    imageSub.textContent = `支持 ${images.map((e) => e.replace('.', '').toUpperCase()).join(' / ')}`;
  }
  const convertSub = document.getElementById('dz-convert-sub');
  const matrix = state.formats && state.formats.convert_matrix;
  if (convertSub && matrix) {
    convertSub.textContent = `支持 ${Object.keys(matrix).length} 种源格式（Office / PDF / Markdown / HTML / EPUB 等）`;
  }
}

/* --------------------------------------------------------- 提交任务 */

/** 提交前的限额校验（单文件大小 + 数量）。 */
function validateFiles(files) {
  const limits = (state.system && state.system.limits) || {};
  if (!files.length) throw new Error('请先选择文件。');
  if (limits.max_batch_files && files.length > limits.max_batch_files) {
    throw new Error(`单次最多处理 ${limits.max_batch_files} 个文件，当前 ${files.length} 个。`);
  }
  if (limits.max_upload_bytes) {
    for (const file of files) {
      if (file.size > limits.max_upload_bytes) {
        throw new Error(`文件「${file.name}」为 ${humanSize(file.size)}，超过单文件上限 ${humanSize(limits.max_upload_bytes)}。`);
      }
    }
  }
}

/** 提交按钮的忙碌态。 */
function setBusy(btn, busy, busyText) {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset.orig) btn.dataset.orig = btn.textContent;
    btn.disabled = true;
    btn.classList.add('busy');
    if (busyText) btn.textContent = busyText;
  } else {
    btn.disabled = false;
    btn.classList.remove('busy');
    if (btn.dataset.orig) {
      btn.textContent = btn.dataset.orig;
      delete btn.dataset.orig;
    }
  }
}

/**
 * 统一的建任务流程：POST /api/jobs → 刷新任务页并切过去。
 * @param {HTMLButtonElement} btn 触发按钮
 * @param {string} kind image | pdf | convert | ocr
 * @param {string} op 操作名
 * @param {object} params 参数对象（会被序列化为 JSON 字符串）
 * @param {File[]} files 文件数组（顺序即处理顺序）
 * @param {object} opts { clearTool }
 */
async function runJob(btn, kind, op, params, files, opts = {}) {
  try {
    validateFiles(files);
    setBusy(btn, true, '提交中…');

    const form = new FormData();
    form.append('kind', kind);
    form.append('op', op);
    form.append('params', JSON.stringify(params || {}));
    for (const file of files) form.append('files', file, file.name);

    const job = await api('/api/jobs', { method: 'POST', body: form });
    toast(`任务已提交：${labelFor(kind, op)}（#${shortId(job && job.id)}）`, 'ok');
    if (opts.clearTool) clearFiles(opts.clearTool);

    await refreshJobs(true);
    switchTab('jobs');
  } catch (err) {
    if (!(err instanceof ApiError)) toast(err.message || '提交失败。', 'error');
  } finally {
    setBusy(btn, false);
  }
}

/** 图片 / PDF 页提交。 */
async function submitTool(key) {
  const tool = state[key];
  if (!tool.files.length) {
    toast('请先选择文件。', 'error');
    return;
  }
  const def = OP_DEFS[key][tool.op];
  const formEl = document.getElementById(`form-${key}`);
  const params = formEl ? readFormValues(formEl, def.fields) : {};

  for (const field of def.fields) {
    if (!field.required) continue;
    if (params[field.name] === undefined || params[field.name] === '') {
      toast(`请填写「${field.label}」。`, 'error');
      return;
    }
  }

  await runJob(
    document.getElementById(`submit-${key}`),
    key,
    tool.op,
    params,
    tool.files.map((item) => item.file),
    { clearTool: key },
  );
}

/* --------------------------------------------------------- 转换页 */

/** 刷新「源 → 目标」下拉。 */
function renderConvert() {
  const tool = state.convert;
  const matrix = (state.formats && state.formats.convert_matrix) || {};
  const first = tool.files[0];
  const srcExt = first ? extOf(first.file.name) : '';
  const targets = (srcExt && matrix[srcExt]) || [];

  const sourceEl = document.getElementById('convert-source');
  if (sourceEl) {
    sourceEl.textContent = first
      ? `${first.file.name}（源格式：${srcExt || '未知'}）`
      : '请先选择文件';
  }

  const select = document.getElementById('convert-target');
  if (!select) return;
  const previous = select.value;
  select.textContent = '';

  if (!targets.length) {
    select.disabled = true;
    select.appendChild(el('option', {
      value: '',
      text: srcExt ? `没有可用的目标格式（.${srcExt}）` : '请先选择文件',
    }));
    return;
  }

  select.disabled = false;
  for (const target of targets) {
    select.appendChild(el('option', { value: target, text: `${srcExt} → ${target}` }));
  }
  if (targets.includes(previous)) select.value = previous;
}

async function submitConvert() {
  const tool = state.convert;
  if (!tool.files.length) {
    toast('请先选择文件。', 'error');
    return;
  }
  const select = document.getElementById('convert-target');
  const target = select ? select.value : '';
  if (!target) {
    toast('请选择目标格式。', 'error');
    return;
  }
  await runJob(
    document.getElementById('submit-convert'),
    'convert',
    'convert',
    { target },
    tool.files.map((item) => item.file),
    { clearTool: 'convert' },
  );
}

/* ----------------------------------------------------------- OCR 页 */

/** 渲染语言多选 chips（已安装置顶 + 标注）。 */
function renderOcrLangs() {
  const wrap = document.getElementById('ocr-langs');
  const note = document.getElementById('ocr-lang-note');
  if (!wrap) return;

  const formats = state.formats;
  const all = (formats && formats.ocr_languages) || { chi_sim: '简体中文', eng: 'English' };
  const installed = new Set((formats && formats.installed_ocr_languages) || []);
  const tool = state.ocr;

  // 首次渲染可能早于 /api/system/formats 返回；只有拿到真实语言表后才确定默认选中项。
  if (formats && !tool.langReady) {
    tool.langReady = true;
    if (installed.has('chi_sim')) tool.languages = ['chi_sim'];
    else if (installed.size) tool.languages = [Array.from(installed)[0]];
    else tool.languages = [];
  }
  if (formats) tool.languages = tool.languages.filter((code) => installed.has(code));

  const entries = Object.entries(all)
    .sort((a, b) => (installed.has(b[0]) ? 1 : 0) - (installed.has(a[0]) ? 1 : 0));

  wrap.textContent = '';
  for (const [code, name] of entries) {
    const isInstalled = installed.has(code);
    const chip = el('button', {
      class: 'lang-chip'
        + (tool.languages.includes(code) ? ' selected' : '')
        + (isInstalled ? '' : ' disabled'),
      type: 'button',
      title: isInstalled ? `已安装：${name}` : TITLE_NEED_TESSERACT,
    });
    chip.appendChild(el('span', { class: 'lang-name', text: `${name}（${code}）` }));
    chip.appendChild(el('span', { class: 'lang-tag', text: isInstalled ? '已安装' : '未安装' }));

    if (isInstalled) {
      chip.addEventListener('click', () => {
        const idx = tool.languages.indexOf(code);
        if (idx >= 0) tool.languages.splice(idx, 1);
        else tool.languages.push(code);
        renderOcrLangs();
      });
    } else {
      chip.disabled = true;
    }
    wrap.appendChild(chip);
  }

  if (!entries.length) wrap.appendChild(el('span', { class: 'muted small', text: '未读取到语言列表。' }));

  if (note) {
    const missing = entries.filter(([code]) => !installed.has(code));
    if (missing.length) {
      note.hidden = false;
      note.textContent = `有 ${missing.length} 种语言未安装。请先在项目根目录运行 ${MSG_FETCH_TESSERACT} 后再选择。`;
    } else {
      note.hidden = true;
    }
  }
}

async function submitOcr() {
  const tool = state.ocr;
  if (!tool.files.length) {
    toast('请先选择文件。', 'error');
    return;
  }
  if (!tool.languages.length) {
    toast('请至少选择一种已安装的 OCR 语言。', 'error');
    return;
  }
  const formEl = document.getElementById('form-ocr');
  const params = formEl ? readFormValues(formEl, OCR_FIELDS) : {};
  params.languages = tool.languages.slice();
  await runJob(
    document.getElementById('submit-ocr'),
    'ocr',
    'ocr',
    params,
    tool.files.map((item) => item.file),
    { clearTool: 'ocr' },
  );
}

/* ---------------------------------------------------------- 任务页 */

/** 耗时文字。 */
function elapsedText(job) {
  if (job.status === 'pending' && !job.started_at) return '排队中';
  const start = job.started_at || job.created_at;
  if (!start) return '—';
  const end = job.finished_at || (Date.now() / 1000);
  const seconds = Math.max(0, end - start);
  return `耗时 ${seconds.toFixed(1)} 秒`;
}

/** 输出条目的预览类型。 */
function previewKind(output) {
  const kind = String(output.kind || '').toLowerCase();
  const ext = extOf(output.name);
  if (kind === 'pdf' || ext === 'pdf') return 'pdf';
  if (kind === 'image' || IMAGE_EXT_FALLBACK.includes(`.${ext}`)) return 'image';
  if (kind === 'text' || TEXT_PREVIEW_EXTS.has(ext)) return 'text';
  return 'none';
}

/** 渲染一组文件 chips。 */
function renderFileSection(title, files, withActions, job) {
  const section = el('div', { class: 'job-files' });
  section.appendChild(el('span', { class: 'job-files-title', text: `${title}：` }));

  if (!files || !files.length) {
    section.appendChild(el('span', { class: 'muted small', text: '—' }));
    return section;
  }

  const chips = el('div', { class: 'chips' });
  for (const file of files) {
    const chip = el('span', { class: 'file-chip' });
    chip.appendChild(el('span', { class: 'file-chip-name', text: file.name, title: file.name }));
    if (file.size !== undefined && file.size !== null) {
      chip.appendChild(el('span', { class: 'file-chip-size muted', text: humanSize(file.size) }));
    }

    if (withActions) {
      const base = `/api/jobs/${encodeURIComponent(job.id)}`;
      const fallback = `${base}/files/${encodeURIComponent(file.name)}`;
      const downloadUrl = isSafeUrl(file.url) ? file.url : fallback;
      if (isSafeUrl(downloadUrl)) {
        chip.appendChild(el('a', { class: 'btn tiny', href: downloadUrl, download: file.name, text: '下载' }));
      }
      const kind = previewKind(file);
      if (kind !== 'none') {
        const previewUrl = isSafeUrl(file.preview_url) ? file.preview_url : downloadUrl;
        const button = el('button', { class: 'btn tiny ghost', type: 'button', text: '预览' });
        button.addEventListener('click', () => openPreview(job, file, previewUrl, kind));
        chip.appendChild(button);
      }
    }
    chips.appendChild(chip);
  }
  section.appendChild(chips);
  return section;
}

/** 渲染单条任务。 */
function renderJobRow(job) {
  const status = STATUS_MAP[job.status] || { label: job.status || '未知', cls: 'unknown' };
  const row = el('div', { class: 'job' });

  const head = el('div', { class: 'job-head' });
  head.appendChild(el('span', { class: 'job-title', text: labelFor(job.kind, job.op) }));
  head.appendChild(el('span', { class: `status-badge status-${status.cls}`, text: status.label }));
  head.appendChild(el('span', { class: 'job-id', text: `#${shortId(job.id)}` }));
  head.appendChild(el('span', { class: 'spacer' }));

  const time = el('span', { class: 'job-time muted small', text: elapsedText(job) });
  if (job.created_at) {
    time.title = `创建于 ${new Date(job.created_at * 1000).toLocaleString('zh-CN')}`;
  }
  head.appendChild(time);

  const actions = el('div', { class: 'job-actions' });
  if (job.status === 'pending' || job.status === 'running') {
    const cancel = el('button', { class: 'btn ghost small', type: 'button', text: '取消' });
    cancel.addEventListener('click', () => cancelJob(job, cancel));
    actions.appendChild(cancel);
  }
  const remove = el('button', { class: 'btn ghost small danger-text', type: 'button', text: '删除' });
  remove.addEventListener('click', () => deleteJob(job, remove));
  actions.appendChild(remove);
  head.appendChild(actions);
  row.appendChild(head);

  if (job.status === 'pending' || job.status === 'running') {
    const progress = Math.max(0, Math.min(1, Number(job.progress) || 0));
    const bar = el('div', { class: 'progress' });
    bar.appendChild(el('div', { class: 'progress-bar', style: `width:${(progress * 100).toFixed(1)}%` }));
    row.appendChild(bar);
    row.appendChild(el('div', {
      class: 'job-msg muted small',
      text: `${(progress * 100).toFixed(0)}%${job.message ? ` · ${job.message}` : ''}`,
    }));
  }

  const body = el('div', { class: 'job-body' });
  body.appendChild(renderFileSection('输入', job.inputs, false, job));
  if (job.outputs && job.outputs.length) {
    body.appendChild(renderFileSection('输出', job.outputs, true, job));
  }
  row.appendChild(body);

  if (job.error) {
    row.appendChild(el('div', { class: 'alert danger', text: `错误：${job.error}` }));
  }
  return row;
}

/** 渲染整个任务列表。 */
function renderJobs() {
  const list = $('#jobs-list');
  if (!list) return;
  list.textContent = '';

  if (!state.jobs.length) {
    const empty = el('div', { class: 'empty' });
    empty.appendChild(el('div', { class: 'empty-icon', text: '🗂' }));
    empty.appendChild(el('div', { class: 'empty-title', text: '还没有任务' }));
    empty.appendChild(el('div', { class: 'empty-sub', text: '在「图片 / PDF / 转换 / OCR」页提交后，任务会显示在这里' }));
    list.appendChild(empty);
    return;
  }
  for (const job of state.jobs) list.appendChild(renderJobRow(job));
}

/** 任务标签上的进行中计数。 */
function updateJobsBadge() {
  const tabBtn = document.querySelector('.tab[data-tab="jobs"]');
  if (!tabBtn) return;
  tabBtn.textContent = '任务';
  const active = state.jobs.filter((j) => j.status === 'pending' || j.status === 'running').length;
  if (active > 0) tabBtn.appendChild(el('span', { class: 'tab-badge', text: String(active) }));
}

/** 安排下一次轮询：有活动任务 1.5 秒，否则 10 秒。 */
function schedulePoll() {
  clearTimeout(state.pollTimer);
  const active = state.jobs.some((j) => j.status === 'pending' || j.status === 'running');
  state.pollTimer = setTimeout(() => refreshJobs(true), active ? 1500 : 10000);
  const note = $('#jobs-poll-note');
  if (note) note.textContent = active ? '有任务进行中，每 1.5 秒刷新' : '每 10 秒刷新';
}

/** 刷新任务列表。 */
async function refreshJobs(silent) {
  // 已有请求在飞时不能直接丢弃：提交 / 删除 / 取消后的立即刷新若被吞掉，
  // 列表会一直停在旧内容，直到下一次轮询（空闲时长达 10 秒）。
  if (state.jobsLoading) {
    state.jobsPending = true;
    return;
  }
  state.jobsLoading = true;
  try {
    const data = await api('/api/jobs?limit=50', { silent: !!silent });
    state.jobs = Array.isArray(data) ? data : [];
    renderJobs();
    updateJobsBadge();
  } catch (err) {
    // 静默轮询失败不打扰用户；手动刷新时 api() 已弹过 toast
  } finally {
    state.jobsLoading = false;
    if (state.jobsPending) {
      state.jobsPending = false;
      await refreshJobs(silent);
      return;
    }
    schedulePoll();
  }
}

/** 取消任务。 */
async function cancelJob(job, btn) {
  try {
    setBusy(btn, true, '取消中…');
    await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: 'POST' });
    toast('已请求取消该任务。', 'ok');
    await refreshJobs(true);
  } catch (err) {
    if (!(err instanceof ApiError)) toast('取消失败。', 'error');
  } finally {
    setBusy(btn, false);
  }
}

/** 删除任务及其产物。 */
async function deleteJob(job, btn) {
  if (!window.confirm(`确定删除任务 #${shortId(job.id)} 及其产物？此操作不可撤销。`)) return;
  try {
    setBusy(btn, true, '删除中…');
    await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: 'DELETE' });
    toast('任务已删除。', 'ok');
    await refreshJobs(true);
  } catch (err) {
    if (!(err instanceof ApiError)) toast('删除失败。', 'error');
  } finally {
    setBusy(btn, false);
  }
}

/* ---------------------------------------------------------- 预览模态 */

function closeModal() {
  const root = $('#modal-root');
  if (!root) return;
  root.hidden = true;
  root.textContent = '';
  document.body.classList.remove('modal-open');
}

/** 打开输出文件预览：图片用 img、PDF 用 iframe、文本内联显示。 */
function openPreview(job, output, url, kind) {
  const root = $('#modal-root');
  if (!root) return;
  root.textContent = '';
  root.hidden = false;
  document.body.classList.add('modal-open');

  const modal = el('div', { class: 'modal' });

  const head = el('div', { class: 'modal-head' });
  head.appendChild(el('div', { class: 'modal-title', text: output.name, title: output.name }));
  head.appendChild(el('span', {
    class: 'modal-sub muted small',
    text: `${labelFor(job.kind, job.op)} · ${humanSize(output.size)}`,
  }));
  head.appendChild(el('span', { class: 'spacer' }));
  if (isSafeUrl(url)) {
    head.appendChild(el('a', { class: 'btn small', href: url, download: output.name, text: '下载' }));
  }
  const close = el('button', { class: 'modal-close', type: 'button', 'aria-label': '关闭', text: '×' });
  close.addEventListener('click', closeModal);
  head.appendChild(close);
  modal.appendChild(head);

  const body = el('div', { class: 'modal-body' });
  modal.appendChild(body);

  if (!isSafeUrl(url)) {
    body.appendChild(el('div', { class: 'alert warn', text: '该输出没有可用的预览地址。' }));
  } else if (kind === 'image') {
    const img = el('img', { class: 'preview-img', alt: output.name });
    img.src = url;
    img.addEventListener('error', () => {
      body.textContent = '';
      body.appendChild(el('div', { class: 'alert warn', text: '图片预览加载失败，请直接下载查看。' }));
    });
    body.appendChild(img);
  } else if (kind === 'pdf') {
    body.appendChild(el('iframe', { class: 'preview-frame', src: url, title: output.name }));
  } else {
    const pre = el('pre', { class: 'preview-text', text: '正在加载…' });
    body.appendChild(pre);
    api(url, { silent: true })
      .then((text) => { pre.textContent = typeof text === 'string' ? text : String(text ?? ''); })
      .catch((err) => { pre.textContent = `加载失败：${err.message || '未知错误'}`; });
  }

  root.appendChild(modal);
}

/* ------------------------------------------------------------ 系统页 */

/** 小标题行。 */
function panelHead(title) {
  return el('div', { class: 'panel-head' }, [el('h2', { class: 'panel-title', text: title })]);
}

/** 键值对；opts.wrap 用于长路径，允许换行完整显示。 */
function kv(label, value, opts = {}) {
  const text = value === null || value === undefined || value === '' ? '—' : String(value);
  const valueNode = el('div', { class: 'kv-v', text });
  if (text.length > 18) valueNode.title = text;
  return el('div', { class: `kv${opts.wrap ? ' kv-wrap' : ''}` }, [
    el('div', { class: 'kv-k', text: label }),
    valueNode,
  ]);
}

/** 渲染系统页。 */
function renderSystem() {
  const overview = $('#system-overview');
  const enginesPanel = $('#system-engines-panel');
  const ocrPanel = $('#system-ocr-panel');
  const notes = $('#system-notes');
  if (!overview) return;

  const system = state.system;
  const formats = state.formats;

  if (!system) {
    overview.textContent = '';
    overview.appendChild(panelHead('系统信息'));
    overview.appendChild(el('p', { class: 'muted', text: '正在读取系统信息…' }));
    return;
  }

  /* --- 概览 --- */
  overview.textContent = '';
  overview.appendChild(panelHead('系统信息'));
  const grid = el('div', { class: 'kv-grid' });
  grid.appendChild(kv('版本', system.version || '—'));
  grid.appendChild(kv('Python', system.python || '—'));
  grid.appendChild(kv('平台', system.platform || '—'));
  grid.appendChild(kv('许可', system.license || 'MIT'));
  if (system.limits) {
    grid.appendChild(kv('单文件上限', humanSize(system.limits.max_upload_bytes)));
    grid.appendChild(kv('批量上限', `${system.limits.max_batch_files} 个`));
    grid.appendChild(kv('并发任务', `${system.limits.max_concurrent_jobs} 个`));
  }
  if (system.retention) {
    grid.appendChild(kv('任务记录保留', humanDuration(system.retention.job_seconds)));
    grid.appendChild(kv('产物保留', humanDuration(system.retention.output_seconds)));
  }
  overview.appendChild(grid);

  if (formats) {
    const fmtGrid = el('div', { class: 'kv-grid kv-grid-wide' });
    fmtGrid.appendChild(kv('图片输出格式', (formats.image_output || []).join(' / ') || '—'));
    fmtGrid.appendChild(kv('Office 目标', (formats.office_targets || []).join(' / ') || '—'));
    fmtGrid.appendChild(kv('Pandoc 目标', (formats.pandoc_targets || []).join(' / ') || '—'));
    fmtGrid.appendChild(kv('图片操作', (formats.image_ops || []).join(' / ') || '—'));
    fmtGrid.appendChild(kv('PDF 操作', (formats.pdf_ops || []).join(' / ') || '—'));
    overview.appendChild(fmtGrid);
  }

  /* --- 引擎表 --- */
  const engines = system.engines || [];
  enginesPanel.textContent = '';
  enginesPanel.appendChild(panelHead('外部引擎'));

  if (!engines.length) {
    enginesPanel.appendChild(el('p', { class: 'muted', text: '未读取到引擎信息。' }));
  } else {
    const wrap = el('div', { class: 'table-wrap' });
    const table = el('table', { class: 'table' });
    const thead = el('thead');
    const headRow = el('tr');
    for (const title of ['名称', '许可', '用途', '状态', '路径']) {
      headRow.appendChild(el('th', { text: title }));
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = el('tbody');
    for (const engine of engines) {
      const bad = !!engine.required && !engine.available;
      const tr = el('tr', { class: bad ? 'row-bad' : '' });

      tr.appendChild(el('td', { text: engine.name || engine.key || '—' }));

      const licenseCell = el('td');
      if (typeof engine.homepage === 'string' && /^https?:\/\//.test(engine.homepage)) {
        licenseCell.appendChild(el('a', {
          href: engine.homepage, target: '_blank', rel: 'noreferrer noopener', text: engine.license || '—',
        }));
      } else {
        licenseCell.textContent = engine.license || '—';
      }
      tr.appendChild(licenseCell);

      tr.appendChild(el('td', { text: engine.purpose || '—' }));

      let statusText;
      let statusCls;
      if (engine.available) { statusText = '就绪'; statusCls = 'ok'; }
      else if (engine.required) { statusText = '缺失（必需）'; statusCls = 'bad'; }
      else { statusText = '未安装（可选）'; statusCls = 'optional'; }
      tr.appendChild(el('td', {}, [el('span', { class: `engine-status ${statusCls}`, text: statusText })]));

      tr.appendChild(el('td', {
        class: 'cell-path',
        text: engine.path || (engine.available ? '—' : '未找到可执行文件'),
      }));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    enginesPanel.appendChild(wrap);
  }

  const missingRequired = engines.filter((e) => e.required && !e.available);
  const missingOptional = engines.filter((e) => !e.required && !e.available);

  if (missingRequired.length) {
    const box = el('div', { class: 'alert danger' });
    box.appendChild(el('div', {
      text: `必需引擎未就绪：${missingRequired.map((e) => e.name).join('、')}。相关功能会直接失败，请在项目根目录运行：`,
    }));
    box.appendChild(el('code', { class: 'cmd', text: MSG_FETCH_ENGINES }));
    enginesPanel.appendChild(box);
  }
  if (missingOptional.length) {
    const box = el('div', { class: 'alert warn' });
    box.appendChild(el('div', {
      text: `可选引擎未安装：${missingOptional.map((e) => e.name).join('、')}。对应功能不可用，如需启用请运行：`,
    }));
    box.appendChild(el('code', { class: 'cmd', text: MSG_FETCH_ENGINES }));
    enginesPanel.appendChild(box);
  }
  if (!missingRequired.length && !missingOptional.length && engines.length) {
    enginesPanel.appendChild(el('div', { class: 'alert ok', text: '全部引擎均已就绪。' }));
  }

  /* --- Tesseract 专项 --- */
  ocrPanel.textContent = '';
  ocrPanel.appendChild(panelHead('Tesseract OCR 语言包'));
  const tesseract = engines.find((e) => e.key === 'tesseract');
  const langNames = (formats && formats.ocr_languages) || {};
  const installedLangs = (formats && formats.installed_ocr_languages) || [];

  const ocrGrid = el('div', { class: 'kv-grid kv-grid-wide' });
  ocrGrid.appendChild(kv('状态', tesseract ? (tesseract.available ? '就绪' : '未安装') : '未登记'));
  ocrGrid.appendChild(kv('tessdata 路径', (tesseract && tesseract.tessdata) || '—', { wrap: true }));
  ocrGrid.appendChild(kv('可执行文件', (tesseract && tesseract.path) || '—', { wrap: true }));
  ocrGrid.appendChild(kv(
    '已安装语言',
    installedLangs.length
      ? installedLangs.map((code) => `${langNames[code] || code}（${code}）`).join('、')
      : '无',
  ));
  ocrPanel.appendChild(ocrGrid);

  const declared = (tesseract && tesseract.languages) || [];
  if (declared.length) {
    const chips = el('div', { class: 'langs' });
    for (const code of declared) {
      const has = installedLangs.includes(code);
      const chip = el('span', { class: `lang-chip${has ? '' : ' disabled'}` });
      chip.appendChild(el('span', { class: 'lang-name', text: `${langNames[code] || code}（${code}）` }));
      chip.appendChild(el('span', { class: 'lang-tag', text: has ? '已安装' : '未安装' }));
      chips.appendChild(chip);
    }
    ocrPanel.appendChild(chips);
  }

  if (!tesseract || !tesseract.available || !installedLangs.length) {
    const box = el('div', { class: 'alert danger' });
    box.appendChild(el('div', { text: 'Tesseract 或语言包缺失，OCR 功能不可用。请在项目根目录运行：' }));
    box.appendChild(el('code', { class: 'cmd', text: MSG_FETCH_TESSERACT }));
    ocrPanel.appendChild(box);
  }

  /* --- 许可红线 --- */
  notes.textContent = '';
  notes.appendChild(panelHead('许可说明'));
  const list = el('ul', { class: 'notes-list' });
  list.appendChild(el('li', {}, [
    el('strong', { text: '本项目采用 MIT 许可' }),
    document.createTextNode('，所有 Python 依赖均为 MIT / BSD / Apache-2.0 / MPL-2.0 宽松许可。'),
  ]));
  list.appendChild(el('li', {
    text: '为守住许可红线，刻意不引入 Ghostscript（AGPL）、PyMuPDF / Poppler（AGPL / GPL）等强传染性组件。',
  }));
  list.appendChild(el('li', {
    text: 'Pandoc 为 GPL-2.0-or-later，仅以独立进程方式调用，不与本项目代码链接，因而不影响 docpix 的 MIT 许可。',
  }));
  list.appendChild(el('li', {
    text: '所有引擎均从官方渠道获取，只解压到项目目录（F 盘），不写入系统盘。',
  }));
  notes.appendChild(list);
}

/* --------------------------------------------------------- 系统自检 */

/**
 * 读取 /api/system 与 /api/system/formats 并刷新界面。
 * @returns {Promise<boolean>} 是否成功
 */
async function loadSystem(silent = false) {
  try {
    const [system, formats] = await Promise.all([
      api('/api/system', { silent }),
      api('/api/system/formats', { silent }),
    ]);
    state.system = system;
    state.formats = formats;
    applySystem();
    return true;
  } catch (err) {
    return false;
  }
}

/** 把系统信息应用到界面各处。 */
function applySystem() {
  const system = state.system;
  if (system) {
    const badge = $('#version-badge');
    if (badge) badge.textContent = `v${system.version || '—'}`;
    document.title = `docpix v${system.version || ''} · 本地文档与图片处理`;
  }
  updateAccepts();
  // 格式表到位后重建参数表单，让下拉框拿到真实候选值
  for (const key of ['image', 'pdf']) {
    saveOpValues(key);
    renderOpSection(key);
  }
  renderConvert();
  renderOcrLangs();
  renderSystem();
}

/** 顶部「系统自检」。 */
async function selfCheck() {
  const btn = $('#selfcheck-btn');
  setBusy(btn, true, '自检中…');
  const ok = await loadSystem(false);
  setBusy(btn, false);

  if (!ok) {
    toast('自检失败：无法读取 /api/system，请确认后端服务已启动。', 'error', 9000);
    return;
  }

  const engines = (state.system && state.system.engines) || [];
  const missingRequired = engines.filter((e) => e.required && !e.available);
  const missingOptional = engines.filter((e) => !e.required && !e.available);

  if (!missingRequired.length && !missingOptional.length) {
    toast('自检完成：全部引擎均已就绪。', 'ok');
  } else if (!missingRequired.length) {
    toast(`自检完成：必需引擎就绪，另有 ${missingOptional.length} 个可选引擎未安装。`, 'warn', 8000);
  } else {
    toast(`自检完成：${missingRequired.map((e) => e.name).join('、')} 未就绪。请在项目根目录运行 ${MSG_FETCH_ENGINES}`, 'error', 12000);
  }
  switchTab('system');
}

/* ------------------------------------------------------------ 标签页 */

/** 切换标签页。 */
function switchTab(name) {
  state.tab = name;
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === name);
  });
  document.querySelectorAll('.page').forEach((page) => {
    page.classList.toggle('active', page.dataset.page === name);
  });
  if (name === 'jobs') refreshJobs(true);
  if (name === 'system') {
    renderSystem();
    if (!state.system) loadSystem(true);
  }
}

/* ------------------------------------------------------------ 初始化 */

/** 图片 / PDF 页初始化。 */
function initToolPage(key) {
  buildOpSelect(key);
  wireDropzone(key);

  const clearBtn = document.getElementById(`clear-${key}`);
  if (clearBtn) clearBtn.addEventListener('click', () => clearFiles(key));

  const opSelect = document.getElementById(`op-${key}`);
  if (opSelect) opSelect.addEventListener('change', (event) => setOp(key, event.target.value));

  const submit = document.getElementById(`submit-${key}`);
  if (submit) submit.addEventListener('click', () => submitTool(key));

  setOp(key, state[key].op, true);
  renderFileList(key);
}

/** 转换页初始化。 */
function initConvertPage() {
  wireDropzone('convert');
  const clearBtn = $('#clear-convert');
  if (clearBtn) clearBtn.addEventListener('click', () => clearFiles('convert'));
  const submit = $('#submit-convert');
  if (submit) submit.addEventListener('click', submitConvert);
  renderConvert();
}

/** OCR 页初始化。 */
function initOcrPage() {
  wireDropzone('ocr');
  const clearBtn = $('#clear-ocr');
  if (clearBtn) clearBtn.addEventListener('click', () => clearFiles('ocr'));
  const submit = $('#submit-ocr');
  if (submit) submit.addEventListener('click', submitOcr);

  const formEl = $('#form-ocr');
  if (formEl) {
    renderParamsForm(formEl, OCR_FIELDS, defaultValues(OCR_FIELDS));
    bindFormConditions(formEl, OCR_FIELDS);
  }
  renderOcrLangs();
  renderFileList('ocr');
}

/** 任务页初始化。 */
function initJobsPage() {
  const refresh = $('#jobs-refresh');
  if (refresh) refresh.addEventListener('click', () => refreshJobs(false));
  const note = $('#jobs-poll-note');
  if (note) note.textContent = '每 10 秒刷新';
  renderJobs();
}

/** 全局事件绑定。 */
function bindGlobal() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  const selfcheck = $('#selfcheck-btn');
  if (selfcheck) selfcheck.addEventListener('click', selfCheck);

  // 模态框：点击遮罩关闭、Esc 关闭
  const root = $('#modal-root');
  if (root) {
    root.addEventListener('click', (event) => { if (event.target === root) closeModal(); });
  }
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal();
  });

  // 阻止拖到页面空白处时浏览器直接打开文件
  window.addEventListener('dragover', (event) => event.preventDefault());
  window.addEventListener('drop', (event) => event.preventDefault());

  // 页面重新可见时立刻刷新一次任务
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshJobs(true);
  });
}

/** 启动。 */
async function boot() {
  bindGlobal();
  initToolPage('image');
  initToolPage('pdf');
  initConvertPage();
  initOcrPage();
  initJobsPage();
  switchTab('image');

  await loadSystem(false);
  await refreshJobs(true);
}

boot();
