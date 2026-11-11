# pet-with-memory · 桌宠小忆 🐣

> **Just for Fun——用 Dify 做一个好玩的智能应用**（Dify × OceanBase，20 分）

一只**会跨会话记住你**的电子桌宠「小忆」。你随口告诉它"我最爱吃西红柿炒蛋"，下次打开对话它会主动提起；你告诉它一个小约定，它会一直记得。所有长期记忆由 **PowerContext** 通过 HTTP API 真实读写，应用编排完全由 **Dify Chatflow / Workflow** 承担。

## 1. 作品创意与玩法

| 项目 | 说明 |
| --- | --- |
| 玩法 | 像养电子宠物一样和小忆聊天。它会软萌地回应你、记住你说的小事，并在下次对话（**新会话也有效**）自然地提起 |
| 好玩在哪 | 记忆是真实持久的：今天告诉它的事，明天再聊它还记得；记忆越多，它越"懂"你 |
| 用户如何参与 | 打开 Dify 聊天界面（或调用应用 API）直接输入一句话即可，无需任何配置 |

**演示效果示例**

- 第一次对话：`我叫小徐，最喜欢吃西红柿炒蛋` → 小忆软萌回应，并在内部写入记忆 `主人小徐最喜欢的菜是西红柿炒蛋`
- 下一次（新会话）：`晚上吃点什么好？` → 小忆回复 `小徐～你不是最喜欢西红柿炒蛋嘛，今晚要不要来一份？🍳`

## 2. 技术分工（Dify + PowerContext）

| 技术 | 在应用中的作用 |
| --- | --- |
| **Dify（必选）** | 承担核心流程编排：输入处理 → HTTP Request 节点调用 PowerContext → LLM 生成宠物回复 → 条件分支 → 结果输出。共 2 个应用：`app.dsl.yml`（Chatflow 主应用）、`pet-with-memory.yml`（Workflow 版「养护速览」，用于验证记忆读写） |
| **PowerContext（必选）** | 长期记忆的存储与检索。每轮对话：先 `POST /v1/context/prepare` 召回与本次输入相关的历史记忆，注入 LLM 系统提示；对话学到新事实时 `POST /v1/memory/remember` 写入长期记忆。**记忆跨会话持久保存**，这是"桌宠记得你"的能力来源 |

技术选型为 **Dify + PowerContext** 组合（符合题面 2.2 节"选择 PowerContext 的作品不额外要求使用 seekdb"）。

**Chatflow 流程图**

```
开始 → [代码]组装查询体 → [HTTP] /v1/context/prepare 召回记忆
     → [代码]解析召回结果 → [LLM]小忆回复（记忆注入系统提示）
     → [代码]拆分「回复」与「【记住】新事实」
     → [条件分支]有新记忆？
         ├─ 是 → [HTTP] /v1/memory/remember 写入记忆 → 直接回复
         └─ 否 → 直接回复
```

PowerContext HTTP 接口契约见官方文档 <https://powercontext.oceanbase.io/zh/docs/develop/http-api/>；本题仅使用 `POST /v1/scopes`、`POST /v1/context/prepare`、`POST /v1/memory/remember` 三个接口，均为无需推理 Provider 的本地读写。

## 3. 交付物清单

```
dify/
├── app.dsl.yml            # 主应用（Chatflow，advanced-chat）——导入这个
├── pet-with-memory.yml    # 配套应用（Workflow：小忆养护速览，用于批量验证记忆读写）
├── README.md              # 本运行说明
├── VALIDATION.md          # 验证样例与运行证据
└── scripts/
    ├── init-scope.py      # 创建 PowerContext scope（可选 --seed 预置示例记忆）
    └── init-scope.sh      # 等价 bash 版（curl）
```

两个 DSL 均为 **Dify 1.x（DSL version 0.3.0）** 导出格式，云端版与 Docker 自部署版均可导入。

## 4. 运行说明（从零到可体验）

### 4.1 环境要求

| 组件 | 版本/说明 |
| --- | --- |
| Dify | 1.x（云端版 <https://cloud.dify.ai> 或 Docker 自部署均可） |
| PowerContext Server | `master` 分支，Python 3.11+，本地默认 `http://127.0.0.1:8000` |
| LLM 模型 | DSL 内默认 `gpt-4o-mini`（OpenAI），可在 Dify 内重新绑定任意 Chat 模型（如 DeepSeek、通义千问），不影响流程 |
| 插件 | 无自定义插件依赖；PowerContext 通过 Dify 内置 **HTTP Request 节点**调用，无需安装任何插件 |

### 4.2 启动 PowerContext Server

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run        # 默认监听 http://127.0.0.1:8000，SQLite 存储
curl http://127.0.0.1:8000/health/ready   # 应返回 ready
```

### 4.3 初始化 Scope（数据分区）

```bash
cd dify/scripts
python init-scope.py --seed
# 输出末尾会打印 SCOPE_ID=... ，复制它
```

`--seed` 会预置 3 条示例记忆（主人喜好、作息、生日），保证评审第一次体验时就能看到"小忆记得事"的效果；不加 `--seed` 则从空记忆开始。

### 4.4 导入 DSL 并配置

1. 打开 Dify 工作室 → **导入 DSL 文件** → 选择 `app.dsl.yml`；
2. 导入后进入应用的 **环境变量** 页，把三项改为实际值：
   - `pc_url`：PowerContext Server 地址（默认 `http://127.0.0.1:8000`；Dify 云端版需填公网可达地址）
   - `pc_scope_id`：第 4.3 步打印的 `SCOPE_ID`
   - `pc_token`：PowerContext 启用 Bearer 鉴权时填写，否则留空
3. 在编排页把 LLM 节点的模型重新绑定为你的工作区可用的 Chat 模型；
4. 保存并 **发布**，右上角「预览」或「访问 API」即为应用入口；
5. （可选）同样方式导入 `pet-with-memory.yml`（Workflow），用于第 5.2 节的批量验证。

> Dify 云端版访问本机 `127.0.0.1` 需要内网穿透（如 frp / cloudflared）；Docker 自部署时让 Dify 容器与 PowerContext 同网络即可。这是唯一的部署注意事项。

### 4.5 已知限制

- 记忆检索默认为全文检索；配置向量检索后召回更智能（见 PowerContext 文档 *configure-vector-search*）。
- LLM 未输出「【记住】」行时，本轮不会写入记忆，属于预期行为（避免记忆噪音）。
- DSL 不包含知识库内容；本作品不使用 Dify 知识库，无需单独交付。

## 5. 验证

见 [VALIDATION.md](VALIDATION.md)：包含 2 组可按步骤执行的完整样例（含初始状态、输入顺序、预期输出与运行证据）。

## 6. 复用声明

- Chatflow / Workflow 编排结构为本作品原创设计；
- DSL 文件结构参考了 Dify 官方导出格式与社区开源示例（wwwzhouhui/dify-for-dsl、dzh-gitcode/dify-workflows）的节点 schema，未复制其业务流程；
- PowerContext 调用方式来自官方文档示例（api-quickstart），按本作品场景改写。
