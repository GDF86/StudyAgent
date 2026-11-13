> Phase 5 更新（2026-10-15）：下文 Step 2A/2B 的“不注入、不保存”描述仅指当时阶段。当前新增持久化调用如下；不带 conversation_id 的旧接口行为不变。

## Phase 5 持久化 Chat / Agent

先 POST /api/conversations 创建会话，再调用原 /api/chat、/api/chat/stream、/api/agent/run、/api/agent/stream。

请求附加字段：conversation_id（UUID，启用持久化）、request_id（可选 UUID，建议客户端生成以支持重放）、regenerate_turn（可选正整数，只允许最后一轮且问题必须一致）。后两者要求 conversation_id。知识库/模式必须与会话一致；不接收客户端自定义 history 或系统 Prompt。

成功响应和 SSE done 在原字段上增加 conversation_id、request_id、user_message_id、assistant_message_id、turn_no、attempt_no。先提交保存事务再返回成功。同 request_id 和相同内容重放已完成结果，不重复模型调用；不同内容为 409，正在执行为 409，已失败请求重放返回已存错误，新尝试须用新 ID。

POST /api/conversations/{conversation_id}/requests/{request_id}/cancel 返回 204（取消已请求或请求已终止）；会话或请求不存在返回 404。实际完成状态通过 messages 读取，取消和完成竞态以最终持久化状态为准。

Message 状态为 pending/completed/failed/cancelled/interrupted；原始问答及尝试保留，取消/失败不产生成功 done。原始 Citation 快照在文档删除后仍可读取。会话详情包含 summary、summary_through_turn、revision；摘要非当前事实证据。

主要新增错误：CONVERSATION_BUSY、CONVERSATION_MODE_MISMATCH、REQUEST_ID_CONFLICT、REGENERATION_NOT_LATEST、REGENERATION_INPUT_MISMATCH（409）；MEMORY_CONTEXT_TOO_LARGE、MEMORY_TURN_TOO_LARGE（413）；MEMORY_SUMMARY_FAILED（502）；CONVERSATION_TIMEOUT（504）。SSE 建立后通过 error 事件报告错误。

# StudyAgent API（Phase 1–3，历史基础契约）

开发地址：`http://127.0.0.1:8000`。请求和正常数据响应使用 JSON。交互文档为 `/docs`。

| 方法 | 路径 | 成功状态 | 响应 |
| --- | --- | --- | --- |
| GET | `/api/knowledge-bases` | 200 | 按更新时间、ID 倒序的知识库数组 |
| POST | `/api/knowledge-bases` | 201 | 创建后的知识库 |
| GET | `/api/knowledge-bases/{id}` | 200 | 单个知识库 |
| PUT | `/api/knowledge-bases/{id}` | 200 | 更新后的知识库 |
| DELETE | `/api/knowledge-bases/{id}` | 204 | 无响应体 |

POST / PUT 请求示例：

```json
{"name":"机器学习","description":"课程笔记与学习目标"}
```

名称：修剪首尾空白后 1–100 字符，不能为空或重复。描述：可省略，默认为空字符串，最多 2000 字符。拒绝额外字段。PUT 必须带名称；省略描述表示清空描述。

单个知识库响应示例（仅结构示意，不是实际运行输出）：

```json
{
  "id": 1,
  "name": "机器学习",
  "description": "课程笔记与学习目标",
  "created_at": "2026-10-12T08:00:00+00:00",
  "updated_at": "2026-10-12T08:00:00+00:00"
}
```

错误：

| 状态 | code | 场景 |
| --- | --- | --- |
| 404 | `NOT_FOUND` | 查询、修改、删除的资源不存在 |
| 409 | `KNOWLEDGE_BASE_NAME_EXISTS` | 创建或修改后与其他知识库重名 |
| 422 | `VALIDATION_ERROR` | 空名称、超长、缺失/错误类型、非法 ID 等 |
| 500 | `INTERNAL_ERROR` | 内部异常，隐藏配置及堆栈信息 |

```json
{"code":"NOT_FOUND","message":"知识库不存在","detail":null}
```

验证错误的 `detail` 是字段与错误描述数组，其余错误一般为 `null`。CORS 预检由中间件处理，不属于业务 JSON 响应；未允许来源的预检返回 400。

不包含认证或 Phase 4+ API。知识库 CRUD 已通过回归测试。


