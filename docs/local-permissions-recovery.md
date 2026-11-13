# 本机写权限403定位与恢复（2026-11-11）

## 实际根因

重新读取8010监听进程确认PID仍为74928，启动时间为2026-11-10 14:42，命令含enable-local-writes。运行时capabilities返回document_writes=true，但max_chunks仍为32，OpenAPI没有新上传preview、原文件processing-plan或retry接口。这证明不是参数未传入或重新登录问题，而是旧进程未加载磁盘上的新接口/中间件。

旧中间件将“路径未匹配”和“写入未开启”合在同一403提示中。POST新上传preview和原文件retry因旧路由白名单缺失被误报缺少参数；创建接口实际上能进入参数校验（空请求422），普通上传能进入文件校验（无效扩展名400）。无效探针未入库、未向量化。

另一个独立问题：旧开发会话的有效期和价格确认均已过期，但状态仍为READY。它会阻止新会话产生ACTIVE_SESSION_EXISTS，不能通过删除账本、改历史状态或恢复HALTED解决。

## 修复

- 保留本机Host/客户端、Origin、显式写标志、会话和Guard检查。区分WRITES_DISABLED、ROUTE_NOT_ALLOWED、PREVIEW_METHOD_MISMATCH、ORIGIN_MISMATCH、SESSION_EXPIRED、PRICE_CONFIRMATION_EXPIRED、SESSION_NOT_READY。
- 正确路由：新上传预览是POST /api/knowledge-bases/{id}/documents/preview（multipart）；已有文件分析是GET /api/documents/{id}/processing-plan；确认重试是POST /api/documents/{id}/retry。GET新上传preview明确405，不假装写权限未开启。
- 增加只读本机/api/local-mode-status，报告接口版本及写标志、会话是否有效，不泄露密钥或会话授权ID。
- 拒绝日志仅记录方法、脱敏路由、状态、固定内部原因、writes/session/origin/host/route布尔值；不记录Authorization、请求正文或私人资料。
- 前端识别运行后端缺少document_api_version=2的情况，提示前后端接口版本不一致，而不是循环要求添加同一启动参数。
- 启动器清除当前子进程Settings同名OS变量后读取固定.env；模型等配置由.env提供，显式启动参数及最后model_copy决定本机写权限。此次不修改.env。

## 恢复操作与结果

离线测试通过后，确认旧服务属于StudyAgent且PENDING=0，停止旧服务。通过Ledger.complete_development_session合法结束唯一已停止的旧READY会话，旧会话8次调用及费用保留；没有直接SQL重写状态、删除seal、清账或复活HALTED。再用新的明确会话名permissions-recovery-20261111启动8010。

5189当时没有监听服务，已重新启动Vite并明确VITE_API_BASE_URL=http://127.0.0.1:8010，检查实际编译模块确认目标地址。新后端状态为document_writes=true、session_valid=true、价格/会话未过期、max_chunks=128。

恢复后验证：创建临时公开诊断库201，公开短DOCX预览200（embedding_called=false），已有操作系统资料本地分析200，错误GET preview为405。临时空诊断库由本次操作确认删除，未改用户资料。累计模型请求53、UNKNOWN3，模型请求未增加。

当前业务数据库里没有DOCX记录，旧Word原文件也不在历史留存路径。本次没有删除它们，无法确认用户何时清理，不能伪造原文件分析或恢复。若失败文件仍在列表，可分析并确认重试；否则需要用户重新选择原DOCX，先本地分析，再自行决定是否生成索引。

## 验证

后端全量589通过，前端构建通过，浏览器Mock8通过；覆盖未启用写入、过期会话/价格、跨Origin/Host/非本机、错误方法、无敏感日志、上传/原文重试、RAG/SSE/Agent兼容。恢复浏览器测试另有严格路由拦截，禁止上传入库、模型问答和真实retry，只允许创建与本地预览。

变更：backend/app/core/web_demo.py、app/main.py、tests/start_web_demo.py、tests/test_local_learning.py；frontend/src/api/documents.ts、views/DocumentsView.vue、tests/demo-contract.spec.ts；新增本机权限浏览器配置/用例及本说明。原Key、历史费用、UNKNOWN、seal与发布副本不改，未commit/push。
