# AxiomForge

[**在线交互展示**](https://sheatheli.github.io/AxiomForge/) · [**工程证据与验证索引**](docs/verification-map.md) · [![Smoke CI](https://github.com/SheatheLi/AxiomForge/actions/workflows/smoke.yml/badge.svg)](https://github.com/SheatheLi/AxiomForge/actions/workflows/smoke.yml)

> 以**失败判据、反例和可重复执行的证据**为核心的程序验证工具集合：从SQL事实粒度问题、异步资源生命周期到有界候选变换和输入缩减，不把“测试通过”误认为全系统正确性证明。

**工程状态：** 可执行CLI与多个可独立使用的研究组件，包含局部验证、历史回归及合成规模实验；部分历史来源缺失的测试仍明确跳过。

## 工具解决的问题

- **SQL事实粒度：** 查询仍然返回结果，但JOIN可能引入重复计量。通过独立Oracle审查业务事实是否保持。
- **反例发现：** 在有界搜索空间中生成候选、寻找可复现失败、验证判定依据。
- **失败保持性缩减：** 将庞大错误输入压缩为更小、可独立重放的见证。
- **异步资源协议：** 检查任务取消时连接借出、归还及真实释放的状态关系。

```text
输入程序/数据 ──> 规范/独立Oracle ──> 失败签名
                          │               │
                          └── 变换与缩减 <─┘
                                    ↓
                         最小见证 / 重放收据
```

## 安装和最短演示

```bash
python -m pip install -e .
python -m pytest -q tests/test_cli.py tests/test_authority_and_evidence.py
python 启动审查.py --demo
```

演示出现`Demo: REGRESSION (PASS -> BLOCK)`，含义是**检测到修订SQL破坏了原有正确性合同**，不是表示自动修复已经成功。示例结果写入`results/sample.html`及对应JSON收据。

完整回归采用按测试文件隔离的方式：

```bash
python scripts/verify_all.py --mode quick --per-file-seconds 40
python scripts/verify_all.py --mode full --per-file-seconds 150
```

## 主要代码与证据

| 路径 | 责任 |
|---|---|
| [`src/axiomforge/guard_cegis.py`](src/axiomforge/guard_cegis.py) | 有界条件搜索与反例Oracle |
| [`src/axiomforge/sql_review.py`](src/axiomforge/sql_review.py) | SQL事实粒度审查 |
| [`src/axiomforge/resource_lifecycle.py`](src/axiomforge/resource_lifecycle.py) | 异步资源取消与生命周期 |
| [`src/axiomforge/incremental_reduction.py`](src/axiomforge/incremental_reduction.py) | 失败保持性缩减 |
| [`evidence/regression/全量回归结果.json`](evidence/regression/全量回归结果.json) | 全量回归收据 |
| [`tests/`](tests/) · [`benchmarks/`](benchmarks/) | 行为合同与性能实验 |

## CI与依赖兼容性

[历史缺陷重放与当前依赖兼容性的双轨CI说明](docs/ci-compatibility.md)：历史缺陷见证使用冻结的第三方库版本；当前依赖测试验证版本无关的合同，不强行要求已修复的上游缺陷再次出现。

## 历史测试与规模证据

按44个测试文件隔离执行的历史收据记录**447项：437通过、10跳过、0失败、0超时**。10项跳过中，4项缺少v1.1冻结证据、6项需要未随包提供的上游CSV；不能把这些测试说成已经通过，也不能因此推断独立的上游修复已合并。

在180/360/540行三种合成SQLite见证中，逐行Greedy分别需要181/361/541次Oracle调用；层级方法为50/58/58次，均保留3行可重放见证。来源：[`evidence/sql_scaling_comparison_20261112.json`](evidence/sql_scaling_comparison_20261112.json)。这是对简单Greedy基线的局部算法对照，**不是与成熟程序分析平台的性能对比**。

## 展示与详细验证

- [可交互的反例缩减说明](docs/README.md)；[主证据图](docs/assets/figures/AxiomForge.svg)与[规模对照图](docs/assets/figures/AxiomForge_规模对照.svg)。
- 已有[`LICENSE`](LICENSE)，其授权范围应以文件原文为准。
