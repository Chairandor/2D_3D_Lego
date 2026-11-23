// AR 面板沿用主工作台的模式切换、模型查看器和任务状态。
const $ = id => document.getElementById(id);
let state = {}, previewUrl = '', timer, imageTimer, disposed = false;

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function notice(text = '', error = false) {
  $('arNotice').replaceChildren();
  if (!text) return;
  const node = document.createElement('div');
  node.className = 'notice ' + (error ? 'error' : 'warning');
  node.textContent = text;
  $('arNotice').append(node);
}

function mountPreview(url, force = false) {
  if (!force && url === previewUrl) return;
  const old = $('arModelView').querySelector('.model-preview');
  if (old) window.unmountModelPreview?.(old);
  $('arModelView').replaceChildren();
  previewUrl = url;
  const view = document.createElement('div');
  if (url) {
    view.className = 'model-preview';
    view.dataset.modelFormat = 'glb';
    view.dataset.autoRotate = 'false';
    view.dataset.modelUrl = url;
    view.textContent = '正在加载乐高模型……';
  } else {
    view.className = 'ar-placeholder';
    view.textContent = state.phase === 'preparing' ? '正在读取零件和颜色……' : '选择模型后在此预览';
  }
  $('arModelView').append(view);
  window.mountModelPreviews?.();
}

function link(id, url, name) {
  $(id).hidden = !url;
  if (url) {
    $(id).href = url;
    if (name) $(id).download = name;
  }
}

function render(data) {
  state = data;
  window.arucoState = state;
  const camera = state.camera || {}, report = state.report || {};
  const gesture = camera.gesture || {};
  $('arBadge').textContent = state.message || '就绪';
  if (window.uiMode === 'ar') $('badge').textContent = 'AR · ' + (state.message || '就绪');
  $('arModelName').textContent = state.model_name || '尚未选择模型';
  $('arStatusText').textContent = state.message || '';
  $('arSourcePath').textContent = state.model_path || '';
  $('arLog').textContent = state.logs || '等待任务……';
  $('arTriangles').textContent = report.triangles?.toLocaleString() || '—';
  $('arColors').textContent = report.colors || '—';
  $('arRendered').textContent = (camera.rendered_frames || 0).toLocaleString();
  $('arRenderedStat').classList.toggle('success', camera.rendered_frames > 0);
  $('arCameraLabel').textContent = state.running && camera.camera_opened ? camera.camera_device?.name || '相机 ' + state.options.camera : '尚未启动';
  $('arCalibrationInfo').textContent = camera.intrinsics_source === 'gemini-color-factory' ? '使用当前 Gemini 彩色流的工厂内参。标记黑边尺寸仍须实测，精确对齐可进一步使用棋盘格标定。' : camera.intrinsics_source === 'estimated' ? '当前使用估计内参。精确比例与对齐需要实测标记尺寸和相机标定。' : 'Gemini 使用自己的 SDK 彩色流，优先读取彩色相机工厂内参。请连接 USB3 并关闭 Orbbec Viewer；标记黑边长仍须实测。';
  $('arStepModel').className = 'step ' + (state.preview_url ? 'active' : 'wait');
  $('arStepCamera').className = 'step ' + (camera.camera_opened ? 'active' : state.phase === 'running' ? 'wait' : '');
  $('arStepMarker').className = 'step ' + (camera.rendered_frames > 0 ? 'active' : camera.camera_opened ? 'wait' : '');
  for (const id of ['arChoose', 'arLoadExample', 'arExamples', 'arCameraSource', 'arCamera', 'arMarkerId', 'arMarkerSize', 'arFitWidth', 'arGestures', 'arSkeleton', 'arGestureAngle']) $(id).disabled = state.running;
  $('arStart').disabled = state.running || !state.preview_url;
  $('arStop').disabled = !state.running;
  $('arResetRotation').disabled = !state.running || state.phase !== 'running';
  mountPreview(state.preview_url || '');
  const preview = $('arModelView').querySelector('.model-preview');
  if (preview) window.setModelPreviewRotation?.(preview, gesture.rotation_ar || [[1,0,0],[0,1,0],[0,0,1]]);
  $('arPoseState').textContent = '人体骨架：' + (gesture.pose_detected && state.running ? '已跟踪' : '未检测');
  $('arHandState').textContent = `手部：${state.running ? gesture.hands_detected || 0 : 0} 只 · 食指指向 ${state.running ? gesture.pointing_hands || 0 : 0} 只`;
  $('arGestureCount').textContent = `指向：${gesture.sequence || 0} 次`;
  const names = {left:'向左指', right:'向右指', up:'向上指', down:'向下指'};
  $('arPointingNow').textContent = state.running && gesture.current_direction ? `当前指向：${names[gesture.current_direction]} · 保持进度 ${Math.round((gesture.hold_progress || 0)*100)}%` : '当前指向：未识别（请伸食指、收起其余三指）';
  $('arGestureLast').textContent = gesture.last_direction ? `${names[gesture.last_direction]} · ${gesture.last_action} ${gesture.step_degrees}°` : gesture.sequence ? '已恢复初始朝向' : '尚无指向动作';
  $('arGestureHint').textContent = !state.running ? '启动摄像头后，伸出食指指向左 / 右 / 上 / 下' : !state.options.gestures ? '指向控制已关闭' : gesture.hint || '正在加载人体与手部跟踪……';
  link('arDownloadModel', state.model_url, state.model_name);
  link('arDownloadPreview', state.preview_url, 'preview.glb');
  link('arSnapshot', state.snapshot_url);
  notice(state.error || '', !!state.error);
  if (!state.frame_available || !state.running) {
    clearTimeout(imageTimer);
    imageTimer = null;
    $('arVideo').hidden = true;
    $('arVideo').removeAttribute('src');
    $('arVideoPlaceholder').hidden = false;
    $('arVideoPlaceholder').textContent = state.running ? '正在连接相机……' : '启动摄像头后，将打印码完整放在画面内';
  } else if (!imageTimer && window.uiMode === 'ar') updateImage();
}

