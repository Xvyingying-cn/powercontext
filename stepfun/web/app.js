// app.js — 端侧知识库主流程：导入 → 本地向量化 → 写入 seekdb → 混合检索 → 性能与质量实测
import { MODELS, loadEmbedder, embedTexts, chunkText, detectBackend } from "./embeddings.js";

const API = ""; // 与页面同源（本地桥接服务）
const KB_ID = "demo-kb";

const $ = (sel) => document.querySelector(sel);
const state = {
  corpus: [], // { id, title, text }
  chunks: [], // { id, docId, chunkNo, title, content }
  emb: null,
  loadedConfig: null,
  perf: {},
  lastEval: null,
};

/* ---------------- 初始化 ---------------- */

function initModelSelect() {
  const sel = $("#model-select");
  sel.innerHTML = Object.entries(MODELS)
    .map(([id, m]) => `<option value="${id}">${m.label}</option>`)
    .join("");
}

async function checkHealth() {
  const backend = detectBackend();
  setBadge("#badge-backend", `后端：${backend === "webgpu" ? "WebGPU（可用）" : "WASM（无 WebGPU）"}`,
    backend === "webgpu" ? "" : "warn");
  try {
    const r = await fetch(`${API}/api/health`);
    const d = await r.json();
    if (d.ok) {
      setBadge("#badge-db", `seekdb：已连接 ${d.seekdb?.port ? ":" + d.seekdb.port : ""}`, "");
      $("#badge-db").title = `嵌入式 seekdb，向量维 ${d.dim}，全文解析器 ${d.ftsParser || "default"}`;
    }
  } catch {
    setBadge("#badge-db", "seekdb：未连接（请先启动本地服务）", "err");
  }
  $("#badge-model").textContent = "模型：未加载";
}

function setBadge(sel, text, cls) {
  const el = $(sel);
  el.textContent = text;
  el.className = "badge" + (cls ? " " + cls : "");
}

/* ---------------- 语料 ---------------- */

