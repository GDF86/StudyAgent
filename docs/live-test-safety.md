# 真实模型限额测试安全层

本机制默认关闭。2026-11-09 首轮真实测试已获用户授权并执行一次 Embedding 请求，返回 HTTP 403 后立即停止；详见文末记录。不得自动续测或重建账本。

## 默认行为

`LIVE_TEST_MODE=false`。只允许显式 MockTransport，或本机固定 `TEST-ONLY*` / `unit-test-*` 测试模型。真实远程模型即使已填写密钥也被拒绝。启用模式本身不构成授权；正式 API 没有设置阶段上下文的入口，因此不能通过另一条 Chat、Agent、History 或 Comparison API 绕过限额。

新增计数器独立于业务数据库，默认位置为 `backend/test-results/bailian-first-live/budget.sqlite`。不要指向业务数据库。所有进程必须使用同一绝对账本路径，固定实验不能切换路径或另建账本续测。

## 账本与阶段

`provision(path, offline=True)` 仅供明确初始化调用，导入或服务启动均不会自动调用。数据库和 `.seal` 均独占创建；缺失、损坏、身份不匹配或初始化失败均拒绝请求。保留 seal 后不能重新初始化丢失的数据库。没有重置、退款或清空接口。

SQLite `BEGIN IMMEDIATE` 在发送前原子预扣；请求序号跨进程共享，失败、超时、中断或进程退出均保留次数及保守费用预留。为避免前一请求用量未知时继续花费，多个并发调用会保守中止实验，而非并行付费。

| 阶段 | Embedding | LLM |
| --- | ---: | ---: |
| ingestion | 1 | 0 |
| rag | 1 | 1 |
| stream | 1 | 1 |
| agent | 0 | 2 |

以 `with Stage(ledger, stage, offline=True) as stage_scope:` 包裹待验业务流程。每阶段完成业务断言后调用 `stage_scope.verify(condition)`；未明确通过、计数不匹配、异常或错误结果均停止，不能借用其他阶段额度。阶段必须顺序执行。仅受控阶段给 Agent 提供 calculator 定义，执行层再次校验工具名；历史记忆生成入口直接拒绝。

## HTTP 和用量

LLM 普通生成、工具调用、流式请求、Embedding 和 Reranker 的 HTTP 客户端全部经过 `GuardAsync` / `GuardSync`。真实受控请求只能使用内部创建的 `retries=0` 传输，禁止代理继承、重定向及自定义 live 传输。离线 scope 只允许 MockTransport，不能切换成真实网络。

账本记录阶段、模型、接口类型、请求前预扣次数、序号、状态、HTTP 状态、错误类型、耗时、输入输出 Token、保守费用预留、本次和累计估算费用。不保存 URL、头、密钥、提示词或文档。流式打开 `stream_options.include_usage`，响应在内存采集 usage 后释放，不落盘正文。缺少 usage 记为 UNKNOWN、费用为 null 并立即停止，不能当零费用。

## 价格和上界

`backend/live-test-policy.example.json` 提供可调整的参考价格（纳元/Token）：qwen-flash 输入 150、输出 1500，Embedding 输入 500，分别对应每百万 Token 0.15、1.50、0.50 元。

参考价不代表已确认的控制台价格。`Ledger.authorize` 必须得到人工核对的适用价格、控制台证据（仅保存哈希）、Token 上界确认及至多 24 小时有效期。价格缺失、过期或无法确认输入档位都拒绝。

受控请求限定文本，使用完整请求 JSON 的 UTF-8 字节数 × 4 + 8192 作为保守输入预留，超过 128000 直接拒绝；输出限制 512，并显式关闭思考。此上界策略在真实模型上使用前必须按该供应商 tokenizer、消息/工具协议开销及非思考输出计费规则核实，未确认不能勾选 attestation。它不是供应商提供的精确计费 tokenizer。

