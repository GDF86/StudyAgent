# Phase 3 交付与实际验证报告

日期：2026-10-12。工作目录：`<项目根目录>`。

## 1. 完成内容

实现当前知识库范围的 Retriever、RAG Prompt、统一 LLM Provider、普通 Chat API、SSE API、Citation，以及 Chat 页面。支持 Markdown/代码块、Loading、错误提示、重新生成、停止和清空；切换知识库时取消旧请求并清空页面记录。保留 Phase 1/2 功能。

没有实现 Agent、Memory、Conversation/Message 持久化、Reranker、RAG 评测模块或任何 Phase 4+ 功能。页面展示的多条问答互相独立，每次请求只发送当前完整问题，不将此前回答作为上下文。

## 2. 新增/修改文件

新增后端：

- `app/rag/retriever.py`：选定知识库的 FAISS 检索、全局 Top-K、阈值和索引兼容检查。
- `app/rag/prompts.py`：RAG 消息构造、资料不足句式、引用编号校验。
- `app/llm/provider.py`、`app/llm/__init__.py`：统一异步普通/流式接口。
- `app/schemas/chat.py`、`app/services/chat.py`、`app/api/chat.py`：请求响应、问答流程和两种 API。
- `tests/test_chat.py`：本阶段 36 项参数展开后的测试。
- `tests/rag_stub_server.py`：仅测试的本地协议服务，含合成向量、固定回复、超时与错误场景。

修改后端：`app/core/config.py`、`app/main.py`、`.env.example`、`tests/make_browser_fixtures.py`。不修改已有数据库表结构。

新增前端：`src/types/chat.ts`、`src/api/chat.ts`、`src/components/MarkdownAnswer.vue`、`src/components/CitationCards.vue`、`src/views/ChatView.vue`、`src/assets/chat.css`、`tests/chat.spec.ts`、`tests/fixtures/问答课程.pdf`。

修改前端：`src/router/index.ts`、`src/main.ts`、`src/components/KnowledgeBaseCard.vue`、`src/views/DocumentsView.vue`、`package.json`、`package-lock.json`。新增依赖 markdown-it、DOMPurify 及 Markdown 类型声明。

更新 README、架构与接口说明，保存桌面/手机运行截图和本报告。

## 3. RAG 调用流程

```text
knowledge_base_id + question
→ 校验输入与知识库
→ 仅选择该库 ready 文档
→ 查询 Embedding（与构建索引时模型/维度一致）
→ FAISS 余弦相似度检索
→ 跨文档全局 Top-K + 最低分数阈值
→ SQLite 分片与来源快照
→ 系统规则 + 用户问题 + JSON 参考资料
→ 环境变量配置的 LLM Provider
→ 引用编号校验
→ 普通 JSON / SSE done + Citation
```

空库或没有达到阈值的分片时直接给出说明，不调用 LLM。上下文超限直接报错，不默默截断来源。已就绪文档的模型不兼容、索引损坏或映射不一致时也明确报错，不混用索引。

## 4. Citation 实现方式

Citation 的文件名、页码、chunk_id、原文、文档和知识库 ID 均由后端从本次检索结果生成，模型只收到这些编号。PDF 保留原始页码，非分页文档为 null。前端卡片点击展开原文，使用文本插值而非执行上传内容。

回答中的数字引用必须属于本次编号集合，且非拒答回答至少有一个有效编号。未知编号或缺少引用不会成功返回；流式错误不发 done，前端清除未确认的回答片段。引用集合是提供给模型的检索来源，不保证所有条目都在正文中使用。

这是来源/编号真实性校验，不是对每句回答进行语义蕴含验证。流式 token 在完成前属于临时文本；真实模型仍可能产生引用有效但内容不被原文支持的结论。

## 5. 已运行测试

```text
backend:  .venv\Scripts\python.exe -m pytest -q
frontend: npm.cmd run build
frontend: PLAYWRIGHT_CHANNEL=chrome，npm.cmd run test:e2e
```

后端重点覆盖：

- 普通知识库问答、全局 Top-K 排序、PDF 第 1/2 页正确来源、TXT 无固定页码。
- 当前库与其他库隔离，Prompt 不包含其他库的文本。
- 空库、无相关内容、空白/超长/null 输入、无效 Top-K、禁止 history、未知知识库。
- LLM 超时、401/429/500、缺少配置、空响应、异常 JSON 结构、输出截断。
- 伪造引用编号、缺少引用、固定句式拒答。
- SSE citation/token/done 顺序、片段拼接、超时/错误事件、无 done 的提前断流。
- 索引模型不匹配、索引丢失、上下文长度限制。
- 前两阶段的 51 项上传、解析、向量及 CRUD 回归。

