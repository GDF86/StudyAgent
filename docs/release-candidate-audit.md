# GitHub 发布候选审计

完成日期：2026-11-13。结论：**READY_FOR_MANUAL_RELEASE_REVIEW**。

本轮只整理源码、安装验证、Mock运行和文档，不再开发功能。候选已通过干净环境检查，等待作者人工确认；没有commit、push或公网部署。此结论不是对第三方许可或全部安全性的自动批准。

## 独立目录与原现场保护

- 开发目录仍位于上级Git根 D:/desktop/code 下，项目整体未跟踪；未在该上级仓库执行add/commit/push。
- C:/StudyAgent-release 是另一个Git仓库，已有大量暂存/未提交修改。本轮只读检查并保存文件/索引摘要，**没有覆盖任何文件或修改其索引**；198个已有文件及.git/index、HEAD、config最终摘要一致。
- 全新独立候选目录：**D:/desktop/StudyAgent-release-candidate-20261112**。目录名保留任务开始日期，验证完成于11月13日。它不在任何上级Git工作区内，尚未git init或设置远程。
- D盘根目录无写权限，用户目录又属于其他Git仓库，故选用上述独立桌面目录；前两个位置均未生成候选文件。
- 55个 backend/app Python业务文件与开发目录一致，且与本轮开始摘要一致。未修改RAG、Agent、Calculator、Guard业务逻辑，未操作生产会话/HALTED/账本/seal或真实服务。

开发README的原文已备份在开发侧忽略目录；只修改用户要求的发布说明，保留架构、技术栈、功能和运行步骤。本轮原先采用MIT；随后按作者明确决定，在候选目录调整为AGPL-3.0-only，保留第三方许可清单。原开发目录没有同步修改。

## 干净安装和测试

候选不是复制虚拟环境或node_modules：先按白名单复制源码，再创建全新venv，执行锁定依赖安装及npm ci。安装可以使用包管理器缓存，这不等于复用旧环境。验证平台为Windows、Python3.13、Node24、npm11及本机Edge；未验证其他操作系统。

| 项目 | 本轮实际结果 |
| --- | --- |
| Python锁定安装 | 32个锁定依赖安装成功，pip check无冲突 |
| npm ci | 成功安装180个适用本平台包，未修改package-lock |
| 全量后端离线 | **737 passed，1 warning，81.06秒** |
| TypeScript | vue-tsc --noEmit通过 |
| 前端生产构建 | 通过，150模块，2.20秒 |
| Playwright Mock | **15 passed，10.8秒** |
| 独立Mock运行 | 空Key、无.env、外部socket连接阻断；公开文本索引、RAG、SSE、五工具、历史和偏好均通过 |

后端使用 tests/run_offline_safety.py 禁用dotenv并阻断真实模型HTTP。第一次执行时，我指定的隔离basetemp缺少test-results父目录，导致638项setup错误（另99项通过）；补建临时目录后完整737项通过。保留首次失败日志，没有删除测试、修改期望或业务代码。

