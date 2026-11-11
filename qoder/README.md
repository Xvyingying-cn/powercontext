# StudyCoach 复习教练 —— Build with Qoder

> 2026 OceanBase 大赛 AI 生态赛题（Qoder × OceanBase，6000 系）
> 用 Qoder 智能体平台开发的「越用越懂你的复习教练」，结合 PowerContext 长期记忆实现核心功能，并通过 Qoder Sites 发布项目网站。

## 一、项目简介

复习最痛的不是"学"，是"忘了自己哪里没学会"。StudyCoach 把每次学习打卡中的**薄弱点**写入
PowerContext 的长期记忆（Scope 级 Memory），复习时自动语义召回你的历史薄弱点，生成个性化
复习清单——**关掉网页明天再来，教练依然记得你哪里没学会**。

- **核心闭环**：学习打卡（写入记忆）→ 复习（召回记忆生成清单）→ 再次打卡（验证是否克服）
- **形式**：纯静态 Web 应用（HTML/JS），无需后端服务器，浏览器即开即用

## 二、技术结合说明

| 技术 | 在项目中的实际作用 |
| --- | --- |
| **Qoder（必选）** | 项目主要开发工作（页面结构、前端逻辑、样式、适配器脚本）由 Qoder 智能体完成；项目网站通过 **Qoder Sites** 发布。开发对话记录见 `QODER_RECORD.md` |
| **PowerContext（核心）** | ① 打卡时调用 `POST /v1/memory/remember` 把薄弱点/进展写入 Scope 记忆；② 复习时调用 `POST /v1/context/prepare` 语义召回历史薄弱点；③ `POST /v1/memory/entries/list` 展示记忆库。数据跨会话持久，可在 PowerContext 服务端核对读写记录 |
| **seekdb** | 本作品使用 PowerContext 单技术路线，未使用 seekdb |

PowerContext 为自部署开源服务，默认监听 `http://127.0.0.1:8000`，参见官方
[部署文档](https://github.com/oceanbase/powercontext)。

## 三、目录结构

```
qoder/
├── site/                 # Qoder Sites 发布的静态网站源码
│   ├── index.html        # 页面结构（打卡 / 复习 / 记忆库 / 技术说明）
│   ├── app.js            # 前端逻辑（API 调用 + 演示模式）
│   └── style.css         # 样式
├── scripts/
│   └── adapter.py        # 本地适配器：为浏览器提供 CORS 转发层（仅标准库）
├── README.md             # 本文件（运行说明）
├── VALIDATION.md         # 验证样例（含预期结果与核对方法）
├── QODER_RECORD.md       # Qoder 开发记录
└── qoder-prompt.md       # 在 Qoder 中复现本项目的提示词
```

## 四、运行说明

### 方式 A：快速体验（演示模式，无需部署 PowerContext）

直接打开 `site/index.html`，或在 Qoder Sites 发布后访问线上地址。未连接服务时页面处于
**演示模式**，使用内置示例数据走通"打卡 → 写入记忆 → 复习召回 → 记忆库"全部交互。

### 方式 B：完整功能（连接真实 PowerContext）

1. **部署 PowerContext 服务**（一次性）：

   ```bash
   pip install powercontext        # 或按官方仓库文档安装
   powercontext serve              # 默认监听 http://127.0.0.1:8000
   ```

   若服务开启鉴权，记录下 Bearer Token。

2. **启动本地适配器**（给浏览器提供 CORS 转发）：

   ```bash
   python scripts/adapter.py       # 默认监听 http://127.0.0.1:8787
   # 如需自定义：
   POWERCONTEXT_URL=http://127.0.0.1:8000 POWERCONTEXT_TOKEN=你的Token ADAPTER_PORT=8787 python scripts/adapter.py
   ```

3. **配置网页**：打开 StudyCoach 网页 → 右上角 **⚙ 设置** →
   - 适配器地址：`http://127.0.0.1:8787`
   - Bearer Token：与 PowerContext 一致（本地开发可留空）
   - Scope ID：自定义（如 `study-coach-me`），留空为 `study-coach-demo`
   - 点 **保存并测试连接**，右上角徽标变绿即连接成功。

4. **开始使用**：左侧打卡 → 右侧生成复习清单 → 「记忆库一览」可看到每条记忆实时入库。

> 环境要求：Python 3.8+（适配器，仅标准库）；浏览器 Chrome/Edge/Firefox 现代版本；无其他依赖。

## 五、验证样例

见 [VALIDATION.md](VALIDATION.md)。核心样例：打卡两条薄弱点 → 刷新页面/第二天重开 →
复习召回确认薄弱点仍在（跨会话记忆复用）。

## 六、提交信息

- 项目网站：见大赛提测表单（Qoder Sites 公开访问地址）
- 代码仓库：本仓库 `qoder/` 目录（分支 `2026-competition-ecosystem`）
- Qoder 账号 UID：见 `QODER_RECORD.md`

## 附：API 依赖明细

| 接口 | 方法 | 请求体 | 用途 |
| --- | --- | --- | --- |
| `/v1/memory/remember` | POST | `{scope_id, kind, text}` | 写入薄弱点（kind=weakness）、进展（progress） |
| `/v1/context/prepare` | POST | `{scope_id, query, max_bytes}` | 复习时语义召回历史记忆 |
| `/v1/memory/entries/list` | POST | `{scope_id}` | 记忆库一览 / 连接测试 |
| `/v1/scopes` | POST | `{title, summary, idempotency_key}` | （可选）创建新 Scope |

字段定义核对自 PowerContext 官方 OpenAPI（`openapi/powercontext.yaml`）。
