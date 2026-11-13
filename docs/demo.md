# 本地 Mock 演示

本文仅演示Mock工程链路：Mock LLM复制来源，Mock Embedding使用固定向量，Mock Reranker按英文词重合排序。此模式不验证真实模型理解或语义检索；Real结果另见[Phase 8](phase8-web-demo.md)。

## 安装与启动

1. 按 [README 本地运行](../README.md#本地运行) 安装后端和前端依赖；示例配置中的密钥保持为空。
2. 按同一节的Mock三终端命令启动模型替身、隔离后端和前端。当前示例使用mock-demo.db及data/mock-demo，不使用私人资料数据库。完整旧E2E用例另需从根目录执行 `backend/.venv/Scripts/python.exe backend/tests/make_browser_fixtures.py`。
3. 手动演示时第三个终端从项目根目录执行以下命令（无需先执行 E2E）：

```powershell
cd frontend
npm.cmd run dev
```

打开 <http://127.0.0.1:5173>。服务仅监听本机，保持单后端 worker。首次启动环境变量配置见 README；仅安装依赖不会自动启用 Mock。

## 展示流程

1. 新建名为“公开 Mock 演示”的知识库，上传 evaluation/corpus 中的六篇原创合成 TXT；等待文档 ready。
2. 打开资料问答，输入 `ALPHA telescope lens`，观察普通或流式响应，展开引用查看原文。回复是固定 Mock 行为。
3. 打开 Agent 学习助手，输入 `MOCK:all`，观察五个工具执行与引用。上述语料提供至少两篇 ALPHA 文档，满足此脚本的前提。可输入 `MOCK:recover` 查看工具错误恢复。
4. 打开持久化会话，连续提问并刷新，查看历史记录；默认记忆窗口为10轮；可在专用Mock终端设置 MEMORY_WINDOW_TURNS=2 并重启后端以验证摘要。摘要质量不代表真实模型能力。
5. 打开“检索对比”，选中该知识库，返回条数设为 1，输入 `ALPHA telescope lens`，比较来源顺序。时间与命中差异仅说明本地 Mock 流程。

不要将测试命令 `MOCK:*` 描述为自然语言 Agent 推理能力，也不要将 Mock 命中率描述为真实 RAG 优化收益。

## 测试与评测

完整测试命令及历史结果见 [README](../README.md#测试) 和 [Phase 7 报告](phase7-testing.md)。浏览器测试依赖本地 Chrome、Mock 服务、测试后端和预先生成的 fixtures。

评测命令见 [evaluation/README.md](../evaluation/README.md)。原始输出默认写入被忽略的 evaluation/results；未经人工评分，Answer Accuracy 为 null。公开截图见 [展示材料](github-showcase.md)。

结束时在服务终端按 Ctrl+C。后续使用新的终端启动其他环境，避免沿用 Mock 环境变量；本地数据库、向量和上传文件无需删除，Git 忽略规则会排除它们。
