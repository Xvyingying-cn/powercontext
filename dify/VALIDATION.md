# 验证样例与运行证据 · pet-with-memory

本文档提供 **2 组完整验证样例**。评审按步骤执行即可复现"小忆跨会话记住主人"的核心能力，并核对 PowerContext 在应用功能中的实际作用。

前置条件（详见 README.md 第 4 节）：

```bash
# 1. PowerContext Server 已启动
curl http://127.0.0.1:8000/health/ready   # -> ready

# 2. 已初始化 scope 并把 SCOPE_ID 填入 Dify 环境变量 pc_scope_id
python dify/scripts/init-scope.py --seed
```

样例均从 **空对话（新会话）** 开始，以验证记忆确实来自 PowerContext 而非 Dify 会话上下文。

---

## 样例一：记住 + 跨会话提起（主应用 app.dsl.yml）

**初始状态**：新会话；PowerContext scope 已用 `--seed` 预置 3 条记忆（小徐爱吃西红柿炒蛋 / 晚 10 点摸鱼提醒 / 小忆生日 11 月 11 日）。

| 步骤 | 输入（用户） | 预期表现 |
| --- | --- | --- |
| 1 | `我叫小徐，最喜欢吃西红柿炒蛋，讨厌香菜` | 小忆软萌回应并表示记住了。后台 Chatflow 中「写入 PowerContext」HTTP 节点执行成功，`/v1/memory/remember` 返回 `records`（可在节点运行详情中看到 200 响应与 artifact 引用） |
| 2（新会话） | `晚上吃点什么好？` | 小忆**在新会话中主动提起记忆**，例如：「小徐～你不是最喜欢西红柿炒蛋嘛，今晚要不要来一份？不过记得别放香菜呀 🍳」。后台「召回长期记忆」节点 `/v1/context/prepare` 返回 `status: ready`，`content` 含步骤 1 写入的记忆文本 |

**核对点（对应题面 3.1-3 技术结合有效）**

- 「召回长期记忆」HTTP 节点的入参 `prepare_body`（scope_id、query、max_bytes）与响应 `status/content`；
- 「写入 PowerContext」HTTP 节点的入参 `remember_body` 与响应 `records`（含 artifact 引用与状态）；
- PowerContext 侧可用下面的命令独立复核写入结果：

```bash
SCOPE_ID=<你的scope_id>
curl -s -X POST http://127.0.0.1:8000/v1/memory/search \
  -H 'Content-Type: application/json' \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"query\":\"西红柿炒蛋\",\"limit\":5}"
# 返回结果应包含：主人小徐最喜欢的菜是西红柿炒蛋，讨厌香菜。
```

**实测记录**（本地 PowerContext Server，master 分支，SQLite 存储）：

- `POST /v1/scopes`（幂等键 `pet-with-memory-dify`）→ 200，返回 `scope_id`；
- `POST /v1/memory/remember`（kind=preference，text=主人小徐最喜欢的菜是西红柿炒蛋，讨厌香菜。）→ 200，`records` 返回 1 条 `artifact` 引用（family=atomic-memory），状态 `active`；
- `POST /v1/context/prepare`（query=晚上吃点什么好，max_bytes=4000）→ 200，`status: "ready"`，`content` 含上述记忆原文，证明写入的记忆可被后续会话召回；
- 空记忆场景对照：全新 scope 下同一查询返回 `status: "empty"`、`content: null`，Chatflow「解析召回结果」节点输出兜底文案「小忆还没有相关长期记忆…」，流程正常走通不报错。

> 评审实际运行时的节点截图请粘贴在此处（Dify 编排页 → 节点运行详情 → HTTP 节点 的请求/响应面板）。

---

## 样例二：养护速览（配套 Workflow pet-with-memory.yml）

**初始状态**：同上（scope 已预置记忆）。

| 步骤 | 操作 | 预期表现 |
| --- | --- | --- |
| 1 | 运行 Workflow，输入 `主人想对小忆说的话` = `最近工作好忙，没时间陪小忆玩` | 「召回长期记忆」节点返回 `status: ready`；结束节点输出 Markdown 报告，`## 💾 记忆里的小忆` 一节中**必须**出现预置记忆内容（如西红柿炒蛋、生日 11 月 11 日），并在 `## 🍚 今日建议` 中把建议与记忆条目对应 |
| 2 | 换一个全新 scope 再运行一次 | 召回返回 `status: empty`，报告的 `## 💾 记忆里的小忆` 说明这是新朋友——证明报告内容确实来自 PowerContext 记忆而非模型编造 |

**核对点**：两次运行输出差异仅由 PowerContext 记忆状态决定，可复核记忆读写真实生效。

---

## 复现与恢复初始状态

- 记忆写入是幂等追加式的，重复执行样例只会多一条记忆，不影响验证；
- 如需完全复位：重新运行 `python dify/scripts/init-scope.py --seed` 会因幂等键 `pet-with-memory-dify` 返回**同一个 scope**（保留旧记忆）；若要干净环境，改用新 idempotency_key（编辑 `scripts/init-scope.py` 中 `"idempotency_key"` 字段）创建新 scope，并同步更新 Dify 环境变量 `pc_scope_id`；
- Dify 侧无需复位：会话与 PowerContext 记忆相互独立。

## 运行证据清单

| 证据 | 说明 |
| --- | --- |
| PowerContext 侧实测记录 | 见样例一「实测记录」（scopes / remember / prepare 三个接口的真实响应摘要） |
| Dify 节点运行截图 | 评审按 README 4.4 导入运行后，从节点运行详情截取（召回 200 + 写入 200 + 回复内容） |
| 记忆复核命令 | 见样例一「核对点」，独立于 Dify 验证 PowerContext 数据真实存在 |
