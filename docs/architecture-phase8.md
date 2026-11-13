# 系统架构与调用流程

## 本机网页架构

```mermaid
flowchart LR
  UI[Vue 3 / 127.0.0.1:5189] -->|HTTP + SSE| ASGI[FastAPI / 127.0.0.1:8010]
  ASGI --> Demo[默认关闭的 Demo ASGI 边界]
  Demo --> Scope[开发会话 ContextVar / 线程池 / SSE]
  Scope --> RAG[RAG 服务]
  Scope --> Agent[有限 Agent 循环]
  RAG --> Store[隔离 SQLite / FAISS]
  Agent --> Tools[工具白名单与参数校验]
  Tools --> Store
  RAG --> Guard[共享模型费用 Guard]
  Tools --> Guard
  Agent --> Guard
  Guard --> Ledger[持久化 SQLite 账本 / 原子预扣]
  Guard --> Models[百炼 qwen-flash / text-embedding-v4]
```

## RAG

```mermaid
sequenceDiagram
  participant Browser as Vue 页面
  participant API as 受控 FastAPI
  participant Guard as 费用 Guard
  participant Index as FAISS / Chunk
  participant Model as 百炼
  Browser->>API: 选择知识库并提交问题
  API->>Guard: 请求查询向量
  Guard->>Model: 原子预扣后 Embedding
  Model-->>Guard: 向量 / usage
  API->>Index: Top-K 检索真实分片
  Index-->>API: 来源证据
  API->>Guard: 带来源的 LLM 请求
  Guard->>Model: 原子预扣后生成
  Model-->>Guard: 文本或流式内容 / usage
  Guard-->>API: 响应及用量核算
  API-->>Browser: citation / token / done 或错误
```

## Agent

```mermaid
flowchart TD
  Task["用户任务与知识库目录"] --> Guard["每次模型出口预扣及核算"]
  Guard --> Model["LLM"]
  Model --> Plan{"tool_calls 或最终回答"}
  Plan -->|工具调用| Validate[工具白名单 / JSON 参数校验]
  Validate --> Execute[本地真实执行工具]
  Execute --> Inner[必要时检索 Embedding 或工具内部 LLM]
  Inner --> Guard
  Execute --> Result[结构化 Tool Result / tool_call_id]
  Result --> Guard
  Plan -->|最终回答| Final[引用与完成条件校验]
  Final --> UI[状态事件 / 工具次数 / 引用卡片]
  Validate -->|失败| Halt[停止并保留失败记录]
  Guard -->|超预算或用量异常| Halt
```

calculator只做本地计算；search_documents涉及查询向量；总结、比较和出题工具内部可能再调用模型。每个出口都经过同一Guard。图示保留 Phase 8 基本调用链；当前版本在显式权限下支持资料写入、持久化会话与有界历史摘要，不能把 Phase 8 当时的网页权限限制当作当前默认功能列表。长文总结、个人模式与最新验收边界见[稳定性检查 (private operational report omitted)](release-candidate-audit.md)。
