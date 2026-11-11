# pet-with-memory · 桌宠小忆 🐣

> **Just for Fun——用 Dify 做一个好玩的智能应用**（Dify × OceanBase，20 分）
> 状态：**已端到端跑通**，PowerContext 侧真实运行证据见 `records/运行证据.md`

一只**会跨会话记住你**的电子桌宠「小忆」。你随口告诉它"我最爱吃西红柿炒蛋"，关掉页面后开一个**全新会话**再问，它依然记得。所有长期记忆由 **PowerContext** 通过 HTTP API 真实读写，应用编排完全由 **Dify Chatflow / Workflow** 承担。

## 1. 作品创意与玩法

| 项目 | 说明 |
| --- | --- |
| 玩法 | 像养电子宠物一样和小忆聊天。它软萌地回应你、记住你说的小事；**换新会话再聊，它仍然记得** |
| 好玩在哪 | 记忆真实持久：今天告诉它的事，明天再聊它还记得；记忆越多，它越"懂"你 |
| 用户如何参与 | 打开 Dify 聊天界面（或 Service API）直接输入一句话即可，无需任何配置 |
| 可判定信号 | 每轮回复末行输出【记忆命中：是/否】，评审**看标记即可判定记忆是否生效**，无需主观揣测 |

## 2. 技术分工（Dify + PowerContext）

| 技术 | 在应用中的作用 | 落在应用里的位置 |
| --- | --- | --- |
| **Dify（必选）** | 流程编排、模型调用、外部服务调用、结果输出 | Chatflow 5 节点：`开始 → PowerContext 召回(HTTP) → 小忆回复(LLM) → PowerContext 写入(HTTP) → 直接回复` |
| **PowerContext** | 长期记忆的存储与检索 | 每轮先 `POST /v1/context/prepare` 召回并注入 system prompt；再 `POST /v1/memory/remember` 写回本轮输入。**记忆跨会话持久**，这是"桌宠记得你"的能力来源 |

技术选型为 **Dify + PowerContext** 组合（符合题面 2.2 节"选择 PowerContext 的作品不额外要求使用 seekdb"）。

### 关键设计（评审先看这里）

- **Scope 绑定**：Scope ID 走环境变量 `PC_SCOPE_ID`（用 `scripts/init-scope.py` 创建），同一主人的记忆都在一个 Scope 里；换会话不换 Scope，所以记忆跨会话有效。换 Scope 即换一个干净记忆空间（见样例 3 隔离性验证）。
- **失败降级（记忆是增强，不是前置依赖）**：两个 HTTP 节点均设 `error_strategy: default-value` + 2 次重试——召回失败→空记忆、写入失败→跳过，**对话绝不中断**，此时按规则输出【记忆命中：否】。
- **地址不硬编码**：两个 HTTP 节点的 URL 均为 `{{#env.POWERCONTEXT_BASE_URL#}}/v1/...`，换环境只改这一个环境变量，DSL 中不含任何内网临时地址。

## 3. 交付物清单

```
dify/
├── app.dsl.yml            # ★ 主应用（Chatflow，advanced-chat）——导入这个
├── pet-with-memory.yml    # 配套 Workflow「养护速览」（可选导入，用于批量验证记忆读写）
├── README.md              # 本运行说明
├── samples/
│   └── 验证样例.md         # 3 组样例：埋记忆 / 新会话召回（核心）/ 空 Scope 对照 + 判定口径 + 重置方法
├── records/
│   └── 运行证据.md         # PowerContext 侧真实响应（scopes/remember/prepare/search/空对照）
└── scripts/
    ├── init-scope.py      # 创建 PowerContext Scope（可选 --seed 预置示例记忆）
    └── init-scope.sh      # 等价 bash 版（curl）
```

**导入顺序**：`app.dsl.yml` → 配置环境变量 → 发布 → 建 API Key。主验证只需 1 个 DSL 文件。
两个 DSL 均为 **Dify 1.x（DSL version 0.7.0）** 导出格式，云端版与 Docker 自部署版均可导入。

