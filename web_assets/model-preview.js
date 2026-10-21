import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';
import { LDrawLoader } from 'three/addons/loaders/LDrawLoader.js';
import { LDrawConditionalLineMaterial } from 'three/addons/materials/LDrawConditionalLineMaterial.js';

const observed = new Set();
const views = new Map();
const gltfLoader = new GLTFLoader();
const plyLoader = new PLYLoader();
let legoLoadQueue = Promise.resolve();

async function loadLegoModel(url) {
  const loader = new LDrawLoader();
  loader.setPartsLibraryPath('/ldraw/');
  loader.setConditionalLineMaterial(LDrawConditionalLineMaterial);
  loader.smoothNormals = false;
  await loader.preloadMaterials('/ldraw/LDConfig.ldr');
  const model = await loader.loadAsync(url);
  model.traverse((part) => {
    if (part.isLineSegments) part.visible = false;
  });
  model.rotation.x = Math.PI;
  return model;
}

function queueLegoModel(url) {
  const task = legoLoadQueue.then(() => loadLegoModel(url));
  legoLoadQueue = task.catch(() => {});
  return task;
}

function releaseModel(model) {
  if (!model) return;
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  model.traverse((part) => {
    if (part.geometry) geometries.add(part.geometry);
    for (const material of [part.material].flat().filter(Boolean)) {
      materials.add(material);
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
      }
    }
  });
  for (const geometry of geometries) geometry.dispose();
  for (const texture of textures) {
    texture.dispose();
    texture.image?.close?.();
  }
  for (const material of materials) material.dispose();
}

function disposeView(element) {
  const view = views.get(element);
  if (!view) return;
  view.disposed = true;
  cancelAnimationFrame(view.frame);
  clearTimeout(view.resumeTimer);
  view.resizeObserver.disconnect();
  view.controls.dispose();
  releaseModel(view.model);
  view.renderer.dispose();
  view.renderer.domElement.remove();
  views.delete(element);
}

function fitCamera(view) {
  const bounds = new THREE.Box3().setFromObject(view.model);
  if (bounds.isEmpty()) throw new Error('模型没有可显示的几何体');
  const sphere = bounds.getBoundingSphere(new THREE.Sphere());
  const radius = Math.max(sphere.radius, 0.001);
  const distance = radius * 2.9;
  view.camera.near = Math.max(radius / 1000, 0.00001);
  view.camera.far = radius * 1000;
  view.camera.position.copy(sphere.center).add(new THREE.Vector3(distance * 0.65, distance * 0.35, distance));
  view.camera.updateProjectionMatrix();
  view.controls.target.copy(sphere.center);
  view.controls.minDistance = radius * 0.65;
  view.controls.maxDistance = radius * 12;
  view.controls.update();
}

function resize(view) {
  const width = Math.max(view.element.clientWidth, 1);
  const height = Math.max(view.element.clientHeight, 1);
  view.renderer.setSize(width, height, false);
  view.camera.aspect = width / height;
  view.camera.updateProjectionMatrix();
  if (view.model) view.renderer.render(view.scene, view.camera);
}

function draw(view) {
  if (view.disposed || !view.visible || !view.model || document.hidden) {
    view.frame = 0;
    return;
  }
  view.frame = requestAnimationFrame(() => draw(view));
  const now = performance.now();
  const deltaSeconds = view.lastFrameTime ? Math.min((now - view.lastFrameTime) / 1000, 0.1) : 0;
  view.lastFrameTime = now;
  view.controls.update(deltaSeconds);
  view.renderer.render(view.scene, view.camera);
}

function startView(view) {
  if (view.frame || !view.model) return;
  view.lastFrameTime = 0;
  draw(view);
}

function pauseView(view) {
  cancelAnimationFrame(view.frame);
  view.frame = 0;
}

async function createView(element) {
  if (views.has(element) || element.dataset.failed) return;
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0xc9cbd1, 2.1));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
  keyLight.position.set(3, 5, 4);
  scene.add(keyLight);
  const fillLight = new THREE.DirectionalLight(0xffffff, 0.9);
  fillLight.position.set(-4, 2, -3);
  scene.add(fillLight);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  } catch (error) {
    element.dataset.failed = '1';
    element.textContent = '当前浏览器无法显示 3D 预览，请下载模型查看';
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);
  const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.enablePan = false;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 2;
  const view = {
    element, scene, renderer, camera, controls,
    lastFrameTime: 0, model: null, visible: true,
    disposed: false, frame: 0, resumeTimer: 0,
    resizeObserver: new ResizeObserver(() => resize(view)),
  };
  views.set(element, view);
  element.replaceChildren(renderer.domElement);
  const loading = document.createElement('span');
  loading.className = 'preview-loading';
  loading.textContent = element.dataset.modelFormat === 'mpd' ? '正在读取 LEGO 零件…' : '正在加载 3D 模型…';
  element.appendChild(loading);
  view.resizeObserver.observe(element);
  resize(view);
  controls.addEventListener('start', () => {
    controls.autoRotate = false;
    clearTimeout(view.resumeTimer);
  });
  controls.addEventListener('end', () => {
    view.resumeTimer = setTimeout(() => { controls.autoRotate = true; }, 3500);
  });

  try {
    let model;
    if (element.dataset.modelFormat === 'glb') {
      model = (await gltfLoader.loadAsync(element.dataset.modelUrl)).scene;
    } else if (element.dataset.modelFormat === 'ply') {
      const geometry = await plyLoader.loadAsync(element.dataset.modelUrl);
      if (!geometry.hasAttribute('normal')) geometry.computeVertexNormals();
      model = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
        color: 0xffffff,
        vertexColors: geometry.hasAttribute('color'),
        side: THREE.DoubleSide,
        roughness: 0.85,
      }));
    } else if (element.dataset.modelFormat === 'mpd') {
      model = await queueLegoModel(element.dataset.modelUrl);
    } else {
      throw new Error('不支持的模型格式');
    }
    if (view.disposed) {
      releaseModel(model);
      return;
    }
    view.model = model;
    scene.add(model);
    fitCamera(view);
    loading.remove();
    startView(view);
  } catch (error) {
    if (view.disposed) return;
    console.error('3D preview failed', error);
    disposeView(element);
    element.dataset.failed = '1';
    element.textContent = element.dataset.modelFormat === 'mpd'
      ? 'LEGO 预览失败，请检查零件库；仍可下载 MPD'
      : '3D 预览加载失败，可下载模型查看';
  }
}

const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    const element = entry.target;
    const view = views.get(element);
    if (view) {
      view.visible = entry.isIntersecting;
      if (view.visible) {
        startView(view);
      } else {
        pauseView(view);
      }
    } else if (entry.isIntersecting) {
      createView(element);
    }
  }
}, { rootMargin: '180px' });

window.mountModelPreviews = () => {
  for (const element of document.querySelectorAll('.model-preview')) {
    if (observed.has(element)) continue;
    observed.add(element);
    observer.observe(element);
  }
};

window.disposeModelPreviews = () => {
  for (const element of observed) {
    observer.unobserve(element);
    disposeView(element);
  }
  observed.clear();
};

document.addEventListener('visibilitychange', () => {
  for (const view of views.values()) {
    if (document.hidden) pauseView(view);
    else if (view.visible) startView(view);
  }
});

window.mountModelPreviews();
