# Untested-company module review — 2026-10-13

This check used four companies that were present in the local corpus but were absent from the previous real module spot-check: AAPL, NVDA, MU and JD. Each run used the same company-page module contract, `mode=agent`, Chinese output, knowledge date `2026-10-13`, and the configured research model. No new files were downloaded during these four runs (`auto_acquire=false`); the test therefore measures writing and evidence use on the currently available corpus rather than global company coverage.

The review checked: (1) whether the result answered the module question, (2) whether each statement cited observed evidence, (3) whether numbers and periods stayed bound to typed evidence or exact source text, (4) whether the module kind was respected, and (5) whether gaps were specific rather than boilerplate.

## Results

| Company | Module | Main local forms | Status | Delivered statements | Model calls | Elapsed | Review |
|---|---|---|---|---:|---:|---:|---|
| AAPL | Business model | 10-K, 10-Q, 8-K | partial | 3 analysis | 8 | 160.5 s | All three statements passed inference review; business channels, customers, geographic management and revenue recognition were tied to passages. |
| NVDA | Operating changes | 10-K, 10-Q, 8-K/EX-99.1 | partial | 2 analysis | 10 | 226.9 s | Annual growth calculations and dated operating events were cited; the run ended with a budget gap after retaining the two reviewed statements. |
| MU | Cash flow and capital investment | 10-K, 10-Q, 8-K/EX-99.1 | partial | 2 analysis | 4 | 90.3 s | OCF, net income, gross PP&E purchases and project FCF were bound to typed evidence; the net-capex gap was correctly retained. |
| JD | Listed shares and currencies | 20-F | partial | 2 narrative | 9 | 242.4 s | The rerun obeyed the factual/restatement contract and passed semantic review; the ADS ratio and listing currencies were stated from cited 20-F text. |

The raw outputs, including the initial JD attempt and its rerun, are retained in the local evaluation archive. They are not included in the public source package.

## Generated text and review

### AAPL — Business model

1. **按10-K披露，公司主要通过地理区域管理业务，报告分部为美洲、欧洲、大中华区、日本与其他亚太地区，各区提供相似的硬件、软件产品与服务；同时公司披露其显著比例的净销售额来自单一产品类别，构成对收入集中度的结构性依赖风险。**
   - Evidence: `passage-06107b6702e2540bdd1a`, `passage-175249ee8fb567d15386`
2. **客户覆盖消费者、小型及中型企业、教育、企业与政府等市场；产品通过自有零售与在线门店及直销队伍直接销售，同时借助第三方蜂窝运营商与经销商等间接渠道分销，服务则通过自有服务平台提供。**
   - Evidence: `passage-c2eb8722a01c5ac65118`
3. **收入按控制权转移确认：硬件多在发货时确认，服务按交付时段分期确认；对 App Store 上的第三方应用仅按净额确认留存佣金，第三方产品按总额确认；公司披露毛利与收入对单一产品类别集中、产品/服务结构、渠道、地域、汇率及贸易政策变化存在敏感度。**
   - Evidence: `passage-dd83ecd6c6156ff40bf6`, `passage-ab91e19230687300d2b6`, `passage-175249ee8fb567d15386`

**判断：** 这组文字专业度较好，结论具体，没有把产品、渠道和收入确认混成泛泛的公司介绍。缺口明确指出产品线营收明细未进入本次引用范围。没有给出金额是合理的，因为本模块问题是商业模式，且当前引用没有绑定产品收入数字。

### NVDA — Operating changes

1. **FY2026年度（截至2026-01-25）经营面全面放量：营收、营业利润、净利润与经营活动现金流同比均大幅上升，营业利润率同比小幅回落但仍处高位。**
   - Evidence: six typed calculations, including revenue growth, operating-income growth, net-income growth, OCF growth and two margin calculations.
2. **已实现事件与已签署安排：Vera Rubin已进入量产出货；2026-08-17宣布与SB Energy在PORTS-Pike园区合作为OpenAI部署算力；2026-09-02签署确定性协议拟收购Hugging Face。管理层前瞻计划：同时供应Blackwell与Rubin、面临供应约束并扩大供应商基础。**
   - Evidence: `passage-c0524f01f967e111eedc`, `passage-bee1a28e63c1b1681414`, `passage-ec2fdb32ab619c899135`

**判断：** 经营方向和已实现事件/计划已经分开，结论有价值。`全面放量`和`仍处高位`属于综合判断，虽然通过了独立审查，后续可进一步改成“各项同比计算均为正”并在页面直接显示具体计算卡片，减少主观措辞。运行因预算结束保留 partial，这是正确的状态。

### MU — Cash flow and capital investment

