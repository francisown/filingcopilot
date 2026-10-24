# FilingCopilot

<p align="center">
  <a href="./README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml"><img src="https://github.com/francisown/filingcopilot/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or later"></a>
</p>

**证据优先的公司披露研究。每个数字可追溯到来源，每个答案可导出并离线复核。**

FilingCopilot 是一个本地优先的 SEC 财报研究工作台。用中文或英文提问，打开数字背后的原文段落，或把事实、计算和引用导出成冻结证据包，让审查者不需要模型就能复核。

![英文工作台，包含股价图与财务研究控件](docs/images/workspace.png)

*界面示例使用内置财报数据与示意价格；图中行情不是实时报价。*

它比普通 RAG demo 多做的三件事：

- **每个数字可追溯**到文件、报告期、币种、单位和行号定位。
- **证据可冻结**为带 SHA256 清单的包，并离线验证。
- **纯数字问题不需要 LLM**：年度数字、增长、利润率、趋势都在本地语料上确定性计算。

## 记录在案的评测

| 信号 | 结果 |
|---|---|
| 双语评测 | 140 题；100 道研究题：**81 完整、18 部分、1 无答案**；40 道边界题给出明确边界解释 |
| 数值 oracle | **116 条与 SEC CompanyFacts 或算术检查完全一致，0 条不一致**；34 条超出检查器范围 |
| FinanceBench full-filing | 61 份已解析 PDF、113 题：**page hit@5 0.850、page@1 0.478、snippet hit@5 0.858、MRR 0.624**；新增文档 held-out page hit@5 0.773 |
| FinanceBench evidence-page（诊断） | 150 题，company+year，section 3000 + parent 4000：**page hit@5 0.920、page@1 0.707、snippet hit@5 0.920、answer-supported top1 0.427 raw / 0.913 evaluable（69/150）、MRR 0.798** |
| FinDER answer-support | 1,085 道 reasoning 题，category-scoped reference corpus，BM25 + FlashRank：**answer-supported top1 0.794、at5 0.876**；1,039 道 evaluable 题上 **0.826 / 0.911**；ref hit@5 0.371，P95 309ms |
| 测试 | 记录源码通过 **1,656 个测试**；当前分支 **1,718 个全部通过** |
| 入口 | 浏览器、CLI、REST API 和 **30 个 MCP 工具**共用同一研究服务 |
| 代码 | 约 23k 行 Python，77 个源码模块 |

**先看限制再看数字：** 评测是自评的，没有独立人工金标准；使用模型的题目中位延迟 92.8 秒、P95 417.1 秒。FinanceBench evidence-page 是 150 条公开数据、company+year scope，不等于完整 filing 检索；full-filing 是 61 份已解析 PDF、113 题，新增文档 22 题 held-out page hit@5 为 0.773。两者都不是人工相关性或生产准确率。这些是工程测量，不是生产承诺。完整方法和分母见[评测](docs/evaluation.md)。

## 工作方式

![架构：接口、研究服务、混合检索、证据账本、确定性门、冻结证据包、离线验证器](docs/images/architecture.svg)

1. 问题从浏览器、CLI、REST API 或 MCP 进入。
2. 研究服务生成能力计划与预算。
3. LangGraph 工具 Agent 检索段落并调用财务工具。
4. 证据账本保存事实、计算、来源和行号定位。
5. 确定性数值门与叙述门接受、修复或拒绝声明；语义审查检查主语、方向、期间、重大性和证据范围。
6. 答案与明确缺口冻结为证据包；独立验证器不需要 Agent、模型或网络即可复核。

架构见[架构说明](docs/architecture.md)，验证器契约见[独立验证器](docs/independent-verifier.md)。

## 60 秒快速路径（不需要 API Key）

```powershell
.\setup.ps1
$env:FC_EMBEDDING_MODE = "off"
.\start.ps1 -Port 8159
```

