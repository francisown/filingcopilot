# ADS 输入准备核查（2026-10-12）

本轮完成的是可复核的输入清单，**尚未启用 PDD/BABA 普通股到美元 ADS 的 EPS 或市盈率转换**。沿用已保留的两份 SEC 原件，逐份核对 SHA256，不重新下载、不调用模型，也不改人工标签或金标。原币普通股 EPS 与现有网页行为保持不变。

## 原件与实际数据

原件定位来自 `artifacts/third-round-cold-PDD-2026-10-11.json`、`artifacts/third-round-cold-BABA-2026-10-11.json` 的最新年度 source。清单保留概念、原文数字、scale、decimals、符号、context/unit ID、期间、公开日期和原件 SHA256；完整候选及拒绝原因保留在本地档案，未随公开副本发布。

| 原件 | 年度期间 | 稀释分子原文 | 稀释加权股数原文 | scale / decimals | 普通股稀释 EPS |
|---|---|---:|---:|---|---:|
| [PDD 20-F](https://www.sec.gov/Archives/edgar/data/1737806/000110465926050727/pdd-20251231x20f.htm)，2026-04-29 提交 | 2025-01-01 至 2025-12-31 | 97,842,539 CNY | 5,929,576 股 | 两个底层数均为 3 / -3 | 16.50 CNY，decimals=2 |
| [BABA 20-F](https://www.sec.gov/Archives/edgar/data/1577552/000119312526231755/baba-20260331.htm)，2026-05-20 提交 | 2025-04-01 至 2026-03-31 | 105,803 CNY | 19,235 股 | 两个底层数均为 6 / -6 | 5.50 CNY，decimals=2 |

表中的分子、股数是**原文表示**，必须分别乘 `10^scale` 才得到报告数值。例如 BABA 分子为 105,803 百万元、分母为 19,235 百万股。它们仍有披露精度限制，扩展为整数不会增加有效精度。

PDD 原件 SHA256：`fdd73a23aca29745b75f9789eaacd6f09285c76aa43a8ff93d3dd7fc0e392eba`。

BABA 原件 SHA256：`45383ad6f5734a8b2ed5e38a763890a321b089f000e2bea8331cb7a245150dff`。

两份原件都存在专门的 `NetIncomeLossAvailableToCommonStockholdersDiluted`。BABA 的基本分子 105,904 与稀释分子 105,803 不同，因此基本利润、合并利润和稀释分子不能互换。PDD 本年度两种分子披露值相同，也不能据此建立对其他年度或公司的替换规则。

## 转换为什么仍未就绪

SEC 的 decimals 说明将有限值与报告的舍入或截断精度关联，INF 表示报告声明的精确金额；scale 与 decimals 是不同属性。本轮从原件看到的是 -3/-6/2，没有把补零后的整数或两位 EPS 认作未舍入输入。[SEC EDGAR XBRL Guide，2026-03，§6.6.4、§6.7.6](https://www.sec.gov/files/edgar/filer-information/specifications/xbrl-guide-2026-03-11.pdf)

两家公司当前清单均返回 `ready=false`、`ads_eps=null`、`ads_pe=null`，缺口为：

- 绑定原件、证券股份口径与有效日期的 ADS 比例输入。
- 带日期、出处与换算方向的汇率快照。
- 同一原币年度的未舍入稀释分子和加权稀释股数；现有候选只满足有限披露精度。

前两项是**本轮没有收集并验收的输入**，不代表年报没有比例说明，也不代表市场不存在汇率。脚本不会从普通股/ADS EPS 的商猜比例，不选择汇率日期，不用基本利润补稀释分子。若之后采用披露精度下的区间估计，应单独设计和验收精度边界，不能称为当前未舍入输入要求已完成。

## 范围、命令与验证

`scripts/inspect_ads_inputs.py` 是只读诊断工具。它限定已登记原币 US-GAAP 20-F/20-F/A、公开日期内的当前年度、合并实体无维度、唯一 ID 与原币单位，保留拒绝候选。它只匹配列出的五个标准概念及所支持的数值表示，**不是完整 XBRL 处理器**；未匹配自定义概念、前缀别名或其他数值格式，不等于原件没有相关披露。没有提交语义核验或新增 API/MCP/UI 功能。

```powershell
.\.venv\Scripts\python.exe scripts/inspect_ads_inputs.py `
  --replay artifacts/third-round-cold-PDD-2026-10-11.json `
  --replay artifacts/third-round-cold-BABA-2026-10-11.json `
  --output artifacts/ads-inputs-new.json
```

输出必须是新路径，存在时在读取原件之前退出；写入使用 exclusive create，防止并发覆盖。复现沿用保留的原件缓存及已有项目虚拟环境，不代表重新现场采集。

本轮最终两份原件解析 2.134 秒（脚本 main 内，排除解释器启动和 import），两家公司零转换结果、零供应商获取/模型调用。17 个新规则案例覆盖有限精度、基本/合并利润误用、实体/币种/维度/重复 ID、缺失与非正股数、矛盾候选、期间错配、超长精确数与亏损符号、原件改动和公开日期。连同已有 ADS/外国发行人相关回归 78 项通过（pytest 10.50 秒、外部墙钟 11.498 秒）。最终 unit 子集 232 项通过、1111 项排除，外部墙钟 6.129 秒；已有依赖/主机缓存，不是全量或冷安装速度。ruff 初次三条 E402 失败及修复后通过日志均保留。

应用模块和前端未改，未重跑历史 140 题/40 次模型评测、全量 1326 冷/热两轮、冻结金标召回评测或浏览器/MCP 现场验收。原定真人验收仍未完成，后续阶段不因规则测试解锁。机器规则通过不表示模型语义或投资判断准确。
