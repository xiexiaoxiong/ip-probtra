# 水枪模块五原文输入夹具

本目录只保存决定书证据清单中被作为现有技术使用的 15 份专利原文，以及可重复校验的著录事实、来源、文件大小、页数和 SHA-256。

- 纳入范围：证据 1–4、7–17。
- 排除范围：证据 5（另一份无效决定）和证据 6（专利及评价报告）。
- 不保存决定书对任何技术特征的对应、区别、组合理由或最终结论。
- 不复制决定书本身；运行时只可读取 `target_document/` 与 `source_documents/`。
- 模块五使用时必须以“一项独立权利要求 × 一份原始文献”为单位，重新执行项目自己的全文可读化、附图读取和 I4-S 判断。
- 本夹具仅允许进入模块五测试输入，不得被 I2 检索式生成、同义词扩展、候选排序或生产运行时读取。

`manifest.json` 中的 `conclusions_imported=false` 和 `expected_disclosures=null` 是强制边界；测试会同时核验 15 份 PDF 的文件签名、页数、大小和哈希。

内置输入包括：

- `target_document/CN216205649U.pdf`：目标水枪专利；
- `source_documents/*.pdf`：15 份逐篇输入 I4-S 的原始对比专利；
- `manifest.json`：仅保存身份、日期、来源与文件校验值，不保存任何比对答案。

在 I2 输出已经盲测冻结后，可以执行：

```bash
PYTHONPATH=src .venv/bin/python scripts/run_decision_i4s_blind.py \
  --plans-dir ../.data/invalidity/test/decision-benchmarks/20260801-five-case-v46/plans \
  --output-dir ../.data/invalidity/test/decision-benchmarks/20260801-five-case-v47/i4s \
  --case water_gun_decision_2021227533927
```

运行器逐篇完成后立即原子冻结结果，并按 PDF SHA-256 断点续跑；不会等 15 篇全部结束后才写文件，也不会读取决定书或分析结论。
