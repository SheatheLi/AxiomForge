# 可重复的事后修复诊断：不属于新问题盲测

本目录的 SQLAlchemy #13637、SQLFluff #8709 都在修复算子扩展前**被研究人员阅读过**。它们属于问题已知之后的**开发期诊断**，不能拿来修订 v0.8 的冻结评测 `0/8`，也不能据此宣称 AxiomForge 达成未见问题泛化。

## 复验

先独立取得目标仓库的原始故障版本及其真实依赖；本项目不分发第三方上游源码。仅对自己信任的仓库运行：

```bash
python benchmarks/retrospective_repairs/run.py \
  --sqlalchemy /path/to/known-bad-sqlalchemy-checkout \
  --sqlfluff /path/to/known-bad-sqlfluff-checkout \
  --sqlfluff-deps /path/to/sqlfluff-runtime-dependencies \
  --case sqlfluff --models expanded \
  --max-candidates 6 --max-seconds 70 --allow-exec \
  --out results/sqlfluff-expanded.json
```

`--models old_family_ablation` 在同样配置下屏蔽这次新增的两个候选生成器。总运行时间可能大于 `--max-seconds`，因为一个候选的验收是不可抢占的多测试事务；生产大规模使用时应另外使用系统进程超时或 CI Job Timeout。不可把超时当作“基线确定不会修好”的数学证明。

### 判定约束

* **SQLAlchemy**：多组 IPv6 方括号 host、空端口/非空端口、普通地址与端口要完全对应。两份已有其他贡献者 PR；不能重复计为独占外部贡献。
* **SQLFluff**：`<=>` 自反向安全之外，必须保留 `<`、`>`、`<=`、`>=` 的正确交换。早期“长度等于一”的伪修复已经在此被否决。
* 产出 `VERIFIED_CANDIDATE` 只代表已声明合同中的局部验证通过，不等于上游全面验证与合并。

v0.8 当时的所有修复算子已归档于 `../external_holdout/v08_frozen_source/`，原始 `results.json` 和预注册清单保持不变。旧版评测脚本默认仍要求原算子哈希匹配；使用升级后的修复器再次测试时，必须显式传 `--retrospective`，其结果将标记为 `POST_TUNING_DIAGNOSTIC`。