复现示例（从候选根目录）：

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-lock.txt
New-Item -ItemType Directory -Force backend/test-results | Out-Null
backend/.venv/Scripts/python.exe backend/tests/run_offline_safety.py tests -q -p no:cacheprovider --basetemp=test-results/release-validation
cd frontend
npm ci
npx vue-tsc --noEmit
npm run build
npx playwright test --config playwright.offline.config.ts
```

basetemp须使用未占用的临时目录，不得指向用户数据。其路径按backend测试启动器工作目录解析。前端Mock测试只启动独立5188前端，不使用生产后端；不应把默认或Real Playwright配置当作本轮命令。

## Mock独立运行证据

从候选新venv启动原TEST-ONLY替身（127.0.0.1:18084）和隔离后端（127.0.0.1:8000），使用候选内两篇原创英文合成TXT，没有复制生产数据库或Word。

实际完成：新建测试知识库、索引两篇文档、返回真实fixture Chunk的RAG、SSE done、MOCK:all执行五个真实本地工具、持久化assistant completed、重新读取历史、保存并读取学习偏好。替身收到14次本地HTTP请求，外部连接尝试0，真实阿里云请求0。固定Fixture的结果和向量不是自然语言规划或语义质量证明；它们只验证工程链路。

两个临时Mock服务均正常关闭。测试后移除了候选中新增的venv、node_modules、dist、测试结果、Mock数据库/索引及缓存，最终候选只保留源码和公开材料；日志在开发侧忽略目录留证。安装与运行步骤见根README和docs/demo.md。

## 最新真实演示状态（复用证据，不重新执行）

- 真实RAG问答已有成功证据，返回来源可关联真实文档Chunk；引用编号合法不等于逐句语义已人工评分。
- 用户最新Calculator问题为 (128 * 8) / 4，已有历史消息显示答案256、status=completed、result_metadata.tool_calls=1、citations=[]；同一时段真实请求#109为TOOL_CALLS、#110为STOP，均SUCCESS/KNOWN。
- Agent代码要求至少成功执行一个工具才能完成，因此不是仅凭最终256认定工具成功。请求形状与Calculator执行链路相符。
- **该次任务没有持久化工具名称、原始参数和工具返回体**，当前日志级别也没有保留工具INFO明细；不能声称能够逐字段重建该次Calculator调用或具备完整工具审计。
- 持久化会话页面的工具进度仅在执行中展示，完成后隐藏，消息卡片未展示持久化工具明细；这是已知展示/审计限制，本轮没有改动功能。
- 较早Calculator无来源引用导致的failed/HALTED仍保留，不把旧失败改写为成功。原始费用、审计和真实模型轨迹没有进入候选。
- README的6张真实截图来自11月10日公开短资料；Calculator图片展示125，不冒充最新256截图。六张Real图已在本轮/前序目视核对，内容为自编公开资料；另外14张历史截图沿用既有公开Mock素材。20张PNG均未发现文本/EXIF元数据。
- Reranker真实模型未启用，只有可选协议Mock。长篇私人Word全文总结、商业级并发、多Agent、大规模语义评分不在已完成声明内。

## 白名单与敏感检查

最终源码候选 **245个文件**。完整相对路径、字节数和SHA256见 [release-candidate-files.json](release-candidate-files.json)；清单自身不计算自哈希。

| 类别 | 文件数 | 范围 |
| --- | ---: | --- |
| 根文件 | 5 | README、LICENSE、COPYRIGHT、.gitignore、THIRD_PARTY_NOTICES |
| backend | 116 | app源码、测试/测试替身、requirements及锁定文件、pytest配置、空.env.example、公开策略模板 |
| frontend | 59 | Vue/TS/CSS源码、包清单与锁、构建配置、测试配置/源码、空模板 |
| evaluation | 12 | 项目自编合成语料、QA数据、指标/评测脚本和说明 |
| docs | 53 | 公开文档、20张历史截图、1份合成Mock结果、本审计及完整发布清单 |

排除：真实.env、Key、私人Word/PDF、生产SQLite及sidecar、FAISS、费用账本、seal、原始真实模型轨迹/日志、node_modules、虚拟环境、dist、测试生成目录、认证文件、缓存和.git。

额外不公开8份私人操作/事故报告：calculator-real-retest-result.md、calculator-recovery-implementation.md、calculator-retest-readiness.md、calculator-citation-fix.md、personal-learning-recovery-result.md、studyagent-job-demo-readiness.md、studyagent-final-stability-review.md、real-data-e2e-acceptance.md。它们仅从新候选中排除，开发原件未删除。公开文档的对应链接已改指向本审计，说明私有报告未分发。

保留 docs/phase7-mock-results.json，因为它明确是自编合成资料的Mock评测，现有公开文档引用它；它不是实际用户模型轨迹，不代表真实Reranker质量。

检查结果：常见Key/私钥/带凭据URL规则无未解释命中；与开发环境当前实际API Key值的精确比对匹配数0（未打印密钥）；唯一假凭据命中位于test_model_proxy.py的故障测试Fixture。没有禁止文件路径，公开Markdown本地链接检查通过。规则扫描不能保证识别所有秘密或版权问题，作者仍需审阅最终清单和图片。

## 第三方版权

当前候选整体采用AGPL-3.0-only；第三方依赖保留其许可，未把依赖源码或二进制捆绑进源码候选。THIRD_PARTY_NOTICES.md列出32项Python安装元数据、230项前端锁文件记录（包括本平台未安装的可选包）。

**PyMuPDF元数据明确标注GNU Affero GPL 3.0 / Artifex商业双许可**。作者必须在手动发布前复核许可及最终分发方式，不得声称全部依赖是MIT或已购买商业授权。其他依赖许可证也应按实际安装包的LICENSE/NOTICE保存。该清单不是法律兼容性认证。

六篇TXT、QA数据和演示截图均沿用项目自编材料；未新增第三方Logo、字体或网页素材。本机Python、Node与Edge是验证环境前提，不作为项目自有组件分发。

## 文件保护、日志和剩余事项

原C:/StudyAgent-release的未提交内容与Git索引摘要均保持不变；开发业务源码未变。未调用任何真实阿里云模型、未重跑真实测试、未变更HALTED/账本/seal/授权，也没有停止或重启用户真实服务。

完整安装/测试/Mock/扫描日志位于开发侧 backend/test-results/release-candidate-audit/，不随GitHub候选分发。公开报告只给出脱敏摘要和源码清单。

作者最后需要：

1. 审阅本报告、README、245文件清单与图片，特别确认PyMuPDF双许可及分发方式。
2. 确认真实演示表述接受“不具备完整工具审计”的限制，历史失败保留，不补造轨迹。
3. 人工决定是否在新候选目录初始化独立Git仓库、设置正确远程和暂存清单；不要在D:/desktop/code上级仓库盲目git add .。
4. 人工确认后再处理发布操作；本轮不覆盖旧发布副本、不commit/push、不部署公网。

**READY_FOR_MANUAL_RELEASE_REVIEW**：当前没有未解释的测试失败或敏感文件阻断，功能开发到此结束。许可、公开材料及最终Git清单仍需作者人工确认；不自动进入下一轮开发或发布。

## 最终许可证处理与现有仓库上传步骤（2026-11-13）

作者已明确选择保留PyMuPDF，将当前项目整体按AGPLv3公开。当前使用精确标识AGPL-3.0-only（仅第3版），不默认为“第3版或更新版本”。根LICENSE为完整正式文本（34,020字节），不删改许可条文，FSF版权声明原样保留；COPYRIGHT另保留项目自有版权声明。README、前端package.json及lock根包许可已一致更新，第三方清单正文和各包原许可保持不变。

候选源码、合成语料及公开素材与已有自编声明一致，未发现明确复制来源或第三方专有素材限制；不过无贡献者协议/完整权属证明可供核验，不能仅凭文件检查证明作者拥有所有权利。本次依据作者作为自有内容权利人的明确指令处理。若存在雇主、学校、外部共同作者或第三方素材权利，应先获得对应授权，不得把根许可证视为替他们授权。

已核对32项Python和230项前端依赖许可元数据，未发现明确禁止本次开源方向的已知条款。MIT/BSD/ISC/Apache/PSF、MPL和CC-BY等仍有各自声明/归属义务，不能统一改标AGPL。特别保留PyMuPDF AGPL/Artifex双许可、certifi的MPL声明、DOMPurify的双许可和caniuse-lite的CC-BY数据归属。未重打包第三方源码或二进制；未来若分发构建物或提供网络服务，仍需复核实际对应源码、NOTICE和AGPL适用义务。这不是法律合规认证，也不宣称撤销先前MIT副本已获许可。

本次仅修改独立候选的许可文本、版权说明、包许可元数据、README、审计和SHA256清单；所有其他既有文件摘要保持不变，尤其业务代码、PyMuPDF版本和全部依赖版本未变。没有安装依赖、重新运行737项测试、调用模型或修改开发目录/C:/StudyAgent-release/生产数据。737项与15项测试仍是此前代码版本的已保存验证结果，本轮不是重新测试。

候选没有.git。只在候选目录执行过git ls-remote --symref https://github.com/GDF86/StudyAgent.git HEAD；返回Connection was reset，故未能核实远端默认分支、提交或访问权限。不能假设目标仓库为空，亦不据此覆盖远端历史。

后续由用户手动操作，建议先克隆现有仓库到另一个全新目录，保留完整远端历史，再建立工作分支：

```powershell
git clone https://github.com/GDF86/StudyAgent.git StudyAgent-upload-review
cd StudyAgent-upload-review
git switch -c codex/release-agpl-v3
```

按候选发布清单逐文件复制到这个克隆，保留.git；同名文件先diff比较，不使用镜像删除，不清空已有仓库。不要从候选目录git init后强推，也不要使用--force、reset远端或--allow-unrelated-histories合并两套历史。

```powershell
git status --short
git diff --stat
git diff
# 人工审阅后，只暂存确认过的路径；不要直接暂存未审查的生成数据
git add -- <已审阅的路径>
git diff --cached --stat
git diff --cached
```

由用户亲自决定提交并推送codex/release-agpl-v3分支，再通过Pull Request比较和合入既有默认分支。若clone失败、远端已有不同许可/其他贡献者或同名改动，先解决访问和权属/差异问题，不强行覆盖。本轮没有clone、git init/add/commit/push或远端历史写操作。

最终：许可处理完成、未发现本地敏感文件或明确许可阻断，可进入人工上传审阅；远端历史仍必须通过成功clone确认。保持READY_FOR_MANUAL_RELEASE_REVIEW，不等于自动完成法律合规或已上传GitHub。