## 4. 运行说明（从零到可体验）

### 4.1 环境要求

| 组件 | 版本/说明 |
| --- | --- |
| Dify | 1.x（云端版 <https://cloud.dify.ai> 或 Docker 自部署均可） |
| PowerContext Server | `master` 分支，Python 3.11+，本地默认 `http://127.0.0.1:8000` |
| LLM 模型 | DSL 内默认 `gpt-4o-mini`（OpenAI）；在 Dify 内重新绑定任意可用 Chat 模型即可，不影响流程。大赛发放的 **MiniMax** Token 可用 `openai_api_compatible` 插件接入 `MiniMax-M3.1-Flash`（推理模型，`max_tokens` 建议 ≥ 2048） |
| 插件 | 无自定义插件依赖；PowerContext 通过 Dify 内置 **HTTP Request 节点**调用 |

### 4.2 启动 PowerContext Server

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run        # 默认监听 http://127.0.0.1:8000，SQLite 存储
curl http://127.0.0.1:8000/health/ready   # 应返回 {"status":"ready",...}
```

本地默认未启用 Bearer 鉴权（实测见 `records/运行证据.md` R1）；如启用鉴权，需在两个 HTTP 节点的 headers 中追加 `Authorization: Bearer <token>`。

### 4.3 初始化 Scope（数据分区）

```bash
cd dify/scripts
python init-scope.py --seed
# 输出末行打印 SCOPE_ID=scp_xxx，复制它
```

`--seed` 预置 3 条示例记忆（主人喜好、作息提醒、生日），保证评审第一次体验就能看到"小忆记得事"的效果；不加 `--seed` 则从空记忆开始（样例 1 的写入即为首个记忆）。

### 4.4 导入 DSL 并配置

1. Dify 工作室 → **导入 DSL 文件** → 选择 `app.dsl.yml`；
2. 导入后进入应用**环境变量**页，确认/修改两项后发布：
   - `POWERCONTEXT_BASE_URL`：PowerContext Server 地址（默认 `http://127.0.0.1:8000`；Dify 云端版需填公网可达地址）
   - `PC_SCOPE_ID`：第 4.3 步打印的 `SCOPE_ID`
3. 在编排页把 LLM 节点的模型重新绑定为工作区可用的 Chat 模型；
4. 保存并**发布**，右上角「预览」或「访问 API」即为应用入口；
5. （可选）同样方式导入 `pet-with-memory.yml`，跑样例"附：养护速览"。

> Dify 云端版访问本机 `127.0.0.1` 需内网穿透（frp / cloudflared）；Docker 自部署让 Dify 容器与 PowerContext 同网络即可。这是唯一的部署注意事项。

### 4.5 已知限制（照实写）

1. **记忆检索为 FTS 关键词匹配**：查询与记忆文本无词项重叠时可能漏召回（例：问"晚上吃点什么好？"召回不了"西红柿炒蛋"）；问法与写入口语保持一致即可稳定命中（验证样例已按此设计）。
2. **写入是追加式**：同一句重复发送会产生重复条目（幂等键只作用于 Scope 创建）。
3. **HTTP 节点 JSON body 为单条目文本**：主人输入含英文双引号时可能破坏 JSON 转义，正常中文输入不受影响。
4. **LLM 末行标记由模型输出**：提示词已强制要求；个别模型偶发漏标不影响记忆本身读写，重发即可。

## 5. 验证

见 [samples/验证样例.md](samples/验证样例.md)：3 组样例（埋记忆 / 新会话召回 / 空 Scope 对照），
每步给出输入、预期、判定口径与重置方法；PowerContext 侧真实响应见
[records/运行证据.md](records/运行证据.md)。

## 6. 复用声明

- Chatflow / Workflow 编排结构为本作品原创设计；
- DSL 文件结构参考了 Dify 官方导出格式与社区开源示例（wwwzhouhui/dify-for-dsl、dzh-gitcode/dify-workflows）的节点 schema，未复制其业务流程；
- PowerContext 调用方式来自官方文档示例（api-quickstart），按本作品场景改写。
