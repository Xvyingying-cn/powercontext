# 基于 Debian（glibc）而非 Alpine：seekdb 原生运行时依赖 glibc 2.28+
FROM node:22-bookworm-slim

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY server/package.json server/package-lock.json* ./server/
RUN cd server && npm install --no-audit --no-fund

COPY server ./server
COPY web ./web
COPY data ./data

ENV PORT=8788 \
    EMBED_DIM=384 \
    SEEKDB_DIR=/app/data/seekdb-data

EXPOSE 8788

HEALTHCHECK --interval=10s --timeout=5s --retries=12 \
  CMD node -e "fetch('http://127.0.0.1:8788/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server/server.mjs"]
