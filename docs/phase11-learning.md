# Phase 11：真实学习模式集成验收

验证日期：2026-11-11。范围仅为开发目录，未修改独立 GitHub 发布副本。

## 根因与修复

| 模块 | 原因 | 处理 |
| --- | --- | --- |
| 历史会话 | API、数据库和执行服务已存在，但受控网页白名单拒绝会话写入与携带 conversation_id 的问答；历史服务另有 HISTORY_FORBIDDEN | 精确开放会话创建、改名、确认删除、取消；仅持久化网页问答的请求上下文开启 history_enabled |
| 学习偏好 | 读取正常，PUT/DELETE 被白名单拒绝；临时 RAG/Agent 未注入当前偏好 | 复用 PreferenceService 和原数据库；每次 prepare 读取最新偏好，注入固定枚举指令，持久化会话仍使用原有上下文服务 |
| 检索对比 | POST 未在白名单内；未配置重排时可能先进行第二次查询向量化再失败 | 开放受控 compare 接口；真实网页在重排分支提前返回 RERANKER_NOT_ENABLED，不调用或伪造重排 |

未移除 loopback、Host、Origin、有效开发会话、写权限、删除确认或费用 Guard。取消接口在相同权限校验后独立处理，避免被生成请求占用的 busy 锁挡住。

## 功能与费用语义

- 会话创建、读取、改名、删除与偏好读写：数据库操作，不调用模型。删除会话仍需页面确认及确认头。
- 普通问答与 SSE：使用原 request_id 幂等、消息状态、取消、失败恢复及引用快照；相同已完成请求不会重复调用模型。
- 偏好为**本机数据库全局**设置，覆盖所有知识库与会话，下一次请求立即读取。默认中文；支持简洁/详细及通用学习/考试复习/研究学习。当前不支持自由文本学习目标或多用户隔离。
- 偏好只影响表达与侧重点，不扩大检索、工具权限或事实来源。
- 长历史摘要是**真实模型调用**，与问答共用当前 Stage 和累计账本。网页最近窗口 4 轮、上下文 2500 字符、摘要最多 600 字符、单批摘要输入 1800 字符、每次最多 2 批；过大单轮/上下文会拒绝并保留原记录。
- 摘要上限是逻辑调用数；传输层若符合现有 429/5xx/瞬时网络错误规则，最多一次重试，每次重新经过 Guard 并占用预算。取消或异常中断可能使受控会话停止，不能保证已发送请求免费；不得静默重放。
- 检索页默认不生成答案、不保存历史，Basic 查询发生一次 Embedding；显示真实 Chunk 原文、相似度与实际顺序。未启用 Reranker 时没有重排结果，也无法评价排序改善。
- 保留累计 ¥50 风险预算、¥30 提醒、请求前原子预扣、跨进程账本、输出限制、有限重试与 Agent 循环。未增加单轮金额限制。

## 离线验证

后端全量 **596 passed**，1 项已存在的 Starlette/httpx 弃用警告。所有用例通过禁止外网传输的入口执行，并使用临时数据库/Mock HTTP：

```powershell
cd backend
.\.venv\Scripts\python.exe tests/run_offline_safety.py tests -q -p no:cacheprovider --basetemp=test-results/phase11-final-all
```

覆盖本机权限、未启用写权限、错误 Origin、停止会话、保存及重复请求、多轮上下文、偏好 Prompt、Agent 历史、摘要 Guard、忙碌期间取消接口准入；既有执行服务测试覆盖取消/中断后的消息一致性。

前端构建通过，离线浏览器 **10 passed**，并在来源顺序断言修正后单独重跑检索用例通过：

```powershell
cd frontend
npm run build
npx playwright test --config playwright.offline.config.ts
```

## 小规模 Real 验收

复用 Bailian-RAG-Live-Test 两篇公开合成资料，没有上传或重新索引私人 Word 文档。新增两个公开验证会话，保留供人工查看。

1. 保存 detailed/exam/zh-CN，刷新恢复；创建资料会话，非流式回答并保存引用。
2. 刷新恢复首轮记录；改为 concise，第二轮 SSE 使用历史上下文；再次刷新保留两轮。
3. Basic 返回两篇真实资料，按文档名展开相应 Chunk；重排栏明确未启用。
4. Agent 会话 calculator 返回 `125.0`，持久化元数据记录 `tool_calls: 1`，刷新保留。

详细回答覆盖 SA-2026、深圳、RAG-1024，简洁回答为：

```text
测试城市：深圳 [2]
测试编号：RAG-1024 [2]
```

**质量边界：**详细回答还包含对“唯一标识、地理模拟、追踪用途”的扩展解释，这些细节未全部由合成资料直接支持。此次验收证明偏好、持久化和接口链路工作，不证明逐句事实正确；后续应开展有标准答案的忠实度评估，不能因附带引用就认定所有表述受资料支持。

