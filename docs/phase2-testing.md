# Phase 2 实际测试报告

日期：2026-10-12。项目在 <项目根目录>。

## 结果

| 验证 | 实际结果 |
| --- | --- |
| Python 依赖安装 | 成功，PyMuPDF 1.28.2、python-docx 1.2.0、FAISS 1.15.1、NumPy 2.5.4 |
| pytest 首轮 | 49 passed，1 failed（损坏 PDF 删除时文件被占用） |
| 修复后 pytest | 50 passed |
| 浏览器首轮 | Phase 1 通过，Phase 2 失败（系统代理导致本地 Embedding 502） |
| 最终 pytest | **51 passed, 1 warning in 4.67s** |
| 最终浏览器 | **2 passed (5.9s)**，实际 Chrome，无页面脚本异常 |
| TypeScript / Vite | **通过**，103 模块，最终构建 1.33s |
| 实际启动 | Uvicorn 8000、Vite 5173、本地协议测试服务 18081 |
| 视觉检查 | 已查看桌面与 390px 移动端截图；无横向溢出 |

非阻断提示：Starlette TestClient 使用 httpx 的弃用提示；Node 的 NO_COLOR/FORCE_COLOR 颜色提示。未屏蔽警告。

## 修复详情

1. 损坏 PDF 导致 Windows WinError 32。原先按文件路径打开失败后句柄被解析库占用，删除知识库时无法清理。改为从内存字节流打开 PDF，原回归用例通过。
2. 系统代理把本机 Embedding 请求转发并返回 502。直连本地端点返回 200，确认原因。对 localhost/回环 IP 关闭环境代理；远程服务保留代理行为。新增真实本地 HTTP 回归，用无效 HTTP_PROXY 验证仍能成功直连。

## 覆盖

- 原 Phase 1 15 项 CRUD/异常/CORS/持久化测试全部回归。
- 四种真实格式解析、DOCX 表格、PDF 页码、中文文件名、UTF-8/BOM/UTF-16/GB18030。
- 固定分片及 overlap、页面边界、分片上限、非法配置。
- 空文件、非法扩展名、路径穿越、损坏 PDF/DOCX、空文本、二进制文本、文件及请求体超限。
- 同库重复、跨库相同文件允许、删除后重传、删除知识库后的 SQL/文件/向量清理。
- Embedding 批处理/顺序/数量/维度/无效数值、限流、上游错误、超时、缺少配置、错误信息脱敏。
- FAISS 文件实际读回，数量/维度匹配、归一化；模拟索引写失败验证无分片残留。
- 重新创建应用后 SQLite 与 FAISS 持久化可读；处理中断转成 failed。
- 真实 Chrome：四种文件上传、重载列表、PDF 原文分片、重复错误、损坏文件失败状态、取消删除、确认删除及知识库清理。

## 验证边界

项目未配置真实 Embedding 地址、模型或密钥。pytest 使用受控协议响应；浏览器测试通过真实 HTTP 请求调用 tests/embedding_stub_server.py，并写入真实 FAISS 索引。该服务返回合成向量，只验证协议及存储，不验证语义能力。未调用供应商的收费 API，未验证其凭据或模型效果。正式应用无测试回退。

尚未验证 OCR、复杂 PDF 阅读顺序、多 worker/高并发、外部删改存储后的恢复；这些不是本阶段实现内容。未开发 Phase 3。

## 复现本次无密钥验证

所有测试数据建议使用独立路径。三个终端分别启动；以下命令均从 StudyAgent 根目录开始。

终端一（仅测试的合成向量协议服务）：

```powershell
cd backend
.\.venv\Scripts\python.exe tests/embedding_stub_server.py
```

终端二（独立测试后端）：

```powershell
cd backend
$env:DATABASE_URL = 'sqlite:///./phase2-e2e.db'
$env:DATA_DIR = './data/phase2-e2e'
$env:EMBEDDING_BASE_URL = 'http://127.0.0.1:18081/v1'
$env:EMBEDDING_MODEL = 'TEST-ONLY-SYNTHETIC'
$env:EMBEDDING_API_KEY = ''
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端三：

```powershell
cd frontend
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm.cmd run test:e2e
```

测试自动启动前端；若 5173 已有服务会复用，需确认该服务属于当前项目且指向 8000。未安装 Chrome 时执行 `npx.cmd playwright install chromium` 并不设置 PLAYWRIGHT_CHANNEL。四种 fixtures 已随项目提供，可用 backend/tests/make_browser_fixtures.py 重建。

pytest 在 backend 下执行 `.\.venv\Scripts\python.exe -m pytest -q`，不需要启动服务。所有 pytest 数据均在临时目录；浏览器用例在 finally 中删除创建的知识库。本次实际服务使用工作目录的 work/phase2-validation.db 与 work/phase2-data，不触碰用户开发数据库。

停止测试服务用 Ctrl+C。后续正式启动请打开新终端，以免沿用 TEST-ONLY 环境变量。正式使用应配置真实 Embedding 服务。

## 真实截图

[桌面文件列表、页码与分片原文](screenshots/phase2-documents.png) · [移动端与失败状态](screenshots/phase2-mobile.png)
