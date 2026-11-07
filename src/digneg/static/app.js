(() => {
  "use strict";

  const STORAGE_KEY = "digneg_profiles_v1";
  const DEFAULT_PROFILES = [
    {
      name: "ilford_mg_rc_default",
      steps_total: 21,
      black_step: 2.0,
      white_step: 17.0,
      midtone_step: 9.5,
      gamma: 1.0,
      ink_mode: "pyro_amber",
      flip_horizontal: true,
      notes: "Ilford Multigrade RC, Filter #2, 21-step calibration baseline.",
      custom_points: []
    }
  ];

  let profiles = [];
  let selectedFile = null;

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(id, message, isError = false) {
    const el = $(id);
    if (!el) return;
    el.textContent = message;
    el.className = "status-line" + (message ? (isError ? " err" : " ok") : "");
  }

  function loadProfiles() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length > 0) {
          profiles = parsed;
          return;
        }
      }
    } catch (_) {
      // fallback to default
    }
    profiles = JSON.parse(JSON.stringify(DEFAULT_PROFILES));
    saveProfiles();
  }

  function saveProfiles() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(profiles));
    } catch (_) {
      // ignore storage quota errors
    }
  }

  function getProfile(name) {
    return profiles.find((p) => p.name === name) || profiles[0] || DEFAULT_PROFILES[0];
  }

  function syncProfileSelectors(preferredName) {
    const convSel = $("convProfileSelect");
    const calSel = $("calLoadSelect");
    const wedgeSel = $("wedgeProfile");

    const curConv = preferredName || convSel.value;
    const curCal = preferredName || calSel.value;
    const curWedge = wedgeSel.value;

    convSel.innerHTML = "";
    calSel.innerHTML = "";
    wedgeSel.innerHTML = '<option value="">Uncalibrated Linear (0%–100%)</option>';

    profiles.forEach((p) => {
      const text = `${p.name} [${p.ink_mode}, ${p.black_step}–${p.white_step}/${p.steps_total - 1}]`;
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = text;
      convSel.appendChild(opt);
      calSel.appendChild(opt.cloneNode(true));
      wedgeSel.appendChild(opt.cloneNode(true));
    });

    if (profiles.some((p) => p.name === curConv)) convSel.value = curConv;
    if (profiles.some((p) => p.name === curCal)) calSel.value = curCal;
    if (profiles.some((p) => p.name === curWedge)) wedgeSel.value = curWedge;
  }

  function populateCalibrationForm() {
    const prof = getProfile($("calLoadSelect").value);
    if (!prof) return;
    $("calName").value = prof.name;
    $("calSteps").value = prof.steps_total;
    $("calBlack").value = prof.black_step;
    $("calWhite").value = prof.white_step;
    $("calMid").value = prof.midtone_step !== null && prof.midtone_step !== undefined ? prof.midtone_step : "";
    $("calGamma").value = prof.gamma;
    $("calInkMode").value = prof.ink_mode;
    $("calFlip").checked = Boolean(prof.flip_horizontal);
    $("calNotes").value = prof.notes || "";
    requestCurveUpdate();
  }

  function readCalibrationForm() {
    const midStr = $("calMid").value.trim();
    return {
      name: $("calName").value.trim() || "profile",
      steps_total: parseInt($("calSteps").value, 10) || 21,
      black_step: parseFloat($("calBlack").value) || 0,
      white_step: parseFloat($("calWhite").value) || 20,
      midtone_step: midStr === "" ? null : parseFloat(midStr),
      gamma: parseFloat($("calGamma").value) || 1.0,
      ink_mode: $("calInkMode").value,
      flip_horizontal: $("calFlip").checked,
      notes: $("calNotes").value,
      custom_points: []
    };
  }

  async function requestCurveUpdate() {
    const payload = readCalibrationForm();
    try {
      const res = await fetch("/api/curve-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("calStatus", data.error || "Invalid parameters", true);
        return;
      }
      setStatus("calStatus", "");
      renderCurvePlot(data.curve);
      $("curveSummary").innerHTML =
        `<span>Dmax floor: <strong>${data.curve.black_pct}%</strong></span>` +
        `<span>Dmin ceiling: <strong>${data.curve.white_pct}%</strong></span>` +
        `<span>Active range: <strong>${(data.curve.white_pct - data.curve.black_pct).toFixed(1)}%</strong></span>`;
    } catch (err) {
      setStatus("calStatus", String(err), true);
    }
  }

  function renderCurvePlot(curve) {
    const canvas = $("curveCanvas");
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const padL = 52;
    const padR = 24;
    const padT = 24;
    const padB = 40;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;

    // Grid
    ctx.strokeStyle = "#222222";
    ctx.lineWidth = 1;
    ctx.font = "10px monospace";
    ctx.fillStyle = "#6e6e6e";

    for (let i = 0; i <= 4; i++) {
      const x = Math.round(padL + (plotW * i) / 4) + 0.5;
      const y = Math.round(padT + (plotH * i) / 4) + 0.5;

      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + plotH);
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();

      ctx.fillText(`${i * 25}%`, x - 10, padT + plotH + 16);
      ctx.fillText(`${100 - i * 25}%`, padL - 34, y + 3);
    }

    // Uncalibrated diagonal
    ctx.strokeStyle = "#3a3a3a";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padL, padT + plotH);
    ctx.lineTo(padL + plotW, padT);
    ctx.stroke();
    ctx.setLineDash([]);

    const xs = curve.positive_input_pct;

    // Calibrated Ink Density
    const inkYs = curve.calibrated_ink_pct;
    ctx.strokeStyle = "#d4882a";
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < xs.length; i++) {
      const px = padL + (xs[i] / 100.0) * plotW;
      const py = padT + plotH - (inkYs[i] / 100.0) * plotH;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.stroke();

    // Negative Pixel Output
    const negYs = curve.negative_pixel_pct;
    ctx.strokeStyle = "#8c8c8c";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let i = 0; i < xs.length; i++) {
      const px = padL + (xs[i] / 100.0) * plotW;
      const py = padT + plotH - (negYs[i] / 100.0) * plotH;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.stroke();

    // Legend
    ctx.fillStyle = "#d4882a";
    ctx.fillText("— Ink Density (%)", padL + 10, padT + 14);
    ctx.fillStyle = "#8c8c8c";
    ctx.fillText("— Negative Output (%)", padL + 135, padT + 14);
    ctx.fillStyle = "#6e6e6e";
    ctx.fillText("Positive Input Luminance (0% Shadow → 100% Highlight)", padL + 85, H - 8);
  }

  async function handleSaveProfile() {
    const payload = readCalibrationForm();
    try {
      const res = await fetch("/api/curve-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("calStatus", data.error || "Validation error", true);
        return;
      }
      const idx = profiles.findIndex((p) => p.name === payload.name);
      if (idx >= 0) profiles[idx] = payload;
      else profiles.push(payload);
      saveProfiles();
      syncProfileSelectors(payload.name);
      setStatus("calStatus", `Saved "${payload.name}" to localStorage`);
    } catch (err) {
      setStatus("calStatus", String(err), true);
    }
  }

  function handleDeleteProfile() {
    const name = $("calLoadSelect").value;
    if (!name) return;
    profiles = profiles.filter((p) => p.name !== name);
    if (profiles.length === 0) {
      profiles = JSON.parse(JSON.stringify(DEFAULT_PROFILES));
    }
    saveProfiles();
    syncProfileSelectors(profiles[0].name);
    populateCalibrationForm();
    setStatus("calStatus", `Deleted "${name}"`);
  }

  function handleExportProfile() {
    const payload = readCalibrationForm();
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${payload.name || "profile"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImportProfile(file) {
    if (!file) return;
    try {
      const text = await file.text();
      const imported = JSON.parse(text);
      const res = await fetch("/api/curve-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(imported)
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("calStatus", data.error || "Invalid profile JSON", true);
        return;
      }
      const idx = profiles.findIndex((p) => p.name === imported.name);
      if (idx >= 0) profiles[idx] = imported;
      else profiles.push(imported);
      saveProfiles();
      syncProfileSelectors(imported.name);
      populateCalibrationForm();
      setStatus("calStatus", `Imported "${imported.name}"`);
    } catch (err) {
      setStatus("calStatus", `Import error: ${err}`, true);
    } finally {
      $("importProfileInput").value = "";
    }
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  function selectImageFile(file) {
    if (!file) return;
    selectedFile = file;
    $("dropzoneText").innerHTML = `<span class="filename">${file.name}</span> (${(file.size / 1024).toFixed(1)} KB)`;
    renderConversionPreview();
  }

  async function renderConversionPreview() {
    if (!selectedFile) {
      setStatus("convStatus", "No source image selected", true);
      return;
    }
    setStatus("convStatus", "Processing preview...");
    try {
      const b64 = await fileToBase64(selectedFile);
      const prof = getProfile($("convProfileSelect").value);
      const payload = {
        filename: selectedFile.name,
        image_base64: b64,
        profile: prof,
        override_ink_mode: $("convInkMode").value || null,
        override_flip: $("convFlip").checked,
        override_dpi: $("convDpi").value ? parseFloat($("convDpi").value) : null
      };
      const res = await fetch("/api/convert/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("convStatus", data.error || "Conversion failed", true);
        return;
      }
      $("convMetaReadout").textContent = `${data.width}×${data.height}px | ${data.dpi} DPI | ${data.ink_mode}`;
      setStatus("convStatus", "Preview updated");
      $("convPreviewBox").innerHTML = `
        <div class="comparison-grid">
          <div class="comparison-pane">
            <div class="comparison-pane-header">
              <span>POSITIVE LUMINANCE</span>
              <span>${data.width}×${data.height}</span>
            </div>
            <img src="${data.positive_preview}" alt="Positive" />
          </div>
          <div class="comparison-pane">
            <div class="comparison-pane-header">
              <span>CALIBRATED NEGATIVE</span>
              <span>${data.ink_mode}</span>
            </div>
            <img src="${data.negative_preview}" alt="Negative" />
          </div>
        </div>
      `;
    } catch (err) {
      setStatus("convStatus", String(err), true);
    }
  }

  async function exportConvertedTiff() {
    if (!selectedFile) {
      setStatus("convStatus", "No source image selected", true);
      return;
    }
    setStatus("convStatus", "Encoding 16-bit TIFF...");
    try {
      const b64 = await fileToBase64(selectedFile);
      const prof = getProfile($("convProfileSelect").value);
      const payload = {
        filename: selectedFile.name,
        image_base64: b64,
        profile: prof,
        override_ink_mode: $("convInkMode").value || null,
        override_flip: $("convFlip").checked,
        override_dpi: $("convDpi").value ? parseFloat($("convDpi").value) : null
      };
      const res = await fetch("/api/convert/tiff", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const err = await res.json();
        setStatus("convStatus", err.error || "Export failed", true);
        return;
      }
      const blob = await res.blob();
      const stem = selectedFile.name.replace(/\.[^.]+$/, "");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${stem}_neg16.tiff`;
      a.click();
      URL.revokeObjectURL(url);
      setStatus("convStatus", `Exported ${stem}_neg16.tiff`);
    } catch (err) {
      setStatus("convStatus", String(err), true);
    }
  }

  function readWedgeParams() {
    const profName = $("wedgeProfile").value;
    return {
      steps: parseInt($("wedgeSteps").value, 10) || 21,
      dpi: parseInt($("wedgeDpi").value, 10) || 300,
      width_inches: parseFloat($("wedgeWidth").value) || 8.0,
      height_inches: parseFloat($("wedgeHeight").value) || 2.5,
      ink_mode: $("wedgeInkMode").value,
      profile: profName ? getProfile(profName) : null,
      flip_horizontal: $("wedgeFlip").checked,
      include_ink_comparison: $("wedgeCompareInks").checked
    };
  }

  async function renderWedgePreview() {
    setStatus("wedgeStatus", "Rendering step tablet...");
    try {
      const payload = readWedgeParams();
      const res = await fetch("/api/wedge/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("wedgeStatus", data.error || "Failed to generate wedge", true);
        return;
      }
      $("wedgeMetaReadout").textContent = `${data.width}×${data.height}px | ${payload.dpi} DPI | ${payload.steps} steps`;
      setStatus("wedgeStatus", "Step tablet ready");
      $("wedgePreviewBox").innerHTML = `<img src="${data.preview}" alt="Step Wedge" />`;
    } catch (err) {
      setStatus("wedgeStatus", String(err), true);
    }
  }

  async function exportWedgeTiff() {
    setStatus("wedgeStatus", "Encoding 16-bit TIFF...");
    try {
      const payload = readWedgeParams();
      const res = await fetch("/api/wedge/tiff", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const err = await res.json();
        setStatus("wedgeStatus", err.error || "Failed to export TIFF", true);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `wedge_${payload.steps}step_${payload.ink_mode}_16bit.tiff`;
      a.click();
      URL.revokeObjectURL(url);
      setStatus("wedgeStatus", "Exported 16-bit step tablet TIFF");
    } catch (err) {
      setStatus("wedgeStatus", String(err), true);
    }
  }

  function initTabs() {
    const buttons = document.querySelectorAll(".nav-tab");
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-tab");
        buttons.forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
        btn.classList.add("active");
        $(`view-${target}`).classList.add("active");
        if (target === "calibrate") requestCurveUpdate();
      });
    });
  }

  function initEvents() {
    initTabs();

    const dz = $("dropzone");
    const fileInput = $("fileInput");
    dz.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files[0]) selectImageFile(fileInput.files[0]);
    });
    dz.addEventListener("dragover", (e) => {
      e.preventDefault();
      dz.classList.add("dragover");
    });
    dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      dz.classList.remove("dragover");
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        selectImageFile(e.dataTransfer.files[0]);
      }
    });

    $("convProfileSelect").addEventListener("change", () => {
      const prof = getProfile($("convProfileSelect").value);
      if (prof) $("convFlip").checked = Boolean(prof.flip_horizontal);
    });

    $("btnPreviewConv").addEventListener("click", renderConversionPreview);
    $("btnDownloadConv").addEventListener("click", exportConvertedTiff);

    $("calLoadSelect").addEventListener("change", populateCalibrationForm);
    ["calSteps", "calBlack", "calWhite", "calMid", "calGamma", "calInkMode"].forEach((id) => {
      $(id).addEventListener("input", requestCurveUpdate);
      $(id).addEventListener("change", requestCurveUpdate);
    });

    $("btnSaveProfile").addEventListener("click", handleSaveProfile);
    $("btnExportProfile").addEventListener("click", handleExportProfile);
    $("btnImportProfile").addEventListener("click", () => $("importProfileInput").click());
    $("importProfileInput").addEventListener("change", (e) => {
      if (e.target.files && e.target.files[0]) handleImportProfile(e.target.files[0]);
    });
    $("btnDeleteProfile").addEventListener("click", handleDeleteProfile);

    $("btnPreviewWedge").addEventListener("click", renderWedgePreview);
    $("btnDownloadWedge").addEventListener("click", exportWedgeTiff);
  }

  window.addEventListener("DOMContentLoaded", () => {
    loadProfiles();
    syncProfileSelectors();
    populateCalibrationForm();
    const prof = getProfile($("convProfileSelect").value);
    if (prof) $("convFlip").checked = Boolean(prof.flip_horizontal);
    initEvents();
  });
})();
