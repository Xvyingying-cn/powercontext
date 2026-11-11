/* StudyCoach 复习教练 — 前端逻辑
 * 数据链路：
 *   打卡   -> POST /v1/memory/remember  {scope_id, kind, text}
 *   复习   -> POST /v1/context/prepare  {scope_id, query}
 *   记忆库 -> POST /v1/memory/entries/list {scope_id}
 * 演示模式下使用内置 mock 数据走通相同交互。
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  /* ---------------- 配置 ---------------- */
  var cfg = {
    base: localStorage.getItem("sc_base") || "",
    token: localStorage.getItem("sc_token") || "",
    scope: localStorage.getItem("sc_scope") || "study-coach-demo",
  };
  var DEMO_SCOPE = "study-coach-demo";
  var demoStore = null; // 演示模式内存存储

  function isLive() { return cfg.base && cfg.base.trim() !== ""; }

  function renderConn() {
    $("connDot").className = "dot " + (isLive() ? "on" : "off");
    $("connText").textContent = isLive()
      ? "已连接 " + cfg.base
      : "未连接 · 演示模式";
    $("cfgBase").value = cfg.base;
    $("cfgToken").value = cfg.token;
    $("cfgScope").value = cfg.scope === DEMO_SCOPE ? "" : cfg.scope;
  }

  function saveCfg() {
    cfg.base = $("cfgBase").value.trim();
    cfg.token = $("cfgToken").value.trim();
    var sc = $("cfgScope").value.trim();
    cfg.scope = sc || DEMO_SCOPE;
    localStorage.setItem("sc_base", cfg.base);
    localStorage.setItem("sc_token", cfg.token);
    localStorage.setItem("sc_scope", cfg.scope);
    renderConn();
    var msg = $("cfgMsg");
    if (isLive()) {
      msg.textContent = "测试连接中…";
      api("POST", "/v1/memory/entries/list", { scope_id: cfg.scope })
        .then(function (r) {
          msg.textContent = "✅ 连接成功（Scope：" + cfg.scope + "）";
          loadMemories();
        })
        .catch(function (e) {
          msg.textContent = "❌ 连接失败：" + e.message;
        });
    } else {
      msg.textContent = "已切回演示模式";
      loadMemories();
    }
  }

  /* ---------------- API 层 ---------------- */
  function api(method, path, body) {
    var headers = { "Content-Type": "application/json" };
    if (cfg.token) headers["Authorization"] = "Bearer " + cfg.token;
    return fetch(cfg.base.replace(/\/$/, "") + path, {
      method: method,
      headers: headers,
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) {
        throw new Error("HTTP " + r.status + " " + t.slice(0, 160));
      });
      return r.json();
    });
  }

  function ensureDemoStore() {
    if (!demoStore) {
      demoStore = { entries: [
        { kind: "weakness", text: "高数·中值定理：拉格朗日中值定理构造辅助函数的思路还不熟", created_at: "2026-11-08T19:30:00Z" },
        { kind: "weakness", text: "高数·中值定理：柯西中值定理与拉格朗日定理的条件区别记不清", created_at: "2026-11-08T19:31:00Z" },
        { kind: "progress", text: "高数·中值定理：已掌握罗尔定理证明与课本例题 3-5", created_at: "2026-11-08T19:32:00Z" },
        { kind: "preference", text: "复习偏好：先看公式推导，再做 3 道变式题", created_at: "2026-11-08T19:33:00Z" },
      ] };
    }
    return demoStore;
  }

  /* ---------------- 打卡：写入记忆 ---------------- */
  function record() {
    var subject = $("inSubject").value.trim();
    var learned = $("inLearned").value.trim();
    var weakLines = $("inWeak").value.split("\n").map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
    if (!subject && !learned && weakLines.length === 0) {
      log("recordLog", "⚠️ 请至少填写一项内容", "warn"); return;
    }
    var items = [];
    weakLines.forEach(function (w) {
      items.push({ kind: "weakness", text: (subject ? subject + "：" : "") + w });
    });
    if (learned) items.push({ kind: "progress", text: (subject ? subject + "：" : "") + learned });
    if (subject && items.length === 0) items.push({ kind: "progress", text: subject });

    log("recordLog", "写入记忆中…（" + items.length + " 条）");
    var chain = Promise.resolve();
    var ok = 0, fail = 0;
    items.forEach(function (it) {
      chain = chain.then(function () {
        if (isLive()) {
          return api("POST", "/v1/memory/remember", { scope_id: cfg.scope, kind: it.kind, text: it.text })
            .then(function () { ok++; log("recordLog", "✅ [" + it.kind + "] " + it.text); });
        }
        ensureDemoStore().entries.unshift({ kind: it.kind, text: it.text, created_at: new Date().toISOString() });
        ok++;
        log("recordLog", "✅（演示）[" + it.kind + "] " + it.text);
        return Promise.resolve();
      }).catch(function (e) {
        fail++;
        log("recordLog", "❌ " + it.text + " — " + e.message, "err");
      });
    });
    chain.then(function () {
      log("recordLog", "完成：成功 " + ok + " 条" + (fail ? "，失败 " + fail + " 条" : "") + (isLive() ? "" : "（演示模式，数据仅存于本页）"), fail ? "err" : "ok");
      $("inLearned").value = ""; $("inWeak").value = "";
      loadMemories();
    });
  }

  /* ---------------- 复习：召回记忆 ---------------- */
  function review() {
    var q = $("inQuery").value.trim();
    var box = $("reviewResult");
    if (!q) { box.className = "review-result warn"; box.textContent = "请输入复习主题"; return; }
    box.className = "review-result";
    box.textContent = "正在从 PowerContext 召回你的历史薄弱点…";
    var p = isLive()
      ? api("POST", "/v1/context/prepare", { scope_id: cfg.scope, query: q, max_bytes: 8000 })
      : Promise.resolve(mockPrepare(q));
    p.then(function (r) {
      renderReview(r, q);
    }).catch(function (e) {
      box.className = "review-result err";
      box.textContent = "❌ 召回失败：" + e.message;
    });
  }

  function mockPrepare(q) {
    var st = ensureDemoStore();
    var kw = q.replace(/复习|考试|下周|,|，/g, "").trim();
    var hits = st.entries.filter(function (e) {
      return !kw || e.text.indexOf(kw.split(/\s+/)[0] || "") >= 0 || kw.indexOf("复习") >= 0;
    });
    if (hits.length === 0) hits = st.entries.filter(function (e) { return e.kind === "weakness"; });
    return { query: q, context: hits.map(function (e) {
      return "[" + e.kind + "] " + e.text;
    }).join("\n"), scope_id: DEMO_SCOPE, demo: true };
  }

  function renderReview(r, q) {
    var box = $("reviewResult");
    var ctx = (r && (r.context || r.content || "")) || "";
    var lines = ctx.split(/\n+/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
    var weak = [], other = [];
    lines.forEach(function (l) {
      (/^\[(weakness|薄弱)\]/i.test(l) ? weak : other).push(l.replace(/^\[[^\]]*\]\s*/, ""));
    });
    var html = "";
    html += '<p class="meta">查询「' + esc(q) + '」— 召回 ' + lines.length + ' 条记忆' +
      (r && r.demo ? "（演示数据）" : "") + '</p>';
    if (weak.length) {
      html += "<h4>🎯 本次优先复习（历史薄弱点）</h4><ol class='weak-ol'>" +
        weak.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ol>";
    }
    if (other.length) {
      html += "<h4>🗂 相关记忆</h4><ul>" +
        other.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul>";
    }
    html += "<h4>📝 建议的复习路径</h4><ol class='plan-ol'>" +
      "<li>先重做上面薄弱点对应的例题各 1 道，检验是否真的会了；</li>" +
      "<li>把仍然卡壳的点再次打卡，教练会记住并下次优先推送；</li>" +
      "<li>连续 2 次打卡不再出现的薄弱点，可以视为已克服。</li></ol>";
    if (!lines.length) {
      html = "<p class='meta'>「" + esc(q) + "」暂无相关记忆（" + (r && r.demo ? "演示" : "Scope：" + cfg.scope) + "）。先去打卡，教练才会越来越懂你～</p>";
    }
    box.className = "review-result";
    box.innerHTML = html;
  }

  /* ---------------- 记忆库列表 ---------------- */
  function loadMemories() {
    var box = $("memoryList");
    var p = isLive()
      ? api("POST", "/v1/memory/entries/list", { scope_id: cfg.scope })
      : Promise.resolve({ entries: ensureDemoStore().entries });
    p.then(function (r) {
      var entries = (r && r.entries) || [];
      if (!entries.length) { box.className = "memory-list empty"; box.textContent = "暂无记忆条目。"; return; }
      box.className = "memory-list";
      box.innerHTML = entries.slice(0, 30).map(function (e) {
        var kind = e.kind || "memory";
        var tag = { weakness: "薄弱", progress: "进展", preference: "偏好" }[kind] || kind;
        return "<div class='mem-item kind-" + esc(kind) + "'>" +
          "<span class='tag'>" + esc(tag) + "</span>" +
          "<span class='text'>" + esc(e.text || e.content || JSON.stringify(e).slice(0, 120)) + "</span>" +
          "</div>";
      }).join("");
    }).catch(function (e) {
      box.className = "memory-list err";
      box.textContent = "❌ 读取失败：" + e.message;
    });
  }

  /* ---------------- 工具 ---------------- */
  function log(id, msg, cls) {
    var box = $(id);
    var line = document.createElement("div");
    line.className = "logline " + (cls || "");
    line.textContent = msg;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------------- 事件绑定 ---------------- */
  $("btnRecord").addEventListener("click", record);
  $("btnReview").addEventListener("click", review);
  $("btnRefresh").addEventListener("click", loadMemories);
  $("btnSaveCfg").addEventListener("click", saveCfg);
  $("btnDemoMode").addEventListener("click", function () {
    $("cfgBase").value = ""; saveCfg();
  });
  $("btnSettings").addEventListener("click", function () {
    $("settingsPanel").classList.toggle("hidden");
  });

  renderConn();
  loadMemories();
})();
