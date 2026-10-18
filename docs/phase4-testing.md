# Phase 4 Agent 实施与实际测试报告

日期：2026-10-15。实际项目：`<项目根目录>`。

## 实现范围

实现单 Agent + 标准 Chat Completions Tool Calling：模型发出 tool_calls，服务端校验白名单和参数，实际执行工具，以 role=tool 和 tool_call_id 返回结果，模型可继续调用或给出最终回答。没有以关键词 if/else 选择工具的生产逻辑。

五个工具均已接入：search_documents 真实 FAISS 检索；summarize_document 读取指定文档全部分片并通过 Provider 生成总结；compare_documents 读取 2–4 篇文档并生成比较；generate_quiz 检索后生成并验证选择题/简答题/判断题；calculator 使用受限 AST 实际计算。总结、比较与出题的语言内容依赖 Provider，测试中使用明确标记的固定响应。

模型可以在一次回复中请求多个工具，也可以根据前一轮结果继续调用。顺序执行工具，所有尝试计数；非法参数、未知工具、文档不可用、模型生成异常等返回结构化错误，允许限制内修正。限制包括工具次数、规划轮次、总执行耗时、输入/输出和上下文长度；异常结束不发 done。计算器限制语法、节点、深度、指数和数值，不使用 eval 或系统命令。

文档工具的 knowledge_base_id 从服务端上下文绑定，不允许工具参数覆盖。文档 ID 与当前库及 ready 状态同时校验；检索复用 Phase 3 隔离逻辑。引用来自真实 SQLite 分片，跨工具稳定去重，失败工具回滚新增引用；最终回答校验编号，纯计算不允许虚构引用。

新增普通 Agent API 和执行状态 SSE。前端可从文档页进入 Agent，展示真实执行事件、最终 Markdown 与引用，支持停止与切库取消；不展示规划消息或模型推理字段。没有新增 Memory/History、Conversation 数据表、Reranker 或任何 Phase 5+ 模块。

## 文件清单

新增：

- `backend/app/agent/__init__.py`
- `backend/app/agent/agent.py`：执行循环、次数/轮次/超时、结构化错误与状态事件。
- `backend/app/agent/tools.py`：五工具注册、参数模型、当前库隔离、文档生成与出题校验。
- `backend/app/agent/calculator.py`：受限算术解析。
- `backend/app/llm/tool_protocol.py`：工具协议响应结构与校验。
- `backend/app/schemas/agent.py`、`backend/app/api/agent.py`：请求响应和普通/SSE API。
- `backend/tests/test_agent.py`：62 项展开后的测试。
- `backend/tests/agent_stub_server.py`：独立测试服务，复用未修改的 Phase 3 协议服务。
- `frontend/src/api/agent.ts`、`frontend/src/views/AgentView.vue`：状态 SSE 客户端和 Agent 页面。
- `frontend/tests/agent.spec.ts`：2 项真实浏览器场景。
- `docs/phase4-testing.md`、`docs/screenshots/phase4-agent.png`、`docs/screenshots/phase4-mobile.png`。

修改：

- `backend/app/llm/provider.py`：新增 tool_turn，保留 complete/stream。
- `backend/app/core/config.py`、`backend/.env.example`：Agent 配置上限。
- `backend/app/main.py`：注入 AgentService、注册路由。
- `frontend/src/router/index.ts`、`frontend/src/views/DocumentsView.vue`：页面路由与入口。
- `README.md`、`docs/architecture.md`、`docs/api.md`：使用方式、架构、协议、边界和结果。

原有测试源码未修改或删除：与 `outputs/StudyAgent-Phase3/StudyAgent` 中 10 份 `.py` / `.ts` 测试源码逐字节比对，差异为 0。旧浏览器用例会重新生成其已有截图；构建生成 dist，测试生成缓存/报告，均为运行产物。未新增依赖，也未修改依赖清单或锁文件。

## 实际测试命令与结果

在 backend 目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：**149 passed, 1 warning in 14.22s**（原有 87 + 新增 62）。没有失败项。保留原 Starlette TestClient/httpx 弃用提示，未屏蔽。

