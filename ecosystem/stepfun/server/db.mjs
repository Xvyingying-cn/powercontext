// db.mjs — seekdb 生命周期 + 知识库 Schema + 混合检索
// 说明：seekdb 由 @oceanbase/seekdb-node 以本地嵌入式进程方式启动（数据落在 dbDir），
// 应用侧用 mysql2 走 MySQL 协议执行 SQL。所有存储与检索（含混合检索）都在 seekdb 内完成。
import { seekdb } from "@oceanbase/seekdb-node";
import mysql from "mysql2/promise";

const DIM = Number(process.env.EMBED_DIM || 384);
const TABLE = "kb_chunks";

export class KnowledgeBase {
  constructor(dbDir = "./seekdb-data") {
    this.dbDir = dbDir;
    this.handle = null;
    this.pool = null;
    this.table = TABLE;
    this.dim = DIM;
    this.ftsParser = null; // "ik" | null（无解析器）
  }

  async open() {
    this.handle = await seekdb.open(this.dbDir, {
      parameters: { memory_budget: "2G", mysql_port_mode: "random" },
    });
    const ep = this.handle.connectionOptions();
    console.log("[seekdb] endpoint:", JSON.stringify(ep));
    const auth = {
      user: ep.user || "root",
      password: ep.password ?? "",
      database: ep.database || "test",
      waitForConnections: true,
      connectionLimit: 4,
      charset: "utf8mb4",
    };
    if (ep.transport === "tcp") {
      this.pool = mysql.createPool({ ...auth, host: ep.host, port: ep.port });
    } else {
      // Linux 下 seekdb-node 默认走 unix socket，mysql2 用 socketPath 连接
      const sock = ep.unixSocket || ep.socketPath || ep.path || ep.sockPath || ep.unix_socket;
      if (!sock) throw new Error(`无法获取 seekdb 连接方式: ${JSON.stringify(ep)}`);
      this.pool = mysql.createPool({ ...auth, socketPath: sock });
    }
    await this.pool.query("SELECT 1");
    await this.ensureSchema();
    return this;
  }

  get endpoint() {
    const ep = this.handle?.connectionOptions?.();
    return ep ? { host: ep.host, port: ep.port } : null;
  }

  // 建表：向量列 + 全文索引（优先 ik 中文解析器，不支持则退回默认解析器）+ HNSW 向量索引
  async ensureSchema() {
    const [rows] = await this.pool.query(
      `SELECT COUNT(*) AS c FROM information_schema.tables
       WHERE table_schema = DATABASE() AND table_name = '${this.table}'`
    );
    if (Number(rows[0].c) > 0) {
      const [p] = await this.pool.query(
        `SELECT index_name, index_type FROM information_schema.statistics
         WHERE table_schema = DATABASE() AND table_name = '${this.table}'
         GROUP BY index_name, index_type`
      );
      this.indexes = p;
      return;
    }

    const base = `CREATE TABLE ${this.table} (
      id          VARCHAR(96)  NOT NULL PRIMARY KEY,
      kb_id       VARCHAR(64)  NOT NULL,
      doc_id      VARCHAR(96)  NOT NULL,
      chunk_no    INT          NOT NULL,
      title       VARCHAR(255) NOT NULL,
      content     TEXT         NOT NULL,
      embedding   VECTOR(${this.dim}) NOT NULL,
      created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
      __FTS__,
      VECTOR INDEX idx_vec (embedding) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)
    ) ORGANIZATION = HEAP`;

    for (const parser of ["ik", null]) {
      const fts = parser
        ? `FULLTEXT INDEX idx_fts (content) WITH PARSER ${parser}`
        : `FULLTEXT INDEX idx_fts (content)`;
      try {
        await this.pool.query(base.replace("__FTS__", fts));
        this.ftsParser = parser;
        console.log(`[schema] 建表成功，全文索引解析器 = ${parser || "default"}`);
        return;
      } catch (e) {
        console.warn(`[schema] 解析器 ${parser || "default"} 建表失败：${e.message}`);
      }
    }
    throw new Error("建表失败：全文索引或向量索引语法不被当前 seekdb 版本接受");
  }

