/* docpix 界面端到端验证：无头 Edge(CDP) 驱动真界面 → 真后端。
 *
 * 前置条件（见 tests/e2e/README.md）：
 *   1. 后端在跑：python -m app
 *   2. 无头 Edge 开着调试端口：
 *      msedge --headless=new --remote-debugging-port=9222 http://127.0.0.1:8765/
 *   3. 素材已生成：python tests/e2e/make_fixtures.py
 *
 * 用法：
 *   node tests/e2e/ui_e2e.mjs
 *   DOCPIX_ORIGIN=http://127.0.0.1:8766 DOCPIX_E2E_LOG=.tmp/ui_e2e.log node tests/e2e/ui_e2e.mjs
 */

import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const CDP = process.env.DOCPIX_CDP || 'http://127.0.0.1:9222';
const ORIGIN = (process.env.DOCPIX_ORIGIN || 'http://127.0.0.1:8765').replace(/\/+$/, '');
const IN = (process.env.DOCPIX_FIXTURES || join(HERE, 'fixtures')).replace(/\\/g, '/');
const LOG = process.env.DOCPIX_E2E_LOG || '';
const PORT = ORIGIN.split(':').pop();

const lines = [];
let failures = 0;
const flush = () => {
  if (!LOG) return;
  try { writeFileSync(LOG, lines.join('\n')); } catch (e) { /* 忽略 */ }
};
const check = (name, ok, detail = '') => {
  if (!ok) failures += 1;
  lines.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  →  ${detail}` : ''}`);
  flush();
};
const info = (name, v) => { lines.push(`INFO  ${name}  →  ${JSON.stringify(v)}`); flush(); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const targets = await (await fetch(`${CDP}/json`)).json();
const target = targets.find((t) => t.type === 'page' && t.url.includes(`:${PORT}`));
if (!target) throw new Error(`找不到 ${ORIGIN} 的页面：` + JSON.stringify(targets.map((t) => t.url)));
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let msgId = 0;
const pending = new Map();
const consoleErrors = [];
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    return;
  }
  if (m.method === 'Runtime.exceptionThrown') {
    consoleErrors.push('EXCEPTION: ' + (m.params.exceptionDetails.exception?.description
      || m.params.exceptionDetails.text));
  }
  if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type)) {
    consoleErrors.push(`console.${m.params.type}: `
      + m.params.args.map((a) => a.value ?? a.description ?? a.type).join(' '));
  }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  msgId += 1;
  pending.set(msgId, { resolve, reject });
  ws.send(JSON.stringify({ id: msgId, method, params }));
});
const ev = async (expr, awaitPromise = false) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) {
    throw new Error('eval 出错: ' + expr.slice(0, 90) + ' → '
      + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  }
  return r.result?.value;
};

await send('Runtime.enable');
await send('Page.enable');
await send('DOM.enable');
await send('Page.reload', { ignoreCache: true });
await sleep(3000);

/* ---------------------------------------------------------------- 基础 */
const badge = await ev(`document.getElementById('version-badge').textContent`);
const backendVersion = (await (await fetch(`${ORIGIN}/api/system`)).json()).version;
check('版本徽标来自真后端 /api/system',
  (badge || '').includes(`v${backendVersion}`), `${badge} vs 后端 v${backendVersion}`);

const tabs = await ev(`[...document.querySelectorAll('#tabs .tab')].map(b => b.dataset.tab)`);
check('6 个标签页', JSON.stringify(tabs) === JSON.stringify(['image', 'pdf', 'convert', 'ocr', 'jobs', 'system']),
  JSON.stringify(tabs));

for (const tab of ['image', 'pdf', 'convert', 'ocr', 'jobs', 'system']) {
  await ev(`document.querySelector('#tabs .tab[data-tab="${tab}"]').click()`);
  await sleep(250);
  const visible = await ev(`(() => {
    const el = document.getElementById('page-${tab}');
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && el.offsetHeight > 0;
  })()`);
  check(`切到「${tab}」页可见`, !!visible);
}

