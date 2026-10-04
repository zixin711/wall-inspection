/* 前端主逻辑。无框架、无外部依赖 —— 私有化部署环境常常没有外网。
   核心交互是采集向导里的洋葱皮对齐：拍第二张时把第一张半透明叠在取景框上，
   用户自然就对齐了。这比在后端跟特征匹配失败率搏斗便宜得多。 */
(function () {
  "use strict";

  var S = {
    lang: "zh",
    camOk: false,
    camReason: null,
    step: 1,                 // 1 白光 · 2 UV · 3 确认
    white: null,             // {blob, url}
    uv: null,
    stream: null,
    taskId: null,
    es: null,
    result: null,
    cfg: null,
    pickTarget: "white"
  };

  var $ = function (id) { return document.getElementById(id); };
  var t = function (k) { return (window.I18N[S.lang] || {})[k] || k; };

  // ---------------------------------------------------------------- 语言

  function applyLang() {
    document.documentElement.lang = S.lang === "zh" ? "zh-CN" : "en";
    Array.prototype.forEach.call(document.querySelectorAll("[data-i18n]"), function (el) {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    Array.prototype.forEach.call(document.querySelectorAll(".langtoggle button"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.lang === S.lang));
    });
    if (S.camReason) showCamFallback(S.camReason);
    renderCapture();
    if (S.result) renderResult(S.result);
  }

  Array.prototype.forEach.call(document.querySelectorAll(".langtoggle button"), function (b) {
    b.addEventListener("click", function () {
      S.lang = b.dataset.lang;
      try { localStorage.setItem("mould.lang", S.lang); } catch (e) {}
      applyLang();
    });
  });

  // ---------------------------------------------------------------- 屏幕

  function show(id) {
    Array.prototype.forEach.call(document.querySelectorAll(".screen"), function (s) {
      s.classList.toggle("active", s.id === id);
    });
    // field = 采集/分析（暖深色，现场关灯时不刺眼）；report = 结果（暖白纸面）
    document.body.dataset.mode = (id === "screen-result") ? "report" : "field";
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- 相机

  function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showCamFallback(window.isSecureContext === false ? "noCamHttps" : "noCamGeneric");
      return Promise.resolve(false);
    }
    S.camOk = false;
    return navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } }, audio: false
    }).then(function (stream) {
      S.stream = stream;
      var v = $("video");
      v.srcObject = stream;
      v.classList.remove("hidden");
      S.camOk = true;
      $("camFallback").classList.add("hidden");
      return v.play().then(function () { return true; }).catch(function () { return true; });
    }).catch(function (err) {
      // 退回拍照/相册，功能不打折 —— 但必须说清楚为什么没有实时取景
      S.camOk = false;
      $("video").classList.add("hidden");
      var name = err && err.name;
      var key = "noCamGeneric";
      if (window.isSecureContext === false) key = "noCamHttps";
      else if (name === "NotAllowedError" || name === "SecurityError") key = "noCamDenied";
      else if (name === "NotFoundError" || name === "OverconstrainedError") key = "noCamMissing";
      showCamFallback(key);
      return false;
    });
  }

  function showCamFallback(bodyKey) {
    S.camReason = bodyKey;
    $("camFbTitle").textContent = t("noCamTitle");
    $("camFbBody").textContent = t(bodyKey);
    $("camFallback").classList.remove("hidden");
  }

  function stopCamera() {
    if (S.stream) {
      S.stream.getTracks().forEach(function (tr) { tr.stop(); });
      S.stream = null;
    }
  }

  function grabFrame() {
    var v = $("video");
    if (!v.videoWidth) return Promise.resolve(null);
    var c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    return new Promise(function (res) {
      c.toBlob(function (b) { res(b); }, "image/jpeg", 0.92);
    });
  }

  // ---------------------------------------------------------------- 采集界面

  function renderCapture() {
    var stepKeys = ["step1", "step2", "step3"];
    $("step-label").textContent = t(stepKeys[S.step - 1]);
    Array.prototype.forEach.call(document.querySelectorAll(".steps i"), function (el, i) {
      el.classList.toggle("on", i < S.step);
    });

    var hint = S.step === 1 ? "hintWhite" : (S.step === 2 ? "hintUv" : "hintDone");
    $("reticle").setAttribute("data-hint", t(hint));

    // 洋葱皮：只在拍 UV 时叠白光图的描边
    var onion = $("onion");
    if (S.step === 2 && S.white && S.white.edges) {
      onion.style.backgroundImage = "url(" + S.white.edges + ")";
      onion.classList.remove("hidden");
    } else {
      onion.classList.add("hidden");
    }

    // 第 3 步没有实时取景，直接显示已拍的 UV（或白光）图
    var still = $("stillPreview");
    if (S.step === 3) {
      var src = (S.uv || S.white);
      still.src = src ? src.url : "";
      still.classList.remove("hidden");
      $("video").classList.add("hidden");
    } else {
      still.classList.add("hidden");
      if (S.stream) $("video").classList.remove("hidden");
    }

    document.querySelector(".viewport").classList.toggle("nocam", !S.camOk && S.step !== 3);
    document.querySelector(".shutterbar").classList.toggle("hidden", !S.camOk);
    $("reticle").classList.toggle("hidden", !S.camOk);
    // 空状态里已经给了这两个按钮，下面就不必再重复一遍
    $("shootRow").classList.toggle("hidden", !S.camOk);

    renderHud();
    renderThumbs();
    renderGuide();

    $("shutter").disabled = S.step === 3 || !S.camOk;
    $("sideLeft").textContent = S.white ? (S.lang === "zh" ? "白光 ✓" : "White ✓") : "";
    $("sideLeft").className = "side" + (S.white ? " act" : "");

    var right = $("sideRight");
    if (S.step === 1) { right.textContent = ""; right.disabled = true; }
    else if (S.step === 2) { right.textContent = t("skipUv"); right.disabled = false; }
    else { right.textContent = t("retake"); right.disabled = false; }

    $("btnAnalyze").disabled = !S.white;
  }

  // 现场是举着手机、单手操作、房间可能是暗的 —— 只显示当前这步用得上的提示
  var GUIDE = { 1: ["guide1", "guide4"], 2: ["guide2", "guide3"], 3: ["guide3", "guide4"] };

  function renderGuide() {
    var box = document.querySelector(".guide ul");
    if (!box) return;
    box.innerHTML = "";
    (GUIDE[S.step] || []).forEach(function (k) {
      if (k === "guide3" && !S.camOk) k = "guide3nocam";
      var li = document.createElement("li");
      li.textContent = t(k);
      box.appendChild(li);
    });
    var safety = document.querySelector(".guide .safety");
    if (safety) safety.classList.toggle("hidden", S.step !== 2);
  }

  function renderHud() {
    var hud = $("hud");
    hud.innerHTML = "";
    if (!S.camOk) return;      // 取景框里没画面时，这些贴条没有依附对象
    function pill(text, cls) {
      var s = document.createElement("span");
      s.className = "pill" + (cls ? " " + cls : "");
      s.textContent = text;
      hud.appendChild(s);
    }
    if (S.step === 1) {
      pill(S.lang === "zh" ? "开灯拍摄" : "Lights on", "ok");
      pill(S.lang === "zh" ? "距离 1–1.5m" : "1–1.5 m");
    } else if (S.step === 2) {
      pill("365nm");
      pill(S.lang === "zh" ? "关灯 · 拉窗帘" : "Lights off", "warn");
      pill(S.lang === "zh" ? "对齐残影" : "Match the ghost");
    }
  }

  function renderThumbs() {
    [["thWhite", S.white, "whiteShot"], ["thUv", S.uv, "uvShot"]].forEach(function (p) {
      var el = $(p[0]), shot = p[1];
      el.innerHTML = "";
      if (shot) {
        el.classList.remove("empty");
        var img = document.createElement("img"); img.src = shot.url; img.alt = "";
        var sp = document.createElement("span"); sp.textContent = t(p[2]);
        el.appendChild(img); el.appendChild(sp);
      } else {
        el.classList.add("empty");
        var s2 = document.createElement("span"); s2.textContent = t(p[2]);
        el.appendChild(s2);
      }
    });
  }

  function setShot(kind, blob) {
    if (!blob) return;
    var slot = { blob: blob, url: URL.createObjectURL(blob), edges: null };
    if (S[kind] && S[kind].url) URL.revokeObjectURL(S[kind].url);
    S[kind] = slot;
    if (kind === "white" && S.step === 1) S.step = 2;
    else if (kind === "uv" && S.step === 2) S.step = 3;
    renderCapture();
    if (kind === "white") {
      edgeMap(slot.url).then(function (d) {
        slot.edges = d;
        if (S.white === slot) renderCapture();
      });
    }
  }

  /* 从白光图生成描边图，用作取景框里的对齐参考。
     叠原图看着直观，但 UV 拍摄要关灯，暗场景下半透明原图要么看不见、
     要么和真实画面糊成一团；描边只留边界，明暗背景下都能对齐。 */
  function edgeMap(url) {
    return new Promise(function (resolve) {
      var img = new Image();
      img.onerror = function () { resolve(url); };
      img.onload = function () {
        try {
          // 分辨率压到 360 再求梯度：霉斑本身是高频纹理，分辨率太高会画出满屏
          // 噪点，对齐时反而看不出结构。要的是墙角、插座、斑块边界这类骨架。
          var W = 360, H = Math.max(2, Math.round(W * img.height / img.width));
          var c = document.createElement("canvas");
          c.width = W; c.height = H;
          var ctx = c.getContext("2d");
          ctx.drawImage(img, 0, 0, W, H);
          var d = ctx.getImageData(0, 0, W, H).data;

          var g = new Float32Array(W * H);
          for (var i = 0, j = 0; i < d.length; i += 4, j++) {
            g[j] = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
          }
          // 3x3 均值模糊，压掉纹理噪声
          var bl = new Float32Array(g);
          for (var by = 1; by < H - 1; by++) {
            for (var bx = 1; bx < W - 1; bx++) {
              var bo = by * W + bx;
              bl[bo] = (g[bo - W - 1] + g[bo - W] + g[bo - W + 1] +
                        g[bo - 1] + g[bo] + g[bo + 1] +
                        g[bo + W - 1] + g[bo + W] + g[bo + W + 1]) / 9;
            }
          }

          var mag = new Float32Array(W * H);
          for (var y = 2; y < H - 2; y++) {
            for (var x = 2; x < W - 2; x++) {
              var o = y * W + x;
              var gx = -bl[o - W - 1] - 2 * bl[o - 1] - bl[o + W - 1]
                       + bl[o - W + 1] + 2 * bl[o + 1] + bl[o + W + 1];
              var gy = -bl[o - W - 1] - 2 * bl[o - W] - bl[o - W + 1]
                       + bl[o + W - 1] + 2 * bl[o + W] + bl[o + W + 1];
              mag[o] = Math.sqrt(gx * gx + gy * gy);
            }
          }

          // 阈值取百分位而不是最大值的比例：按最大值归一时，画面里只要有一条
          // 极强的边（画框、插座），其余边就全被压到阈值以下，描边会整张空掉。
          var hist = new Uint32Array(256), n = 0;
          for (var k = 0; k < mag.length; k++) {
            if (mag[k] > 0) { hist[Math.min(255, mag[k] | 0)]++; n++; }
          }
          var keep = Math.round(n * 0.10), acc = 0, lo = 8;
          for (var v = 255; v >= 0; v--) {
            acc += hist[v];
            if (acc >= keep) { lo = Math.max(6, v); break; }
          }
          var hi = lo * 3;

          var out = ctx.createImageData(W, H);
          for (var q = 0; q < mag.length; q++) {
            var a = 0;
            if (mag[q] > lo) {
              a = Math.round(255 * Math.min(1, (mag[q] - lo) / (hi - lo + 1e-6)));
              a = Math.max(90, a);
            }
            // 赭石橙描边：第 2 步是关灯拍 UV，暖橙线在暗场景里最跳，
            // 也与界面配色同族
            out.data[q * 4] = 240; out.data[q * 4 + 1] = 150; out.data[q * 4 + 2] = 80;
            out.data[q * 4 + 3] = a;
          }
          ctx.putImageData(out, 0, 0);
          resolve(c.toDataURL("image/png"));
        } catch (e) {
          resolve(url);                 // 生成失败就退回原图，不让对齐功能整个消失
        }
      };
      img.src = url;
    });
  }

  $("shutter").addEventListener("click", function () {
    grabFrame().then(function (b) { setShot(S.step === 1 ? "white" : "uv", b); });
  });

  $("sideRight").addEventListener("click", function () {
    if (S.step === 2) { S.step = 3; renderCapture(); }        // 跳过 UV
    else if (S.step === 3) { S.step = S.uv ? 2 : 1; renderCapture(); }
  });

  function pickFrom(inputId) {
    S.pickTarget = (S.step === 1 || !S.white) ? "white" : "uv";
    $(inputId).click();
  }

  ["btnPick", "btnPickFb"].forEach(function (id) {
    $(id).addEventListener("click", function () { pickFrom("filePicker"); });
  });
  ["btnShoot", "btnShootFb"].forEach(function (id) {
    $(id).addEventListener("click", function () { pickFrom("fileCamera"); });
  });

  ["filePicker", "fileCamera"].forEach(function (id) {
    $(id).addEventListener("change", function (e) {
      var f = e.target.files && e.target.files[0];
      if (f) setShot(S.pickTarget, f);
      e.target.value = "";
    });
  });

  // ---------------------------------------------------------------- 分析

  var STAGES = [
    ["intake", "stIntake"], ["register", "stRegister"], ["quantify", "stQuantify"],
    ["interpret", "stInterpret"], ["arbitrate", "stArbitrate"], ["done", "stDone"]
  ];

  function renderStages(states) {
    var box = $("stages");
    box.innerHTML = "";
    STAGES.forEach(function (p) {
      var st = states[p[0]] || "waiting";
      var row = document.createElement("div");
      row.className = "stage " + (st === "start" ? "active" : (st === "waiting" ? "" : st === "skip" ? "skip" : "done"));
      var label = st === "start" ? "stRunning" : st === "waiting" ? "stWaiting"
        : st === "degraded" ? "stDegraded" : st === "skip" ? "stSkip" : "stOk";
      row.innerHTML = '<span class="dot"></span><span class="t"></span><span class="s"></span>';
      row.querySelector(".t").textContent = t(p[1]);
      row.querySelector(".s").textContent = t(label);
      box.appendChild(row);
    });
  }

  $("btnAnalyze").addEventListener("click", function () {
    if (!S.white) return;
    var fd = new FormData();
    fd.append("white", S.white.blob, "white.jpg");
    if (S.uv) fd.append("uv", S.uv.blob, "uv.jpg");
    fd.append("locale", S.lang);

    var states = {};
    renderStages(states);
    show("screen-analyzing");
    stopCamera();

    fetch("/api/v1/analyze", { method: "POST", body: fd })
      .then(function (r) { if (!r.ok) throw new Error("upload"); return r.json(); })
      .then(function (j) {
        S.taskId = j.task_id;
        listen(j.stream, states);
      })
      .catch(function () { fail(t("errUpload")); });
  });

  function listen(url, states) {
    if (S.es) S.es.close();
    var es = new EventSource(url);
    S.es = es;
    if (!S.uv) states.register = "skip";

    es.onmessage = function (ev) {
      var d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      states[d.stage] = d.state;
      renderStages(states);
      if (d.stage === "failed") { es.close(); fail(t("errAnalyze")); return; }
      if (d.stage === "done" && d.result) {
        es.close(); S.es = null;
        S.result = d.result;
        renderResult(d.result);
        show("screen-result");
      }
      if (d.state === "rejected") {
        es.close(); S.es = null;
        fetchResult();
      }
    };
    es.onerror = function () {
      es.close(); S.es = null;
      fetchResult();                 // SSE 断了就退回轮询取结果，不让用户卡住
    };
  }

  function fetchResult() {
    if (!S.taskId) return;
    fetch("/api/v1/tasks/" + S.taskId)
      .then(function (r) { return r.status === 202 ? null : r.json(); })
      .then(function (j) {
        if (!j) { setTimeout(fetchResult, 1500); return; }
        S.result = j; renderResult(j); show("screen-result");
      })
      .catch(function () { fail(t("errAnalyze")); });
  }

  function fail(msg) {
    S.result = null;
    var box = $("resultBanners");
    box.innerHTML = "";
    box.appendChild(banner(t("rejected"), msg, "err"));
    ["verdict", "comparebar", "compare"].forEach(function (c) {
      var el = document.querySelector("#screen-result ." + c);
      if (el) el.classList.add("hidden");
    });
    ["sectRegions", "sectCauses", "sectMorph", "sectNotes"].forEach(function (id) {
      $(id).classList.add("hidden");
    });
    show("screen-result");
  }

  $("btnCancel").addEventListener("click", function () {
    if (S.es) { S.es.close(); S.es = null; }
    show("screen-capture");
    startCamera().then(renderCapture);
  });

  // ---------------------------------------------------------------- 结果

  function banner(title, body, cls) {
    var d = document.createElement("div");
    d.className = "banner" + (cls ? " " + cls : "");
    var b = document.createElement("b"); b.textContent = title;
    d.appendChild(b);
    d.appendChild(document.createTextNode(body));
    return d;
  }

  var LV_COLOR = { 1: "--l1", 2: "--l2", 3: "--l3", 4: "--l4", 5: "--l5" };

  function renderResult(r) {
    ["verdict", "comparebar", "compare"].forEach(function (c) {
      var el = document.querySelector("#screen-result ." + c);
      if (el) el.classList.remove("hidden");
    });

    var zh = S.lang === "zh";
    var banners = $("resultBanners");
    banners.innerHTML = "";

    if (r.status === "rejected") {
      banners.appendChild(banner(t("rejected"), zh ? (r.notes_zh || [])[0] || "" : (r.notes_en || [])[0] || "", "err"));
    }
    (r.quality_issues || []).forEach(function (q) {
      banners.appendChild(banner(t("qualityTitle"), zh ? q.zh : q.en));
    });
    if (r.audit && !r.audit.has_uv) {
      banners.appendChild(banner(t("noUvTitle"), t("noUvBody")));
    }

    var g = r.grade;
    if (g) {
      $("gradeNum").textContent = "L" + g.level;
      $("gradeNum").style.color = "var(" + (LV_COLOR[g.level] || "--ink") + ")";
      $("gradeName").textContent = zh ? g.label_zh : g.label_en;
      $("confVal").textContent = Math.round(g.confidence * 100) + "%";
      $("confVal").style.color = g.confidence >= 0.75 ? "var(--ok)"
        : g.confidence >= 0.5 ? "var(--warn)" : "var(--danger)";
      var flag = $("srcFlag");
      var srcKey = { consensus: "srcConsensus", llm: "srcLlm", cv_only: "srcCvOnly", needs_review: "srcReview" }[g.source] || "srcCvOnly";
      flag.textContent = t(srcKey);
      flag.classList.toggle("review", g.source === "needs_review");
      $("rationale").textContent = zh ? (g.rationale_zh || "") : (g.rationale_en || "");
    }

    renderMetrics(r.metrics || {}, zh);
    renderCompare(r);
    renderRegions(r.regions || [], zh);
    renderCauses(r.cause_hypotheses || [], zh);
    renderMorph(r.morphology_hint, zh);
    renderNotes(zh ? r.notes_zh : r.notes_en);

    var a = r.audit || {};
    $("audit").textContent = [
      "task " + (r.task_id || "-"),
      "algo " + (a.algo_version || "-"),
      "grading " + (a.grading_version || "-"),
      "model " + (a.model || "-"),
      a.registered ? "aligned:" + a.registration_method : "unaligned",
      (a.elapsed_ms || 0) + "ms"
    ].join("  ·  ");
  }

  function metricCell(k, v, sub, cls) {
    var d = document.createElement("div");
    d.className = "m" + (cls ? " " + cls : "");
    var kk = document.createElement("div"); kk.className = "k"; kk.textContent = k;
    var vv = document.createElement("div"); vv.className = "v"; vv.textContent = v;
    d.appendChild(kk); d.appendChild(vv);
    if (sub) {
      var s = document.createElement("div"); s.className = "est"; s.textContent = sub;
      d.appendChild(s);
    }
    return d;
  }

  function renderMetrics(m, zh) {
    var box = $("metrics");
    box.innerHTML = "";

    // 隐性扩散倍数：早期是最能说服人的数字，覆盖率已高时它必然趋近 1，
    // 后端据此给出 display 级别，前端只负责呈现。
    if (m.hidden_spread_display !== "hidden" && m.hidden_spread_ratio) {
      var hero = metricCell(t("mHidden"), m.hidden_spread_ratio + "×",
        null, m.hidden_spread_display === "primary" ? "wide hero" : "wide");
      var note = document.createElement("div");
      note.className = "est";
      note.style.color = "var(--muted)";
      note.textContent = zh
        ? "UV 下可见范围是肉眼的 " + m.hidden_spread_ratio + " 倍"
        : m.hidden_spread_ratio + "× " + t("mHiddenSub");
      hero.appendChild(note);
      box.appendChild(hero);
    }

    box.appendChild(metricCell(t("mWhite"), fmtPct(m.coverage_white_pct)));
    if (m.coverage_uv_pct !== null && m.coverage_uv_pct !== undefined) {
      box.appendChild(metricCell(t("mUv"), fmtPct(m.coverage_uv_pct),
        m.uv_quantification === "estimated" ? t("mEstimated") : null));
    }
    box.appendChild(metricCell(t("mLargest"), fmtPct(m.largest_patch_pct)));

    // L3 起菌落已融合，显示"122 个菌落"会让人误以为可以逐个清除
    if (m.count_is_meaningful) {
      box.appendChild(metricCell(t("mCount"), String(m.patch_count)));
    } else {
      box.appendChild(metricCell(t("mCount"), "—", t("countHidden")));
    }
    if (m.absolute_area_cm2) {
      box.appendChild(metricCell(t("mArea"), m.absolute_area_cm2 + " cm²", null, "wide"));
    }
  }

  function fmtPct(v) {
    return (v === null || v === undefined) ? "—" : (Math.round(v * 10) / 10) + "%";
  }

  function renderCompare(r) {
    var a = r.assets || {};
    var main = $("cmpUv");
    // 容器宽高比跟随图像，object-fit:cover 才不会裁切，
    // 区域框用的百分比坐标也才能和画面严格对上
    main.onload = function () {
      if (main.naturalWidth && main.naturalHeight) {
        $("compare").style.aspectRatio = main.naturalWidth + " / " + main.naturalHeight;
        drawBoxes((S.result && S.result.regions) || []);
      }
    };
    main.src = a.uv || a.white || "";
    $("cmpWhite").src = a.white || "";
    $("cmpMask").src = a.mask || "";
    $("compare").classList.toggle("showmask", $("tgMask").getAttribute("aria-pressed") === "true");
    drawBoxes(r.regions || []);
    setSplit(0.5);
  }

  function drawBoxes(regions) {
    var svg = $("cmpSvg"), box = $("compare");
    var on = $("tgBoxes").getAttribute("aria-pressed") === "true";
    svg.innerHTML = "";
    Array.prototype.forEach.call(box.querySelectorAll(".rlabel"), function (n) { n.remove(); });
    if (!on) return;
    regions.slice(0, 6).forEach(function (rg) {
      var b = rg.bbox;
      var rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", (b[0] * 100).toFixed(2));
      rect.setAttribute("y", (b[1] * 100).toFixed(2));
      rect.setAttribute("width", (b[2] * 100).toFixed(2));
      rect.setAttribute("height", (b[3] * 100).toFixed(2));
      rect.setAttribute("class", "rl");
      svg.appendChild(rect);
      var lab = document.createElement("span");
      lab.className = "rlabel";
      lab.textContent = "#" + rg.id;
      lab.style.left = (b[0] * 100).toFixed(2) + "%";
      lab.style.top = Math.max(2.5, b[1] * 100 - 0.5).toFixed(2) + "%";
      box.appendChild(lab);
    });
  }

  function setSplit(f) {
    f = Math.max(0, Math.min(1, f));
    $("cmpClip").style.clipPath = "inset(0 " + ((1 - f) * 100).toFixed(2) + "% 0 0)";
    $("cmpGrip").style.left = (f * 100).toFixed(2) + "%";
  }

  (function bindSlider() {
    var el = $("compare"), dragging = false;
    function pos(e) {
      var r = el.getBoundingClientRect();
      var x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
      setSplit(x / r.width);
    }
    el.addEventListener("pointerdown", function (e) { dragging = true; el.setPointerCapture(e.pointerId); pos(e); });
    el.addEventListener("pointermove", function (e) { if (dragging) pos(e); });
    el.addEventListener("pointerup", function () { dragging = false; });
    el.addEventListener("pointercancel", function () { dragging = false; });
  })();

  $("tgMask").addEventListener("click", function () {
    var on = this.getAttribute("aria-pressed") !== "true";
    this.setAttribute("aria-pressed", String(on));
    $("compare").classList.toggle("showmask", on);
  });
  $("tgBoxes").addEventListener("click", function () {
    var on = this.getAttribute("aria-pressed") !== "true";
    this.setAttribute("aria-pressed", String(on));
    drawBoxes((S.result && S.result.regions) || []);
  });

  function renderRegions(regions, zh) {
    var box = $("regions");
    box.innerHTML = "";
    $("regionCount").textContent = regions.length ? "(" + regions.length + ")" : "";
    $("sectRegions").classList.toggle("hidden", regions.length === 0);

    var vMap = { mould: "vMould", stain_not_mould: "vStain", uncertain: "vUncertain" };
    var aMap = { active: "aActive", dormant: "aDormant", unknown: "aUnknown" };
    var uMap = { strong_halo: "uvStrong", weak: "uvWeak", none: "uvNone", unknown: "uvUnknown" };

    regions.forEach(function (rg) {
      var row = document.createElement("div");
      row.className = "region";
      var color = rg.verdict === "stain_not_mould" ? "var(--muted)"
        : rg.activity === "active" ? "var(--danger)"
          : rg.activity === "dormant" ? "var(--ok)" : "var(--warn)";
      var sub = [t(aMap[rg.activity] || "aUnknown"), t(uMap[rg.uv_response] || "uvUnknown"),
      (zh ? rg.note_zh : rg.note_en)].filter(Boolean).join(" · ");
      row.innerHTML = '<span class="sw"></span><span class="body">'
        + '<span class="t"></span><span class="s"></span></span><span class="a"></span>';
      row.querySelector(".sw").style.background = color;
      row.querySelector(".t").textContent = "#" + rg.id + " · " + t(vMap[rg.verdict] || "vUncertain");
      row.querySelector(".s").textContent = sub;
      row.querySelector(".a").textContent = (rg.area_cm2 ? rg.area_cm2 + "cm²" : fmtPct(rg.area_pct));
      box.appendChild(row);
    });
  }

  function renderCauses(causes, zh) {
    var box = $("causes");
    box.innerHTML = "";
    causes.forEach(function (c) {
      var name = zh ? c.cause_zh : c.cause_en;
      if (!name) return;
      var d = document.createElement("div");
      d.className = "cause";
      d.innerHTML = '<div class="h"><b></b><span class="lk"></span></div>'
        + '<div class="bar"><i></i></div><div class="ev"></div>';
      d.querySelector("b").textContent = name;
      d.querySelector(".lk").textContent = Math.round((c.likelihood || 0) * 100) + "%";
      d.querySelector(".bar i").style.width = Math.round((c.likelihood || 0) * 100) + "%";
      d.querySelector(".ev").textContent = zh ? (c.evidence_zh || "") : (c.evidence_en || "");
      box.appendChild(d);
    });
    $("sectCauses").classList.toggle("hidden", box.children.length === 0);
  }

  function renderMorph(h, zh) {
    var text = h ? (zh ? h.description_zh : h.description_en) : "";
    $("morphText").textContent = text || "";
    $("sectMorph").classList.toggle("hidden", !text);
  }

  function renderNotes(notes) {
    var box = $("notes");
    box.innerHTML = "";
    (notes || []).forEach(function (n) {
      var d = document.createElement("div");
      d.className = "note";
      d.innerHTML = '<span class="i">—</span><span></span>';
      d.lastChild.textContent = n;
      box.appendChild(d);
    });
    $("sectNotes").classList.toggle("hidden", box.children.length === 0);
  }

  // ---------------------------------------------------------------- 底部操作

  $("btnPrint").addEventListener("click", function () { window.print(); });

  $("btnDelete").addEventListener("click", function () {
    if (!S.taskId) return;
    fetch("/api/v1/tasks/" + S.taskId, { method: "DELETE" }).then(function () {
      S.result = null; S.taskId = null;
      restart();
    });
  });

  $("btnRestart").addEventListener("click", restart);

  function restart() {
    ["white", "uv"].forEach(function (k) {
      if (S[k] && S[k].url) URL.revokeObjectURL(S[k].url);
      S[k] = null;
    });
    S.step = 1; S.result = null; S.taskId = null;
    show("screen-capture");
    startCamera().then(renderCapture);
  }

  // ---------------------------------------------------------------- 启动

  try {
    var saved = localStorage.getItem("mould.lang");
    if (saved === "zh" || saved === "en") S.lang = saved;
  } catch (e) {}

  fetch("/api/v1/config").then(function (r) { return r.json(); }).then(function (c) {
    S.cfg = c;
    if (!localStorageHasLang() && c.default_locale) S.lang = c.default_locale;
    applyLang();
  }).catch(function () { applyLang(); });

  function localStorageHasLang() {
    try { return !!localStorage.getItem("mould.lang"); } catch (e) { return false; }
  }

  document.body.dataset.mode = "field";

  // 深链接：/?task=xxx 直接看历史结果（分享用）
  var qs = new URLSearchParams(location.search);
  if (qs.get("task")) {
    S.taskId = qs.get("task");
    fetchResult();
  } else {
    startCamera().then(renderCapture);
  }
  applyLang();
})();
