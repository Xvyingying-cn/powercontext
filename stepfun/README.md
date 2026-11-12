# 端侧知识库 · 浏览器本地向量化 + seekdb 混合检索

> 2026 OceanBase 数据库大赛 · seekdb × 阶跃星辰赛道作品
> 题目：端侧 AI 推理性能优化与 seekdb 混合检索

一个可以在浏览器里完成文档向量化、并用 seekdb 做向量 + 全文混合检索的迷你知识库应用。

- **所有 embedding 都在本机浏览器里算**（Transformers.js + WebGPU / WASM），不调用任何云端推理接口；
- **所有向量、原文和元数据都存在 seekdb**，检索（包括混合检索）由 seekdb 用**一条 SQL** 完成，前端不做两路结果拼合；
- 支持 **INT8 / FP16 量化**、**WebGPU 优先 + WASM 自动降级**（界面明示当前后端）；
- 内置**性能分阶段计时**与 **Recall@5 / nDCG@5 质量评测**，可一键导出实测 JSON。

---

## 一、目录结构

```
stepfun/
├─ web/                  前端（纯静态，无构建工具）
│  ├─ index.html         页面结构：导入 / 索引 / 性能面板 / 检索
│  ├─ app.js             主流程：分块 → 本地向量化 → 写入 → 检索 → 评测 → 导出
│  ├─ embeddings.js      端侧推理封装：模型加载、后端选择、量化、批处理
│  └─ styles.css
├─ server/               本地桥接服务（Node 22 + 嵌入式 seekdb）
│  ├─ server.mjs         HTTP API + 静态托管
│  ├─ db.mjs             seekdb 生命周期、建表、混合检索 SQL
│  └─ selftest.mjs       不依赖浏览器的链路自检（建表/写入/三种检索模式）
├─ data/
│  ├─ corpus.json        示例语料（8 篇技术文档）
│  ├─ queries.json       质量评测集（8 条查询 + 相关文档标注）
│  └─ seekdb-data/       seekdb 数据目录（运行时生成，已 gitignore）
├─ scripts/
│  ├─ setup.sh           安装 Node 22 与依赖（Linux / WSL2 / macOS）
│  └─ start.sh           启动服务
├─ docker/
│  └─ Dockerfile         Docker 镜像（Debian slim，保证 glibc）
├─ docker-compose.yml
└─ docs/
   ├─ 技术说明.md         一页技术说明（模型与量化 / 端侧运行 / seekdb / 优化）
   └─ 实测记录.md         实测环境、原始数据与结论
```

---

## 二、运行方式

### 方式 A：Linux / WSL2（推荐）

```bash
cd stepfun
bash scripts/setup.sh     # 安装 Node 22（如缺）+ 依赖
bash scripts/start.sh     # 启动服务
```

浏览器打开 <http://127.0.0.1:8788>。

> Windows 用户：如果本机启用了 Smart App Control（智能应用控制），未签名的 `seekdb.exe`
> 会被系统策略直接拦截（`应用程序控制策略已阻止此文件`）。这不是依赖缺失，属于系统策略，
> 请在 **WSL2 / Linux / Docker** 中运行，或把项目用方式 B 跑起来。

### 方式 B：Docker

```bash
cd stepfun
docker compose up --build
```

浏览器打开 <http://127.0.0.1:8788>。数据目录挂载在 `data/seekdb-data`，重启后数据保留。

### 方式 C：macOS（Apple Silicon）

```bash
cd stepfun && bash scripts/setup.sh && bash scripts/start.sh
```

> `@oceanbase/seekdb-node` 的 macOS 原生包要求 macOS 15.6.1+（Apple Silicon）。

### 链路自检（不需要浏览器）

```bash
cd server && node selftest.mjs
```

会依次验证：seekdb 启动 → 建表（向量索引 + 全文索引）→ 批量写入 → 三种检索模式返回结果。

---

## 三、使用步骤

1. 点「载入示例语料」（或选择自己的 `.txt` / `.md` 文件、粘贴文本）；
2. 选择模型与精度（默认 `paraphrase-multilingual-MiniLM-L12-v2` + INT8），点「向量化并写入 seekdb」；
   模型随项目本地分发（`web/models/`，INT8 约 113 MB），由浏览器直接从本地服务加载，无外网依赖；
