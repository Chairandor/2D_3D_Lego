'use strict';

const editElement = id => document.getElementById(id);
const editDialog = editElement('modelEditDialog');
let editSession = '';
let openingModel = false;
let checkingModel = false;
let editPoll;
let editorName = '';
let loadedEditPreview = '';
let lastModelChoices = '';

function updateSizeRecommendation() {
  editElement('legoRecommendation').style.display = editElement('legoSource').value ? 'none' : '';
}

function renderLegoModelChoices(status) {
  const select = editElement('legoSource');
  const previous = select.dataset.folder === status.display_output_dir ? select.value : '';
  const models = status.saved_models || [];
  const signature = JSON.stringify([status.display_output_dir, models]);
  if (lastModelChoices === signature) return;
  lastModelChoices = signature;
  select.dataset.folder = status.display_output_dir || '';
  select.replaceChildren(new Option('原彩色模型', ''), ...models.map(model => new Option(model.name, model.id)));
  if (models.some(model => model.id === previous)) select.value = previous;
  updateSizeRecommendation();
}

editElement('legoSource').addEventListener('change', updateSizeRecommendation);

function modelChat(message, user = false) {
  const history = editElement('modelEditHistory');
  const line = document.createElement('div');
  line.className = user ? 'chat-line user' : 'chat-line';
  line.textContent = message;
  history.append(line);
  history.scrollTop = history.scrollHeight;
  return line;
}

function replaceEditPreview(url) {
  const container = editElement('modelEditView');
  if (loadedEditPreview === url) return;
  loadedEditPreview = url;
  if (container.firstElementChild) window.unmountModelPreview?.(container.firstElementChild);
  container.replaceChildren();
  if (!url) return;
  const preview = document.createElement('div');
  preview.className = 'model-preview';
  preview.dataset.modelFormat = 'glb';
  preview.dataset.modelUrl = url;
  preview.textContent = '正在加载模型…';
  container.append(preview);
  window.mountModelPreviews?.();
}

function updateEditor(state) {
  if (state.session !== editSession) return;
  replaceEditPreview(state.preview_url);
  editElement('saveModelEdit').disabled = !state.can_save;
  editElement('checkModelEdit').disabled = checkingModel || !state.can_check;
  editElement('sendModelEdit').disabled = state.running || !state.configured;
}

async function openModelEditor(modelId, recoveryToken = '') {
  if (openingModel || editSession) return;
  openingModel = true;
  try {
    const state = recoveryToken
      ? await api('/api/model-edit/status', {session: recoveryToken})
      : await api('/api/model-edit/open', {run_dir: currentRunDir, model_id: modelId});
    editSession = state.session;
    editorName = state.name;
    editElement('modelEditName').textContent = state.name;
    editElement('modelEditHistory').replaceChildren();
    editElement('modelEditInput').value = '';
    modelChat(recoveryToken ? state.message : state.configured ? '描述你想修改的地方。确认保存后会生成一张新卡片。' : '当前实例的 3D 修改助手尚未配置。');
    editDialog.showModal();
    updateEditor(state);
  } catch (error) {
    alert(error.message);
  } finally {
    openingModel = false;
  }
}

async function pollModelEdit(token, line) {
  if (token !== editSession) return;
  try {
    const state = await api('/api/model-edit/status', {session: token});
    if (token !== editSession) return;
    line.textContent = state.message;
    updateEditor(state);
    if (state.running) editPoll = setTimeout(() => pollModelEdit(token, line), 1500);
  } catch (error) {
    if (token === editSession) {
      line.textContent = error.message;
      editElement('sendModelEdit').disabled = false;
    }
  }
}

editElement('modelEditForm').addEventListener('submit', async event => {
  event.preventDefault();
  const token = editSession;
  const field = editElement('modelEditInput');
  const instruction = field.value.trim();
  if (!token || !instruction || editElement('sendModelEdit').disabled) return;
  editElement('sendModelEdit').disabled = true;
  editElement('saveModelEdit').disabled = true;
  editElement('checkModelEdit').disabled = true;
  modelChat(instruction, true);
  field.value = '';
  const line = modelChat('正在启动 3D 修改…');
  try {
    const state = await api('/api/model-edit/edit', {session: token, instruction});
    updateEditor(state);
    await pollModelEdit(token, line);
  } catch (error) {
    if (token !== editSession) return;
    line.textContent = error.message;
    editElement('sendModelEdit').disabled = false;
    api('/api/model-edit/status', {session: token}).then(updateEditor).catch(() => {});
  }
});

editElement('checkModelEdit').addEventListener('click', async () => {
  const token = editSession;
  if (!token || checkingModel) return;
  checkingModel = true;
  editElement('checkModelEdit').disabled = true;
  const line = modelChat('正在检查当前模型…');
  try {
    const state = await api('/api/model-edit/check', {session: token});
    if (token !== editSession) return;
    line.textContent = state.checks.join('\n');
    checkingModel = false;
    updateEditor(state);
  } catch (error) {
    if (token !== editSession) return;
    line.textContent = error.message;
    checkingModel = false;
    api('/api/model-edit/status', {session: token}).then(updateEditor).catch(() => {});
  }
});

editElement('saveModelEdit').addEventListener('click', async () => {
  const token = editSession;
  const name = window.prompt('给另存的模型命名', `${editorName} 修改版`);
  if (name === null || !token) return;
  editElement('saveModelEdit').disabled = true;
  try {
    const state = await api('/api/model-edit/save', {session: token, name});
    if (token === editSession) modelChat(`已另存为「${name.trim()}」。`);
    updateEditor(state);
    await refresh();
  } catch (error) {
    if (token !== editSession) return;
    editElement('saveModelEdit').disabled = false;
    modelChat(error.message);
  }
});

async function discardModelSession() {
  const token = editSession;
  editSession = '';
  checkingModel = false;
  clearTimeout(editPoll);
  replaceEditPreview('');
  if (token) {
    await fetch('/api/model-edit/close', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session: token}), keepalive: true,
    }).catch(() => {});
  }
}

editElement('closeModelEdit').addEventListener('click', () => editDialog.close());
editDialog.addEventListener('close', discardModelSession);
window.addEventListener('pagehide', discardModelSession);

const recoveryUrl = new URL(window.location.href);
const recoveryToken = recoveryUrl.searchParams.get('recover_edit');
if (recoveryToken) {
  window.addEventListener('DOMContentLoaded', () => {
    recoveryUrl.searchParams.delete('recover_edit');
    history.replaceState(null, '', recoveryUrl);
    openModelEditor('', recoveryToken);
  }, {once: true});
}

editElement('artifacts').addEventListener('click', async event => {
  const link = event.target.closest('[data-model-edit]');
  if (link) {
    event.preventDefault();
    await openModelEditor(link.dataset.modelEdit);
    return;
  }
  const deletion = event.target.closest('[data-delete-model]');
  if (!deletion || !window.confirm(`删除已保存的模型「${deletion.dataset.name}」？`)) return;
  try {
    await api('/api/model-edit/delete', {run_dir: currentRunDir, model_id: deletion.dataset.deleteModel});
    await refresh();
  } catch (error) {
    alert(error.message);
  }
});
