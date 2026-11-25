import {request, uploadFile, Viewport} from './three-pipeline.js';

const $ = id => document.getElementById(id);
const settings = {target_width_studs: 'sceneStuds', source_unit_mm: 'sceneUnit',
    duration_seconds: 'sceneDuration', palette_exposure_stops: 'sceneExposure'};
const command = $('sceneConvert');
const layout = $('sceneQuickLayout');
const fileInput = $('sceneObjectFile');
const frame = $('sceneExplorerFrame');
const stop = $('sceneStop');
let source, result, object, run, activeJob, polling;
let loading = false, starting = false, stopping = false, statusRequest = 0, assetsKey = '';

function setStatus(text) {
    const status = $('sceneStatus');
    if (status.textContent !== text) status.textContent = text;
    if (window.uiMode === 'scene') $('badge').textContent = '场景分割 · ' + (text || '就绪');
}

function initViews() {
    if (source) return;
    try {
        source = new Viewport($('sceneSourceView'));
        result = new Viewport($('sceneResultView'));
        source.text('导入目标彩色 PLY 后预览');
        result.text('等待生成彩色 LEGO');
    } catch (error) {
        setStatus('3D 预览需要 WebGL。' + error.message);
    }
}

function updateControls() {
    const ownRun = activeJob && activeJob.output === run;
    fileInput.disabled = loading || starting || Boolean(ownRun);
    $('sceneChoose').disabled = fileInput.disabled;
    layout.disabled = command.disabled = loading || starting || Boolean(activeJob) || !object;
    stop.disabled = starting || stopping || !activeJob;
    stop.textContent = ownRun || !activeJob ? '停止' : '停止当前 3D 任务';
    const busy = $('sceneBusy');
    const note = activeJob && !ownRun ? '另一个 3D 任务正在运行。可先导入点云，任务结束后即可生成。' : '';
    if (busy.textContent !== note) busy.textContent = note;
}

$('sceneShowExplorer').onclick = () => {
    frame.src = '/scene-explorer/';
    frame.hidden = false;
    const link = $('sceneOpenExplorer');
    link.href = '/scene-explorer/';
    link.hidden = false;
    $('sceneShowExplorer').textContent = '重新载入场景探索';
};

$('sceneChoose').onclick = () => fileInput.click();
fileInput.onchange = async () => {
    const file = fileInput.files[0];
    if (!file) return;
    await useObject(async () => {
        const pointCloud = await uploadFile('/api/3d/openmask/upload', file, {kind: 'instance'});
        return {...pointCloud, name: file.name};
    });
    fileInput.value = '';
};

async function useObject(load) {
    if (fileInput.disabled) return false;
    loading = true;
    updateControls();
    setStatus('正在读取彩色点云…');
    try {
        object = await load();
        run = null;
        assetsKey = '';
        $('sceneAssets').replaceChildren();
        $('sceneDownloads').replaceChildren();
        $('sceneTextureView').hidden = true;
        $('sceneResultView').hidden = false;
        $('sceneResultTitle').textContent = '彩色 LEGO';
        result?.clear();
        result?.text('等待生成彩色 LEGO');
        $('sceneObjectName').textContent = `${object.name} · ${object.scene_points.toLocaleString()} 点`;
        source?.load({label: object.name, kind: 'points', url: object.url}).catch(() => {
            // The viewport reports its own error. The original PLY is ready for conversion.
        });
        setStatus('彩色 PLY 已就绪');
        return true;
    } catch (error) {
        setStatus(error.message);
        return false;
    } finally {
        loading = false;
        updateControls();
    }
}

window.addEventListener('message', async event => {
    if (event.source !== frame.contentWindow || event.origin !== location.origin) return;
    if (event.data?.type === 'openmask-height' && Number.isFinite(event.data.height)) {
        frame.style.height = `${Math.min(4000, Math.max(480, event.data.height + 2))}px`;
        return;
    }
    if (event.data?.type !== 'openmask-instance' || window.uiMode !== 'scene') return;
    if (fileInput.disabled) {
        $('sceneExplorerNote').textContent = '当前目标正在导入或转换，请稍后重试。';
        return;
    }
    $('sceneExplorerNote').textContent = '';
    $('sceneChoose').scrollIntoView({block: 'center', behavior: 'smooth'});
    const imported = await useObject(() => request('/api/3d/scene-instance', {
        scene: event.data.scene, instance: event.data.instance,
    }));
    if (imported) await startConversion();
});