3. 在右栏输入问题，选检索模式（混合 / 向量 / 全文），查看结果与命中的距离、全文相关度；
4. 展开「查看本次下发的 SQL」，可以看到交给 seekdb 的完整语句；
5. 「运行检索质量评测」得到当前配置的 Recall@5 / nDCG@5；「导出实测 JSON」保存全部计时与指标。

---

## 四、与验收标准的对应

| 验收项 | 实现位置 | 说明 |
| --- | --- | --- |
| 完整流程 | `web/app.js` | 导入 → 分块 → 本地向量化 → 写入 seekdb → 检索 → 排序展示 |
| 本地推理 | `web/embeddings.js` | Transformers.js 在浏览器内执行 ONNX 推理，无云端接口 |
| 模型量化 | `web/embeddings.js` + `docs/技术说明.md` | INT8（q8）/ FP16 / FP32 基线可切换，体积与质量变化见实测记录 |
| 后端与降级 | `web/embeddings.js` `loadEmbedder()` | WebGPU 优先，初始化失败自动降级 WASM，顶部徽标明示 |
| seekdb 数据层 | `server/db.mjs` | 向量列 + 全文索引 + HNSW 索引；混合检索为单条 SQL |
| 状态与刷新 | `server/server.mjs` + `db.mjs` | 知识库 ID 隔离、增量写入、写入后立即验证可见性 |
| 可复现性 | `scripts/`、`docker/` | 一键安装 / 启动，含自检脚本与示例语料、评测集 |

---

## 五、关键设计说明

**为什么浏览器不直连 seekdb？**
seekdb 通过 MySQL 协议通信，浏览器无法直接使用该协议。因此本作品把「推理」和「存储」分开：
推理放在浏览器（满足端侧要求），存储与检索放在本地桥接服务内的嵌入式 seekdb。
桥接服务只做数据搬运，不做任何向量计算——向量由浏览器算好后随文档一起上传。

**混合检索为什么在数据库里做？**
`server/db.mjs` 的 `search()` 中 `mode=hybrid` 只下发一条 SQL：

```sql
SELECT id, doc_id, title, content,
       l2_distance(embedding, ?) AS dist,
       MATCH(content) AGAINST(?) AS score
FROM kb_chunks
WHERE kb_id = ? AND MATCH(content) AGAINST(?)
ORDER BY l2_distance(embedding, ?) APPROXIMATE
LIMIT ?
```

全文匹配作为候选过滤、向量距离作为排序依据，两步在同一执行计划内完成，前端拿到的就是最终排序结果。

**降级策略**
页面启动时检测 `navigator.gpu`；有 WebGPU 时以 `device: "webgpu"` 初始化，抛错则自动用
`device: "wasm"` 重建模型，并把顶部徽标改成「已降级」，同时在性能面板记录实际后端。

---

## 六、模型来源与许可

| 模型 | 来源 | 维度 | 许可 |
| --- | --- | --- | --- |
| `Xenova/paraphrase-multilingual-MiniLM-L12-v2` | Hugging Face（ONNX 转换版） | 384 | Apache-2.0 |
| `Xenova/all-MiniLM-L6-v2` | Hugging Face（ONNX 转换版） | 384 | Apache-2.0 |

两个模型均为公开可获取的开源模型。默认模型已随项目本地分发（`web/models/`），推理与运行时（`web/vendor/`）完全不依赖外网；
若模型目录缺失，可执行 `bash scripts/fetch-model.sh` 从 hf-mirror 镜像一键拉取。

---

## 七、已知限制

- 单机、单用户演示形态，未做鉴权与多租户隔离；
- 语料分块为固定长度 + 重叠窗口，未做语义分块；
- 质量评测集规模较小（8 条查询），指标用于**同配置横向对比**，不代表通用语料上的绝对水平；
- Windows 原生运行受 Smart App Control 限制（见方式 A 的说明）。

## 八、参考

- seekdb 仓库：<https://github.com/oceanbase/seekdb>
- `@oceanbase/seekdb-node`：<https://www.npmjs.com/package/@oceanbase/seekdb-node>
- Transformers.js：<https://github.com/huggingface/transformers.js>
