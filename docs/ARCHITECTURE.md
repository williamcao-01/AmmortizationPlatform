# 客户数据单一来源架构

运行时只读取 `data/customer_snapshot/` 中受控的两份 Excel：

```text
客户 Excel 快照
  -> CustomerExcelRepository
  -> Domain DTO
  -> Ontology / Policy Resolver
  -> 折旧规则引擎
  -> SQLite 业务结果库 + 图数据库
  -> 资产工作台、宽表、场景、问答、反推 API
```

- 启动时校验目录中只能存在 `资产明细表_资产台账明细_20260812.xlsx` 和 `资产相关配置表_20260812.xlsx`。
- 启动时重置业务库与图数据库，只建立当前数据的 `BASELINE` 场景。
- 当前源数据的快照月为 `2026-06`，只预测配置表覆盖的 `2026-07`、`2026-08`。
- Harness 和大模型只接收已计算的证据包；不直接读取 Excel、SQLite 或自行计算金额。
