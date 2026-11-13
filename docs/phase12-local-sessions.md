# Phase 12：本机学习模式与模型会话管理

日期：2026-11-11。适用于已有隔离数据和密封费用账本的本机开发环境，不是公网部署或新账本初始化流程。

## 根因

此前网页中间件把模型会话检查放在所有受控写操作之前，导致偏好、知识库元数据、会话管理和文件本地分析也依赖模型授权。模型会话 2 小时、价格确认 1 小时到期后，只有启动脚本能够创建新授权；脚本还会将旧的硬编码价格重新确认。页面把过期、停止和熔断混为一条提示。

此次只读检查还发现：当前真实账本的最新会话已经是 **HALTED**，没有 PENDING；不是单纯价格到期。最后四条请求为成功，但历史记录没有保存这次熔断的确定原因，不能由此断言是余额、权限、网络或超时造成。

本阶段保留该 HALTED 状态，不通过重启、新建轮次或改写旧行绕过它。当前页面恢复的是本地学习功能；这条历史熔断仍需要开发者诊断后，才能另走已有明确授权的新开发会话流程。正常过期的 READY 会话则可以使用下面的网页重新授权机制。

## 权限分层

| 操作 | 需要有效模型授权 | 其他边界 |
| --- | --- | --- |
| 查看知识库、文档、Chunk、历史、消息、已有引用、偏好 | 否 | 后端仅绑定 127.0.0.1；Host 检查 |
| 保存/重置偏好、会话创建/改名/确认删除、知识库元数据修改 | 否 | 显式本机写权限、正确 Origin/Host、本地客户端、删除确认 |
| 新文件预览、已有文件处理计划 | 否 | 本地解析、上传大小/分片限制；价格过期时预留显示“待核价” |
| RAG、重新生成、SSE、Agent、查询检索对比 | 是 | 请求上下文、Guard、累计预算、合理 Token 上限 |
| 上传并索引、确认重试、历史摘要 | 是 | 真实 Embedding/LLM 仍经过相同 Guard；不得静默重复执行 |

本地操作不创建模型 Stage，也不会获得模型调用能力；即使未来误从本地接口调用适配器，也会被“缺少受控上下文”拒绝。模型授权过期不再改变本机写权限标志。并发模型/写操作仍有忙碌保护，取消接口保留独立准入。

## 重新授权机制

管理默认关闭，需要启动参数 `--enable-session-management`。管理接口只接受正确 loopback 客户端、Host、Origin 的 POST，不依赖浏览器保存密钥或授权令牌。

