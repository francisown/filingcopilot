# FilingCopilot：项目导览

这页用于让第一次看到项目的工程师、面试官或潜在合作者在十分钟内理解它。
建议先看一个可复核的回答，再按边界阅读代码，不需要通读整个仓库。

## 一句话定位

FilingCopilot 是一个以证据为中心的财报研究 Agent：数字结论由有类型的事实和公式生成，
文字结论带有来源引用，保存的研究包可以在不调用模型的情况下由独立验证器重放。

它是个人研究工具和工程实验，不是持牌行情产品、投资顾问，也不声称离线一致就代表叙述或投资判断正确。

## 十分钟、无需 API Key 的演示

仓库自带语料，可以先验证确定性路径：

```powershell
.\setup.ps1 -Language zh
$env:FC_EMBEDDING_MODE = "off"
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

验证不需要网络或模型 Key。继续看浏览器工作台时，在同一个终端运行：

```powershell
.\start.ps1 -Port 8159
```

选择 **Apple / AAPL** 和 **Data only**，询问 FY2025 营收与净利润，打开引用，再导出研究包。
要观察的是回答、来源和计算输入之间的关系。

macOS/Linux 使用 `.venv/bin/python`，并执行：

```bash
FC_EMBEDDING_MODE=off .venv/bin/python -m filingcopilot serve --port 8159
```

## 推荐的代码阅读顺序

1. [`models.py`](../src/filingcopilot/models.py)：事实、声明、请求和研究包契约。
2. [`service_research.py`](../src/filingcopilot/service_research.py)：研究请求、知悉日期和状态。
3. [`service_execution.py`](../src/filingcopilot/service_execution.py)：工具结果、证据和计算。
4. [`audit.py`](../src/filingcopilot/audit.py)：证据账本及来源/期间绑定检查。
5. [`verification.py`](../src/filingcopilot/verification.py)：脱离模型运行时重放研究包。

浏览器、CLI、REST 和 MCP 共用这些规则。完整流程见 [`architecture.md`](architecture.md)。

## 如何理解评测数字

仓库保留冻结的检索和预算实验，用于发现回归，不把它们包装成人类语义准确率或投资表现证明。
当前限制、检索结果和人工审查范围见 [`evaluation.md`](evaluation.md)。后续更有价值的评测是小规模、
盲测、由独立评审完成，并分别报告数字准确率、引用支持率、主动弃答率、延迟和成本。

## 哪些团队可能感兴趣

最适合关注金融数据、研究 Agent、RAG 评测、MCP 集成和本地优先开发者工具的团队。项目的亮点是边界、
证据和可解释的工程取舍，不是多租户部署或生产运维能力。