在 frontend 目录：

```powershell
npm.cmd run build
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm.cmd run test:e2e
```

结果：**7 passed (28.1s)**（原有 5 + 新增 2）。后端与 Mock 预先启动，Vite 由 Playwright 启动；实际 Chrome 浏览器通过 HTTP 联调。Node 的 NO_COLOR/FORCE_COLOR 提示非测试失败。

截图检查发现 Agent 状态与结果面板留白偏紧，补充局部样式后进行最终验证：

```powershell
npm.cmd run build
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm.cmd run test:e2e -- tests/agent.spec.ts
```

最终构建：**TypeScript 通过，Vite 135 个模块，1.24s**。Agent 复测：**2 passed (12.8s)**。桌面和 390px 移动端截图已更新；移动端无横向溢出。后端业务代码在完整 149 项通过后未再改变。

## 新增测试覆盖

- 标准工具定义、请求与 Tool Result ID 对应；同轮多工具和跨轮连续调用。
- 五工具真实逻辑：FAISS 检索、全文读取、比较多文档、三种出题结构、计算结果。
- 每种文档工具的当前库隔离，其他库原文与文件名不进入模型上下文。
- 未知工具、非法 JSON、额外参数、错误类型、除零、危险表达式，以及纠错后的成功执行。
- 引用原文与页码、跨工具编号稳定、错误引用拒绝、失败出题不提交引用。
- 调用次数/轮次限制、重复调用 ID、上下文超限、超时和取消、空资料结果。
- 请求验证、未知库、未使用工具、缺少服务配置、401/429/500、畸形协议响应。
- 前端五工具实际状态、完成结果和 Citation；失败恢复、调用上限、上游错误、停止、切库、移动端。

## 本次联调环境与复现

本次测试数据：`<本地测试目录>/phase4-validation.db` 和 `work/phase4-data`，不使用正式数据库。Mock 监听 `127.0.0.1:18084`，后端 `127.0.0.1:8000`，前端 `127.0.0.1:5173`。没有配置或调用真实收费 API。

本次后端进程环境：EMBEDDING_BASE_URL 与 LLM_BASE_URL 均为 `http://127.0.0.1:18084/v1`；EMBEDDING_MODEL=`TEST-ONLY-RAG`，LLM_MODEL=`TEST-ONLY-AGENT`，密钥为空，LLM_TIMEOUT_SECONDS=1.5，AGENT_MAX_TOOL_CALLS=5。正式默认上限仍是 8；测试使用 5 使五工具正常场景成功、无限循环场景触发次数限制。

完整三终端启动步骤见 README 的“Mock 本地联调”。使用独立测试目录，不要复用真实资料库。Mock 服务继承原 Phase 3 fixture 以支持原有故障场景；所有调用只到本机。`MOCK:all` 等字符串只在测试服务中选择固定协议剧本，不存在于生产 Agent 的决策逻辑。测试结束关闭本次服务，不会保留后台开发任务。

## Mock 边界与尚未验证项

本轮证明协议、编排、工具执行、隔离、错误处理、引用链路和 UI 可运行，不证明模型智能。Mock Embedding 使用合成向量，Mock LLM 使用脚本 tool_calls、固定总结/比较/题目；生成内容已明确标记。

尚未验证真实供应商认证、额度、网络、模型工具选择与参数准确性、真实语义检索、总结/比较/出题质量、引用的语义忠实度和抗提示注入能力。Provider 保留环境变量切换能力，但要求供应商支持当前 Chat Completions 工具协议以及 temperature/max_tokens；未承诺所有模型兼容。

目录仅提供最多 100 篇 ready 文档并标记截断；完整总结/比较的原文总量超限会报错，没有长文档分层总结。按字符而非模型 tokenizer 限制上下文。无自动重试、持久任务、历史或恢复执行；客户端断开会取消异步调用，已开始的只读同步检索可能短暂完成。当前库范围限制不是多用户权限系统；沿用本机单进程应用边界。

**Phase 4 完成，到此停止。**
