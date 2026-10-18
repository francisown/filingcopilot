# 项目导览

FilingCopilot 用于检索公司披露、计算财务指标，并保存回答所依赖的证据。
浏览器、CLI、REST API 和 MCP 共用研究服务。

## 试用离线示例

按[安装说明](installation.md)完成安装后，运行：

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

该示例不需要网络或模型密钥。macOS/Linux 使用 `.venv/bin/python`。
接着可以在浏览器里查看一次研究：

1. 运行 `start.ps1 -Port 8159`。
2. 选择 Apple / AAPL 和“无模型研究”。
3. 查询 FY2025 营收与净利润。
4. 展开引用，检查报告期间，再导出研究包。

## 代码阅读顺序

| 模块 | 职责 |
|---|---|
| [models.py](../src/filingcopilot/models.py) | 定义事实、声明、请求及保存的回答。 |
| [service_research.py](../src/filingcopilot/service_research.py) | 管理研究生命周期、预算、预览及结果保存。 |
| [service_execution.py](../src/filingcopilot/service_execution.py) | 按请求类型选择流程，准备证据并执行研究。 |
| [tools.py](../src/filingcopilot/tools.py) | 查询证据并执行财务计算。 |
| [verification.py](../src/filingcopilot/verification.py) | 检查引用、范围、数字和完整性；通过回调接入语义复核。 |
| [audit.py](../src/filingcopilot/audit.py) | 导出研究包、离线复核，并按需与原始缓存对照。 |

[架构说明](architecture.md)介绍检索、行情及证据账本的关系。
[独立验证器](independent-verifier.md)可以单独安装，不需要研究 Agent。

## 验证与评测

回归测试覆盖计算规则、来源和日期检查、取消、供应商故障及研究包兼容性。
检索实验使用固定的 200 题；模型实验分别记录交付、延迟和用量。

具体数据和分母见[评测说明](evaluation.md)，近期改动见[更新记录](release-2026-10-18.md)。
完整回答的独立人工评审尚未完成。离线复核检查保存的证据与计算，不会重新判断叙述含义。