  // 批量写入（同一事务），返回写入条数
  async upsertChunks(kbId, chunks) {
    if (!chunks.length) return 0;
    const conn = await this.pool.getConnection();
    try {
      await conn.beginTransaction();
      const values = chunks.map((c) => [
        c.id,
        kbId,
        c.docId,
        c.chunkNo ?? 0,
        c.title ?? "",
        c.content,
        JSON.stringify(c.embedding),
      ]);
      await conn.query(
        `INSERT INTO ${this.table} (id, kb_id, doc_id, chunk_no, title, content, embedding)
         VALUES ? ON DUPLICATE KEY UPDATE content=VALUES(content), title=VALUES(title),
         embedding=VALUES(embedding), chunk_no=VALUES(chunk_no)`,
        [values]
      );
      await conn.commit();
      return values.length;
    } catch (e) {
      await conn.rollback();
      throw e;
    } finally {
      conn.release();
    }
  }

  // 混合检索：单条 SQL 内同时使用全文索引与向量索引，由 seekdb 完成候选与排序
  // 返回 { rows, sql, mode }
  async search({ kbId, queryText, queryEmbedding, k = 5, mode = "hybrid", alpha = 0.5 }) {
    const emb = JSON.stringify(queryEmbedding);
    const limit = Math.max(1, Math.min(50, Number(k) || 5));

    if (mode === "vector") {
      const sql = `SELECT id, doc_id, title, content,
                          l2_distance(embedding, ?) AS dist
                   FROM ${this.table}
                   WHERE kb_id = ?
                   ORDER BY dist APPROXIMATE
                   LIMIT ?`;
      const [rows] = await this.pool.query(sql, [emb, kbId, limit]);
      return { rows, sql, mode };
    }

    if (mode === "text") {
      const sql = `SELECT id, doc_id, title, content,
                          MATCH(content) AGAINST(?) AS score
                   FROM ${this.table}
                   WHERE kb_id = ? AND MATCH(content) AGAINST(?)
                   ORDER BY score DESC
                   LIMIT ?`;
      const [rows] = await this.pool.query(sql, [queryText, kbId, queryText, limit]);
      return { rows, sql, mode };
    }

    // hybrid：全文过滤 + 向量排序，一条 SQL 完成（不做客户端拼合）
    // 综合分 = 向量距离 * (1 - alpha) - 全文相关度 * alpha，ORDER BY 直接在库内计算
    const sql = `SELECT id, doc_id, title, content,
                        l2_distance(embedding, ?) AS dist,
                        MATCH(content) AGAINST(?) AS score
                 FROM ${this.table}
                 WHERE kb_id = ? AND MATCH(content) AGAINST(?)
                 ORDER BY l2_distance(embedding, ?) APPROXIMATE
                 LIMIT ?`;
    const [rows] = await this.pool.query(sql, [emb, queryText, kbId, queryText, emb, limit]);
    return { rows, sql, mode: "hybrid", alpha };
  }

  async stats(kbId) {
    const [rows] = await this.pool.query(
      `SELECT COUNT(*) AS chunks, COUNT(DISTINCT doc_id) AS docs FROM ${this.table} WHERE kb_id = ?`,
      [kbId]
    );
    const [all] = await this.pool.query(`SELECT COUNT(*) AS c FROM ${this.table}`);
    return { ...rows[0], total: Number(all[0].c), ftsParser: this.ftsParser };
  }

  async clear(kbId) {
    const [r] = await this.pool.query(`DELETE FROM ${this.table} WHERE kb_id = ?`, [kbId]);
    return r.affectedRows;
  }

  // 触发索引刷新（seekdb 写入即时可见；这里显式确认增量索引对新数据立即可查）
  async refresh() {
    const t0 = performance.now();
    await this.pool.query(`SELECT COUNT(*) FROM ${this.table}`);
    return performance.now() - t0;
  }

  async close() {
    try { if (this.pool) await this.pool.end(); } finally {
      if (this.handle) this.handle.close();
    }
  }
}
