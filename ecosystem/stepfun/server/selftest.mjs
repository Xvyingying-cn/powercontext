// selftest.mjs — 不依赖浏览器，验证 seekdb 建表 / 写入 / 三种检索模式是否可用
// 用法：node selftest.mjs
import { KnowledgeBase } from "./db.mjs";

const DIM = Number(process.env.EMBED_DIM || 384);
const randVec = (seed) => {
  // 造一个可复现的伪向量，仅用于验证链路（真实向量由浏览器端模型生成）
  const v = new Array(DIM).fill(0);
  let x = seed * 9301 + 49297;
  for (let i = 0; i < DIM; i++) {
    x = (x * 9301 + 49297) % 233280;
    v[i] = (x / 233280 - 0.5) * 2;
  }
  const n = Math.sqrt(v.reduce((s, a) => s + a * a, 0)) || 1;
  return v.map((a) => +(a / n).toFixed(6));
};

const docs = [
  { id: "c1", docId: "d1", chunkNo: 0, title: "端侧推理", content: "浏览器端使用 WebGPU 执行 embedding 推理，性能显著优于 WASM 后端。" },
  { id: "c2", docId: "d2", chunkNo: 0, title: "混合检索", content: "混合检索把向量相似度与全文相关度结合，由数据库一次查询完成排序。" },
  { id: "c3", docId: "d3", chunkNo: 0, title: "模型量化", content: "INT8 量化把模型体积压缩到原来的四分之一，检索质量基本持平。" },
];

const kb = await new KnowledgeBase("./seekdb-selftest");
try {
  await kb.open();
  console.log("[ok] seekdb 启动，endpoint =", kb.endpoint);

  await kb.clear("selftest");
  const n = await kb.upsertChunks("selftest", docs.map((d, i) => ({ ...d, embedding: randVec(i + 1) })));
  console.log("[ok] 写入 chunks =", n, "| stats =", await kb.stats("selftest"));

  const q = randVec(1);
  for (const mode of ["vector", "text", "hybrid"]) {
    const t0 = performance.now();
    try {
      const r = await kb.search({ kbId: "selftest", queryText: "混合检索 向量 全文", queryEmbedding: q, k: 3, mode });
      const ms = (performance.now() - t0).toFixed(1);
      console.log(`[ok] mode=${mode} -> ${r.rows.length} 行 / ${ms}ms`);
      r.rows.forEach((row) => console.log("     ", row.id, row.title, "dist=", row.dist, "score=", row.score));
    } catch (e) {
      console.log(`[FAIL] mode=${mode} -> ${e.message}`);
    }
  }
  console.log("[ok] refresh 耗时(ms) =", (await kb.refresh()).toFixed(1));
} finally {
  await kb.close();
  console.log("[done] 已关闭");
}