/* ---------------------------------------------------- 系统页：4 引擎可用 */
await ev(`document.querySelector('#tabs .tab[data-tab="system"]').click()`);
await sleep(600);
const enginesText = await ev(`document.getElementById('system-engines-panel').textContent`);
const need = ['LibreOffice', 'Pandoc', 'qpdf', 'Tesseract'];
check('系统页列出 4 个引擎', need.every((n) => enginesText.includes(n)), need.filter((n) => !enginesText.includes(n)));
check('系统页无「未安装/缺失」红条', !/未安装|未检测到|缺失/.test(enginesText),
  (enginesText.match(/未安装|未检测到|缺失/g) || []).join(','));
const overview = await ev(`document.getElementById('system-overview').textContent`);
check('系统页显示限额与保留时长', /单文件上限/.test(overview) && /保留/.test(overview), overview.slice(0, 60));
const notes = await ev(`document.getElementById('system-notes').textContent`);
check('系统页声明 MIT 与排除项', /MIT/.test(notes) && /Ghostscript/.test(notes));
const ocrPanel = await ev(`document.getElementById('system-ocr-panel').textContent`);
check('系统页 OCR 面板显示 chi_sim 已安装', /chi_sim/.test(ocrPanel) && /已安装/.test(ocrPanel));

/* ------------------------------------------------------------------ 工具 */
async function setFiles(selector, files) {
  const { root } = await send('DOM.getDocument', {});
  const { nodeId } = await send('DOM.querySelector', { nodeId: root.nodeId, selector });
  if (!nodeId) throw new Error('找不到 input: ' + selector);
  await send('DOM.setFileInputFiles', { files, nodeId });
  await sleep(400);
}
async function setSelect(selector, value) {
  await ev(`(() => {
    const el = document.querySelector('${selector}');
    el.value = ${JSON.stringify(value)};
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value;
  })()`);
  await sleep(250);
}
async function jobs() {
  const r = await fetch(`${ORIGIN}/api/jobs?limit=20`);
  return r.json();
}
async function newest(before) {
  const list = await jobs();
  return list.find((j) => !before.has(j.id)) || null;
}
async function submitAndWait(name, selector, before, timeoutMs = 240000) {
  await ev(`document.querySelector('${selector}').click()`);
  await sleep(1500); // toast 会自动消失，先抓一次，便于诊断「为什么没提交」
  const earlyToast = await ev(`document.getElementById('toasts').textContent`);
  const t0 = Date.now();
  let rec = null;
  while (Date.now() - t0 < timeoutMs) {
    rec = await newest(before);
    if (rec && ['done', 'error', 'canceled'].includes(rec.status)) break;
    await sleep(800);
  }
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  let detail = rec ? `${rec.status} ${rec.error || ''} (${secs}s)` : `没有新任务 (${secs}s)`;
  if (!rec) detail += ` | 点击后提示条: ${earlyToast || '(空)'}`;
  check(`${name} · 后端终态 done`, !!rec && rec.status === 'done', detail);
  info(`${name} · 任务 id / 耗时`, rec ? `${rec.id} / ${secs}s` : '无');

  // 前端是轮询刷新的，等它把状态同步到 DOM 再断言
  let uiText = '';
  for (let k = 0; k < 24; k += 1) {
    uiText = await ev(`document.getElementById('jobs-list').textContent`);
    if (/完成|失败|已取消/.test(uiText || '')) break;
    await sleep(500);
  }
  check(`${name} · UI 任务卡显示「完成」`, /完成/.test(uiText || ''), (uiText || '').slice(0, 50));
  check(`${name} · UI 无错误提示`, !/失败|错误/.test(uiText || ''), (uiText || '').slice(0, 60));
  return rec;
}
async function getIdSet() {
  return new Set((await jobs()).map((j) => j.id));
}
async function download(url) {
  const r = await fetch(ORIGIN + url);
  const buf = new Uint8Array(await r.arrayBuffer());
  return { status: r.status, size: buf.length, head: Array.from(buf.slice(0, 4)) };
}

