# 给财务 Agent 增加可复核层

一个研究回答需要分开处理数字、原文与分析。引用链接并不能保证断言受到支持，包内校验和也不能认证来源。FilingCopilot 的独立复核工具提供可复算的机械层，语义判断仍须另外评测。

## 接入顺序

1. 冻结原始资料字节和 SHA256，保存发行人、证券、申报版本、公开日期、财务期间、币种与股份基础。
2. 把可计算数字保存为带原文定位的 Fact；计算记录公式、输入证据 ID 和原始精度。输出金额可以缩写，复算保留原精度。
3. 原文段落与表头保持关联，分析必须引用实际观察到的证据。不要把普通摘录或原币利润悄悄当作美元 ADS EPS。
4. 导出版本化 `filingcopilot.research-package/v1`；schema 位于 `docs/research-package-schema-v1.json`。适配另一个 Agent 时须提供真实的这些字段，不能仅将 Markdown 包在 JSON 外壳中。
5. 在独立进程安装本地 `filingcopilot-verify` wheel，并显式指定调用者信任的原文缓存。复核不启动模型、工作台或联网获取资料。

```powershell
python -m pip install .\dist\filingcopilot_verify-1.0.0-py3-none-any.whl
filingcopilot-verify research-package.json `
  --source-cache .filingcopilot\sec-cache `
  --corpus-home .filingcopilot `
  --market-cache .filingcopilot\market-cache `
  --external-cache .filingcopilot\external-cache `
  --output audit.json --badge consistency.svg
```

只有调用者选定的缓存属于原文对照来源，不从研究包中的任意 URL 发请求。资料库以只读连接打开。构建通过 AST 依赖闭包复用应用审计源码，清单记录模块、资源、CLI 与版本源码哈希，避免复制一套不同规则。

## 徽章可以表达什么

本地 SVG 写明包字节 SHA256、机械一致性检查、原文对照是否请求及检查数量。缺少原文时不会暗示它已检查原件；失败时不会显示 PASSED。它不是签名、出处认证、人工语义支持率或投资质量认证。把包、完整复核结果及其对照范围一起分享才便于他人判断。

语义分析仍须用独立真人查看断言与引用。离线盲标页面提供无预填标签、隐藏审查结果的工作流；样本支持率只适用于相应样本，不能从少量已交付断言推导全部请求或被拒草稿的审查漏检率。

当前 wheel 和 SVG 只在本地生成，没有发布到 PyPI、创建公开徽章服务或代表外部 Agent 做真实对照实验。