请求前以整数纳元累计预留，任何后续请求不能使累计预留超过 单轮 5 元（项目累计 50 元）；预留不返还。响应 usage 超出上界会终止实验。人民币 单轮 5 元（项目累计 50 元）是本地实验的人工预算，绝不是阿里云账户硬限额。若价格、隐藏计费或 Token 上界假设不成立，响应后的停止无法撤回已经发生的费用，因此真实测试仍需人工确认这些假设。

## 离线验证

从项目根目录运行：

```powershell
backend\.venv\Scripts\python.exe backend/tests/run_offline_safety.py tests/test_live_test_safety.py -q -p no:cacheprovider
```

该入口在测试收集前禁用 dotenv 文件加载、清除当前子进程项目配置覆盖，封锁外部 HTTP 传输。已有本机协议回归仅允许 127.0.0.1 的 `unit-test-model` 本地 Mock 服务。所有账本、删除/中断测试均使用 pytest 临时目录，不操作业务数据。Windows 子进程也会加载这些封锁，但不会递归运行 pytest。

## 未来真实测试仍需完成

1. 核实业务空间的实际价格、档位、Token 上界策略和模型参数兼容性。
2. 经人工明确授权后，在固定路径独立初始化 live 账本，不得复用离线账本或变更实验路径恢复额度。
3. 使用经过审阅的单一执行入口，覆盖短文档、普通 RAG、流式 RAG 和 calculator 的业务断言；不传 conversation_id，不打开评测/重排。
4. 该执行入口必须显式启用模式、指定账本、授权价格并建立 Stage；本次没有新增自动启动真实测试的脚本。

保护范围是本项目已审计的 HTTP 出口，不能防止本机管理员手动改代码、同时删除 seal 和账本、使用另一套账本、恢复旧文件快照或绕开本项目直接调用云服务。此类操作不能用来续用同一次实验额度。


## 2026-11-09 双层预算与首次真实测试记录

- SQLite 现含 project、run、requests 三层数据。每轮最多 7 次、最多 5 元；同一项目文件跨轮次累计最多 50 元，计数与预留在同一 BEGIN IMMEDIATE 事务中提交。新轮次必须显式创建，并保留旧请求与全局递增序号。
- 存在未结算/用量未知的旧请求时，新轮次被拒绝。旧格式账本不自动迁移或重建，seal 与已有计数不能自动删除。
- 原 UTF-8 字节估算只保留为短输入筛选，不再作为 live 计费档位的证明。真实预留采用模型公开最大输入：LLM 按 1,000,000 Token 保守封顶，Embedding 单条按 8,192；LLM 预留按最高档输入 1.2、输出 12 元/百万 Token，输出请求限制 512。
- 北京官方实时原价：qwen-flash 输入不超过 128,000 时 0.15/1.50，128,000–256,000 时 0.60/6.00，再上档至 1M 时 1.20/12.00（输入/输出，元/百万 Token）；text-embedding-v4 输入 0.50 元/百万 Token。实际估算按响应输入 Token 对应档位计算，不把优惠或免费额度当作预算依据。
- 所有请求保守预留不返还。完整四阶段最高预留 4.836864 元，小于单轮 5 元；不是云账户级硬限额。
- 双层预算新增离线专项及四阶段实际业务入口 Mock 演练共 50 项通过、0 失败。
- 真实执行入口：backend/tests/first_live_round.py，必须显式传入 --execute-authorized-first-round；不会自动开新轮或续跑已尝试的第一轮。
- 本次真实请求 1/7：Embedding HTTP 403，无可解析 usage，立即停止；LLM 0 次，其余三阶段未执行。实际费用 UNKNOWN，保守预留 0.004096 元。没有重试、切换地址或修复后补测。
- 固定累计账本：backend/test-results/bailian-first-live/budget.sqlite；结果：同目录 first-round-result.json 与 first-round-summary.json。
- 下一轮必须先核实 403 的拒绝来源与业务空间/模型权限，并人工对账本轮未知用量；不能直接重跑、删账本或把 UNKNOWN 改为零。

