/* Image editor for pictures and banners (like Discord's "Edit Image"): drag to move, zoom, rotate, then Apply.
   editImage(file, "avatar" | "banner") -> Promise of a data: URL (or null when cancelled).
   Animated GIFs are kept as they are (cutting them would lose the animation). */
(function () {
  // t() is the app's translator (app.js); this file is loaded first but only runs after the app is ready
  const MAX_BYTES = 7.5 * 1024 * 1024;

  function readAsDataURL(file) {
    return new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = bad; r.readAsDataURL(file); });
  }

  window.editImage = async function editImage(file, kind) {
    if (!file) return null;
    if (file.type === "image/gif") {
      if (typeof toast === "function") toast(t("קובץ GIF נשמר כמו שהוא, כדי לא לאבד את האנימציה."), "ok");
      return readAsDataURL(file);
    }
    const url = URL.createObjectURL(file);
    const img = new Image();
    try {
      await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
    } catch {
      URL.revokeObjectURL(url);
      if (typeof toast === "function") toast(t("לא הצלחתי לפתוח את התמונה."), "error");
      return null;
    }
    return new Promise((resolve) => openEditor(img, kind === "banner", (result) => { URL.revokeObjectURL(url); resolve(result); }));
  };

  function openEditor(img, banner, done) {
    const VW = 480, VH = 320;                                  // the editing area
    const FW = banner ? 440 : 270, FH = banner ? Math.round(440 * 240 / 680) : 270;  // the frame that is kept
    const OUT_W = banner ? 1360 : 1024, OUT_H = banner ? 480 : 1024;
    const st = { zoom: 1, rot: 0, x: 0, y: 0 };

    const box = document.createElement("div");
    box.className = "modal crop-modal";
    box.innerHTML = `
      <div class="modal-card crop-card">
        <div class="row"><h2>${esc(t("עריכת תמונה"))}</h2><div class="spacer"></div>
          <button class="btn btn-ghost btn-sm" data-crop="cancel" aria-label="${esc(t("ביטול"))}">✕</button></div>
        <canvas class="crop-canvas" width="${VW}" height="${VH}"></canvas>
        <div class="crop-tools">
          <span class="crop-ico small">🖼</span>
          <input type="range" class="crop-zoom" min="1" max="4" step="0.01" value="1" aria-label="${esc(t("זום"))}">
          <span class="crop-ico">🖼</span>
          <div class="spacer"></div>
          <button class="btn btn-ghost btn-sm" data-crop="rotate" title="${esc(t("סיבוב"))}">↻</button>
        </div>
        <div class="muted small">${esc(t("גרור כדי להזיז, גלגלת או הסרגל כדי להגדיל."))}</div>
        <div class="row" style="margin-top:14px">
          <button class="btn btn-ghost" data-crop="reset">${esc(t("איפוס"))}</button><div class="spacer"></div>
          <button class="btn" data-crop="cancel">${esc(t("ביטול"))}</button>
          <button class="btn btn-primary" data-crop="apply">${esc(t("החל"))}</button>
        </div>
      </div>`;
    document.body.appendChild(box);
    const canvas = box.querySelector("canvas"), ctx = canvas.getContext("2d"), slider = box.querySelector(".crop-zoom");

    const dims = () => (st.rot % 180 ? [img.naturalHeight, img.naturalWidth] : [img.naturalWidth, img.naturalHeight]);
    const baseScale = () => { const [w, h] = dims(); return Math.max(FW / w, FH / h); };  // the picture always covers the frame
    function clamp() {
      const [w, h] = dims(), s = baseScale() * st.zoom;
      const mx = Math.max(0, (w * s - FW) / 2), my = Math.max(0, (h * s - FH) / 2);
      st.x = Math.min(mx, Math.max(-mx, st.x));
      st.y = Math.min(my, Math.max(-my, st.y));
    }
    function drawImage(c, cw, ch, k) {
      c.save();
      c.translate(cw / 2 + st.x * k, ch / 2 + st.y * k);
      c.rotate(st.rot * Math.PI / 180);
      const s = baseScale() * st.zoom * k;
      c.scale(s, s);
      c.imageSmoothingQuality = "high";
      c.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
      c.restore();
    }
    function draw() {
      ctx.clearRect(0, 0, VW, VH);
      ctx.fillStyle = "#0b0d12";
      ctx.fillRect(0, 0, VW, VH);
      drawImage(ctx, VW, VH, 1);
      const fx = (VW - FW) / 2, fy = (VH - FH) / 2;
      ctx.save();  // darken everything outside the frame
      ctx.beginPath();
      ctx.rect(0, 0, VW, VH);
      if (banner) ctx.rect(fx, fy, FW, FH); else ctx.arc(VW / 2, VH / 2, FW / 2, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,0,0,.6)";
      ctx.fill("evenodd");
      ctx.restore();
      ctx.lineWidth = 3;
      ctx.strokeStyle = "#fff";
      ctx.beginPath();
      if (banner) ctx.rect(fx, fy, FW, FH); else ctx.arc(VW / 2, VH / 2, FW / 2, 0, Math.PI * 2);
      ctx.stroke();
    }
    function setZoom(z) { st.zoom = Math.min(4, Math.max(1, z)); slider.value = st.zoom; clamp(); draw(); }

    let drag = null;
    canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, sx: st.x, sy: st.y }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const r = canvas.getBoundingClientRect(), k = VW / r.width;  // the canvas may be shown smaller than its pixels
      st.x = drag.sx + (e.clientX - drag.x) * k;
      st.y = drag.sy + (e.clientY - drag.y) * k;
      clamp(); draw();
    });
    const endDrag = () => { drag = null; };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("wheel", (e) => { e.preventDefault(); setZoom(st.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08)); }, { passive: false });
    slider.addEventListener("input", () => setZoom(+slider.value));

    function close(result) { box.remove(); document.removeEventListener("keydown", onKey, true); done(result); }
    function apply() {
      const out = document.createElement("canvas");
      out.width = OUT_W; out.height = OUT_H;
      const k = OUT_W / FW;
      drawImage(out.getContext("2d"), OUT_W, OUT_H, k);
      let data = out.toDataURL("image/png");
      if (data.length * 0.75 > MAX_BYTES) data = out.toDataURL("image/jpeg", 0.9);  // big photos: smaller file
      close(data);
    }
    box.addEventListener("click", (e) => {
      const b = e.target.closest("[data-crop]");
      if (!b) { if (e.target === box) close(null); return; }
      const a = b.dataset.crop;
      if (a === "cancel") close(null);
      else if (a === "apply") apply();
      else if (a === "reset") { st.zoom = 1; st.rot = 0; st.x = 0; st.y = 0; slider.value = 1; draw(); }
      else if (a === "rotate") { st.rot = (st.rot + 90) % 360; st.x = 0; st.y = 0; clamp(); draw(); }
    });
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); close(null); }
      if (e.key === "Enter" && !e.target.matches("textarea")) { e.preventDefault(); apply(); }
    }
    document.addEventListener("keydown", onKey, true);
    draw();
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
})();
