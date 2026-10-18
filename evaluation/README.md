# RAG Evaluation

本目录提供框架与项目原创的公开合成数据，不是经过外部验证的模型基准。`qa_dataset.json` 含 6 个问题、预期文件名/页码和参考答案，`corpus/` 的 6 篇英文笔记是可公开的人工编写测试资料。PDF 页码从 1 开始；TXT/MD 的 expected_page 为 null，匹配时同样要求返回 page=null。

## 本地 Mock 复现

先按项目 README 启动 Phase 7 Mock 服务和后端。所有命令在项目根目录执行：

```powershell
backend\.venv\Scripts\python.exe -m evaluation.evaluate_retrieval --seed-corpus evaluation/corpus --environment mock --provider-note "Local TEST-ONLY-RAG, TEST-ONLY-AGENT, TEST-ONLY-LEXICAL" --top-k 1 --include-answers --output evaluation/results/mock.json
backend\.venv\Scripts\python.exe -m evaluation.evaluate_answer --results evaluation/results/mock.json --output evaluation/results/review.json
```

`--seed-corpus` 只创建一个新知识库，上传预期文档，并在 finally 删除这个新库；不会删除已有库。为保证可控开销，仅在本地 Mock 环境演示此命令。若已有资料，可用 `--kb-id <ID>` 替换 `--seed-corpus`，不会上传或删除该库。不要在评测过程中修改语料。`--include-answers` 会实际调用当前后端 LLM；省略时仅测检索，不生成答案。

修改 review.json：填写真实 reviewer，按参考答案及原文逐条打分，删除尚未评分的条目，然后执行：

```powershell
backend\.venv\Scripts\python.exe -m evaluation.evaluate_answer --results evaluation/results/mock.json --grades evaluation/results/review.json --output evaluation/results/answer-summary.json
```

评分为 0=错误、1=部分正确、2=正确；null 是未评分，不接受为数值。重复条目、无效分数、缺失答案和失败答案拒绝评分。review 必须匹配 run_id 与结果文件 SHA-256，修改原始答案后旧评分失效。空 scores 合法，准确率为 null、scored=0、complete=false，不能当作完成了人工评审。

## 指标定义

- Retrieval Hit Rate@K：Top-K 中至少一个来源文件名和页码匹配 expected_document/expected_page 的问题数 / 全部问题数。失败计入分母，不丢弃困难或失败样本。使用 include-answers 时分支生成失败也按完整 RAG 失败处理；单独评价检索应省略该参数。
- Answer Accuracy：显式人工评分之和 / (2 × 已评分答案数)。同时报告 scored、total、complete；零评分为 null，部分评分不能视作全量准确率。不用关键词匹配、LLM 自评分或固定 Mock 回答伪造人工结论。
- Response Time：perf_counter 实测的每分支服务端毫秒数均值，包括失败尝试；默认检索（包含重排），include-answers 时加上生成。HTTP 请求整体失败时记录客户端失败等待时间，并标记 EVALUATION_REQUEST_FAILED。顺序固定为 Basic 后 Reranked，没有预热/随机化/置信区间，不是性能基准。

报告保留逐题引用、答案、错误码、环境声明、provider-note、数据集 SHA-256、时间和 top_k。provider-note 只填写非敏感名称，禁止填密钥。`environment` 是操作者明确声明，并非自动识别，混合使用 Mock/真实服务必须标记 mock 并在备注说明。

## 本次实际结果与限制

[公开 Mock 原始记录](../docs/phase7-mock-results.json) 来自真实本地 HTTP、SQLite/FAISS、Mock Embedding/LLM 和词重合重排脚本，Top-K=1，每模式 6 题。Basic 命中 2/6，Reranked 命中 6/6；平均端到端服务端耗时分别 215.49 ms、385.87 ms（含 Mock 答案生成）。这是合成协议数据，没有真实模型，也没有“检索提升率”结论。重跑时 UUID 同分次序、系统负载和网络时序会变化。

所有答案未经过人工语义评分，准确率仍为 null。评分公式、错误处理与覆盖量已由 pytest 验证；CLI 已实际生成模板和空评分汇总，不能据此声称答案效果验证完成。

## 后续真实验证

在独立数据集上进行真实文档标注，明确来源/许可、版本、预期文件与页码，覆盖改写、跨页、无答案、多来源和难负例；固定服务版本与参数，运行 Basic 与 Reranked，并由人工盲评答案。真实供应商协议、中文语义、正确拒答、性能分布、并发与资源消耗尚未验证。本阶段没有购买或配置收费 API。

`evaluation/results/` 默认被 Git 忽略：输出可能包含用户文档和完整答案。本次明确公开的 docs 样例只含原创合成资料，不应把未来私人评测输出直接复制过去。
