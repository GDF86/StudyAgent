# Phase 7：RAG 优化与项目交付报告

完成日期：2026-10-17。开发依据《StudyAgent_Codex_开发说明书》第 17、18 节和 Phase 7（Reranker、RAG Evaluation、UI 优化、README、Demo）。基线为 Phase 6 的 360 pytest / 15 Chrome E2E。未新增收费 API、多 Agent、GraphRAG、MCP、微服务或依赖。

## 完成内容

- 默认关闭的 HTTP Reranker，开启时全局 Top 20 候选重排后取 Top-K。候选数、服务地址/模型、密钥、超时和输入预算可配置。
- 重排响应严格校验完整索引排列、数值与错误；超时/限流/未配置明确失败，不静默退回 Basic。Citation 原文、页码、chunk_id 与原向量分数不变。
- 显式 Basic/Reranked 对比 API 与双列/移动单列页面，来源可展开，错误和空状态明确；不改变全局配置或历史。可选生成临时答案用于评测。
- 六题公开合成 QA、六篇原创笔记、检索评测脚本、人工答案评分模板/汇总、可追溯 Mock 原始样例。
- README、架构图、接口文档、Demo、GitHub 展示材料和桌面/移动端截图；检查隐私文件忽略规则。

## 实际执行结果

| 验证 | 通过 | 失败 | 跳过 | 最终记录 |
| --- | ---: | ---: | ---: | --- |
| 新增 pytest | 49 | 0 | 0 | 5.50 秒 |
| 完整 pytest | 409 | 0 | 0 | 38.26 秒 |
| TypeScript / Vite build | 成功 | — | — | 退出 0；Vite 1.34 秒 |
| 新增 Chrome E2E 单独回归 | 2 | 0 | 0 | 6.3 秒 |
| 完整 Chrome E2E | 17 | 0 | 0 | 最后一次 39.4 秒 |
| Mock 评测采集 | 6 题 × 2 模式 | 0 分支错误 | 0 | 已执行 HTTP / SQL / FAISS / Mock 生成 |
| 答案模板与空评分汇总 CLI | 成功 | 0 | — | 每模式 scored=0/6，accuracy=null |

均为本阶段实际执行，不沿用旧阶段数量。完整 pytest 后只调整新页面样式及文档，后端代码未再变化；最终前端构建与完整 E2E 已覆盖最终页面。既有测试断言未删改或降低。1 条已有 Starlette/httpx 弃用警告与 Node NO_COLOR/FORCE_COLOR 提示仍存在，不影响通过。

原始日志保存在本地忽略目录：

- `backend/test-results/phase7-new.log`
- `backend/test-results/phase7-full.log`
- `frontend/validation-logs/phase7-build.log`
- `frontend/validation-logs/phase7-new-e2e.log`
- `frontend/validation-logs/phase7-e2e.log`
- `backend/test-results/phase7-delivery-check.json`

实际命令：

```powershell
# backend
.\.venv\Scripts\python.exe -m pytest tests/test_phase7_reranker.py tests/test_phase7_evaluation.py -q
.\.venv\Scripts\python.exe -m pytest -q
# frontend，本地 Mock 与后端已启动
npm.cmd run build
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm.cmd run test:e2e -- tests/phase7.spec.ts
npm.cmd run test:e2e
# 项目根目录
backend\.venv\Scripts\python.exe -m evaluation.evaluate_retrieval --seed-corpus evaluation/corpus --environment mock --provider-note "Local deterministic TEST-ONLY-RAG / TEST-ONLY-AGENT / TEST-ONLY-LEXICAL; synthetic protocol validation only" --top-k 1 --include-answers --output evaluation/results/phase7-mock.json
backend\.venv\Scripts\python.exe -m evaluation.evaluate_answer --results evaluation/results/phase7-mock.json --output evaluation/results/phase7-review-template.json
backend\.venv\Scripts\python.exe -m evaluation.evaluate_answer --results evaluation/results/phase7-mock.json --grades evaluation/results/phase7-unscored-review.json --output evaluation/results/phase7-answer-summary.json
```

空评分 review 从该模板生成，reviewer 明确标记未进行语义评分，scores=[]。没有把未评分答案填成满分。

## 新测试覆盖与开发中修正

`test_phase7_reranker.py` 验证默认关闭不调用重排、扩大候选后改变排序、全局候选上限 20、同分稳定排序、Citation 不变、响应缺项/重复/越界/布尔与浮点索引、非有限分数、非 JSON、预算、HTTP 401/429/500/307、超时/断连、密钥和错误体不回显、空库短路、对比不修改全局开关、分支错误隔离、参数校验、Agent 真实 search 工具及 Chat 持久化引用。

`test_phase7_evaluation.py` 验证文件和页码精确命中、失败计入分母、没有评分时准确率为空、部分评分覆盖量、非法/重复/未知/失败答案评分拒绝、run_id 绑定、数据集唯一性、网络失败记录与公开语料存在。

`phase7.spec.ts` 验证真实 API/数据库/FAISS/Mock 重排和可展开来源、390px 移动布局、空库及网络失败后的恢复；原有 15 项回归保持通过。

