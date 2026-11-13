# HALTED 诊断、修复与人工恢复步骤

日期：2026-11-11。本轮仅本地诊断、离线开发与 Mock 验证，真实模型请求 **0 次**，新增模型费用 **¥0**。

## 结论与证据边界

当前网页不能调用模型的确定原因：绑定的开发会话 `dc2ecddb-aeff-4d7d-8030-e3598f8be152` 在持久化账本中为 HALTED。它不是前端缓存或本机写权限缺失。旧 HALTED 未被恢复、改为 COMPLETE 或删除。

只读检查结果：

| 检查 | 结果 |
| --- | --- |
| 项目真实请求数 | 74 |
| 当前会话请求 | 12，均 HTTP 200、SUCCESS、usage KNOWN |
| 全局 PENDING | 0 |
| 当前会话失败请求 | 0 |
| 历史失败 | 最早 3 次 Embedding 403，保留 UNKNOWN |
| 历史 UNKNOWN 保守预留 | ¥0.012288，未当作零费用 |
| Token 或费用上界突破 | 未发现 |
| 累计已知原价估算 | ¥0.02459490，非平台实际账单 |
| 累计风险占用 | ¥2.46532805 / ¥50，未触及 ¥30 提醒 |
| seal | 存在，匹配项目身份；本轮前后字节校验一致 |
| 其他 READY 会话 | 无 |

**最可能原因是最后一次 SSE 回答达到输出上限后，被旧代码误归类为全局安全故障。** 证据如下：

- 第 74 次请求于北京时间 **11:03:18.814** 开始，约 8.8 秒后结束，HTTP 200。
- 输入 2128 Token，输出 **1024 Token**，正好等于该次请求输出上限 1024；usage 完整，费用与 Token 都没有超界。
- 该次请求时的价格有效期到 **11:46:50**，会话到 **12:46:50**；最后一次模型请求发生在有效期内。
- 旧路径为：provider 检测 `finish_reason=length` → `LLM_INCOMPLETE_RESPONSE` → `ChatService.events()` 的 `forbid_extra('CHAT_FAILED')` → `Ledger.halt()`。因此模型 HTTP/费用可以全部成功，而网页会话随后 HALTED。
- 已用**分包 SSE Mock**复现这类费用已结算但应用回答不完整的情形，并添加回归测试。

**不能将推断说成确定历史事实。** 原账本没有保存第 74 次请求的 `finish_reason`、HALTED 转换时间和原因，也没有可用的对应原始运行日志。输出恰为上限不能独立证明当时返回 length；正常 stop 恰好达到上限、之后取消/连接中断或其他应用异常也不能完全排除。历史会话消息中没有对应最新独立 SSE 的故障记录。没有证据支持将这次故障归因于余额不足、模型权限或新的 HTTP 403。

## 修复

1. provider 将明确的 `finish_reason=length` 分类为 `LLM_OUTPUT_LIMIT`。不把半截文本当成功回答，不自动重试，不执行截断的工具调用。
2. 流式 length 到来后，在同一次响应内继续读取尾部 usage / DONE；读取仍受既有超时和大小限制，没有增加模型请求。避免提前关闭流把已收费请求误记成 UNKNOWN。
3. 只有 Guard 保持 READY、全局无 PENDING、该网页操作内所有模型请求已成功结算且 usage/费用在上界内时，输出上限才只终止当前任务，保留会话可用。历史失败消息仍为 failed，保留 `LLM_OUTPUT_LIMIT`，明确“本次调用仍计费”。
4. 已确认浏览器断开/取消且所有调用已结算时，不再仅因缺少网页 done 就熔断全局。用量未知、请求未完成、网络/协议错误、安全异常仍保持 HALTED。没有把所有取消或 SSE 中断一律视为安全。
5. 为未来事件增加只追加的 `runtime_events`：固定原因码、时间、会话及请求序号；记录 MODEL_FINISH、SAFETY_HALT、REQUEST_STOPPED。禁止存储正文、请求头、密钥、文档、原始响应或任意错误字符串。旧记录不补造原因。
6. 状态栏可显示记录的原因；旧 HALTED 明确提示“旧版本没有保存原因”，不假定它是过期。

没有增加小额单轮金额限制，也没有提高输出上限掩盖截断问题。后续较长学习任务应缩小问题或选择简洁风格；主动重问是新的计费请求，仍经过 Guard。

## 是否可以创建新会话

本轮只读技术预检已通过：没有未完成费用，无当前失败或越界，历史 UNKNOWN 均有原有保守预留，seal 匹配，累计风险可控。

