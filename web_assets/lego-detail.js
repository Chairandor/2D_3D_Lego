import './model-preview.js';
import {mountInstructionPreview} from './instruction-preview.js';
import {mountPartThumbnails, collectPartThumbnails} from './part-thumbnails.js';

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const query = new URLSearchParams(location.search);
const modelPath = query.get('path') || '';
let data;
let generating = false;
let bomError = '';
let exportingParts = false;
let currentPage = 'menu';
let instructions;
let instructionTimer;
let instructionEpoch = 0;
let stepPosition = 0;
let alternate = false;
const resetStepImage = mountInstructionPreview($('stepImage'), $('stepCanvas'), $('stepImageStatus'));

function showPage(name) {
  if (name !== 'menu' && name !== 'message' && !data) return;
  currentPage = name;
  instructionEpoch += 1;
  clearTimeout(instructionTimer);
  window.disposeModelPreviews?.();
  $('viewer').replaceChildren();
  $('comparison').replaceChildren();
  for (const id of ['menu', 'product', 'comparison', 'instructions', 'message']) $(id).hidden = id !== name;
  $('back').hidden = name === 'menu';
  $('downloadInstructions').hidden = name !== 'instructions' || !instructions;
  $('exportParts').hidden = name !== 'product';
  if (name === 'product') {
    const preview = document.createElement('div');
    preview.className = 'model-preview';
    if (data.ldraw_available) {
      preview.dataset.modelFormat = data.format;
      preview.dataset.modelUrl = `${data.url}&v=${data.mtime_ns}`;
      preview.textContent = '正在加载 LEGO 模型……';
    } else {
      preview.textContent = '配置本机 LDraw 零件库后即可预览模型';
    }
    $('viewer').append(preview);
    $('download').href = data.url;
    renderParts();
    if (data.ldraw_available) window.mountModelPreviews?.();
  }
  if (name === 'comparison') {
    if (data.comparison_url) {
      const frame = document.createElement('iframe');
      frame.title = '候选方案比较';
      frame.src = data.comparison_url;
      $('comparison').append(frame);
    } else {
      $('comparison').innerHTML = '<div class="empty"><span>当前没有候选方案</span><span>请返回主页面，勾选“生成完整 Bundle”并运行，完成后即可查看候选方案。</span></div>';
    }
  }
  if (name === 'instructions') {
    if (instructions) renderStep();
    loadInstructions(instructionEpoch);
  }
}

function renderStep(resetScroll = true) {
  if (!instructions?.steps?.length) return;
  stepPosition = Math.max(0, Math.min(stepPosition, instructions.steps.length - 1));
  const step = instructions.steps[stepPosition];
  $('instructionWaiting').hidden = true;
  $('instructionReader').hidden = false;
  $('downloadInstructions').hidden = false;
  $('downloadInstructions').href = instructions.pdf_url;
  $('stepHeading').textContent = `第 ${String(step.index).padStart(2, '0')} 步`;
  $('stepRole').textContent = step.role + (step.rotate ? ' · 按图转动观察方向' : '');
  const imageUrl = alternate && step.alternate_url ? step.alternate_url : step.image_url;
  if ($('stepImage').getAttribute('src') !== imageUrl) {
    resetStepImage();
    $('stepImage').src = imageUrl;
  }
  $('stepImage').alt = `第 ${step.index} 步${alternate ? '另一侧视角' : '拼搭图'}`;
  $('stepCanvas').setAttribute('aria-label', $('stepImage').alt);
  $('alternateStep').hidden = !step.alternate_url;
  $('alternateStep').textContent = alternate ? '返回步骤视角' : '查看另一侧';
  $('stepPartsHeading').textContent = step.quantity ? `本步取 ${step.quantity} 件零件` : '本步无需新零件';
  $('stepParts').innerHTML = step.parts.length ? step.parts.map((part) => `<div class="step-part"><img src="${escapeHtml(part.image_url)}" alt="${escapeHtml(part.name)}"><div><strong>${escapeHtml(part.name)}</strong><small>${escapeHtml(part.colour)} · ${escapeHtml(part.number)}</small></div><b>${escapeHtml(part.quantity)}<span>件</span></b></div>`).join('') : `<div class="empty">${step.attaches ? '本步装入已完成的子组件，无需领取新零件。' : '本步无需领取新零件，请按图操作。'}</div>`;
  $('stepNote').textContent = step.warning || (step.attaches ? '按图装入已完成的子组件。' : '黄色边线标记本步新增内容。');
  $('stepNote').classList.toggle('risk', !!step.warning);
  $('previousStep').disabled = stepPosition === 0;
  $('nextStep').disabled = stepPosition === instructions.steps.length - 1;
  $('stepCounter').textContent = `${stepPosition + 1} / ${instructions.steps.length}`;
  $('stepProgress').style.width = `${100 * (stepPosition + 1) / instructions.steps.length}%`;
  if (resetScroll) $('stepParts').scrollTop = 0;
}

