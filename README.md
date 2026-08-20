# Asset Depreciation Ontology POC

This repository contains a runnable ontology-assisted asset depreciation POC. Runtime data is restricted to the current fixed customer Excel snapshot.

## Quick Start

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```

## Customer Snapshot POC

The customer POC does not expose a browser upload flow. The only runtime source is `data/customer_snapshot/`, which must contain exactly these three files:

- `资产明细表_资产台账明细_20260812.xlsx`
- `资产相关配置表_20260819.xlsx`
- `组织机构表_所属单位表_20260810.xlsx`

The service refuses to start when any required workbook is missing or any additional Excel workbook is present.

The baseline covers `2025-01` through `2027-12`. Months before the June 2026 ledger snapshot are reconstructed backward from the snapshot opening balance using the supplied monthly variables; June 2026 remains the ledger actual; later months are calculated forward. Missing object-month drivers are zero and are never carried forward from an older month. Assets marked as stopped, inactive, excluded, zero-net-value, or without a depreciation code remain in Ontology but do not enter depreciation calculations.

```powershell
$env:PYTHONPATH="src"
$env:DEEPSEEK_API_KEY="<set outside source control>"
$env:DEEPSEEK_MODEL="deepseek-v4-flash"
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
python -m depreciation_poc.app.demo_server --port 8765
```

You can instead copy `.env.example` to `.env` and fill in `DEEPSEEK_API_KEY`. The server loads `.env` at startup; `.env` is ignored by Git.

`DEEPSEEK_BASE_URL` defaults to `https://api.deepseek.com/v1` and can be set for an internal compatible endpoint. The browser never receives the key. If the service is unavailable, the system keeps the deterministic calculation and evidence trace, then displays a template conclusion.

The general knowledge-chat page defaults to local evidence mode because its prompts may contain asset and depreciation details. Set `KNOWLEDGE_CHAT_ALLOW_EXTERNAL=true` only after approving transmission of the retrieved evidence package to the configured compatible model endpoint.

Start the service from the same PowerShell session in which the three `DEEPSEEK_*` variables are set. Check `GET /api/qa/status` after startup: `configured: true` means the wide-table skill will call DeepSeek after its deterministic calculation and Ontology evidence retrieval. The key is intentionally not persisted in source code, SQLite, or browser storage.

## Neo4j Ontology Store

Neo4j Community is mandatory. All Ontology schema, business objects, relations, asset-month evidence, aggregates, rule executions, scenario changes, and attribution records are stored and queried in Neo4j through Cypher over Bolt. SQLite stores calculation transactions only; its legacy Ontology tables are cleared at startup and never populated.

The Ontology projection preserves every non-empty source column from the three controlled workbooks. It includes all asset-master rows, including calculation-excluded assets, all organization rows, asset-category policy rows, and historical production/workload driver rows. Empty columns are omitted from the active property metadata.

Configure the following values in the ignored `.env` file, then restart the service:

```ini
NEO4J_ENABLED=true
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<local-password>
NEO4J_DATABASE=neo4j
```

Start the configured local Neo4j instance before the application. The application fails fast when Neo4j is unavailable; there is no SQLite Ontology fallback. Open `http://localhost:7474` to inspect the graph.

## End-to-End Demo

On first start, the browser demo builds its baseline from the three controlled Excel files. Later starts reopen the persisted baseline and saved What-if scenarios, then refresh their Neo4j graph projection. When the controlled source fingerprint or calculation version changes, the service resets business instances, scenarios, and results before rebuilding `BASELINE`.

```powershell
$env:PYTHONPATH="src"
python -m depreciation_poc.app.demo_server --port 8765
```

Open `http://127.0.0.1:8765`.

Key API endpoints:

- `GET /api/dashboard?scenario_id=BASELINE`
- `GET /api/wide-table?scenario_id=BASELINE&dimension=department&dimension=asset_category&dimension=asset`
- `GET /api/wide-table/dimensions`
- `GET /api/qa/status`
- `GET /api/reverse-planning/catalog`
- `POST /api/reverse-planning/question`
- `POST /api/wide-table/compare`
- `GET /api/assets/cards?scenario_id=BASELINE`
- `GET /api/knowledge-graph?scenario_id=BASELINE&focus=policy`
- `GET /api/snapshot/status`
- `GET /api/forecast-lines?scenario_id=BASELINE&period_from=2025-01&period_to=2027-12`
- `GET /api/anomalies?scenario_id=BASELINE`
- `GET /api/assets/cards?scenario_id=BASELINE`
- `GET /api/semantic-catalog`
- `POST /api/what-if`
- `POST /api/knowledge-chat/stream` (自然语言查询、反推试算、What-if 草稿、金额对比)
- `POST /api/knowledge-chat/actions/{draft_id}/confirm` (确认草稿并创建场景)
- `POST /api/knowledge-chat/actions/{draft_id}/cancel` (取消草稿)

知识问答中的 What-if 指令只会先生成 30 分钟有效的未落库草稿。用户在对话卡片中确认后，服务端才读取已保存的 assumptions 创建场景并返回与基准场景的金额对比；模型不能直接写入场景。
- `POST /api/what-if/planned-asset` for backward compatibility

The customer POC uses DeepSeek for business-language expression only. The rule engine calculates all amounts and saves an execution trace before DeepSeek receives a whitelist evidence package.

## Project Shape

- `src/depreciation_poc/domain`: stable DTOs and value objects.
- `src/depreciation_poc/ports`: interface contracts for data access.
- `src/depreciation_poc/infrastructure`: Excel ingestion, SQLite calculation-result storage, Neo4j Ontology storage, and environment adapters.
- `src/depreciation_poc/infrastructure.neo4j_graph_store`: mandatory Neo4j Ontology and agent evidence read model.
- `src/depreciation_poc/infrastructure.business_store`: embedded business result database for scenarios, forecast lines, summaries, anomalies, what-if changes, and attribution.
- `src/depreciation_poc/ontology`: semantic category, policy, and code matching.
- `src/depreciation_poc/policy`: policy resolution boundary used by calculation.
- `src/depreciation_poc/validation`: deterministic SHACL-like checks.
- `src/depreciation_poc/calculation`: asset-month depreciation forecasting.
- `src/depreciation_poc/aggregation`: summaries and scenario deltas.
- `src/depreciation_poc/scenario`: what-if changes and scenario cloning helpers.
- `src/depreciation_poc/attribution`: baseline vs what-if difference attribution.
- `src/depreciation_poc/explanation`: switchable OpenAI/template business explanation providers.
- `src/depreciation_poc/harness`: controlled orchestration and explanations.
- `src/depreciation_poc/app`: end-to-end POC pipeline.
- `tests`: module tests plus an end-to-end integration test.

See `docs/ARCHITECTURE.md` for coupling boundaries and module responsibilities.
See `docs/TEST_MATRIX.md` for module-level and end-to-end verification coverage.
See `docs/ASSUMPTIONS_AND_CONFIRMATIONS.md` for current assumptions and choices to confirm before expanding the POC.