/* ------------------------------------------------------------ 1) 图片转换 */
await ev(`document.querySelector('#tabs .tab[data-tab="image"]').click()`);
await sleep(300);
let before = await getIdSet();
await setFiles('#input-image', [`${IN}/img_a.png`, `${IN}/img_b.png`, `${IN}/img_c.png`]);
const summary = await ev(`document.getElementById('summary-image').textContent`);
check('图片页识别 3 个真实文件', /3/.test(summary || ''), summary);
const opVal = await ev(`document.getElementById('op-image').value`);
check('图片页默认操作为 convert', opVal === 'convert', opVal);
const fmtOpts = await ev(`[...document.querySelectorAll('#form-image select')]
  .map(s => [...s.options].map(o => o.value))`);
check('convert 目标格式候选来自后端 image_output',
  fmtOpts.some((arr) => arr.includes('webp') && arr.includes('avif') && arr.length >= 9),
  JSON.stringify(fmtOpts));

let rec = await submitAndWait('图片 convert→png', '#submit-image', before);
if (rec && rec.status === 'done') {
  check('图片 convert 产物数 = 输入数 (3)', rec.outputs.length === 3,
    JSON.stringify(rec.outputs.map((o) => o.name)));
  check('产物名带 _out 后缀', rec.outputs.every((o) => /_out\.png$/.test(o.name)),
    JSON.stringify(rec.outputs.map((o) => o.name)));
  const dls = [];
  for (const o of rec.outputs) dls.push(await download(o.url));
  check('3 个产物下载均 200 且非空', dls.every((d) => d.status === 200 && d.size > 0),
    JSON.stringify(dls.map((d) => [d.status, d.size])));
  check('产物是合法 PNG', dls.every((d) => d.head.join() === '137,80,78,71'),
    JSON.stringify(dls.map((d) => d.head.join())));
  let chips = -1;
  // 只看当前任务那张卡片：任务列表会累积历史任务，全页统计会随历史变多而误报
  const shortId = (id) => {
    const parts = String(id || '').split('-');
    return parts.length >= 3 ? parts.slice(-2).join('-') : String(id || '').slice(0, 8);
  };
  for (let k = 0; k < 20; k += 1) {
    chips = await ev(`(() => {
      const card = [...document.querySelectorAll('#jobs-list .job')].find((r) =>
        (r.querySelector('.job-id')?.textContent || '').includes(${JSON.stringify(shortId(rec.id))}));
      if (!card) return -1;
      return new Set([...card.querySelectorAll('a[href*="/files/"]')]
        .map((a) => a.getAttribute('href'))).size;
    })()`);
    if (chips === 3) break;
    await sleep(500);
  }
  check('当前任务卡片上的产物下载链接数 = 3（按唯一 URL 计）', chips === 3, String(chips));
  const wire = rec.params || {};
  check('wire 里 params.format 透传到任务记录', wire.format === 'png', JSON.stringify(wire).slice(0, 120));
}
  const afterSubmit = await ev(`document.getElementById('summary-image').textContent`);
  check('提交成功后上传区自动清空（设计如此）', /尚未选择文件/.test(afterSubmit || ''), afterSubmit);

/* --------------------------------------------------------- 2) 图片 resize */
before = await getIdSet();
await ev(`document.querySelector('#tabs .tab[data-tab="image"]').click()`);
await sleep(200);
// 提交成功后前端会清空上传区，这里必须重新选文件（真实用户也是这么操作的）
await setFiles('#input-image', [`${IN}/img_a.png`, `${IN}/img_b.png`, `${IN}/img_c.png`]);
await sleep(300);
const reSummary = await ev(`document.getElementById('summary-image').textContent`);
check('重新选文件后图片页可用', /3/.test(reSummary || ''), reSummary);
await setSelect('#op-image', 'resize');
const resizeFields = await ev(`[...document.querySelectorAll('#form-image input, #form-image select')]
  .map(e => e.name || e.type)`);
