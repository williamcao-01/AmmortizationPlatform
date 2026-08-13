# 当前核验范围

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
python -m depreciation_poc.app.demo_server --port 8765
```

验收基准：

- 源文件仅为两份 `20260812` Excel。
- `279` 项资产纳入计算，`149` 项按状态、零净额或空折旧码排除。
- 宽表只含 `2026-06` 台账实际、`2026-07` 和 `2026-08` 规则预测。
- 新启动只有一个 `BASELINE` 场景；历史场景与历史 SQLite 结果不保留。
- 任何超出 `2026-08` 的查询期间被明确拒绝。
- 核验包由 `tools/export_customer_validation_data.py` 与 `tools/build_customer_validation_workbook.mjs` 从当前受控 Excel 重新生成。
