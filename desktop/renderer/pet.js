"use strict";

(() => {
  const canvas = document.getElementById("pet-canvas");
  const stage = document.getElementById("pet-stage");
  const loading = document.getElementById("pet-loading");
  const bridge = window.aiNeko;
  let app;
  let model;
  let scale = 1;
  let speechLevel = 0;
  function setSpeechLevel(value) { speechLevel = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0; document.body.dataset.speechLevel = String(speechLevel); }
  let currentState = "idle";
  let interactive = true;
  let pointer = null;
  let drag = null;
  let hitTimer;
  let lastHitAt = 0;
  let size = { width: 1, height: 1 };
  let visibleBounds = null;
  const pixel = new Uint8Array(4);

  function fit() {
    if (!app || !model) return;
    const width = stage.clientWidth;
    const height = stage.clientHeight;
    app.renderer.resize(width, height);
    if (visibleBounds) {
      // Fit actual rendered pixels, not the much larger transparent model canvas.
      // Available screen space caps enlargements so ears and feet remain visible.
      const factor = Math.min(500 * scale / visibleBounds.height,
        (width - 24) / visibleBounds.width, (height - 32) / visibleBounds.height);
      model.scale.set(factor);
      model.position.set(width / 2 - (visibleBounds.x + visibleBounds.width / 2 - model.pivot.x) * factor,
        height - 14 - (visibleBounds.y + visibleBounds.height - model.pivot.y) * factor);
    } else {
      const factor = Math.min((width - 20) / size.width, (height - 24) / size.height) / 1.35;
      model.scale.set(factor);
      model.position.set(width / 2, height - 8);
    }
  }

  function measureVisibleBounds() {
    const gl = app.renderer.gl;
    const pixels = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    let left = canvas.width, right = -1, top = canvas.height, bottom = -1;
    for (let y = 0; y < canvas.height; y++) {
      for (let x = 0; x < canvas.width; x++) {
        if (pixels[(y * canvas.width + x) * 4 + 3] < 24) continue;
        const fromTop = canvas.height - 1 - y;
        left = Math.min(left, x); right = Math.max(right, x);
        top = Math.min(top, fromTop); bottom = Math.max(bottom, fromTop);
      }
    }
    if (right <= left || bottom <= top) throw new Error("avatar_has_no_visible_pixels");
    const resolution = app.renderer.resolution;
    const factor = model.scale.x;
    visibleBounds = {
      x: (left / resolution - model.x) / factor + model.pivot.x,
      y: (top / resolution - model.y) / factor + model.pivot.y,
      width: (right - left + 1) / resolution / factor,
      height: (bottom - top + 1) / resolution / factor,
    };
  }

  function setScale(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return;
    scale = Math.min(1.35, Math.max(0.7, value));
    fit();
  }

  function setState(value) {
    if (!["idle", "thinking", "searching", "responding", "failed"].includes(value)) return;
    if (value === currentState) return;
    currentState = value;
    if (!model) return;
    // Motion signals turn activity; mouth opening separately follows actual audio RMS.
    const motion = {
      idle: ["Idle", 0], thinking: ["neutral", 0], searching: ["neutral", 1],
      responding: ["happy", 0], failed: ["neutral", 0],
    }[value];
    Promise.resolve(model.motion(motion[0], motion[1], 3)).catch(() => {});
  }

  function petHit(x, y) {
    if (!app || !model) return false;
    const rect = canvas.getBoundingClientRect();
    if (x < rect.left || x >= rect.right || y < rect.top || y >= rect.bottom) return false;
    const gl = app.renderer.gl;
    if (!gl) return false;
    const px = Math.min(canvas.width - 1, Math.max(0, Math.floor((x - rect.left) * canvas.width / rect.width)));
    const py = Math.min(canvas.height - 1, Math.max(0, canvas.height - 1 - Math.floor((y - rect.top) * canvas.height / rect.height)));
    try {
      // One framebuffer pixel, not the whole texture; transparent hair gaps remain click-through.
      gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      return pixel[3] >= 24;
    } catch { return false; }
  }

  function refreshInteractive() {
    clearTimeout(hitTimer);
    if (!bridge) return;
    let hit = Boolean(drag);
    if (!hit && pointer) {
      const target = document.elementFromPoint(pointer.x, pointer.y);
      hit = Boolean(target?.closest("[data-interactive]")) || petHit(pointer.x, pointer.y);
    }
    if (hit !== interactive) {
      interactive = hit;
      bridge.setInteractive(hit);
    }
  }

  function endDrag() {
    if (!drag) return;
    if (drag.started) bridge?.drag("end");
    try { canvas.releasePointerCapture(drag.pointerId); } catch { /* Native move may end capture. */ }
    drag = null;
    refreshInteractive();
  }

  document.addEventListener("pointermove", (event) => {
    pointer = { x: event.clientX, y: event.clientY };
    if (drag && !drag.started && Math.hypot(event.screenX - drag.x, event.screenY - drag.y) >= 5) {
      drag.started = true;
      bridge?.drag("start");
    }
    const elapsed = performance.now() - lastHitAt;
    if (elapsed > 25 || drag) { lastHitAt = performance.now(); refreshInteractive(); }
    else { clearTimeout(hitTimer); hitTimer = setTimeout(refreshInteractive, 26 - elapsed); }
  });
  document.addEventListener("pointerleave", () => { if (!drag) { pointer = null; refreshInteractive(); } });
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !petHit(event.clientX, event.clientY)) return;
    event.preventDefault();
    drag = { x: event.screenX, y: event.screenY, pointerId: event.pointerId, started: false };
    canvas.setPointerCapture(event.pointerId);
    refreshInteractive();
  });
  canvas.addEventListener("pointerup", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const clicked = !drag.started;
    endDrag();
    if (clicked) window.dispatchEvent(new Event("pet-click"));
  });
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("lostpointercapture", endDrag);
  canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    if (petHit(event.clientX, event.clientY)) bridge?.showMenu();
  });
  canvas.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); window.dispatchEvent(new Event("pet-click")); }
  });
  document.addEventListener("contextmenu", (event) => {
    if (!(event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement)) event.preventDefault();
  });
  document.addEventListener("visibilitychange", () => {
    if (!app) return;
    if (document.hidden) { app.stop(); endDrag(); } else { app.start(); fit(); }
  });
  window.addEventListener("resize", fit);
  window.addEventListener("pagehide", () => {
    endDrag();
    const previous = app;
    app = null; model = null;
    previous?.destroy(false, { children: true, texture: true, baseTexture: true });
  });
  window.aiNekoPet = Object.freeze({ setState, setScale, setSpeechLevel, refreshInteractive, endDrag });

  async function load() {
    try {
      if (!window.PIXI?.live2d?.Live2DModel) throw new Error("renderer_dependency_missing");
      app = new PIXI.Application({
        view: canvas, width: stage.clientWidth, height: stage.clientHeight,
        backgroundAlpha: 0, antialias: true, autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        preserveDrawingBuffer: true, powerPreference: "low-power",
      });
      app.ticker.maxFPS = 30;
      model = await PIXI.live2d.Live2DModel.from(new URL("../assets/yui-lolita/yui-lolita.model3.json", location.href).href, {
        autoHitTest: false, autoFocus: false, autoUpdate: true, checkMocConsistency: true,
      });
      model.internalModel.on('beforeModelUpdate', () => {
        model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', speechLevel);
      });
      size = { width: model.width, height: model.height };
      model.anchor.set(0.5, 1);
      app.stage.addChild(model);
      fit();
      app.renderer.render(app.stage);
      measureVisibleBounds();
      fit();
      app.renderer.render(app.stage);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      loading.hidden = true;
      stage.dataset.loaded = "true";
      stage.dataset.renderer = "Live2D-Cubism4";
      const latest = currentState;
      currentState = "";
      setState(latest);
      refreshInteractive();
      window.dispatchEvent(new Event("pet-ready"));
    } catch (error) {
      stage.dataset.loaded = "error";
      loading.textContent = "角色加载失败。请从桌宠菜单退出后重新打开；若仍失败，请重新解压完整安装包。";
      loading.classList.add("is-error");
      // A failed renderer is explicit; it cannot pass as a working avatar or block settings and exit.
      console.error("ai-neko Live2D failed:", error?.message || "unknown");
    }
  }
  load();
})();
