# 受控真实 RAG 轮次

此入口仅适用于 2026-11-09 用户授权的首次 RAG 验证，不构成未来轮次授权。

- 单轮最多 5 次请求、人工预算 ¥5；项目累计预算 ¥50。
- ingestion：1 次 Embedding；rag：1 次 Embedding + 1 次 LLM；stream：1 次 Embedding + 1 次 LLM。
- 模型限于 text-embedding-v4、qwen-flash；流式阶段通过后结束，禁止 Agent、工具、Reranker、历史摘要。
- 固定唯一授权标识，重复运行不能恢复额度或新建第二个授权轮次。

`Ledger.begin_rag_round()` 在同一个 SQLite `BEGIN IMMEDIATE` 事务内追加轮次及 rag_grants 授权记录。原 run、requests、response_errors、diagnostic_grants 和 seal 不改写。历史 UNKNOWN 仅允许为已完整预留、失败的 Embedding HTTP 403；其他未知/未完成请求拒绝开轮次。所有历史预留继续占用项目预算，既不退回，也不冒充实际费用。

开轮次前检查整个轮次最坏情况预留，发送前继续逐次原子检查和预扣。使用模型输入上限及最高价格档：3 × 8192 × 500 + 2 × (1000000 × 1200 + 512 × 12000) = 2424576000 纳元，即 ¥2.424576。旧费用已知值也必须不大于其预留。普通 7 次轮次和单次诊断机制保持原有限制。

价格依据：[qwen-flash](https://help.aliyun.com/zh/model-studio/qwen-flash)、[text-embedding-v4](https://help.aliyun.com/zh/model-studio/text-embedding-v4)。价格必须在执行当天复核；原价估算不等同实际账单，也不计免费额度抵扣。本地预留不是阿里云平台消费硬上限。

`backend/tests/rag_live_round.py` 默认不执行。显式执行参数为 `--execute-authorized-five-request-rag`。入口重新加载固定的 backend/.env、清除当前子进程设置覆盖，核对隔离存储、当前两项密钥配置及历史4次请求。当前 LLM 密钥与已验证 Embedding 密钥的本地一致性只能证明配置一致，控制台权限仍依赖用户确认。

运行通过原项目上传、检索、问答和 SSE 路由；不带 conversation_id。测试只创建公开的短文档，并逐项校验数据库 Chunk、FAISS 维度及真实检索引用。失败立即停止，不自动修复后继续调用。结果文件以独占创建方式保存在 backend/test-results/bailian-first-live/rag-five-request-result-20261109.json。

离线入口 `backend/tests/run_offline_safety.py` 禁止真实 HTTP，并禁用真实 dotenv。新增 `test_rag_live_round.py` 覆盖保留历史、轮次/项目限额、额外调用、最高档费用预留、完整 RAG HTTP 与 SSE 中断。所有数据库均为临时测试文件。
