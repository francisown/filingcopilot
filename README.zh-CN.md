# FilingCopilot

<p align="center">
  <a href="./README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml"><img src="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or later"></a>
</p>

**本地优先的公司财报研究工作台。** FilingCopilot 会准备相关 SEC 文件，检索证据，计算财务数字，并返回可以展开、导出和复核的答案。

![FilingCopilot 工作台](docs/images/workspace.png)

项目坚持三条规则：

- **先找来源，再回答。** 问题会先限定公司和知悉日期。如果本地语料缺少所需文件，资料准备层可以获取支持范围内的公开财报，再进行检索。
- **先有证据，再写说明。** 事实、计算、段落、期间、单位、源 URL 和定位信息保存在证据账本中。没有足够支持时明确列出缺口，不静默补全。
- **结果可以复核。** 研究包保存输入、声明、引用、计算和源文件哈希；独立验证器不需要研究模型或网络即可检查包的一致性。

## 最新记录结果

最近一次 140 题运行使用 DeepSeek 研究、Mimo 审查，输入是冻结的 62 份文件快照。100 道应回答研究题和 40 道设计性边界/策略题分开统计。

| 检查项 | 结果 |
|---|---:|
| 应回答研究题覆盖率 | **91 / 100** |
| 预期的证据不足拒答 | **20 / 20** |
| 行情源策略题 | **20 / 20** |
| 不支持声明 / 数值错误 | **0 / 0** |
| 已交付答案的引用绑定率 | **1.000** |
| 使用模型题延迟 | **P50 90.6 秒 / P95 174.9 秒** |
| 记录的模型 token | **3,753,566** |
| 内部检索 span/page/nugget Recall@5 | **0.615 / 0.635 / 0.710** |
| 内部检索延迟 | **P50 66 毫秒 / P95 168 毫秒** |
| 数值 oracle | **116 个精确匹配，0 个不匹配**；34 条超出检查范围 |
| 回归测试 | **1,840 个通过** |

这些是工程和工作流测量，不是独立人工准确率研究。外部答案支持使用严格的 `full / partial / unsupported` 分类，分母和限制见[评测记录](docs/evaluation.md)。最新机器可读记录只保留[全量报告](docs/validation/live-full-deepseek-mimo-2026-10-30-rerun/report.json)、[失败摘要](docs/validation/live-full-deepseek-mimo-2026-10-30-rerun/failures.json)和[检索快照](docs/validation/retrieval-diversity-2026-10-29.json)。

## 请求如何处理

```text
浏览器 / CLI / REST / MCP
            |
       能力与日期范围
            |
    资料准备与财报入库
            |
      混合检索与重排
            |
  证据账本中的事实、段落和计算
            |
       确定性检查 + 独立审查
            |
 答案、引用、明确缺口和可选研究包
```

年度数字、增长、利润率和趋势等确定性问题不需要模型。需要叙述证据或多步计算的问题才进入工具调用研究流程。浏览器工作台、CLI、REST API 和 30 个 MCP 工具共用同一套服务和证据规则。

## 快速开始

需要 Python 3.10 或以上。内置财报示例不需要 API Key 或 GPU。

### Windows PowerShell

```powershell
.\setup.ps1 -Language zh
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
$env:FC_EMBEDDING_MODE = "off"
.\start.ps1 -Port 8159
```

打开 <http://127.0.0.1:8159/>，选择 **Apple / AAPL** 和 **只看数据**。验证内置研究包：

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

## 开启实时资料和研究

把 `.env.example` 复制为 `.env`，只在本机填写真实值。不要提交 `.env`。

| 配置 | 用途 |
|---|---|
| `FC_SEC_USER_AGENT` | SEC 请求所需的联系人和邮箱 |
| `FC_API_BASE` / `FC_API_KEY` | OpenAI 兼容接口地址和私钥 |
| `FC_MODEL` | 研究模型；仓库示例使用 DeepSeek |
| `FC_REVIEW_MODEL` | 独立审查模型；仓库示例使用 Mimo |
| `FC_RESEARCH_TIMEOUT` | 协作式研究上限；公开示例为 3,000 秒 |
| `FC_EMBEDDING_MODE` | `off` 使用关键词检索，`local` 使用可选本地向量 |
| `FC_EXTRA_HEADERS` / `FC_SESSION_ID` | 可选的供应商请求元数据 |
| `FC_MAX_COST_USD` | 单次费用上限；必须同时配置两项 token 单价，否则启动时拒绝 |

模型请求可能产生供应商费用，应用不会下单交易。准备新的财报需要网络和合规的 SEC 用户代理；已有本地资料可以离线查询。

## 文档

- [架构说明](docs/architecture.md)
- [评测与分母口径](docs/evaluation.md)
- [案例研究](docs/case-study.zh-CN.md) · [Case study](docs/case-study.md)
- [安装说明](docs/installation.md)
- [用户指南](docs/user-guide.md)
- [研究包协议](docs/research-package-protocol.md)
- [独立验证器](docs/independent-verifier.md)
- [安全与威胁模型](docs/security-threat-model.md)
- [外部评测数据集](evaluation/external/README.md)
- [参与贡献](CONTRIBUTING.md)

## 开发检查

```powershell
python -m pytest -q
python -m ruff check src tests scripts
python -m compileall -q src scripts
```

外部评测数据和本地财报缓存不会提交到仓库。获取非商业数据集前，请先阅读[evaluation/external/README.md](evaluation/external/README.md)中的许可说明。

FilingCopilot 是独立工程项目，不构成投资建议，不是生产级信贷决策系统，也不是经过认证的模型。