1. 顶部状态栏每 30 秒及窗口重新可见时读取本地状态，也可手动刷新。它不会续期或发送模型请求。
2. 过期且安全条件满足时，操作者点击“重新授权模型”。
3. 操作者现在打开官方 [Qwen Flash 价格](https://help.aliyun.com/zh/model-studio/qwen-flash)与[模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)，核对北京地域、qwen-flash 输入≤128K、text-embedding-v4 实时文本计费。
4. 三个价格字段每次均为空，单位“元/百万 Token”。必须填写当前值并勾选确认；无法确认就取消。界面明确说明：这是**操作者的人工价格确认**，服务器没有独立查询控制台验证价格。填错价格会影响估算，不能把表单验证称为服务商价格核验。
5. 后端校验价格、模型配置、版本及安全状态，并在 SQLite `BEGIN IMMEDIATE` 的**同一事务**中完成旧会话转 COMPLETE、新会话创建和价格授权。

续期必须满足：旧会话确实过期/失效或已正常 COMPLETE、全局无 PENDING、无其他 READY、累计风险未耗尽、历史费用上界完整、seal 有效。旧会话仍有效时拒绝重复创建。HALTED 直接拒绝，不改变其状态。

新会话继承已有工具、Embedding 与工作量限制；累计 ¥50、¥30 提醒、UNKNOWN 风险占用均不重置。状态中的 `session_version` 是非敏感并发版本标识，不是凭据；旧标签页提交旧版本会被拒绝。两个独立进程争抢授权时，最多一个事务成功。

没有通过延长旧价格有效期解决问题。新会话仍有 2 小时会话期限、1 小时价格期限；到期后通过网页重新核价，不必停止整个服务。普通到期不会自动调用模型或自动重试。

还覆盖了“中间件检查后、模型 HTTP 发送前才到期”的竞争条件：Guard 拒绝发送，并把普通到期与安全故障区分；SSE 返回明确的授权到期错误，已完成的前序调用正常记账。真正 HTTP 错误、隐藏调用和异常中断仍执行原熔断保护，不恢复历史 HALTED。

## 启动、授权、停止

在项目根目录的 PowerShell 中启动后端：

```powershell
cd backend
.\.venv\Scripts\python.exe tests/start_web_demo.py --enable-authorized-web-demo --enable-local-writes --enable-session-management
```

另一个终端启动前端：

```powershell
cd frontend
$env:VITE_API_BASE_URL='http://127.0.0.1:8010'
npm run dev -- --host 127.0.0.1 --port 5189
```

打开 `http://127.0.0.1:5189`。启动只复用当前会话，不重新确认价格或自动创建付费授权；已有授权有效时继续使用，正常 COMPLETE/过期时可人工重新授权，HALTED 时保持禁止。原 `--session-name` 参数保留解析兼容，但不再触发启动时的重复开轮次。

启动器先独占本机监听 socket，防止第二个启动进程先修改会话再发现端口被占用。服务不允许多 worker。需要停止时，等待任务完成，在后端终端按 Ctrl+C；启动器正常结束当前 READY 会话，HALTED 不变。存在 PENDING 时不得清除或强行改记成功，应先诊断和结算。前端也可在自己的终端按 Ctrl+C。

本阶段对旧服务做代码升级重启；日常授权到期无需重启前后端。不要为了重新授权删除数据库、账本、seal，或直接改 HALTED 状态。

## 验证

后端最终全量离线回归：**621 passed**；1 项既有 Starlette/httpx 弃用警告。新增会话专项覆盖正常 Guard、过期拒绝、本地保存、明确确认、过期价格、READY/COMPLETE/PENDING/HALTED、跨进程竞争、版本冲突、事务回滚及发送前到期的普通/SSE 路径。

```powershell
cd backend
.\.venv\Scripts\python.exe tests/run_offline_safety.py tests -q -p no:cacheprovider --basetemp=test-results/phase12-release-check
```

前端构建通过，**12 项浏览器 Mock 测试通过**，包括过期后偏好保存、空价格表单、明确确认才授权、熔断时无授权按钮，以及原有文档/RAG/SSE/Agent 页面。

```powershell
cd frontend
npm run build
npx playwright test --config playwright.offline.config.ts
```

另在真实本地服务完成 **1 项零模型调用浏览器验证**：HALTED 仍能原值保存偏好、刷新恢复、浏览既有公开测试会话；所有模型及授权接口由脚本拦截。没有在真实账本上模拟到期，没有创建新付费会话，没有进行真实模型复测。核价授权和并发转换使用 Mock/临时账本验证，不能把它们称为真实付费验证。

本轮真实模型请求 **0 次**，新增模型费用 **¥0**。升级前真实账本已有 **74 次**请求（含用户在 Phase 11 交付后进行的操作），本轮不将其算作新增调用。UNKNOWN、失败记录、累计风险预留和 seal 保留；最终历史完整性校验与费用摘要保存在被忽略的 `backend/test-results/phase12-final.json`。本地浏览器证据位于 `frontend/test-results/phase12-evidence/`。

## 修改范围

- 后端：`app/core/config.py`、`app/core/development_budget.py`、`app/core/live_test.py`、`app/core/web_demo.py`、`app/services/documents.py`、`tests/start_web_demo.py`、`tests/test_phase12_sessions.py`、`tests/test_local_learning.py`、`tests/test_phase11_learning.py`、`.env.example`。
- 前端：`src/components/LocalModelStatus.vue`、`src/App.vue`、`src/api/knowledgeBases.ts`、`tests/demo-contract.spec.ts`、`tests/phase12-local.spec.ts`、`playwright.phase12.config.ts`。
- 文档：本文与根 README。

没有修改业务数据库结构，没有新增账本表或字段；复用原有事务及会话表。未修改真实 API Key、上传资料、FAISS、历史请求和 seal。未修改独立发布副本，未 commit/push。

当前仍为本机可信操作者模式，无多用户认证。人工核价、有限重试和本地 Guard 不等于阿里云账户级消费硬上限。

后续专项检查及输出上限误熔断修复、独立人工审查新会话步骤见 [HALTED 诊断报告](halted-diagnosis-20261111.md)。旧 HALTED 本身仍不恢复。
