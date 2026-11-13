# 真实验收 P1 专项修复（离线）

日期：2026-11-12。本次真实API请求 **0次**，新增费用 **¥0**。未解除HALTED、创建READY会话、修改Key、资料、索引、旧账目或seal；未使用旧review授权命令。

## Calculator：事实与可复现缺陷

历史两次请求的HTTP、usage和finish类型只证明模型传输和费用结算完成，不证明应用闭环成功。第一轮TOOL_CALLS、第二轮STOP后出现AGENT_FAILED，与以下路径完全一致：真实计算成功后，最终模型文本带了无来源引用，Agent抛LLM_INVALID_CITATION，再由统一forbid_extra('AGENT_FAILED')掩盖原码并熔断。另一种可能是STOP消息内容为空或协议字段异常。原始响应、参数和中间结果没有落盘，**不能唯一确定历史事故原因**，也不能补造256的历史证据。

已确认的工程缺陷是错误分类过粗：即使全部模型调用成功结算，参数、计算或引用这类应用失败也无条件升级为全局HALTED。另发现calculator只接受Python ASCII运算符，不支持用户问题中的全角括号、×、÷；已补充有限字符归一化，但这不是历史事故的已证实原因（当时已有第二次LLM调用）。仍使用受限AST，不使用eval。

新的流程为：标准tool_calls解析 → 严格expression参数校验 → 实际AST计算256 → 保留tool_call_id的tool结果消息 → 第二次模型STOP → 校验最终内容及引用 → 完成/错误状态落盘。普通与SSE共享Agent.events，历史执行服务保存completed或failed，不把失败伪装为done。

- 参数错误：INVALID_TOOL_ARGUMENTS；计算错误：CALCULATOR_INVALID；普通工具执行异常：TOOL_EXECUTION_FAILED。
- 最终错误引用：LLM_INVALID_CITATION。保持失败，不删除引用后返回假成功。
- 严重协议错误：LLM_INVALID_TOOL_RESPONSE，保留具体码并熔断。
- 可恢复应用错误仅在Guard仍READY、无全局PENDING、本次已发送请求全部SUCCESS/KNOWN且未越界时，不熔断整个会话。未知usage、PENDING、非法工具、网络/协议严重异常仍保持原有安全处理。
- SSE已经发完error帧时，不再因缺少done而在响应结束后追加ASGI异常；严重错误仍写HALTED。

## 长文输入膨胀

本地只读统计：原文26,137字符，49片合计29,977字符，重叠3,840字符。旧全文工具把全部片段及文件名、编号、UUID等元数据一次序列化到Prompt；Guard再按整个UTF-8 JSON字节×4＋8192计算保守输入上界，得到约357,988。**这不是实际服务端Token数**。

未发现这里存在对话历史反复附加、无限分片或数据库重复Chunk。重叠与JSON元数据放大了输入，但主要问题仍是全量一次提交和保守Token估算。没有删除估算系数或抬高64K保护来通过测试。原工具结果还会将完整来源再次放进Agent下一轮消息，因此仅放宽首次总结输入也不能完成闭环。

## 有界分段总结

- 短文档保留原直接总结路径。来源JSON超过8500 UTF-8字节时使用新流程。
- 单文档最多128片，按原顺序处理；仅同页相邻片段去除匹配重叠，至少16字符，保留覆盖ID。不同PDF页不因相同边缘文本随意去重。
- 分组的messages JSON最多约11000字节；每次构造完整payload后重新检查 `4×UTF8字节＋8192≤64000`。
- 最多16个原文组；阶段摘要最多512输出Token、700字符，携带结构化source_ids。引用必须来自本批提供的原始编号。
- 阶段摘要再分组合并，最多三层；总体摘要最多两个核心来源编号，重映射为真实Chunk引用。Agent只接收紧凑摘要与少量实际引用，不再次装入整篇原文。
- 总结阶段最多24次HTTP尝试（包含传输重试），顺序执行，不递归、不并发扇出。每步仍调用CompatibleLLMProvider和原Guard，预扣费用与usage结算不变。
- 调用前检查当前会话/每日剩余额度和开发模式的最坏费用风险；个人模式保持监控策略与次数限制。为最终Agent回答预留一次调用。
- 结果包含覆盖总片数、已覆盖片数、complete、总结调用数与去重字符数。服务端保留完整覆盖chunk_ids；不向模型回传全部原文。
- 任一阶段失败返回complete=false、已覆盖数量、失败起点或合并失败状态；取消直接传播并停止后续调用，不生成全文成功结果。

对真实49片仅执行本地计划计算：去除3,840字符重叠后预计 **8个原文组**。未将私人原文交给真实模型。覆盖全部输入不代表模型语义上绝无遗漏；标题、术语、章节和忠实度仍须真实复测人工核验。

## 离线验证

新增25项专项：普通/SSE、独立/持久化calculator正常闭环与参数/执行/最终引用/协议错误；49片分组覆盖到最后一片；分包中途失败；使用实际CompatibleLLMProvider＋MockTransport验证Guard payload≤64K及输出512；取消后不再调用；相邻重叠去重；129片超限在模型前拒绝。既有短文、其他四工具、UNKNOWN、PENDING和费用测试仍保留。

最终后端全量 **681 passed**（1项既有Starlette/httpx弃用警告）；前端构建通过，**15项Playwright Mock测试通过**。不能将离线PASS改成历史真实用例PASS。

## HALTED只读复核

实际run_id：`8e4bb9a4-075c-486a-b843-71dc47ff8393`。仍为HALTED；104条历史请求、PENDING=0、3条历史UNKNOWN。当前run没有HTTP费用结算失败，已有AGENT_FAILED审计记录，seal匹配；累计已知费用估算¥0.03660615。统计保存于忽略目录 `backend/test-results/agent-p1-halted-review.json`。

该报告不是恢复授权。下一轮先复核无PENDING、历史UNKNOWN风险已占用、seal完整、无其他READY、修复版本已加载；再由用户明确批准独立新会话及复测范围。旧HALTED不得转READY/COMPLETE。不得直接运行只接受未知旧故障的旧review命令来跳过本次已记录故障；本次未创建任何新会话或执行恢复命令。

## 下一轮最小真实复测建议（未授权、未执行）

1. 先仅复测calculator，最多2次LLM，确认标准tool_calls、真实执行256、tool_call_id回传、最终STOP与持久化；失败即停。
2. 通过后，再单独批准Word全文总结。8个原文组，阶段摘要长度决定合并为1至3次，预计9–11次总结LLM，加Agent决策/最终回答2次。与calculator合计预计13–15次。
3. 保守最大为24次总结HTTP＋4次外层LLM＝**28次**，无Embedding请求。按已保存参考价、64K输入上界、总结512/外层2048输出上限，费用上界 **¥0.29952**。价格或配置变化需重算；估算不是平台扣费硬限额。未来脚本需另加获批批次计数，不能复用已停止的20次批次。

## 修改文件

`backend/app/agent/agent.py`、`calculator.py`、`tools.py`、新增 `long_summary.py`；`backend/app/core/development_budget.py`、`runtime_safety.py`、`web_demo.py`；新增 `backend/tests/test_agent_p1.py`。更新本报告与真实资料验收报告。没有前端业务功能改动，没有业务数据库或索引迁移，也未修改独立发布副本、commit/push。
