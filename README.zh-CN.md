# FilingCopilot

<p align="center">
  <a href="./README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml"><img src="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or later"></a>
</p>

**针对一份美国公司财报提问，得到一个可以核对的答案。**

FilingCopilot 是一个在本地运行的 SEC 财报研究工作台。它会找到相关年报或季报，抽取原文段落，计算财务数字，并把每个结论的来源展示出来。如果财报里没有足够证据，它会明确说“缺证据”，而不是编一个答案。

![FilingCopilot 工作台与 AAPL 价格图](docs/images/workspace-chart.png)

*工作台：价格历史、财报研究和来源核对放在同一个页面。*

## 它能做什么

- 回答营收、利润、利润率、现金流、风险，以及财报中的具体表述。
- 数字类问题由固定规则和计算处理；只有需要阅读大段原文或多步推理时，才会调用 AI 研究模型。
- 返回简短结论、引用的原文段落、由代码计算出的数字，并明确说明缺少什么。
- 可以导出完整的研究包，让独立验证器在离线状态下复核。

## 你可以问什么

- “苹果 FY2025 营收和净利润是多少？”
- “微软的营业利润率为什么变化？”
- “台积电披露了哪些监管、需求和客户集中度风险？”
- “这份财报是否明确否认需求下降？”

## 为什么不一样

- **每个答案都带出处。** 每个结论都指向财报原文或由代码计算出的结果。
- **缺证据就明说。** 不会用流畅的文字去猜；答案会列出缺口或直接拒答。
- **可以离线复核。** 导出的研究包不需要研究模型或网络，独立验证器就能检查。

## 最新快照

最新本地运行：2026-11-02，140 题，DeepSeek 研究、Mimo 审查，同时并行处理 4 道题。100 道本应能回答的研究题和 40 道边界/策略题分开统计。

| 项目 | 结果 |
|---|---:|
| 本应能回答的题中，给出完整答案 | **97 / 100** |
| 预期拒答 / 行情源策略题 | **20 / 20 · 20 / 20** |
| 无来源支持的结论 / 数值错误 | **0 / 0** |
| 给出的结论都带来源引用 | **100%** |
| 回归测试 | **2,307 passed** |

100 道本应能回答的题中，3 道给出了带明确缺口的部分回答，其余为完整回答。完整延迟数据、token 数、数值审计和具体题目见[评测记录](docs/evaluation.md)。这些是工程和工作流层面的测量，不是独立的人工准确率评估。

**速度：** 数字类问题通常不到一秒；需要阅读大段原文的叙述题会调用模型和独立审查，可能要几分钟。最新模型题 P95 为 842.1 秒，完整拆分见评测记录。

## 60 秒试用

需要 Python 3.10 或以上。内置财报示例不需要 API Key 或 GPU。

### Windows PowerShell

```powershell
.\setup.ps1 -Language zh
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
$env:FC_EMBEDDING_MODE = "off"
.\start.ps1 -Port 8159
```

打开 <http://127.0.0.1:8159/>，选择 **Apple / AAPL**，模式选 **只看数据**。验证内置的研究包：

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e "."
cp .env.example .env
FC_EMBEDDING_MODE=off .venv/bin/python -m filingcopilot serve --port 8159
```

完整安装和故障处理见[安装说明](docs/installation.md)。

## 你会得到什么

每个答案包括：

- 简短结论；
- 引用的原文段落或由代码计算出的数字；
- 如果请求的某部分缺少证据，会明确列出缺口；
- 可选的研究包，可导出并离线复核。

## 它如何工作

```text
浏览器 / CLI / REST / MCP
            |
        能力与日期范围
            |
    资料准备与财报入库
            |
      混合检索与重排
            |
 事实、段落和计算，连同来源一起保存
            |
       固定检查 + 独立审查
            |
 答案、引用、明确缺口和可选研究包
```

年度数字、增长、利润率和趋势这类问题由固定规则处理，不需要模型。只有需要阅读大段原文或多步计算的问题，才会进入 AI 研究流程。浏览器工作台、CLI、REST API 和 30 个 MCP 工具共用同一套服务和证据规则。

## 适合谁

- 分析师、投资者、学生，以及需要认真读财报的人。
- 如果你需要本地或私有部署：除了你自己配置的模型/API 调用，数据留在本机。

它不构成投资建议，不是交易系统，不是生产级信贷决策系统，也不能替代阅读原文和独立人工复核。

## 开启实时资料和研究

把 `.env.example` 复制为 `.env`，只在本机填写真实值。不要提交 `.env`。

| 配置 | 用途 |
|---|---|
| `FC_SEC_USER_AGENT` | SEC 请求所需的联系人和邮箱 |
| `FC_API_BASE` / `FC_API_KEY` | OpenAI 兼容接口地址和私钥 |
| `FC_MODEL` | 研究模型；仓库示例使用 DeepSeek |
| `FC_REVIEW_MODEL` | 独立审查模型；仓库示例使用 Mimo |
| `FC_RESEARCH_TIMEOUT` | 单次研究的总体时间上限；公开示例为 3,000 秒 |
| `FC_WATCHLIST_TRIGGERS_ENABLED` | 自选列表：开启后才会扫描并创建研究任务；关闭时只报告候选 |
| `FC_EMBEDDING_MODE` | `off` 使用关键词检索，`local` 使用可选本地向量 |
| `FC_EXTRA_HEADERS` / `FC_SESSION_ID` | 可选的供应商请求元数据 |
| `FC_MAX_COST_USD` | 单次费用上限；必须同时配置两项 token 单价，否则启动时拒绝 |

模型请求可能产生供应商费用，应用不会执行交易。准备新的财报需要网络和合规的 SEC 用户代理；已有本地资料可以离线查询。

## 文档

- [架构说明](docs/architecture.md)
- [评测与分母口径](docs/evaluation.md)
- [案例研究](docs/case-study.zh-CN.md)
- [安装说明](docs/installation.md)
- [用户指南](docs/user-guide.md)
- [研究包协议](docs/research-package-protocol.md)
- [独立验证器](docs/independent-verifier.md)
- [安全与威胁模型](docs/security-threat-model.md)
- [外部评测数据集](evaluation/external/README.md)
- [参与贡献](CONTRIBUTING.md)

## 开发检查

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src scripts
```

外部评测数据和本地财报缓存不会提交到仓库。获取非商业数据集前，请先阅读[evaluation/external/README.md](evaluation/external/README.md)中的许可说明。

FilingCopilot 是独立工程项目，不构成投资建议，不是生产级信贷决策系统，也不是经过认证的模型。
