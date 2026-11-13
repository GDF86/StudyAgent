# 前端真实模型 Demo 验收准备

> 历史准备记录，已由 [Phase 8 实测报告](phase8-web-demo.md)取代当前状态判断。文中“入口未实现”“截图待补”描述的是准备阶段；受控入口及六张Real截图现已完成。仅保留当时的离线结果与场景清单，不作为当前启动授权。

检查日期：2026-11-10。本次仅离线检查、构建及 Mock 验证；真实模型请求0次，新增模型费用¥0。

## 当前结论与启动方式

Vue3 + Vite 前端可构建，FastAPI相关接口离线测试通过。**真实网页 Demo 尚有阻断项：普通 HTTP 请求没有受控 Stage/开发账本上下文，模型出口会拒绝执行。** 已验证的真实模型测试脚本在进程内显式绑定 Ledger + Stage，不能据此认定普通 Uvicorn 的网页请求也具备相同授权。

不得关闭 Guard、把 live_test_mode 简单改为 false，或直接调用模型绕开保护。下一步应增加仅本机启用的受控 Demo 入口/中间件：显式选择新开发会话、绑定每个请求的 Stage、工具白名单、输入/输出/请求数/时间上限、错误熔断；正确覆盖线程池及 SSE 生成器完整生命周期。它必须复用现有持久化账本，并经过取消、并发、错误和费用测试。本次未实现或自动启用该入口。

以下为现有启动方式，**启动只用于页面和本地数据浏览，不代表授权模型调用**：

```powershell
Set-Location backend # 从仓库根目录开始
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
Set-Location frontend # 在另一个位于仓库根目录的终端
npm run dev
```

打开 http://127.0.0.1:5173 。Vite固定5173且strictPort；不存在开发反向代理，浏览器直接请求API。若端口已有服务，先确认服务身份和配置，不强制终止、不复用未知旧进程。

- API地址：`VITE_API_BASE_URL`，默认 `http://127.0.0.1:8000`，见frontend/.env.example。Vite启动时读取，修改后需重启；生产构建时写入产物。
- 后端配置：固定backend/.env，操作系统同名环境变量优先，修改配置后启动新进程。不得将API Key放入VITE变量、浏览器或截图。
- CORS默认允许localhost:5173和127.0.0.1:5173；改端口需同步后端允许列表。前端preview通常4173，不能假定默认CORS已允许。
- 当前真实测试隔离库：backend/studyagent-bailian-20261109.db；目录：backend/data/bailian-real-20261109。启动前确认有效配置指向此处，不能使用默认studyagent.db覆盖原Mock状态。
- 已有测试知识库：Bailian-RAG-Live-Test，历史ID为2；实际操作以名称核对，不依赖ID永久不变。含bailian-rag-fixture.txt和bailian-second-fixture.txt，两篇均已有真实1024维索引。
- 预算账本：backend/test-results/bailian-first-live/budget.sqlite及其seal；保留全部历史记录。本次不初始化、不迁移、不写入。

## 页面与契约核对

| 功能 | 路由/事件与界面行为 | 模型调用 |
| --- | --- | --- |
| 知识库管理 | /knowledge-bases；新建/编辑/删除确认；列表与侧栏保留 | 无；删除会修改本地数据，不用于保留资料的Demo |
| 文档管理 | /knowledge-bases/:id；列表、上传、状态、详情、删除确认 | 列表/详情无；上传解析后会Embedding，不能作为零费用浏览 |
| 普通RAG | /knowledge-bases/:id/chat；取消“使用流式回答”，POST /api/chat | 通常查询Embedding+LLM |
| 流式RAG | 同页，POST /api/chat/stream | 同上；citation/token/done/error事件 |
| 来源引用 | CitationCards展开原文、文档名和页码；Markdown净化并禁用模型图片加载 | 展开/切换引用无 |
| Agent | /knowledge-bases/:id/agent；POST /api/agent/stream | 规划/最终LLM；工具内部可能追加调用 |
| Agent进度 | status的model/tool_started/tool_succeeded/tool_failed；done携带answer/citations/tool_calls计数 | 状态显示本身无 |

tool_calls在完成响应中是**次数整数**，不是原始LLM tool_calls数组；页面展示状态和次数，不能仅凭截图证明真实Tool Calling。原始工具往返证据见此前真实测试JSON报告。

