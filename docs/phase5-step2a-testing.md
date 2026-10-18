# Phase 5 Step 2A：History 后端交付与测试

日期：2026-10-15。项目目录：`<项目根目录>`。

## 范围

只新增 Conversation/Message 数据模型、ConversationService 和六个 History API。未实现 Conversation Memory、摘要、Memory 表/偏好 API、前端历史页面、Chat/Agent 自动保存或后续阶段功能。未配置或调用真实模型，没有安装依赖。

Conversation 与 KnowledgeBase 关联，Message 与 Conversation 关联，均使用 UUID 主键。Conversation 预留 summary、summary_through_turn、revision。Message 保存轮次、尝试次数、请求 ID、状态、结构化引用快照和白名单元数据。新表有唯一约束、检查约束和查询索引；旧三张表的业务字段和表结构不变。SQLite 连接显式启用外键约束，不引入 Alembic。

History API 支持创建 chat/agent 会话、知识库/模式筛选、limit/offset 分页、updated_at/id 稳定排序、详情、消息排序读取、改标题、删除。知识库删除同时清理会话与消息。服务层消息插入有明确事务边界，无任何 LLM 调用；与现有文档服务共用锁，防止知识库删除与会话写入竞争。

## 新增与修改文件

新增：

- `backend/app/schemas/conversation.py`
- `backend/app/services/conversations.py`
- `backend/app/api/conversations.py`
- `backend/tests/test_conversations.py`
- `docs/phase5-step2a-testing.md`

修改：

- `backend/app/database/models.py`：仅追加新模型及所需导入。
- `backend/app/database/session.py`：SQLite connect 时启用 foreign_keys。
- `backend/app/main.py`：创建 ConversationService、注册 History 路由。
- `backend/app/api/knowledge_bases.py`：删除知识库的最终事务中清理会话消息。
- `README.md`、`docs/api.md`：记录 Step 2A 进度、接口及边界。

## 实际测试

从 backend 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_conversations.py
.\.venv\Scripts\python.exe -m pytest -q
```

| 执行 | 通过 | 失败 | 跳过 | 收集错误 | 用时 |
| --- | ---: | ---: | ---: | ---: | --- |
| 首轮新增测试 | 0 | 0 | 0 | 1 | 1.29s |
| 修复后新增测试 | 73 | 0 | 0 | 0 | 5.99s |
| 完整 pytest | 222 | 0 | 0 | 0 | 17.50s |

首轮收集错误原因：ConversationService 的 list 方法遮蔽同一类中后续类型注解使用的内置 list，导致导入失败。已将方法改名 list_conversations。复跑前也修正了新建旧库 fixture 中 documents INSERT 的占位符数量。修复后的新增测试实际从项目根目录以 `.\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/test_conversations.py` 运行，等效于表中 backend 目录命令。

完整结果为原有 149 + 新增 73。每次均保留原 Starlette TestClient/httpx 弃用警告（1 warning），没有屏蔽。未运行 Playwright 或前端构建，因为本步没有前端改动；Phase 4 的 7 项 E2E 不作为本次执行结果。

## 新增覆盖

- Conversation 两种模式的 CRUD、UUID、UTC、保留字段默认值。
- 不存在知识库/会话、错误 UUID、空/过长标题、额外字段和非法参数。
- 只允许修改标题，拒绝变更模式、归属或摘要。
- 按知识库/模式筛选；分页边界、同时间记录稳定排序、修改后的顺序。
- 所有单会话接口的知识库范围校验及服务层跨库消息拒绝。
- 消息轮次、尝试和 user/assistant 顺序、分页、元数据模式校验。
- 引用快照完整保存/读取、文档删除后恢复、跨库/伪造来源拒绝。
- 删除会话清理消息；删除知识库清理会话消息并保留其他库。
- 重复轮次/请求冲突、批量写入全部回滚、会话时间和版本不被失败写入改变。
- 注入创建/修改/删除/写消息/删知识库的存储故障，验证回滚与错误信息脱敏。
- 数据库外键与模式检查约束，拒绝孤立消息/会话。
- 独立旧版 SQLite DDL + 既有知识库/文档/分片数据；两次启动后比较旧表 SQL 和全部旧数据完全一致，同时验证新消息跨重启保留。
- 无公开消息写入端点、无 Memory API。

## 边界

消息持久化仅为内部数据访问能力，未调用它自动保存 Chat/Agent。summary 为空、摘要游标为 0，无上下文读取或摘要逻辑。请求唯一约束只拒绝重复插入，尚不提供生成任务的幂等重放、状态转换、取消/重启恢复或重新生成编排。

来源在写入时必须仍存在且与快照一致；写入后的历史快照可独立于源文档保留。后续流式生成接入需单独处理生成期间文档删除的策略。本步不修改现有 Citation 校验规则。

会话与消息删除是数据库事务；知识库原有文档/文件删除流程仍按逐文档提交，不宣称整个知识库的文件和数据库操作具有跨存储原子性。新增外键约束不修复历史数据库可能已有的孤立数据，也不会主动删除旧数据。

完成 Step 2A 后停止，整个 Phase 5 尚未完成。
