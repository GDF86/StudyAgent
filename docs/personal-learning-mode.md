# 个人长期学习模式：personal-monitor-v2

> 2026-11-12 状态更新：下文首轮开发和测试记录保留为历史证据。当前生产账本已有 personal-monitor-v2 人工价格，模型配置绑定匹配；最新 run f74a344f-4137-42df-bb99-03932f880b04 为 HALTED，无合法 READY，UNKNOWN=3、PENDING=0、seal 完整。ensure 的 HALTED_REQUIRES_REVIEW 会阻止自动续期和网页重新核价。以下启动步骤仅适用于已独立审查后的合法安全状态，不能用于当前解封；不得新建付费 run 绕过。完整评估见[稳定性检查 (private operational report omitted)](release-candidate-audit.md)。

本阶段仅离线开发与 Mock 验证。真实环境尚未执行策略迁移、保存价格或创建个人会话；没有真实模型请求，新增模型费用为 ¥0。

## 策略与迁移

旧 project 表的 CHECK 固定为 ¥50，旧 run 还有开发阶段兼容金额字段。它们不是可以直接改值的普通配置。本实现保留 project、旧 run、requests、UNKNOWN、HALTED 和 seal，不修改历史授权价格。

显式 `--personal-learning` 开启新策略。首次人工确认后，在原密封账本中追加：

- `personal_prices`：策略版本、模型/北京地域地址/输入档位绑定、人工确认时间、三个单价、提醒阈值。没有 Key、请求正文或代理凭据。
- `personal_runs`：新模型会话与价格版本的绑定；只有这些新会话使用监控型金额策略。
- `personal_alerts`：阈值提醒记录。

旧开发会话继续使用原 ¥50 限制；新个人会话不把 ¥50 当作金额硬上限。首次迁移要求旧开发会话已正常结束，不能改写尚在工作的旧授权，更不能自动处理 HALTED。事务失败会回滚新增配置和会话。

费用 Guard 没有被删除：每次 HTTP 前仍原子预扣次数和保守费用，响应后采集 usage。累计已知费用、UNKNOWN 风险预留及实际请求记录保留。仅在明确绑定 v2 的新会话中，将累计金额拒绝改为提醒；旧数据仍保留在累计统计中。

## 价格保存与边界

第一次需要人工核对 qwen-flash 输入/输出及 text-embedding-v4 输入单价，勾选个人模式保存和自动续期授权。之后刷新、重新打开网页、正常重启后端不会再次要求填写。

保存的是**最近一次人工确认价格**，不是服务商自动核价结果。本项目没有接入已验证的官方自动计价接口，不会猜测价格，也无法自动得知官方单价变化。页面显示人工确认时间，提醒价格可能变化；可从“模型状态”选择“重新核对价格”。费用均为估算，不是阿里云实际扣款金额。

价格绑定模型名称、北京业务空间 Base URL、地域与≤128K输入档位。配置变化会拒绝模型调用并提示重新确认。当前只支持已验证的 qwen-flash/text-embedding-v4 与北京地域；切换其他模型/地域还需对应适配和 Guard 支持，不能只填一个价格就调用新模型。

## 会话生命周期与安全限制

- 页面刷新仅读取状态，不新建会话、不调用模型。
- 启动后复用有效个人会话；正常 COMPLETE 或到期时，在同一个 SQLite 事务中合法创建新会话并复用保存价格。服务不初始化新账本。
- 正常模型操作前按需续期；两小时边界或已用64次时切换会话，为一次有界索引任务及重试预留容量。单会话仍最多100次，每个北京时间自然日全项目最多200次，计数跨重启/续期保留。
- 同一进程忙碌期间不重签价格；全局存在 PENDING 时拒绝续期。多进程通过事务及单例监听端口防止重复会话；并发续期测试证明只产生一个 READY。
- 任务内部仍经过 Guard，不会因旧的一小时价格兼容字段到期中断个人请求；使用的是绑定的持久人工价格版本，不伪称重新做了官方核价。
- HALTED 及历史故障保持原样，不自动恢复。未知用量、费用无法结算、鉴权/权限错误、Token越界仍停止；个人模式遇到 UNKNOWN 失败不自动重试。其余既有有限重试规则最多一次，每次重新记账。
- 输入上界64K、输出最多2048、Embedding单片/批次/上传限制、Agent轮次和工具次数、网页频率限制均保留。

金额提醒默认 ¥30、¥50。维护者可以在本机 `.env` 设置 `PERSONAL_ALERT_THRESHOLDS=[30,50,80]`，后端启动/下一次操作会追加新的阈值配置版本，保留原价格确认时间。30和50始终保留。金额超过提醒线仍可学习，但调用量和异常熔断不放宽。

## 首次启用与日常操作

1. 等待任务结束，在现有后端终端 Ctrl+C 正常停止。确认没有 PENDING。若旧会话 HALTED，先按独立安全审查流程处理，不能用个人模式绕过。
2. 在项目 backend 目录启动：

```powershell
.\.venv\Scripts\python.exe tests/start_web_demo.py --enable-authorized-web-demo --enable-local-writes --enable-session-management --personal-learning
```

3. 前端仍用原命令：

```powershell
# 在 frontend 目录的另一终端
$env:VITE_API_BASE_URL='http://127.0.0.1:8010'
npm run dev -- --host 127.0.0.1 --port 5189
```

4. 打开 `http://127.0.0.1:5189`，首次从状态入口核对并保存价格。此操作不调用模型；之后用户主动提问或索引才可能计费。
5. 每天服务已运行时直接打开网页；服务关闭时使用上述带 `--personal-learning` 的命令启动即可。无需每隔一两小时核价。停止前等待生成/索引结束，再对后端和前端终端分别 Ctrl+C。

选用个人策略后，忘记启动标志会被明确拒绝，避免用开发模式误读个人会话。新功能默认关闭；不修改真实 `.env` 中的 Key。

## 测试与变更

测试使用隔离账本、临时业务数据库及 Mock HTTP：覆盖首次持久化、复用/重启/过期、并发续期、超50提醒继续预扣、配置变化、HALTED/PENDING、每日限额、未知用量不重试、阈值版本与本地预览。既有 RAG、SSE、五工具、历史、文档和旧开发费用测试继续保留。

完整后端离线回归 **653 passed**，1项既有 Starlette/httpx 弃用警告；最终显式启用/个人策略专项 **13 passed**。前端构建和 **15项 Playwright Mock 浏览器测试通过**。这些是离线结果，不是生产个人模式或付费模型验收结果。

生产账本只读检查时有86次历史请求、3条 UNKNOWN、无 PENDING，seal匹配，尚无 personal_prices 表；这些不是本轮新增调用。

修改：`app/core/personal_learning.py`、`development_budget.py`、`live_test.py`、`runtime_safety.py`、`web_demo.py`、`config.py`、`app/services/documents.py`、`tests/start_web_demo.py`、`tests/test_personal_learning.py`、`.env.example`、前端 `LocalModelStatus.vue`、`demo-contract.spec.ts` 及本文。没有业务数据库结构迁移、删除索引或文档，没有修改发布副本、commit或push。