## Phase 2 文档 API

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| POST | /api/knowledge-bases/{id}/documents | 201 | multipart/form-data 的 file 字段，同步处理后返回文档 |
| GET | /api/knowledge-bases/{id}/documents | 200 | 文档列表，包含处理中/失败记录 |
| GET | /api/documents/{uuid} | 200 | 文档详情、source 文件名和 chunks 数组 |
| DELETE | /api/documents/{uuid} | 204 | 清理原文件、分片与向量，无响应体 |

Document 字段：id、knowledge_base_id、filename、file_type（带点后缀）、file_size（字节）、status、error_message、chunk_count、embedding_model、embedding_dimension、created_at（UTC）。不返回服务器路径、密钥或向量数组。

Chunk 字段：id、document_id、ordinal（从 0 开始）、page（PDF 从 1 开始，其他 null）、content、vector_id。所有分片共同对应详情根对象的 source。

状态：processing、embedding、ready、failed、deleting。解析或 Embedding 失败时 POST 返回错误并保留 failed 记录，detail.document_id 可定位文档。删除后可重传。

错误 code：400 EMPTY_FILE/UNSUPPORTED_FILE_TYPE/INVALID_FILENAME；404 NOT_FOUND；409 DOCUMENT_DUPLICATE；413 FILE_TOO_LARGE/TEXT_TOO_LARGE/TOO_MANY_CHUNKS/DOCUMENT_TOO_LARGE；422 DOCUMENT_PARSE_FAILED/DOCUMENT_EMPTY_TEXT/VALIDATION_ERROR；429 EMBEDDING_RATE_LIMITED；502 EMBEDDING_FAILED/EMBEDDING_TIMEOUT/EMBEDDING_INVALID_RESPONSE；503 EMBEDDING_NOT_CONFIGURED；500 DOCUMENT_PROCESSING_FAILED/INTERNAL_ERROR。

建记录前的参数校验失败不留文档，建立记录后的处理失败保留 failed 状态。重复按同库内容判定，包含失败记录。不会自动重试收费请求。删除知识库也清理全部文档、分片、原文件和向量。

## Phase 3 Chat API

### 普通回答：POST /api/chat

请求：

```json
{"knowledge_base_id":1,"question":"这份资料的主要结论是什么？","top_k":5}
```

knowledge_base_id 为正整数；question 去除首尾空白后长度 1–4000；top_k 可省略（使用 TOP_K，默认 5），范围 1–20。拒绝 history 等额外字段。没有跨请求历史、Memory 或客户端自传检索上下文。

成功响应结构示例（示意，非实际模型输出）：

```json
{
  "knowledge_base_id": 1,
  "answer": "该结论来自当前资料。[1]",
  "status": "answered",
  "citations": [{
    "id": 1,
    "knowledge_base_id": 1,
    "document_id": "文档 UUID",
    "document_name": "课程.pdf",
    "page": 2,
    "chunk_id": "分片 UUID",
    "content": "本次实际检索到的完整分片原文",
    "score": 0.87
  }]
}
```

status：answered、empty_knowledge_base、no_relevant_content、insufficient_evidence。空库/无相关分片直接返回 200、可读说明和空 citations，不调用 LLM。insufficient_evidence 表示模型按指定句式说明现有资料不足，最终 citations 为空。

citations 来自当前知识库的实际检索结果，编号只对该请求有效；集合包含提供给 LLM 的所有检索来源，不表示每个来源都被正文使用。PDF 页码从 1 开始，非分页格式 page=null。

### SSE：POST /api/chat/stream

请求体相同，成功后 Content-Type 为 text/event-stream。Cache-Control:no-cache，X-Accel-Buffering:no。每条消息由 `data: JSON` 加两个换行组成：

```text
data: {"type":"citation","data":{"id":1,"document_name":"课程.pdf","page":2,"...":"其他 Citation 字段"}}

data: {"type":"token","content":"回答片段"}

data: {"type":"done","knowledge_base_id":1,"answer":"完整回答 [1]","citations":[],"status":"answered"}

```

上方为字段示意；实际 done.citations 包含最终来源对象，与普通响应结构相同。citation 在模型 token 前发送；done 包含完整权威回答。流式片段在 done 前尚未完成引用校验，不能当作已确认答案。

模型错误发生后发送 `{"type":"error","code":"LLM_TIMEOUT","message":"模型服务超时，请稍后重试"}` 并结束，不再发送 done。已启动 SSE 的 HTTP 状态仍为 200，客户端必须检查事件；请求校验/检索准备阶段的错误则正常返回非 200 JSON。

