# Phase 6 系统测试与异常测试报告

文档收尾：2026-10-17。依据《StudyAgent_Codex_开发说明书》的 Phase 6 范围，保留 Phase 1–5；不增加 Reranker、RAG Evaluation、Memory 新功能、多 Agent 或 Phase 7。

## 结果与证据来源

开发前基线为 301 项 pytest、10 项 E2E。以下是此前本任务真实执行的最终结果，不是本次重新运行结果。本次核对源码、测试文件时间、构建产物及 Playwright 最后运行标记，只更新 README 和本报告。

| 验证 | 通过 | 失败 | 跳过 | 已记录耗时 |
| --- | ---: | ---: | ---: | --- |
| 新增后端测试 | 59 | 0 | 0 | 9.63 秒 |
| 完整 pytest | 360 | 0 | 0 | 35.37 秒 |
| TypeScript / Vite build | 成功（退出码 0） | — | — | 最后一次 Vite 1.32 秒 |
| 新增 Chrome E2E | 5 | 0 | 0 | 10.3 秒 |
| 完整 Chrome E2E | 15 | 0 | 0 | 最后一次 35.4 秒 |

执行输出记录：新增 pytest 会话 10252、完整 pytest 32272、新增 E2E 47603、最后完整 E2E 22820，均退出成功。网络中断后曾再次执行构建与完整 E2E；完整 pytest 后没有后端代码修改，因此沿用 360 passed 的有效结果。最后构建产物时间为 2026-10-15 20:57:52，晚于相关源码修改时间。`frontend/test-results/.last-run.json` 为 `status: passed`、`failedTests: []`；该标记不能独立证明用例总数，数量和耗时来自此前执行输出。没有将会话摘要伪造成原始日志文件，也没有生成代码覆盖率百分比。

完整 pytest 有 1 条已有 Starlette/httpx 弃用警告；浏览器运行有 NO_COLOR/FORCE_COLOR 提示，不影响通过。未因此修改依赖。

## 新增测试与风险对应

| 文件 | 数量 | 验证场景与风险 |
| --- | ---: | --- |
| `backend/tests/test_phase6_execution.py` | 11 | 同会话同/不同 request_id 并发拒绝、成功结果并行重放及单次模型执行；不同会话并发；完成/取消两种确定性交错；预留、完成及失败状态的 DBAPI COMMIT 故障与回滚/锁释放；遗留 pending 恢复；流中断；保存后 done 丢失的重放；子进程 os._exit 后启动恢复 |
| `backend/tests/test_phase6_memory.py` | 8 | 默认 10 轮的 9/10/11 边界；只摘要新增旧轮次；多批摘要的原子水位及中途失败恢复；字符预算触发；Agent 跨轮摘要/上下文与三项偏好；不同答案重新生成后选择最新成功尝试；Citation/工具元数据落库及源文档删除后的快照 |
| `backend/tests/test_phase6_rag.py` | 15 | 相似度阈值上下及精确边界；同分稳定排序和 Top-K；非 ready 文档排除；实际 FAISS 文件损坏、数量/维度/向量 ID 不匹配、SQL 分片缺失及非有限索引值在调用 LLM 前拒绝 |
| `backend/tests/test_phase6_faults.py` | 25 | 原文件部分写入、向量替换和 ready 提交失败清理；文件删除及最终提交失败恢复；畸形 Embedding 数值/索引；非法题目结构与引用；认证头实际发送且错误响应/日志不泄露哨兵密钥；无 Content-Length 分块超限上传 |
| `frontend/tests/phase6.spec.ts` | 5 | 21 个会话分页/过滤；51 轮产生 102 条真实消息的分页读取；切换会话实际中止旧读取且迟到错误不污染新页面；偏好部分保存及恢复默认失败；真实保存后丢弃 done 并刷新/同 ID 重放；慢 SSE 实际浏览器取消、刷新及新请求 |

合计新增 59 项 pytest、5 项 E2E。P0 六类和 P1 四类均有针对性测试；数量不是完整覆盖所有执行路径的声明。此前对 13 个原有测试源码进行了哈希核对，未发现变化；没有删除、跳过或弱化原有断言。

## 测试发现并修复的真实缺陷