修复：RAG最终响应新增citation知识库归属检查，覆盖非流式与SSE done；Agent完成事件新增answer类型与tool_calls非负整数校验。原有SSE读取支持UTF-8、CRLF、跨网络分块，缺少done/错误事件不显示任务完成；切库及卸载取消旧请求。

## 可人工执行的 Demo 顺序

**前置：受控网页入口完成并通过离线测试、人工确认新会话和风险预算后，才执行下面标为模型调用的步骤。当前这些步骤是待执行清单。**

1. 打开首页和知识库列表，进入Bailian-RAG-Live-Test。展示两篇就绪文档及分片详情。纯本地浏览，不重新上传已有资料。
2. 打开资料问答，取消流式，问“第一份测试资料的项目代号是什么？请引用来源。”【模型调用】期望SA-2026，展开[1]核对文档和原文。
3. 开启流式，问“第一份测试资料的城市和编号是什么？请引用来源。”【模型调用】观察逐步输出及完成状态，期望深圳、RAG-1024。展开引用不额外调用模型。
4. 打开Agent，输入“请使用 calculator 工具计算 (125+375)/4，并告诉我结果。”【模型调用】期望工具开始→成功→任务完成、结果125，纯计算可以没有引用。
5. 分别演示检索项目代号、总结第一篇、比较两篇、根据资料生成两道选择题。【每次均模型调用】比较应区分SA-2026/深圳/RAG-1024与SA-2027/广州/RAG-2048，第二篇新增学习进度分析；题目答案必须与来源一致。
6. 截图完毕停止，不自动重新生成、重试或批量执行。重新生成是新的付费调用；停止请求不会返还已发出请求费用。

| 操作 | 正常最短调用链，不含受控重试 |
| --- | --- |
| calculator | 2 LLM |
| search_documents | 2 LLM + 1 Embedding |
| summarize_document | 3 LLM |
| compare_documents | 3 LLM；复用索引无需Embedding |
| generate_quiz | 3 LLM + 1 Embedding |

持久化会话、历史记忆、检索对比页、学习偏好先作为本地浏览展示，不纳入首次真实网页演示。会话发送/重生成可能触发额外记忆机制；检索对比提交可能有多个检索分支，不把它当作普通一次问答。上传文档的分片批次也必须另算预算。

## 尚未覆盖的实际用户操作

- 真实浏览器→受控后端→百炼的完整网页链路及SSE预算上下文生命周期。
- 真实网络断开/停止后服务端是否结束、已收费请求如何记账；多标签页及并发点击。
- PDF/DOCX/大文件、空/损坏/重复文档、上传进度100%后仍处理、上传中离开页面。
- 未就绪或空知识库、低相关性问题、无效引用、401/403/429/5xx和价格/预算拒绝的真实页面提示。
- 持久化会话的刷新恢复、重生成、删除确认、偏好和记忆成本；多工具连续规划及异常恢复。
- 移动端、键盘操作、长表格/公式/代码、长引用、两份文档的来源切换。

上述异常优先使用Mock；本次没有发出真实请求来触发这些错误。

## 离线复核与截图清单

```powershell
# frontend目录；系统Edge，独立5188端口，浏览器全部API拦截为Mock
npm run build
npx playwright test --config playwright.offline.config.ts

# backend目录；禁用真实dotenv和外部HTTP，临时数据库
.\.venv\Scripts\python.exe tests/run_offline_safety.py tests/test_chat.py tests/test_agent.py tests/test_documents.py tests/test_knowledge_base.py -q -p no:cacheprovider
```

本次：构建通过，浏览器5项通过，后端137项通过。浏览器测试使用路由Mock，后端测试使用Mock HTTP：这是两侧契约与UI交互验证，不冒充完整浏览器直连后端验收。不要直接运行其他会连接默认8000后端的旧E2E用例，以免写入既有数据。

还需人工补拍并注明真实/Mock来源：①首页与侧栏；②测试库两篇文档就绪；③普通RAG回答+展开原文；④SSE生成中与完成；⑤calculator执行事件与结果；⑥比较两文档及双来源；⑦两道选择题与答案；⑧Mock错误/停止提示；⑨移动端。不得拍摄.env、密钥、Authorization、控制台密钥管理页。仓库已有旧截图不能直接标注成本次真实网页截图。

GitHub整理可先进行目录、运行说明、真实后端证据与已知限制整理；正式宣称“真实模型网页Demo验收完成”应等待受控入口及人工截图验收。不把当前Mock UI截图冒充真实模型结果。
