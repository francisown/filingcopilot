# 验证范围与耗时口径

每个命令使用当前解释器，在新进程中运行并记录日志、依赖版本、实际命令与墙钟。环境缓存不默认清空，不把这些结果称为全新机器冷安装的速度。

```powershell
.\.venv\Scripts\python.exe scripts/run_quality_checks.py --profile unit --output artifacts/unit-current.json
.\.venv\Scripts\python.exe scripts/run_quality_checks.py --profile full --cache-state both --output artifacts/full-current.json
.\.venv\Scripts\python.exe scripts/run_quality_checks.py --profile regression --output artifacts/regression-current.json
.\.venv\Scripts\python.exe scripts/run_quality_checks.py --profile frontend --output artifacts/frontend-current.json
```

| Profile | 实际范围 | 不证明什么 |
|---|---|---|
| unit | `pytest -m "not slow"` 选择的规则、范围、身份与导出等单元子集，不是固定文件列表 | 全量兼容、现场供应商或模型表现，以及任意机器上的绝对速度 |
| full | 同一完整收集的冷/热两轮；空 fixture/应用/pytest 缓存，再立即复跑；已有依赖/权重且操作系统缓存未控制 | 干净机器安装或下载速度、真人语义准确率 |
| regression | pytest 完整收集；包含临时资料库、并发、CLI/隔离包及模拟网络故障 | 真人语义、现场所有网络和冷模型 |
| frontend | 所有静态 JS 语法、受控界面脚本 | 实际浏览器绘制与手机触控 |

`unit` 是相对于完整集合的子集名称，不承诺每台机器都快。`fast` 保留为旧命令兼容别名；新输出的 `profile` 和日志统一使用 `unit`，并保留 `requested_profile`。数据库/服务 fixture、并发、重试、子进程和昂贵索引用例通过已注册 `slow` marker 纳入完整门禁。CI 执行 unit 和完整冷/热两轮，失败日志也上传。

历史本机 1265 项完整套件复跑 143.77 秒、先前 136.99 秒均为已有依赖/权重的环境观测。另一环境 Python 3.13.14 约 24 分钟、项目 Python 3.10.11 超过 20 分钟尚未结束的记录同时保留；该环境缓存状态未受控。本机 203 项规则子集 6.394 秒；同一集合在沙箱环境两次约 105/107 秒，归因为沙箱环境开销，未独立复现具体 syscall 原因。

较早 2.84 秒是早期固定文件子集，不能与后来 marker 选取的 203 项集合混作同一实验。初始 unit 为 214 项、1109 项排除，外部计时 6.411 秒；最终（含 CSV 往返和跨午夜修复）unit 为 215 项、1111 项排除，6.003 秒。完整相同 1326 项冷/热两轮分别 151.125 / 137.478 秒。已有依赖/权重、fixture/应用/pytest 缓存口径及操作系统缓存未控制的限制不变，不能把 unit 时间标成全量。原 214/1323 项记录亦保留。

跨午夜的合成供应商日期回归独立运行（也接入 CI）：

```powershell
.\.venv\Scripts\python.exe scripts/check_midnight_fixtures.py
```

此命令模拟收集到执行跨本机日期，检查当前日默认值、过去/未来日期拒绝、API 与测试时钟恢复。修复原 35 项午夜失败只统一测试 fixture 日期，不改变生产知悉日期门禁；失败原日志保留。

## 独立、按需执行的慢测量

```powershell
# 冻结200题、固定已有HF模型，全候选排序消融；不改变运行默认设置
.\.venv\Scripts\python.exe scripts/evaluate_full_pool_rerank.py --output artifacts/rerank-current.json

# 原始HTML来自缓存；--index 创建一个全新的派生索引，沿用已有模型权重
.\.venv\Scripts\python.exe scripts/profile_annual_parsing.py --ticker PDD --index --output artifacts/index-current.json

# 空原文/资料库/向量缓存，真实SEC采集；默认沿用主机网络代理
.\.venv\Scripts\python.exe scripts/validate_cold_preparation.py --ticker PDD --output artifacts/cold-current.json
```

真实采集需要已配置 SEC 联系人，保留所有失败和公开源哈希；脚本不会打印联系人或其他凭据。`--direct-sec` 仅在本次进程里显式选择直接访问 SEC，不能将这种结果冒充用户代理条件下的速度。它不下载模型权重、不调用研究 LLM、不启动生产网页。API 计时、网页轮询和浏览器首屏绘制须分别说明。

完整模型问答、人工盲标、长期行情供应商现场与移动端验收是另外的评测；冻结旧问答记录不因代码迭代自动变成新版本成绩。

质量脚本产生的隔离目录默认是临时的：`validate_cold_preparation.py` 复用主机已有的只读 BGE 权重，不再把数百 MB 模型复制到每个 UUID 目录，成功或失败后默认删除本次 replay；需要保留现场时显式加 `--keep-scope`。`diagnose_retrieval.py`、`profile_annual_parsing.py --index`、`validate_native_eps.py`、wheel/application 隔离检查也默认清理临时数据库、派生索引和安装目录，分别用 `--keep-snapshot`、`--keep-isolated`、`--keep-home` 保留取证副本。报告会记录是否保留。已有历史目录和失败记录不由新脚本自动删除。

## MCP 双传输回归记录

`python scripts/smoke_mcp.py` 用临时库及合成供应商缓存检查 stdio / streamable HTTP。默认生成带 UTC 时间戳的新 JSON，不再覆盖历史 `artifacts/mcp-smoke.json`；可用 `--output` 指定新路径，存在则在启动任何测试进程前拒绝。CI 指定 `artifacts/ci/mcp-smoke.json`，随同其他 CI 记录上传。报告记录耗时和合成数据范围，不能把通过称为现场供应商或模型准确率。
