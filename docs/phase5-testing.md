# Phase 5 恢复开发与测试报告

日期：2026-10-15（Asia/Shanghai）。项目：D:/desktop/code/StudyAgent/outputs/StudyAgent。

## 恢复时实际状态

- 项目自身没有独立 Git 仓库；上层 Git 将整个 StudyAgent 目录视为未跟踪内容，不能据此还原上次对话的逐文件 diff。未修改上层仓库的其他文件。
- 修改前文件 SHA-256 记录于 phase5-resume-baseline.json。本轮清单由实际文件指纹比较得出，不把上次遗留实现冒充本轮新写。
- Phase 1–4、Step 2A History、Step 2B Preference API 已存在；恢复时实际运行 pytest 为 276 passed，前端构建通过。
- 中断前已落盘：conversation_memory.py（完整轮次窗口、分批摘要、预算、水位/revision、偏好注入）、conversation_execution.py（请求预留、幂等、锁、保存、取消、恢复），Chat/Agent API 与生命周期接线、相关配置和请求字段。
- HistoryView.vue、PreferencesView.vue、history.ts、history.css 已落盘，但路由和导航未接线；ConversationView.vue 不存在，因此历史列表点击会落回首页。Chat/Agent 仍只有临时入口。
- 没有针对上述持久化执行与 Memory 集成的测试，README 仍停在 Step 2B。

## 本轮完成

保留已有正确后端实现，补齐会话详情、历史/偏好路由和导航、临时页到持久化会话入口。支持连续提问、普通/SSE Chat、Agent 执行状态、刷新恢复、摘要查看、最后一轮重新生成并保留各次尝试、停止/刷新未完成请求、引用快照展示、会话筛选/分页/重命名/删除、偏好保存与逐项恢复。

修复历史页 select 缺少稳定可访问名称，修复新增页面输入框样式和四项导航在移动端挤压。Mock 协议服务补齐历史摘要响应。

浏览器回归发现本机连续工具调用受每次 AsyncClient 重复加载证书影响，耗尽旧测试的 5 秒等待时限。已在本地 Provider 复用 SSLContext，保留 CERT_REQUIRED、hostname 校验；外部服务仍沿用原环境证书配置。没有放宽旧测试等待时限或改变旧断言，新增测试验证证书校验未关闭。

## 实际验证结果

| 验证 | 结果 |
| --- | --- |
| 恢复前完整 pytest | 276 passed，56.06 秒 |
| 最终完整 pytest | **301 passed，0 failed，0 skipped**，53.96 秒 |
| TypeScript + Vite 生产构建 | **通过** |
| 完整 Playwright Chrome E2E | **10 passed，0 failed**（原 7 + 新 3） |
| 原测试源码 SHA-256 比对 | 原 276 项所在 pytest 文件及原 4 个 E2E spec 文件均未修改 |

新增 25 个 pytest 场景覆盖：Chat 普通/SSE 持久化与重放、偏好/上下文注入、跨会话/知识库/模式隔离、request_id 冲突、重新生成版本、Agent 普通/SSE、增量摘要与原文保留、摘要异常/空/过长、输入与最终 Prompt 字符/token 预算、失败轮次排除、进程重启 pending 恢复、并发锁与删除保护、取消与解锁、保存失败不发 done、摘要 revision 冲突、总超时、重放不释放其他请求锁、空库自动保存、失败重生成保留最近成功答案及证书验证上下文复用。

新增 3 个 E2E 覆盖：创建会话→4 轮问答→摘要→刷新→重生成→删除来源后引用快照→390px 移动端→重命名→删除；Agent 保存/刷新/错误/取消/继续；三种偏好保存/刷新/逐项恢复默认。截图见 screenshots/phase5-history.png。

首轮 E2E 为 8/10（历史 select 名称、Agent 等待超时）；针对修复的中间轮为 3/5（全部新功能通过，旧 Agent 两项仍超时）。完成证书上下文复用后完整套件 10/10 通过；没有把失败轮记为通过，也没有删除测试。

## 测试环境与复现

后端 pytest 使用临时 SQLite/FAISS 与 httpx.MockTransport；浏览器使用真实 Chrome、Vite、FastAPI、SQLite/FAISS 和本机协议 Mock（127.0.0.1:18084）。未改正式 .env，未配置或调用真实 API。

浏览器后端使用 DATABASE_URL=sqlite:///./phase5-e2e-resume.db、DATA_DIR=./data/phase5-e2e-resume；LLM/EMBEDDING_BASE_URL 均为 http://127.0.0.1:18084/v1；模型 TEST-ONLY-AGENT / TEST-ONLY-RAG，空 API key；LLM_TIMEOUT_SECONDS=1.5、AGENT_MAX_TOOL_CALLS=5、MEMORY_WINDOW_TURNS=2。窗口 2 仅为 E2E 加速触发摘要，产品默认 10。

完整启动与测试命令见 README 的 Mock 本地联调/测试章节。测试产物 dist、test-results、独立数据库/data 和截图保留。服务在完成后关闭。

## 本轮实际修改文件

- `README.md`
- `docs/api.md`
- `frontend/src/App.vue`
- `frontend/src/assets/history.css`
- `frontend/src/router/index.ts`
- `frontend/src/views/AgentView.vue`
- `frontend/src/views/ChatView.vue`
- `frontend/src/views/HistoryView.vue`
- `backend/app/main.py`
- `backend/tests/agent_stub_server.py`
- `backend/app/llm/provider.py`

新增：

- `frontend/src/views/ConversationView.vue`
- `frontend/tests/history.spec.ts`
- `backend/tests/test_conversation_execution.py`
- `docs/phase5-testing.md`
- `docs/phase5-resume-baseline.json`
- `docs/screenshots/phase5-history.png`

完整 E2E 另刷新了 docs/screenshots 中原有 11 张截图（首页、CRUD、文档、Chat、Agent 和移动端）。后端 .env.example 已在中断前包含 Phase 5 配置，本轮内容未改变。原有 PreferencesView、history.ts、Memory/持久化服务实现经验证后保留。

## 未解决问题及边界

无阻塞功能或测试失败。仍有一条已有 Starlette/httpx TestClient 弃用警告；Playwright 有 NO_COLOR/FORCE_COLOR 环境提示，均不影响结果。

单进程锁要求单 Uvicorn worker，尚未提供多进程/多用户授权。token 预算使用保守 UTF-8 字节代理，不是具体模型的精确分词。取消为异步请求，页面提供刷新最终状态。历史页面会分页读取全部消息，超大历史的渲染性能未压测。Mock 验证协议、存储和交互，不验证真实模型语义、摘要质量或真实供应商兼容性。未开发 Reranker、Evaluation 或 Phase 6–7。
