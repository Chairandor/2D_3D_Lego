import './model-preview.js';

const $ = (id) => document.getElementById(id);
if (new URLSearchParams(location.search).get('embedded') === '1') {
  document.querySelector('.top').style.display = 'none';
  document.body.style.background = '#fffdf8';
}
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && new URLSearchParams(location.search).get('embedded') === '1') {
    parent.postMessage({type: 'lego-detail-close'}, location.origin);
  }
});
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

function fact(label, value, digits = 3) {
  if (value === null || value === undefined || value === '') return '';
  const formatted = typeof value === 'number' ? value.toFixed(digits) : value;
  return `<span>${escapeHtml(label)}：${escapeHtml(formatted)}</span>`;
}

let options = [];

function selectOption(index) {
  const option = options[index];
  if (!option?.available) return;
  $('selectedPathValue').value = option.path;
  $('selectedPath').hidden = false;
  $('copyStatus').textContent = '这里只返回路径，不修改任何文件。';
  for (const [itemIndex, card] of document.querySelectorAll('.comparison-option').entries()) {
    card.classList.toggle('selected', itemIndex === index);
  }
}

function renderOption(option, index, ldrawAvailable) {
  const metrics = option.metrics || {};
  const facts = option.original ? '<span>当前卡片显示的原版模型</span>' : [
    fact('积木数', metrics.brick_count, 0),
    fact('目标分数', option.selection_objective),
    fact('颜色误差', option.cross_colour_error, 4),
    fact('稳定', metrics.stable === true ? '是' : metrics.stable === false ? '否' : null),
    fact('最大应力', metrics.max_score),
    fact('最弱连接余量', metrics.maximin_capacity),
  ].filter(Boolean).join('');
  const preview = option.available && ldrawAvailable
    ? `<div class="model-preview" data-model-format="${escapeHtml(option.format)}" data-model-url="${escapeHtml(option.url)}&v=${option.mtime_ns}">等待加载 3D 预览…</div>`
    : `<div class="model-preview-missing">${option.available ? '本机未配置 LDraw 零件库' : '该候选没有可用模型文件'}</div>`;
  const tag = option.original ? '<span class="tag original">原版</span>' : option.winner ? '<span class="tag">自动选中</span>' : '';
  return `<article class="comparison-option"><div class="option-head"><h2>${escapeHtml(option.label)}</h2>${tag}</div>${preview}<div class="facts">${facts}</div><div class="option-foot"><button type="button" data-select="${index}" ${option.available ? '' : 'disabled'}>选择此方案 · 返回路径</button>${option.available ? `<a href="${escapeHtml(option.url)}" download>下载模型</a>` : ''}</div></article>`;
}

async function loadComparison() {
  const query = new URLSearchParams(location.search);
  const response = await fetch(`/api/comparison?${query.toString()}`);
  const data = await response.json();
  if (response.status === 404) {
    $('loading').innerHTML = '<div>当前没有候选方案</div><p style="font-size:13px;line-height:1.7;margin:12px 0 0">请返回主页面，勾选“生成完整 Bundle”并运行，完成后即可查看候选方案。</p>';
    return;
  }
  if (!response.ok) throw new Error(data.error || '读取比较报告失败');
  options = data.comparison.options;
  $('title').textContent = `${data.studs} Stud · 候选方案比较`;
  $('subtitle').textContent = `${data.run_dir} · ${data.comparison.bundle}`;
  $('summary').hidden = false;
  $('comparisonList').innerHTML = options.map((option, index) => renderOption(option, index, data.ldraw_available)).join('');
  $('comparisonList').addEventListener('click', (event) => {
    const button = event.target.closest('[data-select]');
    if (button) selectOption(Number(button.dataset.select));
  });
  $('loading').hidden = true;
  window.mountModelPreviews?.();
}

$('copyPath').addEventListener('click', async () => {
  const input = $('selectedPathValue');
  if (!input.value) return;
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable');
    await navigator.clipboard.writeText(input.value);
    $('copyStatus').textContent = '已复制路径。';
  } catch {
    input.select();
    $('copyStatus').textContent = '请按 Ctrl+C 复制选中的路径。';
  }
});

loadComparison().catch((error) => {
  $('loading').hidden = true;
  $('error').hidden = false;
  $('error').textContent = error.message;
});