客户端取消 fetch 会关闭上游流；连接关闭但缺少 done 时应显示中断错误。前端取消、切换知识库或离开页面均使用 AbortController，并阻止旧请求的迟到数据污染新知识库。

### 问答错误码

| 状态 | code | 场景 |
| --- | --- | --- |
| 404 | NOT_FOUND | 知识库不存在 |
| 409 | EMBEDDING_MODEL_MISMATCH / EMBEDDING_DIMENSION_MISMATCH | 查询与索引不兼容 |
| 409 | VECTOR_INDEX_UNAVAILABLE | 索引缺失、损坏或分片映射不一致 |
| 413 | RAG_CONTEXT_TOO_LARGE | Top-K 原文超过配置上限 |
| 422 | VALIDATION_ERROR | 空输入、超长、无效 Top-K、额外字段 |
| 429 | LLM_RATE_LIMITED | 上游限流 |
| 502 | LLM_TIMEOUT / LLM_UPSTREAM_ERROR | 超时或上游非成功状态 |
| 502 | LLM_INVALID_RESPONSE / LLM_INCOMPLETE_RESPONSE | 格式异常、截断或提前断流 |
| 502 | LLM_INVALID_CITATION | 无有效引用或引用编号不存在 |
| 503 | LLM_NOT_CONFIGURED | 未配置 LLM 地址/模型 |

查询 Embedding 失败沿用 Phase 2 的错误码。API 不返回上游响应体、密钥或内部路径。

## Phase 4 Agent API

`POST /api/agent/run` 返回完整 JSON，`POST /api/agent/stream` 返回执行状态 SSE；共用请求体：

```json
{"knowledge_base_id":1,"task":"比较这两篇文档的结论，并生成练习题"}
```

knowledge_base_id > 0，task 去空白后 1–4000 字符。拒绝 history、客户端工具定义等额外字段。每次独立请求，模型仅获得当前库文档目录。

成功返回 `{knowledge_base_id, answer, citations, tool_calls}`。Citation 与 Chat 共用结构，tool_calls 统计所有尝试（含失败）。SSE 示例：

```text
data: {"type":"status","phase":"model","round":1,"message":"正在请求模型选择工具或生成最终结果"}

data: {"type":"status","phase":"tool_started","tool":"calculator","call":1,"message":"开始执行工具"}

data: {"type":"status","phase":"tool_succeeded","tool":"calculator","call":1,"message":"工具执行完成"}

data: {"type":"done","knowledge_base_id":1,"answer":"结果为 42。","citations":[],"tool_calls":1}

```

phase 也可能为 tool_failed；这表示单次工具失败，Agent 可以继续修正。终止错误事件为 `{type:"error",status,code,message}`，之后无 done。SSE 已开始时 HTTP 仍为 200；请求校验、知识库不存在等准备阶段错误直接返回非 200 JSON。只有 done 才能确认最终结果。

| 工具 | 参数 |
| --- | --- |
| search_documents | query: 1–4000 字符；top_k: 1–20，默认 5 |
| summarize_document | document_id: 当前库文档 ID |
| compare_documents | document_ids: 2–4 个不同文档 ID |
| generate_quiz | topic: 1–1000 字符；count: 1–6 默认 3；question_type: multiple_choice/short_answer/true_false，默认 short_answer |
| calculator | expression: 1–256 字符 |

上述参数仅供 LLM 工具协议使用，不开放任意工具执行接口。工具结果返回 `ok:true,result` 或 `ok:false,error:{code,message}`，并以 tool_call_id 对应请求。错误包括 UNKNOWN_TOOL、INVALID_TOOL_ARGUMENTS、DOCUMENT_UNAVAILABLE、CALCULATOR_INVALID、QUIZ_INVALID_RESPONSE，以及原有检索/模型错误。

终止错误：422 AGENT_CALL_LIMIT / AGENT_ROUND_LIMIT；413 AGENT_CONTEXT_TOO_LARGE；504 AGENT_TIMEOUT；502 AGENT_NO_TOOL_RESULT / AGENT_DUPLICATE_CALL / LLM_INVALID_TOOL_RESPONSE / LLM_INVALID_CITATION；另沿用 LLM_TIMEOUT、LLM_UPSTREAM_ERROR、LLM_RATE_LIMITED、LLM_NOT_CONFIGURED。上游正文、内部异常、模型推理和密钥不会作为错误输出。

## Phase 5 Step 2A：History 后端 API

