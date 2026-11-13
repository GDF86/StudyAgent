# 真实模型开发策略（2026-11-10）

适用范围：用户授权的 StudyAgent 本地真实模型调试。累计人工风险预算 ¥50，提醒阈值 ¥30；不代表阿里云平台级硬性扣费上限，不自动充值。

## 会话与历史保护

通过 `Ledger.begin_development_session()` 显式追加开发会话。唯一 grant 防止同一执行授权被重复启动。旧 run、requests、UNKNOWN、HALTED、seal 不改写、不重新初始化。新增三张表保存开发策略、每次尝试及提醒事件，不修改旧表定义。

开发会话无单轮金额上限，也不再使用旧 1/2/5 次阶段配额。旧 run 的 budget_nano 字段因原表约束仍保留兼容值，新策略不使用它作为会话限额，报告显示 null。默认每会话最多 100 次 HTTP 尝试、有效期 2 小时；可显式选择 1–200 次作为防误批保护，不会到期自动创建下一会话。价格确认最多有效 24 小时。

风险占用采用保守规则：旧请求保留全额预留；新成功且 usage 已知的请求按原价估算费用占用；失败、用量未知或发送中的请求仍占用完整预留。UNKNOWN 不转为零。不能确定历史预留能覆盖费用、存在未完成请求或发生 Token 上界突破时拒绝新会话。

所有请求依然经过同一个 SQLite BEGIN IMMEDIATE 原子预扣。多个进程共享账本，待完成请求会阻止新的并发发送。重启不恢复预算。超过累计 ¥50 时拒绝发送；预计达到 ¥30 时持久化提醒并输出固定安全日志。

## 请求大小与估算

仅允许 qwen-flash、text-embedding-v4 的文本 OpenAI 兼容请求，不启用托管搜索、图片、音频或 Reranker。

- LLM 输入保守估算为完整请求 JSON 的 UTF-8 字节数 ×4 +8192，包含 messages、工具定义及工具结果；上限 64000 Token，超过即拒绝，不能自动选更高计费档。这是带余量的 byte-BPE 估算，不是模型精确分词结果或厂商账单保证。
- Embedding 每条文本估算为 UTF-8 字节数 ×4 +256，单条上限8192，一批最多16条。
- 正式适配器在普通受控 scope 内限制输出至512 Token，Phase 8网页scope显式设为1024 Token，均关闭 thinking；请求 Guard 同时拒绝超过2048的原始输出上限。实际 usage 如超出预留上界，则记录失败、立即熔断，不能重试或以新会话绕过。
- 价格需每次执行前复核：[qwen-flash 北京](https://help.aliyun.com/zh/model-studio/qwen-flash) 输入≤128K时 ¥0.15/百万、输出 ¥1.50/百万；[text-embedding-v4 北京](https://help.aliyun.com/zh/model-studio/text-embedding-v4) 输入 ¥0.50/百万。不预减免费额度或缓存优惠。

## 重试与工具限制

底层 HTTPX retries=0、trust_env=False、follow_redirects=False，代理由 MODEL_HTTP_PROXY 显式选择，无自动回退。只有 Guard 在新开发策略下允许一次受控重试：HTTP429、5xx，或响应返回前的 Connect/Read/Write 等瞬时网络错误。失败先完整记账，重试绑定原请求内容的内部摘要，原子追加新序号并再次预扣；二次失败停止。不保存请求正文、Authorization 或密钥指纹。

401、403、4xx参数错误、代理认证错误、工具执行错误、无效 usage、已返回给调用方的流式中断均不自动重试。取消/中断不能恢复额度。历史固定轮次策略继续禁止重试。

工具由会话白名单决定；calculator 会话禁止 Embedding，仅暴露 calculator。受控 Agent 最多6轮决策、8次工具调用；本次 calculator 入口进一步设为2轮、1次工具调用，LLM超时30秒、Agent总超时90秒。不运行历史摘要。

## 工具验证准备

| 工具 | 所需资料 | 正常最短调用链（不含瞬时重试） |
| --- | --- | --- |
| calculator | 本地知识库存在即可，不检索 | 规划LLM → 本地计算 → 最终LLM |
| search_documents | 至少一篇已索引文档 | 规划LLM → 查询Embedding → 最终LLM |
| summarize_document | 一篇文档ID及其分片 | 规划LLM → 总结LLM → 最终LLM |
| compare_documents | 同知识库至少两篇文档ID | 规划LLM → 比较LLM → 最终LLM |
| generate_quiz | 可检索主题资料 | 规划LLM → 查询Embedding → 出题LLM → 最终LLM |

此段最初为calculator阶段准备记录；后续已加入第二篇公开短资料，五个工具分别完成真实后端验证。Phase 8又完成RAG、calculator、比较及出题网页验收，详见[网页报告](phase8-web-demo.md)。本说明本身不启动任何测试。

执行入口：`backend/tests/calculator_development_once.py --execute-authorized-calculator`。默认导入不执行，重新加载固定 .env，使用已有隔离数据，复用真实 Agent/工具实现。必须验证 tool_calls、本地执行、tool_call_id和工具结果往返、最终答案125。结果独占保存至 `backend/test-results/bailian-first-live/calculator-development-20261110.json`，重复执行不创建新会话。

离线测试通过 `backend/tests/run_offline_safety.py` 启动，禁用真实 dotenv 与外部 HTTP；临时账本不接触真实历史数据。
