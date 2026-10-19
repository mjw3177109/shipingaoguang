(() => {
  "use strict";
  const video = document.getElementById("player");
  const layer = document.getElementById("roi-layer");
  const data = document.getElementById("initial-hoops");
  const field = document.getElementById("hoops-json");
  const toggle = document.getElementById("roi-toggle");
  if (!video || !layer || !data || !field || !toggle) return;

  let boxes = JSON.parse(data.textContent);
  if (!Array.isArray(boxes)) boxes = [];
  let drawing = false, origin = null, draft = null, enabled = false;

  const clamp = (n) => Math.min(1, Math.max(0, n));
  const limit = (n) => Number(n.toFixed(5));
  function updateGeometry() {
    const width = video.clientWidth;
    const height = video.clientHeight;
    const ratio = video.videoWidth && video.videoHeight ?
      video.videoWidth / video.videoHeight : (width / Math.max(1, height));
    const innerWidth = Math.min(width, height * ratio);
    const innerHeight = innerWidth / ratio;
    layer.style.left = ((width - innerWidth) / 2) + "px";
    layer.style.top = ((height - innerHeight) / 2) + "px";
    layer.style.width = innerWidth + "px";
    layer.style.height = innerHeight + "px";
    render();
  }
  function render() {
    layer.replaceChildren();
    boxes.concat(draft ? [draft] : []).forEach((b, i) => {
      const rect = document.createElement("div");
      rect.className = "roi-box";
      rect.textContent = String(i + 1);
      rect.style.cssText = `left:${b.x * 100}%;top:${b.y * 100}%;width:${b.w * 100}%;height:${b.h * 100}%`;
      layer.appendChild(rect);
    });
    field.value = JSON.stringify(boxes);
  }
  function position(e) {
    const r = layer.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width),
             y: clamp((e.clientY - r.top) / r.height) };
  }
  function shape(a, b) {
    return {x:limit(Math.min(a.x,b.x)), y:limit(Math.min(a.y,b.y)),
            w:limit(Math.abs(a.x-b.x)), h:limit(Math.abs(a.y-b.y))};
  }
  toggle.addEventListener("click", () => {
    enabled = !enabled;
    if (enabled) video.pause();
    layer.classList.toggle("active", enabled);
    toggle.textContent = enabled ? "完成框选" : "开始框选篮筐";
  });
  layer.addEventListener("pointerdown", (e) => {
    if (!enabled || boxes.length >= 2) return;
    drawing = true; origin = position(e); draft = shape(origin, origin);
    layer.setPointerCapture(e.pointerId);
    render();
  });
  layer.addEventListener("pointermove", (e) => {
    if (!drawing) return;
    draft = shape(origin, position(e)); render();
  });
  layer.addEventListener("pointerup", (e) => {
    if (!drawing) return;
    draft = shape(origin, position(e));
    if (draft.w >= .01 && draft.h >= .01 && draft.w <= .7 && draft.h <= .7) {
      boxes.push(draft);
    }
    drawing = false; origin = null; draft = null; render();
  });
  layer.addEventListener("pointercancel", () => {drawing=false;draft=null;render();});
  document.getElementById("roi-undo").addEventListener("click", () => {boxes.pop();render();});
  document.getElementById("roi-clear").addEventListener("click", () => {boxes=[];render();});
  video.addEventListener("loadedmetadata", updateGeometry);
  window.addEventListener("resize", updateGeometry);
  requestAnimationFrame(updateGeometry);
})();