1. **FY2026（截至2026-09-03）经营活动现金流高于当期净利润，并显著高于PP&E购置毛额，项目公式下的“自由现金流”为正，但该差值仅用于经营现金与资本开支的粗略比较，不等同可分配现金，也未扣减融资租赁本金、收购及其他投资。**
   - Typed evidence: OCF `89,675,000,000 USD`; net income `84,969,000,000 USD`; gross cash PP&E purchases `30,712,000,000 USD`; project FCF `58,963,000,000 USD`.
2. **现金PP&E购置毛额同比大幅上升，但公司披露资本支出未必产生预期回报，产能扩建的延迟与投资决策判断失误可能影响长期现金回报，未来现金流入的持续性依赖投资决策与产能爬坡结果，不宜将当期现金流入的增长直接等同于盈利质量改善。**
   - Evidence: `passage-f8e5431757dc417f5a89`, `calc-5acdd0db0f962baf8c72`

**判断：** 这是四组中数字与结论结合最好的一组：正文给判断，页面数字卡片给精确值，边界说明项目 FCF 不是可分配现金。净资本开支缺少出售/激励收款，因此没有伪造净额或净额同比。

### JD — Listed shares and currencies (corrected rerun)

1. **股权与存托结构：JD普通股权分为A类（每股一票）与B类（每股二十票），B类可按一比一转为A类，A类不可反向。一份ADS代表两份A类普通股（面值US$0.00002），在纳斯达克全球精选市场以代码JD交易。**
   - Evidence: `passage-0748196b85cf67c05fe5`, `passage-792c3fd90dcb8f85cd6a`, `tablepassage-71e9f48186dc16dd3c12`
2. **挂牌与交易币种：A类普通股在港交所主板以代码9618（港元柜台）及89618（人民币柜台）挂牌，ADS在纳斯达克挂牌，A类普通股可与ADS互换。作为双重上市，即使考虑币种差异，两市场ADS与A类普通股价格也不必然相同。**
   - Evidence: `passage-2190d60cfe3307989a0d`, `passage-412d3c71ed374ba7aee1`, `passage-f2c79f7d2053457b35e2`

**判断：** 首次运行把整理型模块错误提交为 `analysis`，虽然文本内容大体正确，但契约不合格。新增后端门禁后重跑，最终变为两条 `narrative`，均通过语义审查。关键 ADS 比例、投票权和交易柜台已直接给出，满足“需要数字时给数字”的要求。仍有一条预算缺口，但没有丢掉已审查内容。

## 这轮发现并修复的问题

1. **整理型模块类型没有强制执行。** 后端现在要求分析型模块的定性条目为 `analysis`，整理型模块的定性条目为 `narrative`；数字仍然可以作为独立 typed claim。
2. **ADS 比例只留在原文抽屉。** 公司页现在能从被引用的 20-F 原文中提取明确的 ADS 比例，显示为“原文存托比率”，并绑定原文证据；不会从普通股 EPS 或汇率自行推算。
3. **定向证据和结论卡片已加入上一轮修改。** 经营、现金流和估值模块先准备对应的 typed evidence；没有模型文字但已有可信计算时，页面显示代码生成的确定性结论，并明确这不是模型语义判断。

## 数据覆盖结论

当前四家公司证明系统可以处理不同形态的资料，但还不能回答所有公司研究问题。当前高价值覆盖是：年度 10-K/20-F、可用 10-Q、近期 8-K/EX-99.1、XBRL/结构化事实、现金流计算和限定的行情/估值。下一步应该按问题增加资料，而不是把所有历史文件全部下载：

1. 外国发行人的 6-K，尤其是没有美国式 10-Q 的公司；
2. 分部历史维度和管理层指引的结构化数据；
3. 发行人 IR/电话会原文；
4. DEF 14A 的持股、管理层激励和审计委员会信息；
5. 登记并冻结来源的独立行情和交易所公告。

SEC 的财务报告手册说明，外国私人发行人不适用美国发行人的季度 10-Q 机制，重大中期信息通常通过 6-K 提供，因此 JD 这类公司不能只按 10-K/10-Q 设计。[SEC Financial Reporting Manual](https://www.sec.gov/files/cf-financial-reporting-manual.pdf)

## Limitations

- 这些是四次真实模型运行的质量抽检，不是人工语义准确率或投资判断准确率。
- 使用的是当前本地缓存和已配置模型；本轮没有自动下载新资料。
- 生成时间为 90–242 秒，当前模型服务的限流和审查回合仍是主要耗时来源。
- 报告中的原始 JSON 保留完整 trace、usage、evidence 和 gaps；没有把失败或 partial 状态改写成成功。

代码回归：修复整理型模块门禁后，完整 pytest 仍为 **1439 passed in 175.32 s**；公司模块定向测试为 **10 passed in 5.88 s**，ruff、前端语法、公司工作区 UI 门禁和 locale catalogue 均通过。
