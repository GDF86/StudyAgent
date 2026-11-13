# Phase 9：求职作品集与发布审查

日期：2026-11-10。纯本地整理与离线验证；本阶段真实模型请求0次，新增模型费用¥0。未启动真实Demo、未修改密钥、历史账本或真实测试资料，未git commit、git push。

## 文档与展示

README重整为定位、工程实现、六张Real截图、技术栈、系统架构、五工具、验证范围、本地运行、测试、模块导航、限制与许可。删除重复部署说明、原开发者绝对路径及“尚未验证任何真实模型”的过时结论。Memory、Reranker、多工具编排的Mock覆盖不冒充Real质量结果。

六张Real截图已逐张目视检查：存在且文本可读，内容为公开合成测试资料，未见Key、邮箱、用户目录、浏览器认证信息或私人文档。保留原图，不生成或重拍虚假结果。README使用相对路径、两列排版与原图链接；高分辨率原图便于阅读长答案。流式截图仅显示完成状态，不能冒充动态过程录像。

架构图修正了LLM Guard与工具回传的先后关系，采用GitHub支持的flowchart和sequenceDiagram基础语法；本地进行结构检查，未连接GitHub验证远端实际渲染。根README内图和详细图职责分开，引用路径本地检查。历史准备文档加上已被Phase 8取代的说明，保留当时测试事实。

## 可复现性与限制

- requirements-lock.txt与package-lock.json存在；README补全venv、pip安装、npm ci及配置模板复制步骤。未重装依赖，不把当前环境测试等同于所有平台全新安装成功。
- Mock模型替身仅监听本机，使用TEST-ONLY模型名与显式空Key、隔离数据库、关闭Real网页标志；API不会自动回退到Mock。
- 离线入口禁用真实dotenv与外部HTTP；浏览器离线用例全部拦截API，不触碰既有数据库。
- Real需要LLM/Embedding各自的Key、模型、兼容Base URL，模型名称和地址仅示例，不放真实凭据或业务空间ID。
- 已有真实验收脚本有历史隔离路径与唯一grant约束，依赖不发布的账本、seal和数据，不是可在新克隆上直接运行的安装器。该边界在README明确说明，不通过初始化新账本绕过已有预算。新的通用初始化流程仍是后续工作。
- 无生产认证、授权、任务队列或多worker文档一致性；不得描述为可直接部署公网的生产服务。

## 离线质量结果

| 检查 | 当前执行结果 |
| --- | --- |
| 后端全量离线回归 | 567 passed，1条已有Starlette/httpx兼容性弃用提示 |
| 前端类型检查及Vite构建 | 通过，146模块构建 |
| 浏览器Mock契约 | 5 passed，使用本机Edge与5188独立端口 |
| 六张Real图片 | 逐张检查，文件存在、可读、无可见敏感内容 |
| Markdown路径及图结构 | 23份Markdown、59个相对路径检查，失效路径0；6段Mermaid结构检查，未实测远端渲染 |

历史Phase 8的566项是在新增SSE测试之前的真实记录，保留不回写；当前567项属于本阶段重新执行。没有新增付费测试。

## Git与凭据风险

**当前不能直接提交发布。** 项目未有独立.git，Git自动使用包含其他项目的上级仓库，顶层在本项目之外；本项目在该仓库中呈untracked，按项目范围查询无跟踪文件、暂存变更或提交历史。上级暂存区存在大量无关项目文件，不能在此执行`git add .`后提交推送。

没有初始化或改动Git元数据、没有重写历史、没有移动/清理无关文件。建议由维护者在全新独立目录检出目标仓库，再仅复制经过审查的公开源码和资料；不要将上级仓库整体关联到StudyAgent远端。仓库边界确认后，再人工审查暂存区。

目标GitHub页面本次读取失败，未fetch远端或取得完整历史。因此只确认本地项目路径下没有已跟踪历史，**不能据此证明GitHub远端历史从未包含密钥**。公开前需维护者检查目标远端完整历史；若发现真实密钥，先撤销/轮换，再另行决定历史处理，本次不擅自改写。

当前忽略规则明确排除.env、凭据、数据库/sidecar、FAISS、上传资料、预算账本/seal、test-results、原始日志、node_modules和dist。以git check-ignore核对示例路径；.env.example和六张Real截图作为公开候选保留。发现三个不可读的本地scratch目录，增加定向忽略，不读取或发布未知内容。

公开候选文本的常见长Key格式扫描仅命中已有test_live_test_safety.py内故障脱敏哨兵；不是读取真实.env后进行比对。扫描不能保证识别所有秘密格式，仍须提交前人工审查。清理一份历史报告中的本机绝对路径，不改测试结果。

## 提交范围与发布条件

适合提交：backend/app源码、测试代码、依赖文件；frontend源码与锁文件；evaluation原创公开合成语料和协议；docs、六张Real截图、README、.gitignore及无密钥.env.example。

必须继续忽略：真实.env和代理凭据、业务数据库、FAISS、上传文档、预算账本与seal、模型请求日志、test-results和评测运行JSON、虚拟环境、依赖缓存、dist。不要用`git add -f`绕过。

本阶段修改：README.md、backend/.env.example、.gitignore；docs/demo.md、frontend-demo-acceptance.md、development-model-budget.md、architecture-phase8.md、github-release-check.md、phase5-testing.md；新增本报告。其余业务实现及截图原件不改。

推荐提交信息：`docs: polish StudyAgent portfolio and document verified demo boundaries`。

材料已可供人工审阅；发布前仍需：确认独立目标仓库、核查远端历史与暂存候选、选择并添加许可证。项目目前没有LICENSE，不能替维护者擅自授予开源使用权。完成这些条件后再由用户决定commit/push；本次未执行。

图片补充检查：六张PNG均为1280像素宽、高985–1596像素；未发现tEXt/iTXt/zTXt或eXIf元数据块。未修改图片。
