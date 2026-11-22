# 验证路线｜独立Oracle、最小反例与历史收据

本项目包含多条程序分析与故障复现路径。**最小见证和真实故障贡献、工具通过和被测程序正确，是不同层面的判断。**

## 工程证据矩阵

| 命题 | 源码／运行入口 | 证据边界 |
|---|---|---|
| SQL事实粒度错误可复现 | `src/axiomforge/sql_review.py`、`tests/` | 查询返回值不为空不意味着聚合业务语义正确 |
| 独立Oracle与反例缩减 | `src/axiomforge/incremental_reduction.py`、`evidence/sql_scaling_comparison_20261112.json` | 三种合成SQLite见证；不是成熟验证平台的全面比较 |
| 异步资源取消时的最终状态 | `src/axiomforge/resource_lifecycle.py`、`tests/test_resource_lifecycle.py` | Close调用不必然等于真实资源已释放 |
| 历史隔离回归记录 | `evidence/regression/全量回归结果.json` | 447项中437通过、10跳过；跳过不是通过 |

## CI修复与复核

首次公开CI在pytest收集期间进入`targets/pandas_cases.py`，出现`ModuleNotFoundError: pandas`。原因是基础安装声明无`pandas`，而CLI入口导入了依赖pandas与SQLAlchemy的真实目标适配。此次将`numpy`、`pandas`、`SQLAlchemy`纳入核心依赖；其余额外功能仍遵循项目中独立可选依赖。**修改依赖不自动等于新一轮回归通过**。

```bash
python -m pip install -e '.[test]'
python -m pytest -q tests/test_cli.py tests/test_authority_and_evidence.py tests/test_resource_lifecycle.py
```

历史[完整回归收据](../evidence/regression/全量回归结果.json)必须保留测试跳过的来源条件；未来应独立记录新的GitHub Actions实跑结果。
