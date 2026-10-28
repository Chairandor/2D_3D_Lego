import * as THREE from 'three';
import {LDrawLoader} from 'three/addons/loaders/LDrawLoader.js';
import {LDrawConditionalLineMaterial} from 'three/addons/materials/LDrawConditionalLineMaterial.js';

// One offscreen renderer serves the whole list; rows contain ordinary PNGs,
// not one WebGL context or animation loop per part.
let renderer, scene, camera, loaderReady, observer;
let queue = Promise.resolve();
const thumbnails = new Map();

async function tools() {
  if (!loaderReady) {
    loaderReady = (async () => {
      const loader = new LDrawLoader();
      loader.setPartsLibraryPath('/ldraw/');
      loader.setConditionalLineMaterial(LDrawConditionalLineMaterial);
      loader.smoothNormals = false;
      await loader.preloadMaterials('/ldraw/LDConfig.ldr');
      return loader;
    })().catch((error) => { loaderReady = null; throw error; });
  }
  const loader = await loaderReady;
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({alpha: true, antialias: true});
    renderer.setSize(144, 108, false);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setClearColor(0x000000, 0);
    scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xffffff, 0xc5cad1, 2));
    const light = new THREE.DirectionalLight(0xffffff, 2.1);
    light.position.set(3, 5, 4); scene.add(light);
    camera = new THREE.OrthographicCamera();
  }
  return loader;
}

async function renderPart(part, colour) {
  const loader = await tools();
  const text = `0 Part thumbnail\n1 ${colour} 0 0 0 1 0 0 0 1 0 0 0 1 ${part}.dat\n`;
  const model = await new Promise((resolve, reject) => loader.parse(text, resolve, reject));
  model.rotation.x = Math.PI;
  scene.add(model);
  try {
    const bounds = new THREE.Box3().setFromObject(model);
    if (bounds.isEmpty()) throw new Error('零件没有可显示的几何体');
    const sphere = bounds.getBoundingSphere(new THREE.Sphere());
    const radius = Math.max(sphere.radius, .001);
    camera.left = -radius * 1.5; camera.right = radius * 1.5;
    camera.top = radius * 1.125; camera.bottom = -radius * 1.125;
    camera.near = radius / 100; camera.far = radius * 100;
    camera.position.copy(sphere.center).add(new THREE.Vector3(radius * 3, radius * 2.1, radius * 3.6));
    camera.lookAt(sphere.center); camera.updateProjectionMatrix();
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL('image/png');
  } finally {
    scene.remove(model);
    // LDrawLoader owns shared geometry/material caches used by later rows.
    renderer.renderLists.dispose();
  }
}

function thumbnail(part, colour) {
  part = String(part).replace(/\.dat$/i, '');
  colour = String(colour);
  if (!/^[A-Za-z0-9_-]+$/.test(part) || !/^\d+$/.test(colour)) {
    return Promise.reject(new Error('零件编号或颜色无效'));
  }
  const key = `${part}:${colour}`;
  let promise = thumbnails.get(key);
  if (!promise) {
    promise = queue.then(() => renderPart(part, colour));
    thumbnails.set(key, promise);
    queue = promise.catch(() => { thumbnails.delete(key); });
  }
  return promise;
}

function load(element) {
  const promise = thumbnail(element.dataset.part || '', element.dataset.colour || '');
  promise.then((url) => {
    if (!element.isConnected) return;
    const image = document.createElement('img');
    image.src = url;
    image.alt = element.getAttribute('aria-label') || `${element.dataset.part} 零件预览`;
    element.removeAttribute('role'); element.removeAttribute('aria-label');
    element.replaceChildren(image);
  }).catch(() => {
    if (!element.isConnected) return;
    element.textContent = '—'; element.title = '暂时无法加载此零件缩略图';
  });
}

export async function collectPartThumbnails(rows, progress = () => {}) {
  const result = {};
  for (const [index, row] of rows.entries()) {
    const part = String(row.ldraw_part).replace(/\.dat$/i, '');
    const key = `${part}:${row.colour_code}`;
    result[key] = await thumbnail(part, row.colour_code);
    progress(index + 1, rows.length);
  }
  return result;
}

export function mountPartThumbnails(container, available) {
  observer?.disconnect();
  if (!available) {
    container.querySelectorAll('[data-part-thumbnail]').forEach((element) => { element.textContent = '—'; });
    return;
  }
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      load(entry.target);
    }
  }, {root: container, rootMargin: '100px'});
  container.querySelectorAll('[data-part-thumbnail]').forEach((element) => observer.observe(element));
}

window.addEventListener('pagehide', () => { observer?.disconnect(); renderer?.dispose(); });