以下 History 接口已实现，不接入 Chat/Agent 自动保存、不生成摘要。Step 2B 新增的用户偏好接口见后文。会话 ID 和消息 ID 为 UUID 字符串，时间为带 +00:00 的 UTC ISO 字符串。

### 创建：POST /api/conversations

请求：

```json
{"knowledge_base_id":1,"title":"课程问答","mode":"chat"}
```

knowledge_base_id 为正整数且知识库必须存在；mode 只能是 chat/agent；title 去除首尾空白后 1–100 字符。拒绝未知字段、客户端 ID、summary 等保留字段。返回 201：

```json
{
  "id":"85e45327-de4d-4b75-9688-3f9052469dbe",
  "knowledge_base_id":1,
  "title":"课程问答",
  "mode":"chat",
  "summary":"",
  "summary_through_turn":0,
  "revision":0,
  "created_at":"2026-10-15T00:00:00+00:00",
  "updated_at":"2026-10-15T00:00:00+00:00"
}
```

此处 ID 和时间为格式示例。summary 字段仅预留；本步不更新摘要或摘要游标。revision 在标题修改或消息批次写入时递增。

### 列表：GET /api/conversations

支持可选 knowledge_base_id、mode、limit、offset。limit 默认 20，范围 1–100；offset 默认 0，范围 0–1000000。按 updated_at DESC、id DESC 排序；返回 `{items:[会话对象],total:匹配总数,limit,offset}`。筛选不存在的知识库返回 404，合法但无匹配返回空 items。

### 详情与消息

`GET /api/conversations/{id}` 返回单个会话。`GET /api/conversations/{id}/messages` 支持 limit/offset，返回相同分页信封；items 为 Message。消息按 turn_no ASC、attempt_no ASC、user 在 assistant 前、id ASC 排序，不根据时间猜测轮次。

两接口支持可选 knowledge_base_id；传入后严格核对会话所属知识库。UUID 不合法返回 422；资源不存在或归属不符返回 404。

消息对象示例（字段示意，不表示已接入自动保存）：

```json
{
  "id":"51561010-1d0c-4a08-b177-424d43a2f22d",
  "conversation_id":"85e45327-de4d-4b75-9688-3f9052469dbe",
  "role":"assistant",
  "content":"依据资料的结论 [1]",
  "turn_no":1,
  "attempt_no":1,
  "request_id":null,
  "status":"completed",
  "citations":[],
  "result_metadata":{"chat_status":"answered","tool_calls":null},
  "error_code":null,
  "created_at":"2026-10-15T00:00:00+00:00",
  "updated_at":"2026-10-15T00:00:00+00:00"
}
```

示例 citations 省略了来源对象；实际快照结构沿用 id、knowledge_base_id、document_id、document_name、page、chunk_id、content、score。服务层在写入时校验归属和原文一致性，保留结构化 JSON；来源文档删除后仍能读取历史快照。这里只提供存储校验，不执行新的回答引用规则或调用 LLM；后续自动保存必须接在现有回答校验之后。

### 修改与删除

`PUT /api/conversations/{id}` 请求只能为 `{"title":"新标题"}`，成功返回 200 和更新后的会话。传入 mode 或 knowledge_base_id 到请求体会返回 422；不可变更绑定关系。可通过 query knowledge_base_id 校验当前库范围。

`DELETE /api/conversations/{id}` 支持相同范围校验，删除消息与会话使用同一事务，成功返回 204 空响应；再次删除返回 404。删除知识库时，在删除知识库的最终数据库事务内清理其全部消息和会话，不影响其他库。

### 服务层消息访问与错误

ConversationService.save_message/save_messages 为内部数据访问方法，无对应公开 POST messages 路由。save_messages 限制每批 1–100 条，包含 role、content、turn_no 及可选 attempt_no/request_id/status/citations/result_metadata/error_code。role 只接受 user/assistant；工具轨迹和推理字段不在存储接口内。

turn_no/attempt_no 为正整数，唯一约束为 `(conversation_id,turn_no,attempt_no,role)` 和 `(conversation_id,request_id,role)`。request_id 可为空；非空重复冲突返回服务层 HISTORY_CONFLICT，不自动重放旧结果。Step 2A 未实现生成任务调度、幂等重放、状态转换或重启任务恢复。

未完成 assistant 状态 pending/failed/cancelled/interrupted 只能保存空正文、空引用及空结果元数据；completed 正文不能为空。用户消息仅允许 completed 状态，不能携带 assistant 元数据。结果元数据白名单仅含 chat_status 或 tool_calls，并验证与会话模式匹配。