1. **pending 恢复被回滚**：同 request_id 重试在恢复 pending 的同一事务内返回冲突，导致恢复状态随异常回滚。现在在会话锁内先独立提交恢复，再处理请求，确保 interrupted 持久化。
2. **文档删除提交失败未显式回滚**：最终删除提交失败后，复用连接可能观察到未提交删除。现在失败时明确 rollback，保留已提交的 deleting 标记，允许恢复继续清理。
3. **Embedding 索引错误接受布尔/浮点数**：Python 的 False/0.0 与 0 相等，原顺序检查不足。增加严格整数类型检查，保持合法协议不变。
4. **切换会话未取消历史读取**：原版本仅屏蔽旧成功响应，旧请求仍执行且错误可能污染新会话。历史 API 传递 AbortSignal，会话视图取消旧读取，并忽略取消/过期错误。

此前失败记录包括 pending 恢复、删除提交、布尔/浮点索引以及浏览器旧请求未取消断言；修复后相关子集和最终完整套件均通过。未为通过测试改变业务规则。

## 实际修改文件

生产代码：

- `backend/app/services/conversation_execution.py`
- `backend/app/services/documents.py`
- `backend/app/rag/embeddings.py`
- `frontend/src/api/history.ts`
- `frontend/src/views/ConversationView.vue`

测试：上述四个新增 pytest 文件、`frontend/tests/phase6.spec.ts`，以及修改 `backend/tests/rag_stub_server.py`（仅测试协议 `PHASE6:SLOW` 将流式间隔设为 0.4 秒，便于在实际 token 后取消；普通场景仍为 0.12 秒）。

文档：更新 `README.md`，新增 `docs/phase6-testing.md`。本次收尾只修改这两个文档。构建目录、Playwright 结果/截图及隔离测试数据库/数据目录属于运行产物。父级 Git 将项目整体显示为未跟踪，不能用该状态冒充逐文件历史差异。

## 测试环境与复现

沿用现有 Python 虚拟环境、Node 依赖与本机 Chrome，不增加依赖，不修改正式 `.env`，不配置真实 API。浏览器使用真实 Vite、FastAPI、SQLite、FAISS 和既有本地 Mock LLM / Embedding。

在 backend 目录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase6_execution.py tests/test_phase6_memory.py tests/test_phase6_rag.py tests/test_phase6_faults.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

按 README 的三个终端配置启动 `tests/agent_stub_server.py`（18084）和单 worker 后端（8000），隔离数据库 `sqlite:///./phase6-e2e.db`，目录 `./data/phase6-e2e`；两个 Provider 地址均为 `http://127.0.0.1:18084/v1`，测试模型分别为 `TEST-ONLY-AGENT`、`TEST-ONLY-RAG`，密钥为空。浏览器测试要求 `LLM_TIMEOUT_SECONDS=1.5`、`AGENT_MAX_TOOL_CALLS=5`、`MEMORY_WINDOW_TURNS=2`；默认 10 轮边界由后端测试覆盖。

在 frontend 目录：

```powershell
npm.cmd run build
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm.cmd run test:e2e
```

数据库故障是在真实 SQLAlchemy/SQLite DBAPI COMMIT 边界注入 `sqlite3.OperationalError`，验证真实 SQL、事务、回滚与后续连接行为，不代表物理磁盘断电实验。pending 恢复包含真实子进程 `os._exit(23)`。浏览器取消使用实际请求；done 丢失是在真实后端提交后拦截响应，偏好失败也是明确的网络故障注入。固定 Mock 回答不代表模型智能或语义质量。

## 未覆盖风险及验收结论

- 未模拟整机断电、物理磁盘损坏；COMMIT 故障注入和进程退出不能替代硬件故障验证。
- 当前仅支持单进程锁设计；未验证多 worker、多用户隔离或所有并发交错。确定性竞争用例不等于压力测试。
- 浏览器验证仅 Chrome；未验证 Firefox/WebKit。21 个会话和 102 条消息覆盖分页，不证明大规模性能。
- 未接入真实模型，供应商兼容、语义检索、摘要/题目质量及抗提示注入能力未验证；本阶段禁止真实 API 和 Evaluation 开发。
- 日志测试仅证明所测哨兵密钥和错误场景不泄露，不构成完整安全审计；未生成行/分支覆盖率。

在上述明确支持范围内，Phase 6 已达到说明书的 pytest、API、异常处理与 RAG 测试验收要求。Phase 1–5 回归通过，未开发 Phase 7。收尾完成后停止，等待下一条指令。
