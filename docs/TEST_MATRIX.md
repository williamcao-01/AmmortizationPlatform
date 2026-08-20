# 当前核验范围

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
python -m depreciation_poc.app.demo_server --port 8765
```

验收基准：

- 源文件仅为三份受控 Excel：`资产明细表_资产台账明细_20260812.xlsx`、`资产相关配置表_20260819.xlsx`、`组织机构表_所属单位表_20260810.xlsx`。
- `279` 项资产纳入计算，`149` 项按状态、零净额或空折旧码排除。
- 宽表包含 `2025-01` 至 `2027-12` 共36个月，`2026-06`为台账实际锚点。
- 首次启动创建一个 `BASELINE` 场景；在源文件指纹和计算版本未变时，保存的 What-if 场景可跨重启保留。任一项变化时，业务结果与场景会重建。
- 任何超出 `2025-01` 至 `2027-12` 的查询期间被明确拒绝。
- 核验包由 `tools/export_customer_validation_data.py` 与 `tools/build_customer_validation_workbook.mjs` 从当前受控 Excel 重新生成。