首轮新增 pytest 为 48 通过 / 1 失败：新测试向 Agent 工具传对象，而工具协议要求 JSON 字符串。修正测试调用后 49 通过，无业务规则改变。首轮完整 E2E 为 15 通过 / 2 失败：新页面选择控件的标签包含选项文本，严格按名称定位失败。给控件设置明确 aria-label 后两个新用例通过；没有放宽断言、增加测试超时或跳过测试。最终完整 E2E 为 17 通过。

## Mock 评测数据及解释

原始结果：[phase7-mock-results.json](phase7-mock-results.json)。数据来自 `evaluation/corpus/` 的六篇项目原创英文短笔记及 `qa_dataset.json` 的六个问题，非私人文档，非公开模型基准。Run ID：09c50a4028994eed8ad0143973de0286。数据集 SHA-256：59776584b5198df06101970ff1b52184d4f21efd891180b4feb241e44586d875。

| 指标（仅合成 Mock，本次运行） | Basic | Reranked |
| --- | ---: | ---: |
| Hit@1 | 2/6（33.33%） | 6/6（100%） |
| 分支错误 | 0 | 0 |
| 平均响应时间，含 Mock 生成 | 215.49 ms | 385.87 ms |
| 人工已评分 / 总数 | 0/6 | 0/6 |
| Answer Accuracy | null（未验证） | null（未验证） |

Embedding 仅用 ALPHA/BETA 生成固定向量；Reranker 是英文词重合脚本；LLM 复制来源文本。上述数字是实测协议框架结果，不能解释为真实模型的检索提升率或性能。没有计算或宣称真实提升百分比，也没有声称答案准确率。

两分支顺序执行，未预热/随机化，非单一不可变快照；Mock 同分 UUID 排序和负载变化会影响重复运行。评测只统计文件/页码命中，不能代表充分证据覆盖或语义蕴含。完整指标定义、命令和手动评分流程见 [评测说明](../evaluation/README.md)。

## 实际修改文件

修改：

- `.gitignore`
- `backend/.env.example`
- `backend/app/core/config.py`
- `backend/app/rag/retriever.py`
- `backend/app/main.py`
- `frontend/src/App.vue`
- `frontend/src/router/index.ts`
- `README.md`
- `docs/architecture.md`
- `docs/api.md`

新增：

- `backend/app/rag/reranker.py`
- `backend/app/api/comparison.py`
- `backend/app/schemas/comparison.py`
- `backend/tests/test_phase7_reranker.py`
- `backend/tests/test_phase7_evaluation.py`
- `backend/tests/phase7_stub_server.py`
- `frontend/src/views/ComparisonView.vue`
- `frontend/tests/phase7.spec.ts`
- `evaluation/__init__.py`
- `evaluation/metrics.py`
- `evaluation/evaluate_retrieval.py`
- `evaluation/evaluate_answer.py`
- `evaluation/qa_dataset.json`
- `evaluation/README.md`
- `evaluation/corpus/seedling.txt`
- `evaluation/corpus/battery.txt`
- `evaluation/corpus/telescope.txt`
- `evaluation/corpus/archive.txt`
- `evaluation/corpus/sensor.txt`
- `evaluation/corpus/backup.txt`
- `docs/phase7-mock-results.json`
- `docs/phase7-testing.md`
- `docs/demo.md`
- `docs/github-showcase.md`
- `docs/screenshots/phase7-comparison.png`
- `docs/screenshots/phase7-comparison-mobile.png`

完整 E2E 也重新生成了既有阶段的展示截图。dist、数据库、索引、Playwright 结果和日志为被忽略的运行产物。没有修改原有测试文件，也没有修改依赖清单。父级 Git 将本项目整体视为未跟踪，不能把空 git diff 当成没有变化。

## GitHub 交付检查

实际检查 15 条应忽略路径与 7 条应允许路径，均符合预期。包括 .env、数据库、上传数据、私有 Markdown 目录、PDF/DOCX/TXT、密钥、生成测试 fixtures、评测输出和日志。六篇公开合成语料为明确白名单。对当时 152 个候选文件的常见 API Key / 私钥模式扫描无命中；仅为当前文件检查，不覆盖 Git 历史、所有秘密格式或人为强制添加。

README、架构图、API、Demo、桌面/移动端截图和展示文案已准备；没有创建 GitHub 远程仓库、上传私人资料或推送代码。发布者仍应检查暂存内容和许可证。

## 尚未完成的真实验证

- 未连接真实 LLM / Embedding / Reranker，供应商协议、认证、中文语义与真实模型效果未验证。
- 尚无人工盲评答案或代表性真实资料测试集，准确率未验证；不以 Mock 数字替代。
- 未验证真实服务延迟分布、吞吐、费用、多 worker 或大规模语料。同步重排持有文档锁，外部调用会延长等待；浏览器取消等待不保证同步重排立即停止。
- 对比期间修改资料可能造成两次检索快照不同，正式实验需冻结语料。
- 仅测试 Chrome，未覆盖 Firefox/WebKit；没有物理断电/磁盘损坏实验或完整安全审计。

Phase 7 的代码、框架、UI、文档和本地 Demo 已完成并通过本地 Mock 验收；真实模型效果验证明确留待实际模型与人工标注条件具备后开展。本阶段到此停止。
