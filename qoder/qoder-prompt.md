# 在 Qoder 中复现 StudyCoach 的提示词

> 用法：在 Qoder 中新建项目，把下面的提示词发给 Agent，即可复现本项目的开发过程
> （也作为 Qoder 开发记录的对话素材）。生成后把仓库 `site/`、`scripts/` 的代码与之对照。

## 第一轮：需求拆解

```
我想做一个面向大学生的「复习教练」网页应用，解决"复习时找不到自己哪里没学会"的痛点。
核心机制：学习打卡时把薄弱点保存到 OceanBase PowerContext（一个 AI Agent 长期记忆服务，
HTTP API 部署在本机 http://127.0.0.1:8000）；复习时用语义检索召回历史薄弱点生成复习清单，
跨会话复用。

请先帮我拆解功能清单和页面结构（不要写代码）：
1. 学习打卡：学科主题 + 掌握内容 + 薄弱点列表 → 写入 PowerContext 记忆
2. 复习模式：输入复习主题 → 召回薄弱点 → 生成个性化复习清单
3. 记忆库一览：查看当前已保存的记忆条目
4. 连接设置：可配置 PowerContext 适配器地址和 Scope，也有演示模式兜底
```

## 第二轮：页面与样式

```
按上面的拆解实现前端页面，技术栈用纯 HTML/CSS/JS（无框架、无构建工具，便于静态托管）：
- 单页布局：顶部标题栏含连接状态徽标（绿=已连接适配器，灰=演示模式）+ 设置按钮
- 左栏打卡表单，右栏复习 + 记忆库
- 浅绿主色调，卡片式面板，移动端单栏自适应
- PowerContext 接口字段（已核对官方 OpenAPI）：
  POST /v1/memory/remember   {"scope_id": "...", "kind": "weakness|progress", "text": "..."}
  POST /v1/context/prepare   {"scope_id": "...", "query": "...", "max_bytes": 8000}
  POST /v1/memory/entries/list {"scope_id": "..."}
- API 通过本地适配器 http://127.0.0.1:8787 访问（适配器负责加 CORS 头）
- 未连接时进入演示模式：用内存 mock 数据走通同样交互，界面标注"演示"
```

## 第三轮：适配器脚本

```
写一个 scripts/adapter.py：Python 标准库 http.server 实现，监听 8787，把收到的 POST
原样转发到环境变量 POWERCONTEXT_URL（默认 http://127.0.0.1:8000），转发时附加
Authorization: Bearer $POWERCONTEXT_TOKEN（若有），响应统一加
Access-Control-Allow-Origin 等 CORS 头，并正确处理 OPTIONS 预检。不要任何第三方依赖。
```

## 第四轮：调试迭代（示例）

```
运行后发现：复习清单把进展条目也当成薄弱点输出了。请修复分类逻辑：
context 文本按 [weakness]/[progress]/[preference] 前缀分组，只有 weakness 进
"本次优先复习"，其余进"相关记忆"；context 为空时提示先打卡。
```

## 发布

```
在 Qoder 中：Sites → 新建站点 → 选择本项目 site/ 目录 → 发布 →
可见范围设置为「任何人都可访问」→ 用无痕窗口验证可访问。
```