首次检索脚本错误地假定第一名必为 SA-2026 文档，断言失败后停止。实际第一名是另一篇同知识库资料，并非混库。修正为按文档名匹配，在离线验证后只补验比较与 Agent；增加一次查询 Embedding，没有重跑问答。另一次恢复脚本在读取被 Playwright 清理的报告时本地退出，未发送请求；证据移至独立目录后完成。没有网络自动重试。

### Token 与费用

本次开始前只读基线为 **62 次**真实请求，结束 **70 次**。历史 1–62 行逐字段与基线比较一致；3 次历史 UNKNOWN、全部失败记录及 seal 保留。旧 READY 会话仅在服务退出且无 PENDING 时，通过已有 complete_development_session 合法结束；未复活 HALTED 轮次。

| 序号 | 用途 | 模型 | HTTP | 输入 Token | 输出 Token | 原价估算 ¥ |
| --- | --- | --- | --- | ---: | ---: | ---: |
| 63 | 普通会话查询 | text-embedding-v4 | 200 | 37 | 0 | 0.00001850 |
| 64 | 普通会话回答 | qwen-flash | 200 | 617 | 361 | 0.00063405 |
| 65 | SSE 会话查询 | text-embedding-v4 | 200 | 28 | 0 | 0.00001400 |
| 66 | SSE 会话回答 | qwen-flash | 200 | 1069 | 25 | 0.00019785 |
| 67 | Basic 比较 | text-embedding-v4 | 200 | 19 | 0 | 0.00000950 |
| 68 | 修正来源断言后的 Basic 补验 | text-embedding-v4 | 200 | 19 | 0 | 0.00000950 |
| 69 | Agent 工具决策 | qwen-flash | 200 | 1159 | 26 | 0.00021285 |
| 70 | Agent 工具结果回答 | qwen-flash | 200 | 1230 | 9 | 0.00019800 |

- 新增 **8 次：4 LLM + 4 Embedding**，输入 4178、输出 421 Token。没有摘要、Reranker 或意外隐藏请求。
- 本次已知原价估算 **¥0.00129425**。
- 项目已知原价估算 **¥0.02218315**；3 次 UNKNOWN 的原风险预留 **¥0.012288**，未知实际扣费未改成零。
- 含早期保守预留的累计风险占用 **¥2.46291630 / ¥50**，剩余风险空间 **¥47.53708370**，未到 ¥30 提醒。
- 价格依据：北京 qwen-flash 输入≤128K，输入 ¥0.15/百万、输出 ¥1.50/百万；text-embedding-v4 输入 ¥0.50/百万。参见[官方 Qwen Flash 价格](https://help.aliyun.com/zh/model-studio/qwen-flash)及[官方模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)。请求 Guard 上界保持≤64K，并非按模型最大上下文预留。

费用为服务端 usage 按公开原价计算，未假定免费额度抵扣；本地预算不是阿里云账户级消费硬限额。

## 人工使用

前端地址：`http://127.0.0.1:5189`；后端仅 `127.0.0.1:8010`。

1. 学习偏好：选择风格、目标和语言，保存后可刷新验证。当前验收后为“简洁 / 考试复习 / 中文”。
2. 历史会话：输入标题，选择知识库与模式，创建；发送后等待完成再切换，刷新可恢复。资料回答可切换普通/SSE；重新生成是新模型请求。
3. 检索对比：选知识库、输入问题；点击会调用查询 Embedding。展开来源原文；当前没有真实 Reranker，不会暗中开启新服务。
4. 删除会话需确认；停止请求保留状态，不代表撤销已产生费用。失败/取消不可静默重复执行。

本次已通过原数据库和原账本重启后端，新代码生效；前端开发服务器使用 HMR，刷新即可。服务默认关闭真实模式，后续需要启动时仍使用现有显式启动入口，session-name 必须唯一，不能复用、清空账本或删除 seal。价格确认有效期 1 小时、开发会话 2 小时；过期会明确拒绝，应合法结束旧服务并新建会话。

本地证据位于被忽略的 `backend/test-results/phase11-final-budget.json`、`frontend/test-results/phase11-evidence/`，不作为公共运行数据提交。公开源码、测试和本文可在单独安全审查后同步发布副本；本阶段没有修改该副本，也没有 commit/push。

## 修改文件

- 后端：`app/core/web_demo.py`、`app/services/conversation_memory.py`、`app/services/preferences.py`、`app/services/chat.py`、`app/agent/agent.py`、`app/api/comparison.py`、`app/main.py`、`tests/start_web_demo.py`、`tests/test_web_demo.py`、`tests/test_phase11_learning.py`。
- 前端：`src/api/history.ts`、`src/views/PreferencesView.vue`、`src/views/ComparisonView.vue`、`src/views/ConversationView.vue`、`tests/demo-contract.spec.ts`、`tests/phase11-real.spec.ts`、`playwright.phase11.config.ts`。
- 文档：`docs/phase11-learning.md`。

真实验收脚本默认 skip，必须设置显式授权变量，不属于默认离线测试入口。不要为普通回归开启真实验收。