打开 <http://127.0.0.1:8159/>，选择 **Apple / AAPL**，选择 **只看数据**，提问
**“What were Apple's FY2025 revenue and net income?”**，打开引用，再导出研究包。也可以直接验证内置示例：

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

同一套证据层也可以跑在合成的信贷风控语料上：

```powershell
python examples/credit-risk/demo.py demo
```

逐步演示见 [3 分钟演示脚本](docs/outreach/demo-script-3min.md)；信贷风控垂直案例见 [examples/credit-risk/README.md](examples/credit-risk/README.md)。

## 从哪里开始

- [案例研究（中文）](docs/case-study.zh-CN.md) · [Case study (English)](docs/case-study.md) — 问题、架构、评测、失败分类和限制。
- [评测](docs/evaluation.md) — 140 题运行、检索金集、负面结果和精确分母。
- [外部评测数据集](evaluation/external/README.md) — FinanceBench、DocFinQA、FiQA 的许可、规模和拉取方式。
- [FinanceBench 测试](docs/financebench-scoped-2026-10-23.md) — 150 道 evidence-page 题（page hit@5 0.920、snippet hit@5 0.920）和 113 道 full-filing 题（0.850/0.858），含新增文档 held-out 与失败案例。
- [研究包协议](docs/research-package-protocol.md) — 冻结包里有什么，如何重放。
- [项目导览](docs/showcase.zh-CN.md) — 离线示例和一条请求的代码阅读顺序。
- [MCP 接入](docs/mcp-tools.md) · [参与贡献](CONTRIBUTING.md) · [完整快速开始](#快速开始)

## 主要功能

| 功能 | 可以做什么 |
|---|---|
| 公司与股票搜索 | 根据名称或代码查找证券，明确选择上市市场；为支持的公司自动准备 SEC 年报等资料。 |
| 股票行情与长期图 | 查看一年、五年、全部历史等区间，悬停读取日期和价格；支持线性与对数刻度，展示币种、数据时间和复权口径。 |
| 年度、季度与 TTM 财务 | 查询营收、利润、现金流等指标，计算同比和利润率；区分单季度、累计、全年和连续十二个月。 |
| 多年财务趋势 | 展示最近三年或五年的营收、盈利和现金流图表，点击数字查看来源；缺失年度保留空缺。 |
| 公司研究资料 | 浏览经营、商业模式、现金流和风险原文，查看可取得的分部、三表、申报版本及历史单季度资料。 |
| 官方公告 | 获取支持公司的近期 SEC 官方披露，查看原文、公开日期和相关事件日期；配置模型后可生成带引用的摘要，语言由本次选择决定。 |
| 公司与基准比较 | 比较所选证券的共同历史区间和可用财务数据，支持加入 SPY 或 QQQ 行情基准，以及可选的当前 TTM 估值对比。 |
| 估值与假设分析 | 查看年度和供应商 TTM 市盈率、支持证券的历史年度估值参考；修改 EPS 增长与期末倍数，或进行来源匹配的现金流敏感性计算。 |
| Agent 研究与追问 | 让模型按问题检索原文、调用工具并形成解释；支持后续提问和六主题公司研究，显示实际执行步骤及已完成内容。 |
| 自选与研究记录 | 保存关注的上市证券，刷新行情，重新打开研究结果，导出研究包或比较研究版本。 |
| 离线复核 | 保存事实、原文、公式、配置和来源摘要，使用独立验证器重新检查结构与算术；提供对应原件缓存时进一步核对来源。 |
| Web、CLI、API 与 MCP | 在浏览器中使用，或通过命令行、REST API 和 MCP 的 30 个工具接入其他助手。 |

行情查询与财报准备分别进行，**看股票图无需先下载年报**。
明确的年度金额、同比、利润率及多年财务趋势可以直接通过代码查询与计算；原因解释、公告摘要和综合研究需要模型配置。

## 数字和引用如何处理

财务数字从来源事实和公式生成，模型负责组织研究步骤和解释。引用保留公司、申报文件、公开日期、
财务期间、币种与单位，计算保留原始输入。网页通常显示最多两位小数，底层计算和导出仍保留原始精度。

一项结论缺少支持、资料口径不匹配或执行没有完成时，结果会说明对应缺口，并保留已经取得的有效内容。
来源详情可以展开查看；研究包用于保存当时使用的资料，而后续刷新不会改写旧研究。

## 快速开始

需要 Python 3.10 或以上。在下载后的项目根目录执行以下命令。
内置财报和财务计算不需要模型密钥；实时行情和新文件下载需要网络。

### Windows PowerShell

```powershell
.\setup.ps1 -Language zh
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item .env.example .env
}
$env:FC_EMBEDDING_MODE = "off"
```

启动工作台：

```powershell
.\start.ps1 -Port 8159
```

安装脚本会选择支持的 Python 版本，复用兼容的 `.venv`，并从 PyPI 安装核心依赖。
解释器选择、可选配置及安装故障处理见[安装说明](docs/installation.md)。

打开 [本地工作台](http://127.0.0.1:8159/)，并保持服务终端运行。停止服务时，在该终端按 `Ctrl+C`。
已经安装过的环境可直接使用启动命令；复制配置文件的步骤会保留现有 `.env`。

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e "."
if [ ! -f .env ]; then
    cp .env.example .env
fi
```

如果配置的镜像无法提供构建依赖，请给安装命令加上 `--index-url https://pypi.org/simple` 重试。

使用关键词检索启动：

```bash
FC_EMBEDDING_MODE=off .venv/bin/python -m filingcopilot serve --port 8159
```

浏览器同样访问 `http://127.0.0.1:8159/`。

### 配置哪些内容

| 配置 | 用途 |
|---|---|
| `FC_SEC_USER_AGENT` | 在线获取 SEC 资料所需的真实联系信息，包含联系人和邮箱。 |
| `FC_API_BASE` | 支持工具调用的 OpenAI 兼容模型接口地址。 |
| `FC_API_KEY` | 该模型服务的访问密钥。 |
| `FC_EXTRA_HEADERS` / `FC_SESSION_ID` | 可选的供应商请求头配置；真实值仅保存在本地。 |
| `FC_MODEL` | 用于研究与解释的模型名称。 |
| `FC_REVIEW_MODEL` | 用于审查分析的模型；建议使用与研究模型不同的模型。 |
| `FC_EMBEDDING_MODE` | `off` 使用关键词检索；`local` 使用可选本地向量检索。 |
| `FC_TWELVE_DATA_API_KEY` | 可选独立日线回退来源的密钥，需要相应供应商权限。 |

首次体验可使用内置财报快照和“无模型研究”。只安装基础依赖时，可将 `FC_EMBEDDING_MODE` 设为 `off`。
需要本地向量检索时，在相应虚拟环境中安装 `.[semantic]`，再将该配置设为 `local`；首次使用会准备模型文件。
更多参数见 [安装与配置](docs/user-guide.md#安装与配置)。

## 如何使用

首次打开时，界面默认英文，不受浏览器语言影响；右上角可选择中文，并在本机记住主动选择的语言。
新研究按当前界面语言生成回答。原始财报摘录和已保存的历史回答保留其原有语言，切换界面不会改写研究包。

### 1. 找到公司，先查看行情

在“研究工作台”输入公司名称或股票代码，例如“亚马逊”或 `AMZN`，点击“查找股票”。
从候选中选择明确的上市证券，查看长期股价图。切换一年、五年或全部历史，将鼠标移到曲线上查看日期和价格。
报价、曲线末日和抓取时间分别显示；来源失败时可重试，已有缓存会保留其原始时间。

### 2. 阅读财报与公司资料

进入“公司研究”，读取已有资料，或点击“检查最新披露”。首次研究支持的公司时，资料准备流程会获取所需财报，
已取得的财务数字和原文会逐步显示。你可以选择报表、财季或申报版本，展开相关原文；
“财务趋势”页面用于查看最近三年或五年的变化。

### 3. 提出具体问题

选择公司和“知悉日期”，填写问题，然后点击“开始研究”。知悉日期决定可使用哪些已公开资料。
例如：

- “FY2022 营收和净利润是多少？”
- “近三年的营收、利润和现金流有什么变化？”
- “最近披露的经营现金流与现金资本开支有什么变化？”
- “这家公司的主要业务和财报披露的风险有哪些？”

“无模型研究”适合数字、趋势及原文查询；“Agent 研究”用于需要检索和解释的问题。
综合了解公司时，可以点击“生成完整公司研究”，分别研究商业模式与分部、增长与最新经营、现金流与资本投入、竞争、风险和估值。
各部分按实际取得的证据交付，未完成的部分会标明状态。研究过程中可以查看步骤、取消任务或重新连接进度。

### 4. 比较公司，修改估值假设

进入“公司对比”，选择研究对象和时间区间，也可以加入 SPY 或 QQQ 基准。
公司研究页提供同行候选，由你决定纳入哪些公司。不同上市市场、币种和每股口径分别保留。

在“估值情景”中修改增长率、期末市盈率、年限和折现率，查看不同假设对应的结果。
现金流敏感性入口在所需年度现金流、现金购置支出和普通股数能够匹配时显示。
这些计算表达你设定的假设；现金流代理折现值与每股结果采用页面明确标注的口径。

### 5. 保存并复查研究

点击数字或引用查看出处与计算输入。将股票加入“自选观察列表”，或在“研究记录”中重新打开结果。
使用“导出研究包”保存当时的事实、原文、公式及研究输出，之后可以离线复核。

无需配置模型或联网，也可以先验证随仓库提供的示例：

```powershell
.\.venv\Scripts\python.exe -m filingcopilot verify-package examples/annual-facts.json
```

macOS / Linux 使用 `.venv/bin/python -m filingcopilot verify-package examples/annual-facts.json`。
复核报告中的 `consistent` 表示已执行的相应检查通过；核对远程原件需要另外提供对应缓存。

## 数据范围

财报研究主要围绕 SEC 公开披露。美国普通发行人可以动态发现，外国发行人的财务提取依赖已登记配置；
股票搜索和行情的覆盖范围与财报覆盖范围分别判断。可取得的财季、分部、公告和历史估值取决于公司及来源。
页面会显示支持的研究入口、数据来源和具体缺口。

历史财务研究使用当时已公开的资料。行情可能延迟；年度市盈率、供应商 TTM 市盈率、历史参照与用户情景各自标明口径。
季度浏览使用明确披露的单季度，未自动推导所有年报第四季度；完整电话会资料和部分跨币种 ADS 桥接仍需补充。

## 技术与扩展

后端使用 Python、FastAPI 和 SQLite。RAG 结合 FTS5 关键词检索与可选本地嵌入，
Agent 通过 LangGraph 编排工具调用；财务计算、来源记录和核验由共享服务层处理。
Web、CLI、REST 与 MCP 使用相同的研究能力和证据规则。

| 文档 | 内容 |
|---|---|
| [使用指南](docs/user-guide.md) | 配置、页面操作、数据口径和常见问题。 |
| [MCP 接入](docs/mcp-tools.md) | 将财报查询、计算和研究能力接入其他助手。 |
| [系统架构](docs/architecture.md) | 服务层、Agent、RAG、证据与复核流程。 |
| [研究包协议](docs/research-package-protocol.md) | 冻结输入、来源信息及离线复核边界。 |
| [独立验证器](docs/independent-verifier.md) | 独立安装和复算已导出的研究包。 |
| [项目导览](docs/showcase.zh-CN.md) | 离线示例、模块职责和代码阅读顺序。 |
| [开发与评测](docs/evaluation.md) | 评测方法、历史记录与当前限制。 |
| [发布检查](docs/publication-checks.md) | 依赖、源码包和发布范围。 |

项目代码采用 [MIT 许可证](LICENSE)。
