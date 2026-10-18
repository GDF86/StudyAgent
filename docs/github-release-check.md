# GitHub 发布前安全检查

检查日期：2026-10-18。范围：StudyAgent Phase 1–7 当前文件；只调整发布文档和 Git 忽略规则。

## 结论与发布前提

当前候选文件未发现常见真实密钥、个人邮箱、手机号、带凭据的 URL 或本地用户绝对路径。14 张展示截图逐张查看，内容与合成测试资料一致，PNG 未发现文本或 EXIF 元数据。可以在建立独立项目仓库、复核暂存清单后公开当前候选文件；这不是对所有秘密格式的绝对保证。

项目检查时没有自己的 .git，Git 自动使用包含其他项目的上级仓库；上级索引还有无关改动。本项目在该索引中无已跟踪文件，按项目路径查询也无提交记录。不要在上级仓库提交或推送本项目；应在 StudyAgent 根目录初始化独立仓库。此次没有修改上级 Git 索引，没有初始化项目仓库、提交、配置远程或推送。

## 检查与修复

- 使用临时独立 Git 元数据及禁用全局忽略的方式验证本项目 .gitignore，不依赖上级规则。46 条应排除路径和 15 条应保留路径均通过。
- 排除环境配置、常见凭据文件、虚拟环境、node_modules、SQLite 及 sidecar、FAISS 与向量目录、上传及私有目录、pytest/Playwright 缓存、认证状态、覆盖率、日志、归档备份和评测运行输出。
- .env.example 仅放行 backend 和 frontend 的两个已检查模板，后端密钥为空。
- 保留 docs、evaluation、六篇原创合成 TXT、测试代码、样例生成脚本、14 张展示截图和公开 Mock 结果。
- 清理 README 与六份历史测试报告的本地绝对路径；补齐缺失的 docs/demo.md，保留历史验证日期与结果。
- 测试中的 TEST_ONLY_SECRET_6、SENTINEL-SECRET 是故障测试哨兵，不是真实凭据。历史基线 JSON 仅含相对文件名和校验摘要，保留为历史记录。

## README 完整性

| 要求 | 结果 |
| --- | --- |
| 项目介绍与技术栈 | 已包含；补充独立技术栈段落 |
| RAG 与 Agent 架构 | 已包含 RAG 图、Agent 执行链路及 Memory 约束 |
| 功能说明 | 已包含知识库、问答、工具、历史、偏好和重排 |
| 本地安装与启动 | 已包含 Windows PowerShell 步骤、版本与配置 |
| Mock 演示 | 已包含三终端联调；补齐手动展示文档 |
| 测试命令与结果 | 已包含；历史日志可核对 409 pytest、17 Chrome E2E |
| 项目截图 | 已包含，链接指向保留的公开测试截图 |
| 已知限制 | 已包含单进程、无登录、无 OCR、真实模型未验证等 |

## Mock 与测试结果的准确表述

当前演示和既有验证使用 Mock LLM / Embedding / Reranker。API、SQL、FAISS、工具与浏览器交互实际运行，但固定响应、固定向量及词重合重排不能代表真实模型能力。没有完成真实模型语义检索、自然语言工具选择、答案质量、抗提示注入或真实供应商兼容性评测。

409 pytest 通过和 17 Chrome E2E 通过是 2026-10-17 的历史回归结果，本次核对了对应本地日志，未重新执行业务回归。构建成功沿用 Phase 7 报告。本次执行的是发布文件扫描、忽略规则检查、文档链接检查与变更范围校验。不能将 Mock Hit@1 差异或耗时表述为真实模型提升；未人工评分的 Answer Accuracy 保持 null。

## 变更范围与资料保护

本次修改 .gitignore、README.md、docs/testing.md、docs/phase2-testing.md、docs/phase3-testing.md、docs/phase4-testing.md、docs/phase5-step2a-testing.md、docs/phase5-step2b-testing.md；新增 docs/demo.md 和本报告。

写入整理前后校验了 backend、frontend、evaluation 中排除依赖、构建和字节码目录后的 158 个现有文件，内容保持不变。没有改动 RAG、Agent、Memory 或其他业务逻辑，没有删除数据库、索引或上传资料。

## 手动 Git 步骤

从项目根目录开始，先创建独立仓库，再检查范围：

```powershell
git init -b main
git rev-parse --show-toplevel
git status --short --untracked-files=all
git add --dry-run -- .
```

确认仓库顶层正是 StudyAgent 项目目录，候选列表仅包含项目公开文件后：

```powershell
git add -- .
git diff --cached --name-only
git diff --cached --check
git diff --cached
# 完成人工复核后提交
git commit -m "Initial release: StudyAgent RAG and Agent demo"
```

推荐仓库名称：studyagent-rag-agent。本次不创建远程仓库，不执行 push。不要使用 git add -f 绕过忽略规则。未来添加私人 Markdown/JSON、截图或评测输出时应重新检查内容；.gitignore 无法识别任意文件中的秘密，也不会移除已跟踪历史。
