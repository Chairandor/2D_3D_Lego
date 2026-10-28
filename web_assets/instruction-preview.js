// Frame each step around its bricks; the source PNG and booklet stay intact.
export function mountInstructionPreview(image, canvas, status) {
  const bounds = new Map();
  let ready = false;
  function draw() {
    if (!ready || !image.naturalWidth) return;
    const key = image.currentSrc;
    let frame = bounds.get(key);
    if (!frame) {
      const scan = document.createElement('canvas');
      const factor = Math.min(1, 600 / Math.max(image.naturalWidth, image.naturalHeight));
      scan.width = Math.max(1, Math.round(image.naturalWidth * factor));
      scan.height = Math.max(1, Math.round(image.naturalHeight * factor));
      const context = scan.getContext('2d', {willReadFrequently: true});
      context.drawImage(image, 0, 0, scan.width, scan.height);
      const pixels = context.getImageData(0, 0, scan.width, scan.height).data;
      const background = Array.from(pixels.slice(0, 3));
      const transparent = pixels[3] < 10;
      let left = scan.width, top = scan.height, right = -1, bottom = -1;
      for (let y = 0; y < scan.height; y++) {
        for (let x = 0; x < scan.width; x++) {
          const i = (y * scan.width + x) * 4;
          if (pixels[i + 3] > 10 && (transparent || background.some((c, n) => Math.abs(c - pixels[i + n]) > 20))) {
            left = Math.min(left, x); right = Math.max(right, x);
            top = Math.min(top, y); bottom = Math.max(bottom, y);
          }
        }
      }
      if (right < left) { left = 0; top = 0; right = scan.width - 1; bottom = scan.height - 1; }
      const padding = Math.max(5, Math.max(right - left, bottom - top) * .08);
      left = Math.max(0, left - padding); top = Math.max(0, top - padding);
      right = Math.min(scan.width, right + padding + 1); bottom = Math.min(scan.height, bottom + padding + 1);
      frame = {x: left / factor, y: top / factor, width: (right - left) / factor,
        height: (bottom - top) / factor, background: transparent ? '#303436' : `rgb(${background.join(',')})`};
      bounds.set(key, frame);
    }
    const area = canvas.parentElement;
    const width = Math.max(1, area.clientWidth - 24), height = Math.max(1, area.clientHeight - 24);
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    const context = canvas.getContext('2d');
    context.fillStyle = frame.background; context.fillRect(0, 0, canvas.width, canvas.height);
    const scale = Math.min(canvas.width / frame.width, canvas.height / frame.height);
    const w = frame.width * scale, h = frame.height * scale;
    context.drawImage(image, frame.x, frame.y, frame.width, frame.height,
      (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
    status.hidden = true;
  }
  image.addEventListener('load', () => { ready = true; draw(); });
  image.addEventListener('error', () => { ready = false; status.hidden = false; status.textContent = '步骤图加载失败，请重新进入说明书'; });
  new ResizeObserver(draw).observe(canvas.parentElement);
  return () => {
    ready = false;
    status.textContent = '正在加载步骤图……'; status.hidden = false;
    canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  };
}