因此**具备交由独立人工审查、批准新会话的技术条件**；这不是已经批准或已经恢复真实调用，也不是证明旧 HALTED 原因完全确定。操作者需阅读上述证据缺口及修复结果，再明确决定是否批准。

提供独立命令 `tests/review_halted_session.py`：

- 默认纯只读，不加载 `.env`，不接触 API Key，不访问网络，不写账本。
- 创建必须显式传入旧 run-id、唯一 grant-id、创建选项和“已知历史原因不确定”的确认选项。
- 创建前要求 8010 后端停止，避免在线切换旧进程；同一 SQLite 事务再次检查全局 PENDING、其他 READY、模型、费用和历史风险。
- 旧 HALTED 完整保留。只追加一个**未授权、未确认价格**的新开发会话及人工审查记录。
- 已记录的安全故障、当前失败/UNKNOWN/PENDING、越界、其他 READY、重复 grant 都会拒绝，不能用此命令自动消除已知故障。
- 新会话之后还要在网页重新核对价格。没有价格授权时，适配器不能发出真实请求。

本轮**没有执行创建选项**，没有新建或授权真实会话。

## 操作者恢复步骤

以下命令用于已独立决定批准之后；不要跳过核价，也不要直接 UPDATE 旧会话状态。

1. 等待任务停止，确认 PENDING 仍为 0。正常停止已经核实属于 StudyAgent 的后端（所在终端 Ctrl+C），前端可保持运行。脚本也会检查 8010 是否仍被占用；占用时拒绝创建，不自动结束进程。
2. 在项目 `backend` 目录只读复核：

```powershell
.\.venv\Scripts\python.exe tests/review_halted_session.py
```

3. 阅读本报告并明确批准后，执行一次，`grant-id` 使用新的唯一名称：

```powershell
.\.venv\Scripts\python.exe tests/review_halted_session.py --run-id dc2ecddb-aeff-4d7d-8030-e3598f8be152 --create-unauthorized-session --acknowledge-unknown-halt --grant-id manual-reviewed-halt-20261111-01
```

该命令不发模型请求；成功输出 `authorized: false`。如果拒绝，不得换名字反复绕过，先查看拒绝原因。

4. 启动后端并刷新现有网页：

```powershell
.\.venv\Scripts\python.exe tests/start_web_demo.py --enable-authorized-web-demo --enable-local-writes --enable-session-management
```

5. 打开 `http://127.0.0.1:5189`，点击“重新授权模型”。现在核对北京地域官方价格，填写 qwen-flash 输入≤128K 的输入/输出价格、text-embedding-v4 输入价格，明确勾选后授权。无法确认价格就取消。
6. 顶部显示“模型已就绪”后，后续用户主动提问才会产生费用。当前累计风险预算继续为 ¥50，旧 UNKNOWN 和历史预留仍占用预算。日常普通过期继续使用 Phase 12 网页重新授权，不需要反复重启；本次停止仅用于独立的历史熔断审查与代码升级。

## 测试与改动

- 后端最终全量离线 **636 passed**，既有 Starlette/httpx 弃用警告 1 项；此前原因显示及恢复专项 **39 passed**，额外的 Agent 工具内部输出上限用例已包含于最终全量回归。
- 前端构建通过，浏览器全 Mock **13 passed**，包括输出上限不伪装成功、不显示权限误报、不自动重试。
- 覆盖普通/SSE、独立/持久化会话、分包尾部 usage、主动复问重新扣费、UNKNOWN 仍熔断、已结算取消与 PENDING 取消的区别、旧 HALTED 保留、新会话未授权、已知故障禁止通用恢复。
- 未对真实服务执行付费验证；不能把 Mock 结果称为真实模型复测通过。

修改后端：`app/core/runtime_safety.py`、`app/core/live_test.py`、`app/core/development_budget.py`、`app/core/web_demo.py`、`app/llm/provider.py`、`app/services/chat.py`、`app/agent/agent.py`、`tests/review_halted_session.py`、`tests/test_halt_diagnosis.py`、`tests/test_agent.py`。

修改前端：`src/components/LocalModelStatus.vue`、`tests/demo-contract.spec.ts`。业务数据库结构未变；未来的审计表仅追加到费用账本，原 run/requests/seal 不改写。本轮真实账本没有新增该表或审查记录。

本地只读诊断基线和结果保存在忽略目录 `backend/test-results/halt-diagnosis-*.json`，不应随源码发布。未修改真实密钥、私人资料、向量索引和独立 GitHub 发布副本，未 commit/push。