错误：422 VALIDATION_ERROR（HTTP 参数）；404 NOT_FOUND（知识库）、CONVERSATION_NOT_FOUND（会话/范围）；409 HISTORY_CONFLICT（存储唯一性/完整性冲突）；500 HISTORY_STORAGE_ERROR（会话存储异常）。服务层另有 INVALID_MESSAGE_BATCH、INVALID_MESSAGE_METADATA、INVALID_CITATION_SNAPSHOT。异常不返回数据库 SQL、路径或内部异常文本。

本应用仍是单用户本机应用。可选知识库参数是范围校验，不构成登录鉴权。offset 分页在数据不变时稳定；有新增或更新时应重新刷新列表。

## Phase 5 Step 2B：Preference Memory API

偏好为本机实例级配置，只有显式 PUT 会保存；不从聊天、文档或历史记录推断。此阶段不把偏好注入 Chat/Agent Prompt。

| key | 枚举 | 默认值 |
| --- | --- | --- |
| answer_style | concise、detailed | concise |
| learning_goal | general、exam、research | general |
| preferred_language | zh-CN、en | zh-CN |

### GET /api/memory

返回 200，未保存任何偏好时：

```json
{
  "preferences": {"answer_style":"concise","learning_goal":"general","preferred_language":"zh-CN"},
  "defaults": {"answer_style":"concise","learning_goal":"general","preferred_language":"zh-CN"},
  "overrides": {}
}
```

preferences 是默认值加显式设置后的有效结果；defaults 始终为完整默认值；overrides 只包含用户主动保存的字段。显式保存一个恰好等于默认值的选项，仍会出现在 overrides 中。读取不落库，也不暴露数据库 ID/内部路径。

### PUT /api/memory/{key}

示例 `PUT /api/memory/answer_style`：

```json
{"value":"detailed"}
```

成功返回 200：

```json
{
  "preferences": {"answer_style":"detailed","learning_goal":"general","preferred_language":"zh-CN"},
  "defaults": {"answer_style":"concise","learning_goal":"general","preferred_language":"zh-CN"},
  "overrides": {"answer_style":"detailed"}
}
```

请求体只允许 value，且必须是与 key 对应的精确枚举字符串，不接受 null、数字、布尔、数组、对象、首尾空白、未知字段或额外指令。key 不允许 system_prompt、模型名称/密钥、权限等其他配置。

同一 key 更新同一条 Memory 记录，保留 id 和 created_at；值发生变化时更新 updated_at，相同值重复提交不增加行数或更新时间。提交成功后才返回响应。

### DELETE /api/memory/{key}

删除显式设置，成功返回 204 空响应。随后 GET 中该键恢复默认值且从 overrides 移除。合法键即使尚未显式设置也返回 204；未知键返回 422。删除知识库或会话不会删除实例级偏好。

### 错误格式

沿用 `{code,message,detail}`，例如未知键：

```json
{"code":"INVALID_PREFERENCE_KEY","message":"只允许设置回答风格、学习目标和语言偏好","detail":null}
```

| HTTP | code | 场景 |
| --- | --- | --- |
| 422 | VALIDATION_ERROR | 请求结构、类型、长度或额外字段不合法 |
| 422 | INVALID_PREFERENCE_KEY | key 不在白名单内 |
| 422 | INVALID_PREFERENCE_VALUE | value 不属于该 key 的枚举 |
| 409 | PREFERENCE_CONFLICT | 数据库完整性冲突 |
| 500 | PREFERENCE_STORAGE_ERROR | 数据库读取、写入或提交异常 |
| 500 | PREFERENCE_DATA_INVALID | 数据库被外部修改后出现非法偏好值 |

存储异常不会返回 SQL、参数或内部数据库异常文本。非法已存值不会作为有效偏好返回；可重新设置或删除对应合法 key 修复。PreferenceService 使用相同 Schema 校验内部调用，默认值只来自 Preferences Schema。本接口不提供任意键值配置存储。


## Phase 7 检索对比

`POST /api/rag/compare`，请求：

```json
{"knowledge_base_id": 1, "question": "ALPHA telescope lens", "top_k": 5, "include_answer": false}
```

question 非空且最多 4000 字符，top_k 为 1–20，禁止额外字段。返回 `runs`（basic、reranked 各一个）、`candidate_k` 和 `score_kind: vector_cosine_similarity`。每个 run 包含 citations、elapsed_ms、answer（默认 null）、error_code/error_message（成功 null）。可预期的领域错误保留为分支错误；HTTP 200 不等于两个分支都成功，调用者必须检查 error_code。结构验证错误 HTTP 422。

