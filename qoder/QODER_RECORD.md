# Qoder 开发记录

> 本项目的主要开发工作在 Qoder 智能体平台中完成，并使用 Qoder Sites 发布项目网站。
> 本文件用于核对「Qoder 开发可核对」基础验收项。

- **Qoder 账号 UID**：`01a1ca35-4cde-7e35-bd1b-bf9befac81ca`（账号邮箱 `17542118972@163.com`，提测表单同填此 UID）
- **Qoder 项目/工作区**：`Studycoach`（任务名「复习教练应用功能拆解」）
- **Qoder Sites 站点**：`https://study-review-coach-wnoqq0xhf8x.qoder.website`（发布状态：已发布；可见范围：所有人 · 任何拥有链接的人都可以访问）
- **开发时间**：2026-11-11

## 开发过程

| 环节 | 与 Qoder 的协作方式 | 产出/记录 |
| --- | --- | --- |
| 需求拆解 | 向 Qoder Agent 描述痛点（复习时找不到薄弱点），Agent 拆解功能清单：打卡写入 PowerContext 记忆、复习召回、记忆库展示、连接设置/演示模式，并主动识别出 CORS 风险与适配层方案 | 对话截图 1 |
| 方案确认 | 就 5 个设计决策与 Agent 往返确认：接口契约按官方三接口、Scope 用户自填、清单 MVP 规则生成不接 LLM、技术栈纯 HTML/CSS/JS（拒绝 React 建议以便静态托管）、掌握内容不参与召回 | 对话截图 2 |
| 页面与逻辑生成 | Agent 分批生成 `index.html`、`assets/styles.css`、`assets/adapter.js`（本地存储/演示数据/HTTP 适配/召回排序）、`assets/app.js`（四页路由与全部交互，约 3300 行） | 对话截图 3 |
| 断点续写 | 单轮输出达到长度上限后，指令 Agent「继续完成未写完部分、不重写已完成代码」，完整交付并自做回归测试（打卡写入、演示模式召回/筛选/勾选、失败提示与自动回退、Scope 切换） | 对话截图 4 |
| 收尾迭代 | 要求补充 README.md 并检查移动端窄屏（<600px）布局；Agent 实测 375px/509px，修复 2 处真实布局问题（「语义召回」标签竖排、设置页保存提示压成单列），见 `styles.css:732/2688` | 对话截图 5 |
| Qoder Sites 发布 | 在 Qoder 中通过「站点」创建站点并发布静态项目，可见范围设为「任何人都可访问」，发布状态「已发布」 | 发布截图 6 |

## 发布验证

- 站点地址：`https://study-review-coach-wnoqq0xhf8x.qoder.website`
- 发布状态：已发布（Qoder 站点设置页可见「发布状态：已发布」「可见范围：所有人」）
- 访问验证：无痕窗口直接打开站点链接可正常加载「复习教练」页面；适配器不可用时自动进入演示模式并显示提示条，四个页面（复习/打卡/记忆库/设置）均可交互。

## 复现说明

任何人可以在 Qoder 中用仓库根目录 `qoder-prompt.md` 的提示词复现本项目的生成过程；
Qoder 工作区中 Agent 产出的代码（`index.html`、`assets/styles.css`、`assets/adapter.js`、
`assets/app.js`、`README.md`）与仓库 `site/` 下的交付物功能一致（仓库版本做了少量文档与
注释规范化）。
