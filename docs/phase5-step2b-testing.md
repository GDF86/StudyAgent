# Phase 5 Step 2B：Preference Memory API 交付与测试

日期：2026-10-15。实际项目：`<项目根目录>`。

## 完成范围

新增 Memory 模型、独立 PreferenceService 和 GET/PUT/DELETE 三个偏好接口。只允许 answer_style（默认 concise）、learning_goal（默认 general）、preferred_language（默认 zh-CN）；值必须在各自枚举内。偏好是本机实例级配置，不引入用户账号。

Memory 字段为 UUID id、唯一 key、JSON value、created_at、updated_at；额外为 key 增加白名单检查约束。value 由 Pydantic Schema 在服务层写入和读取时校验。沿用 create_all，未改动已有五张表结构。默认值只读取不保存；显式设置同一个键更新同一行，删除后恢复默认值。

服务层短事务包围 SQL 操作，异常自动回滚；GET 返回有效值、默认值及显式 overrides。非法 key/value 和额外字段返回明确 422；数据库异常脱敏。没有调用模型、分析聊天或文档，没有接入 Chat/Agent Prompt。

## 文件变更

新增：

- `backend/app/schemas/memory.py`
- `backend/app/services/preferences.py`
- `backend/app/api/memory.py`
- `backend/tests/test_preferences.py`
- `docs/phase5-step2b-testing.md`

修改：

- `backend/app/database/models.py`：在既有内容后追加 Memory。
- `backend/app/main.py`：创建 PreferenceService、注册 Memory 路由。
- `backend/tests/test_conversations.py`：仅按用户明确授权替换过时的 GET /api/memory == 404 断言，改为 200 且完整默认响应一致；其他断言保留。
- `README.md`、`docs/api.md`：Step 2B 当前进度、请求响应及边界。

未修改 Vue 前端、Chat/Agent 执行逻辑、RAG/Citation、依赖清单或锁文件。未新增依赖或配置真实 API。

## 旧测试冲突处理

Step 2A 的阶段性测试明确要求 Memory 接口不存在（404），与 Step 2B 要求实现 GET 接口冲突。实现前向用户说明并取得明确授权，只将这一过时断言更新为更完整的成功响应检查：200、三项默认偏好、defaults 和空 overrides。没有删除测试、跳过测试或降低其余断言。完整回归仍包含此前全部 222 个用例。

## 实际命令与结果

从 backend 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_preferences.py
.\.venv\Scripts\python.exe -m pytest -q
```

| 执行 | 通过 | 失败 | 跳过 | 收集错误 | 用时 |
| --- | ---: | ---: | ---: | ---: | --- |
| 新增 Preference 测试 | 54 | 0 | 0 | 0 | 5.70s |
| 完整 pytest | 276 | 0 | 0 | 0 | 21.99s |

两次运行均首次通过；完整结果为原有 222 + 新增 54。保留原 Starlette TestClient/httpx 弃用提示（1 warning），没有屏蔽。未运行 Playwright 或前端构建，本阶段没有前端改动。

## 测试覆盖

- 默认值准确，重复 GET 不写入数据库。
- 三个字段全部合法枚举；更新保留 UUID/created_at，重复相同设置只保留一行。
- JSON 存储与读取；删除后恢复默认值，重复删除合法键成功。
- SQLite 重启后偏好保留，已删除值不会恢复。
- 非法 key/value、跨字段枚举、类型、空白、额外字段及指令注入拒绝。
- 内部服务调用也受相同 Schema 校验，不能绕过 API 写入其他配置。
- History 消息/会话和知识库 CRUD 正常；删除知识库不删除实例级偏好。
- 注入插入、更新、删除、读取及提交错误，验证事务回滚与错误脱敏。
- 唯一 key 约束、外部写入非法 JSON 值时拒绝使用并支持删除修复。
- 用既有五表结构和数据建立数据库，再重复启动，验证旧表 SQL 与原有数据保持不变，只新增 Memory 表。
- 通过原有 Mock Provider 实际执行上传、Chat、Agent API，验证不会从聊天/文档指令推断偏好，也不会把保存的偏好注入本轮 Prompt。

## 尚未实现

Conversation Memory、最近约 10 轮上下文、摘要生成、Chat/Agent 自动保存、偏好注入 Prompt、前端历史/偏好页面均未开发。历史 summary 字段仍仅预留。测试使用临时 SQLite 与 Mock 协议，不代表真实模型已验证。

Step 2B 完成后停止，整个 Phase 5 尚未完成。
