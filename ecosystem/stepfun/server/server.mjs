// server.mjs — 本地桥接服务：托管前端静态页 + 暴露 seekdb 存储/检索 API
// 架构：浏览器端完成 embedding（WebGPU/WASM）→ 本服务把向量与原文写入 seekdb →
//       检索请求在本服务内以单条 SQL 交给 seekdb 完成混合检索 → 结果回传浏览器渲染。
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { KnowledgeBase } from "./db.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB_DIR = path.resolve(__dirname, "../web");
const DATA_DIR = path.resolve(__dirname, "../data");
const PORT = Number(process.env.PORT || 8788);
const DB_DIR = process.env.SEEKDB_DIR || path.resolve(__dirname, "../data/seekdb-data");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".wasm": "application/wasm",
  ".onnx": "application/octet-stream",
};

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", ...CORS });
  res.end(body);
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  if (!chunks.length) return {};
  const raw = Buffer.concat(chunks).toString("utf8");
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error("请求体不是合法 JSON");
  }
}

function streamFile(res, file) {
  res.writeHead(200, {
    "Content-Type": MIME[path.extname(file)] || "application/octet-stream",
    "Cache-Control": "no-cache",
    ...CORS,
  });
  fs.createReadStream(file).pipe(res);
}

function serveStatic(req, res, urlPath) {
  let rel = decodeURIComponent(urlPath.split("?")[0]);
  if (rel === "/" || rel === "") rel = "/index.html";

  // /data/* → 数据目录（示例语料、评测集），其余走前端目录
  const isData = rel.startsWith("/data/");
  const baseDir = isData ? DATA_DIR : WEB_DIR;
  const file = path.join(baseDir, isData ? rel.slice("/data/".length) : rel.replace(/^\//, ""));

  if (!file.startsWith(baseDir)) return json(res, 403, { error: "forbidden" });
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) return json(res, 404, { error: "not found", path: rel });
    streamFile(res, file);
  });
}

const kb = new KnowledgeBase(DB_DIR);

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS);
    return res.end();
  }

  try {
    // ---------- API ----------
    if (url.pathname === "/api/health") {
      return json(res, 200, { ok: true, seekdb: kb.endpoint, ftsParser: kb.ftsParser, dim: kb.dim });
    }

    if (url.pathname === "/api/kb/stats" && req.method === "GET") {
      const kbId = url.searchParams.get("kbId") || "default";
      return json(res, 200, await kb.stats(kbId));
    }

    if (url.pathname === "/api/kb/docs" && req.method === "POST") {
      const body = await readBody(req);
      const kbId = body.kbId || "default";
      const docs = Array.isArray(body.docs) ? body.docs : [];
      const bad = docs.find((d) => !d.content || !Array.isArray(d.embedding) || d.embedding.length !== kb.dim);
      if (bad) {
        return json(res, 400, {
          error: `chunk 校验失败：embedding 维度需为 ${kb.dim}`,
          chunkId: bad?.id,
        });
      }
      const t0 = performance.now();
      const n = await kb.upsertChunks(kbId, docs);
      const writeMs = performance.now() - t0;
      const refreshMs = await kb.refresh();
      return json(res, 200, { inserted: n, writeMs: +writeMs.toFixed(1), refreshMs: +refreshMs.toFixed(1), stats: await kb.stats(kbId) });
    }

    if (url.pathname === "/api/kb/search" && req.method === "POST") {
      const body = await readBody(req);
      const kbId = body.kbId || "default";
      if (!Array.isArray(body.queryEmbedding) || body.queryEmbedding.length !== kb.dim) {
        return json(res, 400, { error: `queryEmbedding 维度需为 ${kb.dim}` });
      }
      const t0 = performance.now();
      const r = await kb.search({
        kbId,
        queryText: body.queryText || "",
        queryEmbedding: body.queryEmbedding,
        k: body.k || 5,
        mode: body.mode || "hybrid",
      });
      const searchMs = performance.now() - t0;
      return json(res, 200, { mode: r.mode, searchMs: +searchMs.toFixed(1), sql: r.sql, rows: r.rows });
    }

    if (url.pathname === "/api/kb/explain" && req.method === "GET") {
      const q = url.searchParams.get("query") || "混合检索是怎么工作的";
      const kbId = url.searchParams.get("kbId") || "default";
      return json(res, 200, await kb.explainHybrid(kbId, q));
    }

    if (url.pathname === "/api/kb/refresh" && req.method === "POST") {
      const ms = await kb.refresh();
      return json(res, 200, { refreshMs: +ms.toFixed(1) });
    }

    if (url.pathname === "/api/kb/clear" && req.method === "POST") {
      const body = await readBody(req);
      const n = await kb.clear(body.kbId || "default");
      return json(res, 200, { deleted: n });
    }

    // ---------- 静态资源 ----------
    if (req.method === "GET") return serveStatic(req, res, url.pathname);
    return json(res, 405, { error: "method not allowed" });
  } catch (e) {
    console.error("[error]", e);
    return json(res, 500, { error: String(e?.message || e) });
  }
});

await kb.open();
server.listen(PORT, "127.0.0.1", () => {
  console.log(`[server] http://127.0.0.1:${PORT}  (seekdb: ${JSON.stringify(kb.endpoint)}, fts=${kb.ftsParser || "default"}, dim=${kb.dim})`);
});

const shutdown = async () => {
  console.log("\n[server] 正在关闭...");
  server.close();
  await kb.close();
  process.exit(0);
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