async function loadSampleCorpus() {
  try {
    const r = await fetch(`${API}/data/corpus.json`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.corpus = await r.json();
    updateCorpusInfo();
  } catch (e) {
    alert(`载入示例语料失败：${e.message}\n请确认本地服务已启动（bash scripts/start.sh）。`);
  }
}

function addPastedText() {
  const text = $("#paste-box").value.trim();
  if (!text) return;
  const id = `paste-${Date.now()}`;
  state.corpus.push({ id, title: `粘贴文本 ${state.corpus.length + 1}`, text });
  $("#paste-box").value = "";
  updateCorpusInfo();
}

async function addFiles(files) {
  for (const f of files) {
    const text = await f.text();
    state.corpus.push({ id: f.name, title: f.name, text });
  }
  updateCorpusInfo();
}

function updateCorpusInfo() {
  const chunkCount = state.corpus.reduce((s, d) => s + chunkText(d.text).length, 0);
  $("#corpus-info").textContent = `语料：${state.corpus.length} 篇 / 约 ${chunkCount} 块`;
}

/* ---------------- 向量化与写入 ---------------- */

async function ensureEmbedder() {
  const model = $("#model-select").value;
  const dtype = $("#dtype-select").value;
  const device = ($("#device-select") || {}).value || "auto";
  const cfg = `${model}|${dtype}|${device}`;
  if (state.emb && state.loadedConfig === cfg) return state.emb;

  setBadge("#badge-model", `模型：加载中（${dtype}）…`, "warn");
  $("#index-hint").textContent = "正在加载模型（首次会下载，之后走浏览器缓存）…";
  const emb = await loadEmbedder({
    model,
    dtype,
    device,
    onProgress: (p) => {
      if (p.phase === "download" && p.total) {
        const pct = Math.round((p.loaded / p.total) * 100);
        $("#index-bar").style.width = `${Math.min(90, pct * 0.8)}%`;
        $("#index-hint").textContent = `下载模型：${p.file || ""} ${pct}%`;
      }
    },
  });
  state.emb = emb;
  state.loadedConfig = cfg;
  state.perf.modelLoad = emb.loadMs;
  state.perf.warmup = emb.warmMs;
  state.perf.backend = emb.backend;
  state.perf.dtype = dtype;
  state.perf.model = model;
  state.perf.modelBytes = emb.modelBytes || emb.downloadedBytes;
  setPerfRow("modelLoad", fmtMs(emb.loadMs), `首次含下载 · ${(MB(emb.modelBytes || emb.downloadedBytes))} MB`);
  setPerfRow("warmup", fmtMs(emb.warmMs), "首帧编译");
  setBadge(
    "#badge-model",
    `模型：${model.split("/").pop()} · ${dtype} · ${emb.backend.toUpperCase()}${emb.degraded ? "（已降级）" : ""}`,
    emb.degraded ? "warn" : ""
  );
  return emb;
}

async function buildIndex() {
  if (!state.corpus.length) {
    alert("请先导入语料（选择文件或载入示例语料）");
    return;
  }
  const btn = $("#btn-index");
  btn.disabled = true;
  try {
    const emb = await ensureEmbedder();

    // 1) 分块
    const chunks = [];
    for (const doc of state.corpus) {
      chunkText(doc.text).forEach((content, i) => {
        chunks.push({
          id: `${doc.id}#${i}`,
          docId: doc.id,
          chunkNo: i,
          title: doc.title,
          content,
        });
      });
    }
    state.chunks = chunks;

    // 2) 本地向量化（批处理）
    const batchSize = Number($("#batch-size").value) || 8;
    $("#index-hint").textContent = `本地向量化：0 / ${chunks.length}`;
    const { vectors, ms } = await embedTexts(emb, chunks.map((c) => c.content), {
      batchSize,
      onBatch: ({ done, total }) => {
        $("#index-bar").style.width = `${10 + (done / total) * 70}%`;
        $("#index-hint").textContent = `本地向量化：${done} / ${total}`;
      },
    });
    state.perf.embedDocs = ms;
    state.perf.chunkCount = chunks.length;
    setPerfRow("embedDocs", fmtMs(ms), `${chunks.length} 块 · 批 ${batchSize} · ${(chunks.length / (ms / 1000)).toFixed(1)} 块/秒`);

    // 3) 写入 seekdb（事务批量）+ 索引刷新确认
    const payload = { kbId: KB_ID, docs: chunks.map((c, i) => ({ ...c, embedding: vectors[i] })) };
    const t0 = performance.now();
    const r = await fetch(`${API}/api/kb/docs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "写入失败");
    const totalMs = performance.now() - t0;
    state.perf.write = d.writeMs;
    state.perf.refresh = d.refreshMs;
    setPerfRow("write", fmtMs(d.writeMs), `插入 ${d.inserted} 块（库内共 ${d.stats.total}）`);
    setPerfRow("refresh", fmtMs(d.refreshMs), "增量可见性确认");

    $("#index-bar").style.width = "100%";
    $("#index-hint").textContent = `完成：${chunks.length} 块已写入 seekdb（端到端 ${fmtMs(totalMs)}）`;
  } catch (e) {
    $("#index-hint").textContent = `失败：${e.message}`;
    console.error(e);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- 检索 ---------------- */

async function doSearch(textOverride) {
  const query = textOverride ?? $("#query-input").value.trim();
  if (!query) return null;
  const emb = await ensureEmbedder();
  const mode = $("#mode-select").value;
  const k = Number($("#topk").value) || 5;

  const t0 = performance.now();
  const { vectors, ms: embedMs } = await embedTexts(emb, [query], { batchSize: 1 });
  const tAfterEmbed = performance.now();

  const r = await fetch(`${API}/api/kb/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kbId: KB_ID, queryText: query, queryEmbedding: vectors[0], k, mode }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || "检索失败");
  const e2e = performance.now() - t0;

  state.perf.embedQuery = embedMs;
  state.perf.search = d.searchMs;
  state.perf.e2e = e2e;
  setPerfRow("embedQuery", fmtMs(embedMs), "单条查询向量");
  setPerfRow("search", fmtMs(d.searchMs), `mode=${mode} · top ${k}`);
  setPerfRow("e2e", fmtMs(e2e), "提交 → 结果展示");

  renderResults(d.rows, query);
  $("#sql-pre").textContent = d.sql;
  return d;
}

function renderResults(rows, query) {
  const box = $("#results");
  if (!rows?.length) {
    box.innerHTML = `<div class="empty">没有命中：换个问法，或先导入并索引语料。</div>`;
    return;
  }
  box.innerHTML = rows
    .map((r, i) => {
      const score = r.dist != null ? `距离 ${Number(r.dist).toFixed(4)}` : "";
      const fts = r.score != null ? `全文 ${Number(r.score).toFixed(3)}` : "";
      return `<article class="card">
        <div class="head">
          <span class="title">#${i + 1} ${escapeHtml(r.title || "未命名")}</span>
          <span class="meta">${[score, fts].filter(Boolean).join(" · ")}</span>
        </div>
        <p class="body">${highlight(escapeHtml(r.content || ""), query)}</p>
      </article>`;
    })
    .join("");
}

/* ---------------- 质量评测 ---------------- */

function recallAtK(rows, expected, k = 5) {
  const got = rows.slice(0, k).map((r) => r.doc_id ?? r.docId);
  const hit = new Set(got.filter((id) => expected.includes(id)));
  return hit.size / expected.length;
}

function ndcgAtK(rows, expected, k = 5) {
  const got = rows.slice(0, k).map((r) => r.doc_id ?? r.docId);
  let dcg = 0;
  got.forEach((id, i) => {
    if (expected.includes(id)) dcg += 1 / Math.log2(i + 2);
  });
  const ideal = Array.from({ length: Math.min(k, expected.length) })
    .map((_, i) => 1 / Math.log2(i + 2))
    .reduce((a, b) => a + b, 0);
  return ideal ? dcg / ideal : 0;
}

async function runEval() {
  const hint = $("#eval-hint");
  try {
    const qs = await (await fetch(`${API}/data/queries.json`)).json();
    if (!state.chunks.length) {
      hint.textContent = "请先导入示例语料并完成索引，再运行评测。";
      return;
    }
    hint.textContent = `评测中：0 / ${qs.length}`;
    let r5 = 0, n5 = 0, ms = 0;
    for (let i = 0; i < qs.length; i++) {
      const q = qs[i];
      const emb = await ensureEmbedder();
      const { vectors } = await embedTexts(emb, [q.q], { batchSize: 1 });
      const r = await fetch(`${API}/api/kb/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kbId: KB_ID, queryText: q.q, queryEmbedding: vectors[0], k: 5, mode: "hybrid" }),
      });
      const d = await r.json();
      r5 += recallAtK(d.rows, q.expected, 5);
      n5 += ndcgAtK(d.rows, q.expected, 5);
      ms += d.searchMs;
      hint.textContent = `评测中：${i + 1} / ${qs.length}`;
    }
    const res = {
      queries: qs.length,
      recall5: +(r5 / qs.length).toFixed(4),
      ndcg5: +(n5 / qs.length).toFixed(4),
      avgSearchMs: +(ms / qs.length).toFixed(1),
      dtype: state.perf.dtype,
      backend: state.perf.backend,
    };
    state.lastEval = res;
    hint.textContent = `Recall@5 = ${res.recall5} · nDCG@5 = ${res.ndcg5} · 平均检索 ${res.avgSearchMs} ms（${res.dtype}/${res.backend}）`;
  } catch (e) {
    hint.textContent = `评测失败：${e.message}`;
  }
}

/* ---------------- 导出 ---------------- */

function exportReport() {
  const report = {
    generatedAt: new Date().toISOString(),
    environment: {
      userAgent: navigator.userAgent,
      webgpuAvailable: !!navigator.gpu,
      hardwareConcurrency: navigator.hardwareConcurrency,
    },
    config: {
      model: state.perf.model,
      dtype: state.perf.dtype,
      backend: state.perf.backend,
      batchSize: Number($("#batch-size").value),
      chunks: state.chunks.length,
      docs: state.corpus.length,
      modelSizeMB: state.perf.modelBytes ? +(state.perf.modelBytes / 1048576).toFixed(1) : null,
    },
    timingsMs: {
      modelLoad: round(state.perf.modelLoad),
      warmup: round(state.perf.warmup),
      embedDocs: round(state.perf.embedDocs),
      writeSeekdb: round(state.perf.write),
      indexRefresh: round(state.perf.refresh),
      embedQuery: round(state.perf.embedQuery),
      seekdbSearch: round(state.perf.search),
      endToEnd: round(state.perf.e2e),
    },
    quality: state.lastEval,
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `benchmark-${state.perf.dtype || "unknown"}-${state.perf.backend || "unknown"}.json`;
  a.click();
}

/* ---------------- 工具 ---------------- */

const fmtMs = (ms) => (ms == null ? "-" : ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms.toFixed(1)} ms`);
const MB = (b) => (b ? (b / 1048576).toFixed(1) : "-");
const round = (v) => (v == null ? null : +Number(v).toFixed(1));
const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function highlight(html, q) {
  if (!q) return html;
  const terms = q.split(/[\s，。？！、,?.!]+/).filter((t) => t.length > 1).slice(0, 6);
  let out = html;
  for (const t of terms) out = out.replaceAll(t, `<mark>${t}</mark>`);
  return out;
}
function setPerfRow(key, value, note) {
  const tr = document.querySelector(`tr[data-k="${key}"]`);
  if (!tr) return;
  tr.children[1].textContent = value;
  if (note) tr.children[2].textContent = note;
}

/* ---------------- 事件绑定 ---------------- */

initModelSelect();
checkHealth();

$("#btn-pick").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (e) => addFiles([...e.target.files]));
$("#btn-sample").addEventListener("click", loadSampleCorpus);
$("#btn-use-paste").addEventListener("click", addPastedText);
$("#btn-index").addEventListener("click", buildIndex);
$("#btn-search").addEventListener("click", () => doSearch());
$("#query-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
$("#btn-eval").addEventListener("click", runEval);
$("#btn-export").addEventListener("click", exportReport);
$("#btn-clear").addEventListener("click", async () => {
  const r = await fetch(`${API}/api/kb/clear`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kbId: KB_ID }),
  });
  const d = await r.json();
  $("#index-hint").textContent = `已清空知识库：删除 ${d.deleted ?? 0} 块`;
  $("#results").innerHTML = "";
});
$("#dtype-select").addEventListener("change", () => {
  state.emb = null;
  state.loadedConfig = null;
  $("#badge-model").textContent = "模型：待加载（精度已切换）";
});
$("#device-select").addEventListener("change", () => {
  state.emb = null;
  state.loadedConfig = null;
  $("#badge-model").textContent = "模型：待加载（后端已切换）";
});