浏览器包含 5 项：

1. RAG 页面：流式回答、Markdown 代码块、引用原文与页码、重新生成、普通回答、空库、无匹配、切库与清空。
2. 超时、上游错误、无效引用错误、恶意 Markdown 不执行、停止生成。
3. 前端 SSE 解析器：把 UTF-8 中文按单字节拆分，支持 CRLF，缺少 done 必须报错（此项为浏览器内组件测试）。
4. Phase 1 CRUD 回归。
5. Phase 2 四种文件上传、分片和删除回归。

## 6. 实际结果

| 检查 | 结果 |
| --- | --- |
| 完整 pytest | **87 passed, 1 warning in 7.85s** |
| Chrome / Playwright | **5 passed (17.4s)** |
| TypeScript + Vite | **通过**；131 个模块，最终构建 1.40s |
| 普通 API / SSE | 实际 HTTP 联调通过 |
| 桌面/390px 移动端 | 截图已检查，无横向溢出 |

本阶段测试没有出现失败项。实现时加入了流式结束校验、错误时丢弃未验证片段、请求版本隔离、Markdown 清理和引用检查，并验证其行为。原有 Starlette TestClient 的 httpx 弃用提示及 Node 的终端颜色提示仍存在，均非失败，未隐藏警告。

## 7. 实际联调环境与复现

实际启动 Uvicorn 8000、Vite 5173、协议测试服务 18082。数据使用 `work/phase3-validation.db` 和 `work/phase3-data`，没有操作用户正式数据。浏览器通过真实 HTTP 调用后端，Retriever 读取真实 FAISS 索引和 SQLite。

本次并未调用真实供应商的 Embedding/LLM：测试服务使用固定向量与回复，并以真实 HTTP 模拟分段 SSE、延迟与 500。它不具备语义推理能力，也不会被正式应用自动启用。

复现完整浏览器套件需要专用测试服务。三个终端，从 StudyAgent 项目根目录开始：

```powershell
# 终端一：本地协议测试服务
cd backend
.\.venv\Scripts\python.exe tests/rag_stub_server.py
```

```powershell
# 终端二：独立测试后端
cd backend
$env:DATABASE_URL = 'sqlite:///./phase3-e2e.db'
$env:DATA_DIR = './data/phase3-e2e'
$env:EMBEDDING_BASE_URL = 'http://127.0.0.1:18082/v1'
$env:EMBEDDING_MODEL = 'TEST-ONLY-RAG'
$env:EMBEDDING_API_KEY = ''
$env:LLM_BASE_URL = 'http://127.0.0.1:18082/v1'
$env:LLM_MODEL = 'TEST-ONLY-RAG'
$env:LLM_API_KEY = ''
$env:LLM_TIMEOUT_SECONDS = '1.5'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# 终端三：浏览器测试，自动启动 Vite
cd frontend
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm.cmd run test:e2e
```

如无 Chrome，可安装 Playwright Chromium 并不设置 PLAYWRIGHT_CHANNEL。端口被占用时先停掉旧的本项目服务，不要误测其他项目。结束后用 Ctrl+C 停止服务，正式启动请使用新终端，避免继承 TEST-ONLY 环境变量。

正式使用请在 backend/.env 填写自己的 Embedding 和 LLM 配置，保持 Embedding 与上传建库时一致，按 README 启动后上传非敏感样例进行验证。浏览器套件含固定故障场景，不应原样用于收费模型质量评估。

## 8. 当前仍存在的问题与边界

- 真实模型服务凭据、兼容细节、额度、实际语义检索与回答效果尚未验证。
- 编号和来源校验无法保证每句话都由原文支持；阈值默认 0.3，需要结合实际 Embedding 模型调节。
- 仍按 Phase 2 单进程、每文档索引设计运行，未做大规模负载或多 worker 验证；同名模型跨供应商的语义兼容性应由配置保持一致。
- 对话仅是当前页面展示，每次问题独立，不保存历史、不带前文；这是 Phase 3 范围限制，不是 Memory。
- 不包含 OCR、Agent、Memory、Reranker 或后续功能。本轮结束后停止。

[桌面真实截图](screenshots/phase3-chat.png) · [移动端真实截图](screenshots/phase3-mobile.png)