info('resize 字段', resizeFields);
const filled = await ev(`(() => {
  const w = document.getElementById('fld-width');
  const h = document.getElementById('fld-height');
  if (!w || !h) return 'missing-input';
  const set = (el, v) => {
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  set(w, '120'); set(h, '80');
  return [w.value, h.value];
})()`);
check('resize 宽高能写入表单', JSON.stringify(filled) === '["120","80"]', JSON.stringify(filled));
rec = await submitAndWait('图片 resize→120x80 (mode=fit)', '#submit-image', before, 120000);
if (rec && rec.status === 'done') {
  const o = rec.outputs[0];
  const d = await download(o.url);
  check('resize 产物非空', d.status === 200 && d.size > 0, JSON.stringify(d));
  info('resize 产物', `${o.name} ${d.size}B`);
/* --------------------------------------------------- 2b) to_ico（新增入口） */
await ev(`document.querySelector('#tabs .tab[data-tab="image"]').click()`);
await sleep(300);
before = await getIdSet();
await setFiles('#input-image', [`${IN}/img_a.png`]);
await sleep(300);
await setSelect('#op-image', 'to_ico');
const opOptions = await ev(`[...document.querySelectorAll('#op-image option')].map(o => o.value)`);
check('图片操作下拉里有 to_ico', opOptions.includes('to_ico'), JSON.stringify(opOptions));
rec = await submitAndWait('图片 to_ico（生成 .ico）', '#submit-image', before, 120000);
if (rec && rec.status === 'done') {
  const o = rec.outputs[0];
  const d = await download(o.url);
  check('to_ico 产物扩展名是 .ico', /\.ico$/.test(o.name), o.name);
  check('to_ico 产物是 ICO 魔数（不是 .png 里装 ICO）',
    d.head.join() === '0,0,1,0', `${o.name} head=${d.head.join()} ${d.size}B`);
  info('to_ico 产物', `${o.name} ${d.size}B`);
}
}

/* ------------------------------------------------------------ 3) md → pdf */
await ev(`document.querySelector('#tabs .tab[data-tab="convert"]').click()`);
await sleep(300);
before = await getIdSet();
await setFiles('#input-convert', [`${IN}/notes.md`]);
const src = await ev(`document.getElementById('convert-source').textContent`);
check('转换页识别 md 源格式', /md/i.test(src || ''), src);
const targets2 = await ev(`[...document.querySelectorAll('#convert-target option')].map(o => o.value)`);
check('md 的目标候选含 pdf 且不含图片格式',
  targets2.includes('pdf') && !targets2.includes('png'), JSON.stringify(targets2.slice(0, 8)));
await setSelect('#convert-target', 'pdf');
rec = await submitAndWait('转换 md→pdf（pandoc+LibreOffice）', '#submit-convert', before, 300000);
if (rec && rec.status === 'done') {
  const o = rec.outputs[0];
  const d = await download(o.url);
  check('md→pdf 产物是 PDF', d.head.join() === '37,80,68,70', JSON.stringify(d));
  check('md→pdf 记录里带两跳链路',
    Array.isArray(rec.plan) && rec.plan[0]?.steps?.length === 2,
    JSON.stringify((rec.plan || []).map((p) => p.steps?.map((s) => s.op))));
  const pv = await fetch(ORIGIN + o.preview_url);
  const pvBuf = new Uint8Array(await pv.arrayBuffer());
  check('PDF 预览接口返回 PNG', pv.status === 200 && Array.from(pvBuf.slice(0, 4)).join() === '137,80,78,71',
    `${pv.status} ${pvBuf.length}B`);
}

/* ---------------------------------------------------------------- 4) OCR */
await ev(`document.querySelector('#tabs .tab[data-tab="ocr"]').click()`);
await sleep(600);
const chips = await ev(`[...document.querySelectorAll('#ocr-langs .lang-chip')].map(c => ({
  text: c.textContent, selected: c.classList.contains('selected'), disabled: c.classList.contains('disabled') }))`);