价格与上界依据（本次查询官方公开文档，不是额外模型调用）：

- https://help.aliyun.com/zh/model-studio/qwen-flash
- https://help.aliyun.com/zh/model-studio/model-pricing
- https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai
- https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions


## 可选显式模型代理（默认关闭）

本地 HTTPX 0.28.1 的 Client、HTTPTransport、AsyncHTTPTransport 均支持显式 `proxy` 参数。由于项目已经使用 Guard 传输封装，代理配置放在 Guard 内层 HTTPTransport/AsyncHTTPTransport 上，不能只在外层 Client 配置而使其失效。

新增环境变量（本次没有改动真实 `.env`）：

```dotenv
# 默认留空，直连
MODEL_HTTP_PROXY=
# 未来经授权验证时可手动配置；当前未启用
# MODEL_HTTP_PROXY=http://127.0.0.1:7897
```

仅作用于 LLM 和 Embedding，Reranker 不继承。始终保持 `trust_env=False`、`retries=0` 和 `follow_redirects=False`；环境中的 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 不会覆盖该选项。只接受 HTTP/HTTPS 代理 URL，不接受 SOCKS、路径、查询参数或 fragment；不会自动降级、直连回退或重试。

代理 URL 使用 SecretStr 保存，错误只返回固定提示。账本仍在代理连接之前预扣，代理握手失败也不退额度。代理用户名和密码会在保存的服务端错误字段中额外脱敏。MockTransport 不创建底层网络传输，也不启用代理。

本机配置文件的非敏感字段为 mixed-port:7897、mode:rule；已存在对应代理进程监听。按配置它是 HTTP/SOCKS 混合端口，访问 HTTPS 目标使用 HTTP CONNECT（代理 URL 仍为 http://127.0.0.1:7897）。这仅证明配置与监听存在，不证明该代理的外网能力、规则匹配或百炼可达性。

代理离线测试只使用随机端口的本机假代理：HTTP 绝对地址路由、本机 HTTPS CONNECT 拒绝、407 错误、同步/异步传输、凭据脱敏和计数保护。假代理绝不转发、不解析外部域名，也不连接真实7897服务。

未来对比直连与代理仍需要新的明确调用授权与独立诊断 grant，旧 grant 已消耗；不得清除 HALTED 或 UNKNOWN 历史，也不能以切换代理恢复额度。若代理路径仍返回403，应携带request_id联系百炼支持；本地检查不能确认拒绝来源。


## 403 错误解码的离线修复

传输层 Capture 捕获的是 HTTPX 自动解压之前的字节。现在会按 Content-Encoding 有界解码 gzip、zlib deflate 和 raw deflate，再提取 JSON 字段；对 Mock 已预读并解码的响应不重复解压。解压输出上限 2 MB，损坏、不完整、多余压缩数据或不支持的编码均明确失败关闭，不把失败当成免费或成功。

错误 JSON 同时支持嵌套 error 对象、error 字符串、顶层 code/message 和 detail 字符串。声明为 JSON 但解析失败时明确说明“declared JSON”，不再断言正文一定不是 JSON。诊断响应头白名单补充 Content-Encoding，不保存正文、Authorization 或 Cookie。

纯离线配置比对确认当前有效 Embedding 密钥与 backend/.env 一致、无进程覆盖、无额外 Bearer 前缀；仅输出布尔结果，不保存密钥指纹或片段。历史进程已经结束，未保存当时密钥版本，不能据当前比对追溯证明历史实际使用的 Key。

旧响应未保存原始正文和压缩头，因此无法认定历史403一定使用了gzip，也不回写历史错误或费用。修复只针对后续记录准确性，真实拒绝原因仍需凭request_id由百炼确认。本次未运行任何真实请求。
