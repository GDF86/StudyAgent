# Phase 1 测试记录与验收步骤

> 当前统一回归：从根目录使用 `backend/.venv/Scripts/python.exe backend/tests/run_offline_safety.py tests -q -p no:cacheprovider`，不要把下文历史 `pytest`/完整 E2E 命令作为当前默认入口。标准入口禁用 dotenv 并阻断真实模型 HTTP；当前代理测试依赖其本地 Mock 注册。前端当前零付费命令为在 frontend 下 `npm run build`、`npx playwright test --config playwright.offline.config.ts`。下文涉及 CRUD 的历史 E2E 需要专用隔离后端，禁止对生产数据库执行。最新完整结果见[稳定性检查 (private operational report omitted)](release-candidate-audit.md)。

本文件保留 Phase 1 的历史验收记录。当前 Phase 2 结果见 [Phase 2 测试报告](phase2-testing.md)。

## 真实执行状态

记录日期：2026-10-12。

以下为当时本地开发环境的验证记录，命令均以项目根目录为基准。

| 项目 | 实际结果 |
| --- | --- |
| Python 虚拟环境 | 已核验，可执行；Python 3.13.3 |
| 后端依赖安装 | 成功；pip check：No broken requirements found |
| 前端依赖安装 | 成功；新增 96 个包；Node.js 24.16.0 / npm 11.13.0 |
| pytest | **15 passed, 1 warning in 1.91s** |
| Vue/TypeScript 构建 | **通过**；Vite 6.4.4，99 个模块，构建 1.04s |
| 前后端服务启动 | **成功**；Uvicorn 8000，Vite 5173 |
| 真实浏览器 CRUD 联调 | **1 passed (5.2s)**；已安装的 Chrome，单用例 1.5s |
| 移动端布局和截图 | 390×844 无横向溢出；5 张真实截图已保存并检查列表及移动视图 |

本次测试和构建均无失败项，无需修改业务代码修复失败。为完成本机验证，增加了可选的 `PLAYWRIGHT_CHANNEL` 浏览器配置，补充了重名错误、空表单禁用、无页面脚本异常检查及截图。

Playwright 自带 Chromium 下载速度较慢，取消下载后改用已经安装的 Chrome，实际运行通过。无需等待下载即可复现本机验证。

非阻断提示：pytest 中 Starlette 1.7.0 提示其 TestClient 的 httpx 适配将弃用，建议未来迁移 httpx2；当前 15 项均通过，未屏蔽警告。浏览器测试中 Node 报告 NO_COLOR/FORCE_COLOR 同时设置，仅影响终端颜色显示。上述提示均不属于功能测试失败。

已保存前端 package-lock.json 和后端 requirements-lock.txt，记录本次实际依赖版本。实测主要后端版本：FastAPI 0.143.0、SQLAlchemy 2.1.4、Pydantic 2.14.1、pytest 9.1.1。

## 已编写的 pytest 覆盖

`backend/tests/test_knowledge_base.py`：

1. 完整 CRUD：空列表 → 创建 → 详情/列表 → 修改 → 删除 → 404。
2. 7 组无效创建：空名称、纯空白、名称超长、描述超长、null 名称、缺少名称、额外字段。
3. 重名创建和重名修改返回 409，回滚后原数据保留，编辑自身同名允许。
4. 3 组不存在资源：查询、修改、删除。
5. 非法修改不能破坏现有记录。
6. 允许来源和拒绝来源的 CORS 预检。
7. 应用关闭后重新创建，数据仍可从相同 SQLite 文件读取。

每个测试使用 pytest 临时目录数据库，不触碰开发数据库。

运行（依赖安装完毕后，从 backend 目录）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 真实浏览器联调

`frontend/tests/crud.spec.ts` 不使用 API mock。测试浏览器操作首页、创建知识库、重复名称错误、空表单保存禁用、刷新后确认持久化、编辑、取消删除、确认删除，同时直接请求详情核对后端结果，并检查 390px 宽度下横向溢出以及页面脚本异常。

测试使用唯一名称，finally 中尝试删除本条测试创建的数据；仍应在独立测试数据库执行。它不能替代完整视觉验收。

在终端一的 backend 目录启动独立测试后端（确保 8000 端口没有另一个后端）：

```powershell
$env:DATABASE_URL = 'sqlite:///./studyagent-e2e.db'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在终端二的 frontend 目录：

```powershell
npm.cmd install
npm.cmd run build
npx.cmd playwright install chromium
$env:VITE_API_BASE_URL = 'http://127.0.0.1:8000'
$env:E2E_API_BASE_URL = 'http://127.0.0.1:8000'
npm.cmd run test:e2e
```

本次使用已安装的 Chrome，以上 `npx.cmd playwright install chromium` 可替换为：

```powershell
$env:PLAYWRIGHT_CHANNEL = 'chrome'
```

本次验证后端使用独立数据库 `work/phase1-validation.db`，不操作用户开发数据库；测试记录已通过正常 DELETE API 清理。

Playwright 配置会启动前端开发服务器；本机已启动的 5173 服务会被复用，应确认它正是本项目并使用上述后端地址。如配置不同，先停止现有前端再运行测试。`E2E_API_BASE_URL` 用于测试直接请求，应与前端 `VITE_API_BASE_URL` 一致。

测试后在运行服务的终端按 Ctrl+C 停止。PowerShell 中如要恢复默认数据库，执行 `Remove-Item Env:DATABASE_URL` 或打开新终端。测试数据库文件由使用者按需保留或手动删除。

## 验收覆盖

- [x] 两个服务都能启动，首页可加载。
- [x] 创建中文名称和描述，刷新后仍存在。
- [x] 修改名称，列表更新且真实详情 API 返回新名称；描述修改由 pytest 覆盖。
- [x] 空名称被阻止；重复名称显示服务端错误，数据不变由 pytest 覆盖。
- [x] 取消删除保留记录；确认删除后详情接口返回 404。
- [ ] 停止后端后显示错误，恢复服务后点击重试可恢复。
- [x] 桌面和移动端卡片已视觉检查，移动端无横向溢出。
- [ ] 完整键盘交互与所有表单尺寸专项验收。
- [x] pytest、前端 build、Playwright 全部通过，本文件记录实际结果。
- [x] 补充首页、编辑、删除确认、列表和移动端的真实截图。

Phase 1 核心 CRUD 已通过运行验收。未勾选项为未执行的扩展人工验证，未声称已覆盖。