本接口显式尝试两种模式，不修改 `ENABLE_RERANKER`。没有重排配置且存在候选时，basic 仍可成功，reranked 返回 RERANKER_NOT_CONFIGURED。空候选直接返回空结果。`include_answer=true` 才调用 LLM 并返回 ChatResponse；不保存历史、不使用偏好或记忆。elapsed_ms 覆盖该分支的检索与可选生成，不含浏览器渲染。若分支生成失败，该分支作为失败记录，不能当成成功的完整 RAG 结果。

### 后端重排适配协议

`POST {RERANKER_BASE_URL}/rerank`；可选 Bearer 密钥仅由服务端设置。请求：

```json
{"model":"your-reranker","query":"question","documents":["source A","source B"],"top_n":2}
```

响应必须包含所有候选，每个 index 恰好一次；score 为有限数值，范围由供应商定义：

```json
{"results":[{"index":1,"relevance_score":0.9},{"index":0,"relevance_score":0.2}]}
```

后端本地排序，同分保持输入向量顺序，再取 Top-K。禁止以供应商返回原文替代数据库 Citation。未验证供应商适配，不声称所有 `/rerank` 接口兼容。

| 错误码 | 正式问答 HTTP 状态 |
| --- | --- |
| RERANKER_NOT_CONFIGURED | 503 |
| RERANKER_INPUT_TOO_LARGE | 413 |
| RERANKER_RATE_LIMITED | 429 |
| RERANKER_TIMEOUT / RERANKER_FAILED / RERANKER_INVALID_RESPONSE | 502 |

对比接口将这些错误放入对应 run；不会返回供应商错误正文、请求密钥或私有调试信息。

## 当前本机能力、预览与模型管理接口

以下为 2026-11-12 对照代码补充的契约；前文 Phase 1–5 保留历史分层说明，后续扩展以本节及对应阶段文档为准。

| 方法 | 路径 | 作用与边界 |
| --- | --- | --- |
| GET | /api/runtime-capabilities | 返回 local_real_mode、document_writes、document_api_version=2、max_upload_bytes、max_chunks、embedding_batch_size，不返回凭据 |
| POST | /api/knowledge-bases/{id}/documents/preview | multipart file；只做本地解析与分片计划，不索引、不调用 Embedding |
| GET | /api/documents/{uuid}/processing-plan | 对已有文件生成本地计划；缺少源文件返回 SOURCE_MISSING |
| POST | /api/documents/{uuid}/retry | 仅 failed 文档，要求 X-Confirm-Retry:true；成功索引可能产生真实模型调用，受 Guard 约束 |
| GET | /api/local-mode-status | 仅启用 WebDemoMiddleware 时存在；状态、策略版本、价格确认与额度摘要，无模型请求 |
| POST | /api/local-model-session/reauthorize | 仅启用本机管理权限、合法 Origin/Host、非忙碌状态下人工核价；不能恢复 HALTED |

本机真实模式的预览、计划、写操作仍要求相应写权限及匹配的 Origin。模型管理两个接口由 ASGI 中间件直接处理，不在标准 OpenAPI 路由表内；其余新增接口可在 /docs 中查看。

核价 JSON 字段：confirmed=true、session_version（当前实际 run ID）、region=beijing、input_tier=<=128K，以及 llm_input_per_million、llm_output_per_million、embedding_input_per_million（三项为人工核对后的每百万 Token 人民币单价）。核价不调用模型；格式错误返回 400，管理权限/来源拒绝返回 403，HALTED 等状态阻断返回 409/MODEL_REAUTHORIZATION_BLOCKED。当前事故状态不能通过此接口重签授权。

预览计划包含 parsed_chars、planned_chunks、embedding_requests、max_attempts_with_retry、estimated_reserve_yuan、worst_reserve_with_retry_yuan、embedding_called。无法确认价格时金额可以为空；计划不是模型请求或实际扣款。

Calculator 成功结果包含 result_kind=calculation、expression、value，不提供文档 Chunk。无来源编号仍报 LLM_INVALID_CITATION；文档工具继续严格校验实际来源。长文 summarize_document 的分组/覆盖/失败语义见 [P1 修复](agent-p1-fixes.md)；最新验收边界见[稳定性检查 (private operational report omitted)](release-candidate-audit.md)。
