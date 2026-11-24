'use strict';

(() => {
  const byId = id => document.getElementById(id);
  const log = byId('log');
  const camera = byId('boardCamera');
  const surface = byId('boardDrop');
  const frame = byId('boardFrame');
  const placeholder = byId('boardPlaceholder');
  const form = byId('boardPlacement');
  const message = byId('boardStatus');
  const artifacts = byId('artifacts');
  const mime = 'application/x-lego-board-model';
  const dragSelector = '.info[data-board-model]';
  let state = {};
  let selection = null;
  let statusTimer;
  let frameTimer;
  let statusRequest = false;

  function active() {
    return !camera.hidden && (!window.uiMode || window.uiMode === 'image');
  }

  function syncBoardDrag() {
    const enabled = active();
    for (const label of artifacts.querySelectorAll(dragSelector)) {
      label.draggable = enabled;
      for (const link of label.querySelectorAll('a[href]')) {
        if (enabled) link.draggable = false;
        else link.removeAttribute('draggable');
      }
    }
  }
  window.syncBoardDrag = syncBoardDrag;

  function tell(text, error = false) {
    message.textContent = text;
    message.classList.toggle('error', error);
  }

  function showFrames() {
    frameTimer = undefined;
    if (!active() || !state.board_mode || !state.running || !state.frame_available) return;
    placeholder.hidden = true;
    frame.hidden = false;
    frame.src = '/api/ar/file?kind=frame&t=' + Date.now();
    frameTimer = setTimeout(showFrames, 180);
  }

  function render(next) {
    state = next;
    const running = state.board_mode && state.running;
    const cameraState = running ? state.camera || {} : {};
    byId('boardStart').disabled = !!state.running;
    byId('boardStop').disabled = !running;
    byId('boardCameraSource').disabled = !!state.running;
    if (running && state.options?.camera_source) byId('boardCameraSource').value = state.options.camera_source;
    if (state.running && !state.board_mode) {
      tell('摄像头正在 ArUco 预览页使用。');
    } else if (state.board_mode && state.error) {
      tell(String(state.error).split('\n')[0], true);
    } else if (running) {
      const title = cameraState.marker_tracked ? '硬板已识别' : state.message;
      const count = '已摆放 ' + (cameraState.placed_models || 0) + ' 个模型';
      const calibrated = cameraState.intrinsics_source === 'estimated' ? ' · 使用估算内参' : '';
      const warnings = cameraState.placement_errors || [];
      tell([title, count].filter(Boolean).join(' · ') + calibrated +
           (warnings.length ? ' · ' + warnings.join('；') : ''), !!warnings.length);
    } else {
      tell('使用 ID 0，外侧黑框边长 5 cm');
    }
    if (running && state.frame_available) {
      if (frameTimer === undefined) showFrames();
    } else {
      clearTimeout(frameTimer);
      frameTimer = undefined;
      frame.hidden = true;
      frame.removeAttribute('src');
      placeholder.hidden = false;
      placeholder.textContent = running ? '正在连接摄像头……' : '开启摄像头后，将码放入画面';
    }
  }

  async function refresh() {
    clearTimeout(statusTimer);
    if (!active() || statusRequest) return;
    statusRequest = true;
    try {
      const response = await fetch('/api/ar/status', {cache: 'no-store'});
      if (!response.ok) throw new Error('摄像头状态读取失败');
      render(await response.json());
    } catch (error) {
      tell(error.message, true);
    } finally {
      statusRequest = false;
      if (active()) statusTimer = setTimeout(refresh, 650);
    }
  }

  function cancel() {
    selection = null;
    form.hidden = true;
  }

  function setView(enabled) {
    camera.hidden = !enabled;
    log.hidden = enabled;
    byId('boardShowLog').setAttribute('aria-pressed', String(!enabled));
    byId('boardShowCamera').setAttribute('aria-pressed', String(enabled));
    byId('imageWorkflow').classList.toggle('board-mode', enabled);
    syncBoardDrag();
    if (enabled) refresh();
    else {
      cancel();
      clearTimeout(statusTimer);
      clearTimeout(frameTimer);
      frameTimer = undefined;
    }
  }

  function choose(label) {
    if (!active()) return;
    if (!state.running || !state.board_mode) {
      tell('请先开启图片页摄像头。', true);
      return;
    }
    selection = label.dataset.boardModel;
    byId('boardModelName').textContent = label.dataset.boardLabel;
    byId('boardX').value = byId('boardY').value = byId('boardAngle').value = '0';
    form.hidden = false;
    byId('boardX').focus();
  }

  async function action(route, data) {
    return api('/api/ar/board/' + route, data);
  }

  byId('boardShowLog').onclick = () => setView(false);
  byId('boardShowCamera').onclick = () => setView(true);
  byId('boardStart').onclick = async () => {
    byId('boardStart').disabled = true;
    try {
      render(await action('start', {camera_source: byId('boardCameraSource').value}));
    } catch (error) {
      tell(error.message, true);
      byId('boardStart').disabled = false;
    }
  };
  byId('boardStop').onclick = async () => {
    try { render(await action('stop', {})); cancel(); }
    catch (error) { tell(error.message, true); }
  };
  byId('boardCancel').onclick = cancel;

  artifacts.addEventListener('dragstart', event => {
    if (!active()) return;
    const label = event.target.closest(dragSelector);
    if (!label) return;
    if (event.target.closest('button, input, select')) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData(mime, label.dataset.boardModel);
    event.dataTransfer.effectAllowed = 'copy';
  });
  surface.addEventListener('dragover', event => {
    if (!active() || !event.dataTransfer.types.includes(mime)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    surface.classList.add('is-drop-target');
  });
  surface.addEventListener('dragleave', event => {
    if (!surface.contains(event.relatedTarget)) surface.classList.remove('is-drop-target');
  });
  surface.addEventListener('drop', event => {
    surface.classList.remove('is-drop-target');
    if (!active() || !event.dataTransfer.types.includes(mime)) return;
    event.preventDefault();
    const value = event.dataTransfer.getData(mime);
    const label = [...artifacts.querySelectorAll(dragSelector)]
      .find(item => item.dataset.boardModel === value);
    if (label) choose(label);
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!active() || !selection) return;
    byId('boardConfirm').disabled = true;
    try {
      render(await action('place', {model: selection, x_cm: byId('boardX').valueAsNumber,
                                   y_cm: byId('boardY').valueAsNumber, angle: byId('boardAngle').valueAsNumber}));
      cancel();
    } catch (error) {
      tell(error.message, true);
    } finally {
      byId('boardConfirm').disabled = false;
    }
  });
  form.addEventListener('keydown', event => {
    if (event.key === 'Escape') cancel();
  });
  window.addEventListener('pipeline-mode-change', () => {
    syncBoardDrag();
    if (active()) refresh();
    else cancel();
  });
})();