function instructionWaiting(summary, failed = false) {
  $('instructionReader').hidden = true;
  $('instructionWaiting').hidden = false;
  $('downloadInstructions').hidden = true;
  $('instructionStatus').textContent = failed ? '说明书暂未生成' : '正在生成拼搭说明书';
  $('instructionProgress').textContent = summary || '正在读取任务状态';
  $('instructionSpinner').hidden = failed;
  $('retryInstructions').hidden = !failed;
  $('instructionWaitHint').hidden = failed;
}

async function loadInstructions(epoch, start = false) {
  try {
    const response = await fetch(start ? '/api/lego-instructions' : `/api/lego-instructions?${new URLSearchParams({path: modelPath})}`, start ? {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: modelPath, title: $('title').textContent}),
    } : {});
    const result = await response.json();
    if (currentPage !== 'instructions' || epoch !== instructionEpoch) return;
    if (!response.ok) throw new Error(result.error || '读取说明书失败');
    if (result.state === 'missing') {
      instructionWaiting('正在启动说明书生成');
      return loadInstructions(epoch, true);
    }
    if (result.state === 'ready') {
      instructions = result.instructions;
      renderStep(false);
      if (result.analysis_running) instructionTimer = setTimeout(() => loadInstructions(epoch), 5000);
      return;
    }
    instructionWaiting(result.summary, result.state === 'error');
    if (result.state !== 'error') instructionTimer = setTimeout(() => loadInstructions(epoch), 1800);
  } catch (error) {
    if (currentPage === 'instructions' && epoch === instructionEpoch) instructionWaiting(error.message, true);
  }
}

function moveStep(delta) {
  if (!instructions) return;
  stepPosition = Math.max(0, Math.min(stepPosition + delta, instructions.steps.length - 1));
  alternate = false;
  renderStep();
}

function renderParts() {
  $('exportParts').disabled = exportingParts || !data.bom || !data.ldraw_available;
  if (!data.bom) {
    $('parts').innerHTML = `<div class="empty"><span>当前还没有零件统计列表</span><button id="generateBom" class="generate" ${generating ? 'disabled' : ''}>${generating ? '正在生成统计……' : '生成统计列表'}</button>${bomError ? `<span class="error">${escapeHtml(bomError)}</span>` : ''}</div>`;
    $('generateBom').addEventListener('click', generateBom);
    return;
  }
  const rows = data.bom.total;
  const colourCount = new Set(rows.map((row) => row.colour_code)).size;
  $('mass').textContent = data.bom.mass_g ? `约 ${data.bom.mass_g} g` : '';
  $('parts').innerHTML = `<div class="totals"><div><b>${data.bom.brick_count}</b><span>零件总数</span></div><div><b>${rows.length}</b><span>零件 / 颜色组合</span></div><div><b>${colourCount}</b><span>颜色</span></div></div><div class="search-wrap"><input id="partsSearch" placeholder="搜索零件或颜色" aria-label="搜索零件或颜色"></div><table class="parts-table"><thead><tr><th>零件</th><th>颜色</th><th>数量</th></tr></thead><tbody id="partsRows"></tbody></table>`;
  const renderRows = () => {
    const search = $('partsSearch').value.trim().toLowerCase();
    $('partsRows').innerHTML = rows.filter((row) => `${row.ldraw_part} ${row.part_key} ${row.colour_name} ${row.colour_code}`.toLowerCase().includes(search)).map((row) => {
      const colour = /^#[0-9a-f]{6}$/i.test(row.colour_hex || '') ? row.colour_hex : '#d1d4ca';
      const label = `${row.part_key}，${row.colour_name}`;
      return `<tr><td><div class="part-cell"><span class="part-thumbnail" data-part-thumbnail data-part="${escapeHtml(row.ldraw_part)}" data-colour="${escapeHtml(row.colour_code)}" role="img" aria-label="${escapeHtml(label)}">…</span><span class="part-meta">${escapeHtml(row.ldraw_part)}<span class="part-name">${escapeHtml(row.part_key)}</span></span></div></td><td><div class="colour"><span class="swatch" style="background:${colour}"></span><span>${escapeHtml(row.colour_name)}<small>#${escapeHtml(row.colour_code)}</small></span></div></td><td>${escapeHtml(row.quantity)}</td></tr>`;
    }).join('');
    mountPartThumbnails($('parts'), data.ldraw_available);
  };
  $('partsSearch').addEventListener('input', renderRows);
  renderRows();
}

