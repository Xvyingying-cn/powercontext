// embeddings.js — 端侧 embedding：模型加载、后端选择（WebGPU 优先 / WASM 降级）、量化、批处理
// 运行时：Transformers.js（onnxruntime-web 驱动），模型与推理运行时随项目分发到本地，
//         全程不依赖任何外部 CDN / 模型仓库，断网也能跑。
import { pipeline, env } from "./vendor/transformers.min.js";

// 优先本地加载（web/models 随项目分发），本地缺失时回退 hf-mirror 镜像下载
env.allowLocalModels = true;
env.localModelPath = "/models/";
env.allowRemoteModels = true;
env.remoteHost = "https://hf-mirror.com";
env.useBrowserCache = true;
// onnx 运行时 wasm 也本地化，不依赖 CDN（不同版本 env 结构不同，容错设置）
try {
  env.backends.onnx.wasm.wasmPaths = "/vendor/ort/";
} catch (e) {
  console.warn("[embeddings] wasmPaths 设置失败，使用默认：", e?.message);
}

export const MODELS = {
  // 多语言模型：中英文语料都能编码，384 维（与建表 VECTOR(384) 对齐）
  "Xenova/paraphrase-multilingual-MiniLM-L12-v2": {
    label: "paraphrase-multilingual-MiniLM-L12-v2（多语言，384 维）",
    dim: 384,
  },
  "Xenova/all-MiniLM-L6-v2": {
    label: "all-MiniLM-L6-v2（英文，384 维）",
    dim: 384,
  },
};

export function detectBackend() {
  const hasGpu = typeof navigator !== "undefined" && !!navigator.gpu;
  return hasGpu ? "webgpu" : "wasm";
}

/**
 * 加载 embedding 模型
 * @param {object} opts
 * @param {string} opts.model     模型 id
 * @param {"fp32"|"fp16"|"q8"} opts.dtype  精度/量化方案
 * @param {"webgpu"|"wasm"|"auto"} opts.device 推理后端（auto = WebGPU 优先，失败降级 WASM）
 * @param {(p:{phase:string,loaded:number,total:number,file?:string})=>void} opts.onProgress
 */
export async function loadEmbedder({ model, dtype, device = "auto", onProgress } = {}) {
  const wanted = MODELS[model] ? model : "Xenova/paraphrase-multilingual-MiniLM-L12-v2";
  const preferred = device === "auto" ? detectBackend() : device;

  const tryLoad = async (dev) => {
    const t0 = performance.now();
    let downloaded = 0;
    let total = 0;
    const extractor = await pipeline("feature-extraction", wanted, {
      dtype,
      device: dev,
      progress_callback: (p) => {
        if (p?.status === "progress" && p.total) {
          total = Math.max(total, p.loaded ?? 0);
          onProgress?.({ phase: "download", loaded: p.loaded ?? 0, total: p.total, file: p.file });
        } else if (p?.status === "done" && p.file) {
          downloaded += p.loaded ?? 0;
          onProgress?.({ phase: "file-done", loaded: downloaded, total, file: p.file });
        }
      },
    });
    const loadMs = performance.now() - t0;
    // 预热一次，排除首帧编译开销（冷/热分离统计）
    const warm = performance.now();
    await extractor(["warm up"], { pooling: "mean", normalize: true });
    const warmMs = performance.now() - warm;
    return { extractor, loadMs, warmMs, backend: dev, downloadedBytes: downloaded, modelBytes: total };
  };

  try {
    const r = await tryLoad(preferred);
    return { ...r, model: wanted, dtype, requestedDevice: preferred, degraded: false };
  } catch (e) {
    if (preferred === "webgpu") {
      console.warn("[embeddings] WebGPU 初始化失败，自动降级 WASM：", e?.message || e);
      const r = await tryLoad("wasm");
      return { ...r, model: wanted, dtype, requestedDevice: "webgpu", degraded: true, degradeReason: String(e?.message || e) };
    }
    throw e;
  }
}

/** 文本 → 向量（批处理，正常化到单位长度以便余弦距离） */
export async function embedTexts(emb, texts, { batchSize = 8, onBatch } = {}) {
  const vectors = [];
  const t0 = performance.now();
  for (let i = 0; i < texts.length; i += batchSize) {
    const batch = texts.slice(i, i + batchSize);
    const out = await emb.extractor(batch, { pooling: "mean", normalize: true });
    const dim = out.dims[out.dims.length - 1];
    const flat = out.data;
    for (let j = 0; j < batch.length; j++) {
      vectors.push(Array.from(flat.slice(j * dim, (j + 1) * dim)));
    }
    onBatch?.({ done: Math.min(i + batchSize, texts.length), total: texts.length });
    // 让出主线程，保持进度条与界面响应
    await new Promise((r) => setTimeout(r, 0));
  }
  return { vectors, ms: performance.now() - t0 };
}

/** 文本分块：按段落聚合到目标长度，重叠窗口保证跨段语义连续 */
export function chunkText(text, { target = 320, overlap = 60 } = {}) {
  const paras = text
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}|\n(?=第[一二三四五六七八九十]+[、章节])/)
    .map((s) => s.trim())
    .filter(Boolean);

  const chunks = [];
  let buf = "";
  for (const p of paras) {
    if ((buf + "\n" + p).length <= target) {
      buf = buf ? buf + "\n" + p : p;
    } else {
      if (buf) chunks.push(buf);
      if (p.length <= target) {
        buf = p;
      } else {
        for (let i = 0; i < p.length; i += target - overlap) {
          chunks.push(p.slice(i, i + target));
        }
        buf = "";
      }
    }
  }
  if (buf) chunks.push(buf);
  return chunks;
}