check('OCR 语言标签渲染中文名（不是 [object Object]）',
  chips.length > 0 && chips.every((c) => !/object Object/.test(c.text)),
  JSON.stringify(chips.slice(0, 3)));
const installedChips = chips.filter((c) => !c.disabled);
check('已安装语言为 chi_sim/eng', installedChips.length === 2, JSON.stringify(installedChips.map((c) => c.text)));
check('默认已选中简体中文', installedChips.some((c) => c.selected && /简体中文/.test(c.text)),
  JSON.stringify(installedChips.map((c) => [c.text, c.selected])));
check('未安装语言被禁用', chips.filter((c) => c.disabled).length >= 5,
  String(chips.filter((c) => c.disabled).length));
const ocrSelects = await ev(`[...document.querySelectorAll('#form-ocr select')].map(s => ({
  name: s.name || s.id, value: s.value,
  options: [...s.options].map(o => o.value) }))`);
const outSel = ocrSelects.find((s) => s.options.includes('pdfa-3'));
check('OCR 输出类型候选来自后端（含 pdfa-1/2/3 共 6 项）',
  !!outSel && outSel.options.length === 6, JSON.stringify(ocrSelects.map((s) => s.options)));
const optSel = ocrSelects.find((s) => JSON.stringify(s.options) === JSON.stringify(['0', '1', '2', '3']));
check('OCR optimize 是 0-3 档下拉且默认 1', !!optSel && optSel.value === '1',
  JSON.stringify(ocrSelects));

before = await getIdSet();
await setFiles('#input-ocr', [`${IN}/ocr_sample.png`]);
rec = await submitAndWait('OCR 图片→可搜索 PDF', '#submit-ocr', before, 300000);
if (rec && rec.status === 'done') {
  const o = rec.outputs[0];
  const d = await download(o.url);
  check('OCR 产物是 PDF', d.head.join() === '37,80,68,70', JSON.stringify(d));
  const pvRes = await fetch(`${ORIGIN}/api/jobs/${rec.id}/preview/${encodeURIComponent(o.name)}`);
  const pvBuf = new Uint8Array(await pvRes.arrayBuffer());
  check('OCR 产物预览返回 PNG', pvRes.status === 200
    && Array.from(pvBuf.slice(0, 4)).join() === '137,80,78,71', `${pvRes.status} ${pvBuf.length}B`);
}

/* ---------------------------------------------------------- 5) 预览弹窗 */
await ev(`document.querySelector('#tabs .tab[data-tab="jobs"]').click()`);
await sleep(500);
const modalOk = await ev(`(async () => {
  const btn = [...document.querySelectorAll('#jobs-list .job button')].find(b => b.textContent.includes('预览'));
  if (!btn) return 'no-button';
  btn.click();
  await new Promise(r => setTimeout(r, 1500));
  const root = document.getElementById('modal-root');
  return { hidden: root.hidden, html: root.innerHTML.length,
           hasImg: !!root.querySelector('img'), hasFrame: !!root.querySelector('iframe'),
           hasPre: !!root.querySelector('pre') };
})()`, true);
check('任务页可打开预览弹窗', !!modalOk && modalOk !== 'no-button' && modalOk.hidden === false,
  JSON.stringify(modalOk));
await ev(`(() => { const b = document.querySelector('#modal-root .modal-close, #modal-root [data-close]');
  if (b) b.click(); return true; })()`);

/* ---------------------------------------------------------------- 结果 */
info('控制台异常/警告数', consoleErrors.length);
if (consoleErrors.length) consoleErrors.slice(0, 6).forEach((e) => info('console', e));

console.log(lines.join('\n'));
console.log(`\n${failures === 0 ? '全部通过' : failures + ' 项失败'}  (共 ${lines.filter((l) => l.startsWith('PASS') || l.startsWith('FAIL')).length} 项断言)`);
if (consoleErrors.length) process.exitCode = 2;
else process.exitCode = failures === 0 ? 0 : 1;
process.exit(process.exitCode);