async function exportParts() {
  if (exportingParts || !data.bom) return;
  exportingParts = true;
  $('exportParts').disabled = true;
  $('exportParts').textContent = '正在导出…';
  const message = $('partsExportMessage');
  message.hidden = false;
  message.textContent = '正在准备图文零件清单…';
  try {
    // Always export the complete BOM, regardless of the current search filter.
    const thumbnails = await collectPartThumbnails(data.bom.total, (done, total) => {
      message.textContent = `正在准备零件缩略图 ${done} / ${total}`;
    });
    message.textContent = '正在排版零件清单 PDF…';
    const response = await fetch('/api/lego-bom-export', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: modelPath, source_sha256: data.source_sha256,
        title: $('title').textContent, thumbnails}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '零件清单导出失败');
    const link = document.createElement('a');
    link.href = result.url;
    link.download = '零件清单.pdf';
    document.body.appendChild(link); link.click(); link.remove();
    message.hidden = true;
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
  } finally {
    exportingParts = false;
    $('exportParts').disabled = !data.ldraw_available;
    $('exportParts').textContent = '导出零件清单 ↓';
  }
}

async function generateBom() {
  if (generating) return;
  generating = true;
  bomError = '';
  renderParts();
  try {
    const response = await fetch('/api/lego-bom', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: modelPath}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '零件统计生成失败');
    data.bom = result.bom;
  } catch (error) {
    bomError = error.message;
  } finally {
    generating = false;
    if (currentPage === 'product') renderParts();
  }
}

$('back').addEventListener('click', () => showPage('menu'));
$('showProduct').addEventListener('click', () => showPage('product'));
$('exportParts').addEventListener('click', exportParts);
$('showComparison').addEventListener('click', () => showPage('comparison'));
$('showInstructions').addEventListener('click', () => showPage('instructions'));
$('previousStep').addEventListener('click', () => moveStep(-1));
$('nextStep').addEventListener('click', () => moveStep(1));
$('alternateStep').addEventListener('click', () => { alternate = !alternate; renderStep(); });
$('retryInstructions').addEventListener('click', () => {
  instructionWaiting('正在重新启动说明书生成');
  loadInstructions(instructionEpoch, true);
});
function requestClose() {
  if (parent !== window) parent.postMessage({type: 'lego-detail-close'}, location.origin);
  else showPage('menu');
}
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') requestClose();
  if (currentPage === 'instructions' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) {
    if (event.key === 'ArrowRight') { event.preventDefault(); moveStep(1); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); moveStep(-1); }
  }
});
window.addEventListener('message', (event) => {
  const frame = $('comparison').querySelector('iframe');
  if (event.origin !== location.origin || !frame || event.source !== frame.contentWindow) return;
  if (event.data?.type === 'lego-detail-back') showPage('menu');
  if (event.data?.type === 'lego-detail-close') requestClose();
});

fetch(`/api/lego-detail?${new URLSearchParams({path: modelPath})}`).then(async (response) => {
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '读取模型失败');
  data = result;
  $('title').textContent = query.get('label') || `${data.folder} · LEGO 模型`;
  $('title').title = data.path;
}).catch((error) => {
  showPage('message');
  $('message').textContent = error.message;
});
