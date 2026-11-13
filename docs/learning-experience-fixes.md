# 真实学习体验集中修复

## 原因与修改

1. 网页启动配置与请求 scope 均固定 1024 Token，详细偏好此前只调整表达。启动器改为最大 2048，网页请求准备时根据数据库偏好选择：简洁 1024、详细 2048，RAG、SSE、持久化会话与 Agent 共用该策略。适配器仍使用 OpenAI 兼容 `max_tokens`，不混发 `max_completion_tokens`，保留非思考模式。模型参数范围参见[官方 qwen-flash 文档](https://help.aliyun.com/zh/model-studio/qwen-flash)。
2. Guard 已支持最多 2048，预留公式直接使用实际 payload 的 `max_tokens * output_rate`，无需另设固定金额；输入字节上界、计费档位校验及累计 ¥50/¥30 提醒保持。新增测试同时断言 payload 上限、账本 output_bound 和预留金额，未仅放大启动参数。
3. Prompt 对定义、对比题先给核心内容；简洁建议约300字、详细约800字，避免重复总结和无依据扩展。这是长度引导，不能保证所有回答都不会截断。length 仍为失败，不伪装成功；安全结算后不熔断整个会话。历史页增加“重新提问（简短回答）”，由用户主动触发新的一轮、产生新的计费请求，保留原失败记录，不修改全局偏好。
4. Reranker 没有真实模型、接口、价格和权限授权。Basic 仍使用真实查询 Embedding 与 FAISS；API 原有提前跳过重排分支保持，一次比较只需一次查询向量化，不复制 Basic 伪造结果。前端将不可用重排变成内容自适应的紧凑提示，不再与 Basic 等高占据半屏空白。现有 reranker.py 是 `/rerank`、`results[index,relevance_score]` 协议，Phase 7 的 Mock/评估不能证明它兼容未指定的真实服务；本次没有擅自接入第三方。
5. 顶部改成小型连接状态与“模型状态”折叠入口。详情显示模型配置存在性、会话/价格期限、已知费用估算（不含 UNKNOWN 实际费用）、累计风险及刷新。只返回布尔状态，不返回 Key 或请求头。过期重新授权、HALTED/账本异常/预算不足及 ¥30 提醒仍在折叠区域外可见。绿色风格、移动端自动换行保留。

## 验证

- 完整后端离线回归 **644 passed**，已有 Starlette/httpx 弃用警告1项。
- 最终公开合成资料的输出策略矩阵 **8 passed**：普通/流式 × 独立/历史会话 × 简洁/详细，提问“进程和线程有什么区别”，验证输出参数、费用上界、保存状态。Mock 响应测试的是工程契约，不能当作真实模型质量证明。
- 原有输出截断、未知 usage、取消、Agent 五工具、历史恢复、检索引用及单次 Basic Embedding 测试继续通过。
- 前端构建通过；**14 项 Playwright Mock 测试通过**。正常状态栏桌面折叠高度小于100px；390px视口无水平溢出，截图位于忽略目录 `frontend/test-results/learning-experience/mobile.png`。
- 本次由助手发出的真实模型请求 **0 次**、新增模型费用 **¥0**。检查时真实账本已有79次请求、无 PENDING、最新会话 READY；该数量包含用户此前操作，不应计入本次测试调用。
- 没有修改真实密钥、业务数据库、文档、索引、历史账本、UNKNOWN 或 seal。没有修改独立发布副本，没有 commit/push。

## 生效与真实验收边界

需要重启后端一次加载启动器和输出策略；前端 Vite 开发模式刷新即可。请先等待当前任务完成再正常停止后端；新启动仍通过既有显式本机入口与网页人工核价流程，不恢复旧 HALTED、不自动续费。

本轮未在旧后端进程上发送付费请求来冒充新版验证。真实“操作系统”私人资料上的回答质量仍需更新后人工验收：设置简洁/详细、在历史会话提问、检查引用、刷新恢复，若截断再主动点击简短重问。现有授权及 Guard 有效前不得发送请求；重新授权不是自动执行真实问题测试。

## 文件

- 后端：`tests/start_web_demo.py`、`app/core/web_demo.py`、`app/services/preferences.py`、`app/rag/prompts.py`、`tests/test_learning_output_policy.py`。
- 前端：`src/components/LocalModelStatus.vue`、`src/views/ComparisonView.vue`、`src/views/ConversationView.vue`、`tests/demo-contract.spec.ts`。
- 本文。公开源码与文档可在复核后单独同步发布目录，本次未进行同步。