function updateImage() {
  if (disposed || !state.running || !state.frame_available || window.uiMode !== 'ar') { imageTimer = null; return; }
  $('arVideo').hidden = false;
  $('arVideoPlaceholder').hidden = true;
  $('arVideo').src = '/api/ar/file?kind=frame&t=' + Date.now();
  imageTimer = setTimeout(updateImage, 200);
}

async function refresh() {
  clearTimeout(timer);
  try { render(await request('/api/ar/status')); }
  catch (error) { notice('AR 状态读取失败：' + error.message, true); }
  if (!disposed && (window.uiMode === 'ar' || state.running)) timer = setTimeout(refresh, 650);
}

async function act(path, body) {
  try { notice(); render(await request(path, body)); await refresh(); }
  catch (error) { notice(error.message, true); }
}

$('arChoose').onclick = () => $('arModelFile').click();
$('arModelFile').onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (!/\.(ldr|mpd)$/i.test(file.name)) throw new Error('请选择 .ldr 或 .mpd 文件');
    if (file.size > 32 * 1024 * 1024) throw new Error('模型不能超过 32 MB');
    $('arChoose').disabled = true;
    $('arModelName').textContent = '正在上传 ' + file.name + '……';
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('无法读取文件'));
      reader.readAsDataURL(file);
    });
    await act('/api/ar/upload', {name: file.name, data});
  } catch (error) { notice(error.message, true); }
  finally { $('arChoose').disabled = !!state.running; event.target.value = ''; }
};
$('arLoadExample').onclick = () => act('/api/ar/select', {path: $('arExamples').value});
$('arStart').onclick = () => {
  const fields = ['arCamera', 'arMarkerId', 'arMarkerSize', 'arFitWidth', 'arGestureAngle'];
  if (fields.some(id => !$(id).reportValidity())) return;
  act('/api/ar/start', {camera: $('arCamera').value, marker_id: $('arMarkerId').value,
    camera_source: $('arCameraSource').value,
    marker_size_cm: $('arMarkerSize').value, fit_width_cm: $('arFitWidth').value,
    gestures: $('arGestures').checked, show_skeleton: $('arSkeleton').checked, gesture_angle: $('arGestureAngle').value});
};
$('arStop').onclick = () => act('/api/ar/stop', {});
$('arResetRotation').onclick = () => act('/api/ar/reset-rotation', {});
$('arResetView').onclick = () => mountPreview(previewUrl, true);
$('modeArButton').onclick = () => window.setPipelineMode?.('ar');
window.addEventListener('pipeline-mode-change', () => { if (window.uiMode === 'ar') refresh(); });
window.addEventListener('beforeunload', () => { disposed = true; clearTimeout(timer); clearTimeout(imageTimer); });
$('arVideo').onload = () => { $('arVideo').dataset.frameLoaded = '1'; };

try {
  const bootstrap = await request('/api/ar/bootstrap');
  $('arExamples').replaceChildren(...bootstrap.presets.map(preset => new Option(preset.label, preset.path)));
  $('arLoadExample').disabled = !bootstrap.presets.length;
  if (!bootstrap.available) notice('未找到 AR 运行环境，请先完成本机 AR 环境配置。', true);
  const initial = await request('/api/ar/status');
  const options = initial.options || {};
  for (const [id, key] of [['arCamera', 'camera'], ['arMarkerId', 'marker_id'], ['arMarkerSize', 'marker_size_cm'], ['arFitWidth', 'fit_width_cm']]) {
    if (options[key] !== undefined) $(id).value = options[key];
  }
  $('arGestures').checked = options.gestures !== false;
  $('arSkeleton').checked = options.show_skeleton !== false;
  $('arGestureAngle').value = options.gesture_angle || 30;
  $('arCameraSource').value = options.camera_source || 'gemini';
  render(initial);
  if (window.uiMode === 'ar' || state.running) refresh();
} catch (error) { notice('AR 面板初始化失败：' + error.message, true); }
