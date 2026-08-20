# 客户数据单一来源架构

运行时只读取 `data/customer_snapshot/` 中受控的三份 Excel：

```text
客户 Excel 快照
  -> CustomerExcelRepository
  -> Domain DTO
  -> Ontology / Policy Resolver
  -> 折旧规则引擎
  -> SQLite 计算结果库 + Neo4j Ontology/证据库
  -> 资产工作台、宽表、场景、问答、反推 API
```

- 启动时校验目录中只能存在资产台账、资产配置和组织机构三份受控 Excel。
- 当源文件指纹或计算版本变化时，保留 ontology metadata 表，清空业务实例、场景和结果后重建 `BASELINE`。
- baseline 覆盖 `2025-01` 至 `2027-12`：快照前按变量反向重建、`2026-06`保留台账实际、快照后向前测算；缺失对象月份变量按0处理。
- Harness 和大模型只接收已计算的证据包；不直接读取 Excel、SQLite 或自行计算金额。
- Neo4j 是唯一 Ontology 存储和问答推理读模型；服务在 Neo4j 不可用时直接失败，不回退 SQLite。
- SQLite 仅保存场景、折旧明细、汇总、异常和变更等计算数据；Ontology metadata、对象和关系表保持为空。
- Ontology 投影保留三张源表所有非空字段，并覆盖全部资产源记录（含计算排除资产）、组织记录、类别政策配置和历史月度驱动配置；全列为空的属性不进入活动 metadata。