async function showAsset(asset) {
    $('sceneResultTitle').textContent = asset.label;
    const asImage = asset.kind === 'image';
    const texture = $('sceneTextureView');
    texture.hidden = !asImage;
    $('sceneResultView').hidden = asImage;
    if (asImage) texture.src = asset.url;
    else await result?.load(asset);
}

function renderAssets(assets) {
    const key = JSON.stringify(assets.map(asset => asset.path));
    if (key === assetsKey) return;
    assetsKey = key;
    const previews = assets.filter(asset => asset.id !== 'source' && ['mesh', 'lego', 'image', 'voxel'].includes(asset.kind));
    const controls = $('sceneAssets');
    const downloads = $('sceneDownloads');
    controls.replaceChildren();
    downloads.replaceChildren();
    for (const asset of previews) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = asset.label;
        button.onclick = () => showAsset(asset).catch(error => setStatus(error.message));
        controls.append(button);
    }
    for (const asset of assets) {
        const link = document.createElement('a');
        link.textContent = asset.label;
        link.href = asset.url;
        link.download = asset.path.split(/[\\/]/).pop();
        downloads.append(link);
    }
    const preferred = previews.find(asset => asset.kind === 'lego') || previews.find(asset => asset.kind === 'mesh') || previews[0];
    if (preferred) showAsset(preferred).catch(error => setStatus(error.message));
}

async function pollRun() {
    clearTimeout(polling);
    const requestId = ++statusRequest;
    const id = run;
    const query = new URLSearchParams({flow: 'ply_color'});
    if (id) query.set('output', id);
    try {
        const status = await request('/api/3d/status?' + query);
        if (requestId !== statusRequest || id !== run) return;
        activeJob = status.active_job;
        if (!run && !object && !loading && activeJob?.workflow === 'scene') run = activeJob.output;
        if (id && !loading) {
            const assets = status.assets || [];
            const input = assets.find(asset => asset.id === 'source');
            if (!object && input && status.source) {
                const name = status.source.split(/[\\/]/).pop();
                object = {name, path: status.source, url: input.url};
                $('sceneObjectName').textContent = name;
                source?.load(input).catch(() => {});
            }
            renderAssets(assets);
            const note = status.message || (status.running ? '正在转换…' : status.status);
            if (!stopping) setStatus(note);
        }
    } catch (error) {
        if (requestId === statusRequest && !loading && !starting) setStatus(error.message);
    } finally {
        if (requestId === statusRequest) {
            updateControls();
            if (window.uiMode === 'scene') polling = setTimeout(pollRun, 1200);
        }
    }
}

async function startConversion(layoutOnly = false) {
    if (!object || loading || starting || activeJob) return;
    const fields = Object.values(settings).map(id => $(id));
    if (fields.some(field => !field.reportValidity())) {
        setStatus('请检查转换参数。');
        return;
    }
    const options = {source_up_axis: $('sceneUpAxis').value};
    for (const [name, id] of Object.entries(settings)) {
        options[name] = $(id).value === '' ? null : Number($(id).value);
    }
    starting = true;
    clearTimeout(polling);
    ++statusRequest;
    run = null;
    updateControls();
    setStatus('正在提交有色 PLY 转换…');
    try {
        const started = await request('/api/3d/start', {flow: 'ply_color', workflow: 'scene', source: object.path, options, layout_only: layoutOnly});
        run = started.output;
        activeJob = {output: run, workflow: 'scene'};
    } catch (error) {
        setStatus(error.message);
    } finally {
        starting = false;
        updateControls();
        await pollRun();
    }
}
command.onclick = () => startConversion();
layout.onclick = () => startConversion(true);

stop.onclick = async () => {
    if (!activeJob || stopping) return;
    const output = activeJob.output;
    stopping = true;
    updateControls();
    try {
        await request('/api/3d/stop', {output});
        if (output === run) setStatus('正在停止转换…');
    } catch (error) {
        setStatus(error.message);
    } finally {
        stopping = false;
        await pollRun();
    }
};

function showCurrentMode() {
    if (window.uiMode !== 'scene') {
        clearTimeout(polling);
        return;
    }
    initViews();
    source?.resize();
    result?.resize();
    setStatus($('sceneStatus').textContent);
    pollRun();
}

window.addEventListener('pipeline-mode-change', showCurrentMode);
showCurrentMode();
