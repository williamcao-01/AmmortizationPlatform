# Asset Depreciation Ontology POC

This repository contains a runnable ontology-assisted asset depreciation POC. Runtime data is restricted to the current fixed customer Excel snapshot.

## Quick Start

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```

## Customer Snapshot POC

The customer POC does not expose a browser upload flow. The only runtime source is `data/customer_snapshot/`, which must contain exactly these two files:

- `资产明细表_资产台账明细_20260812.xlsx`
- `资产相关配置表_20260812.xlsx`

The service refuses to start when either file is missing or any additional Excel workbook is present. The forecast starts in the month after the ledger snapshot and is limited to source-covered driver months.

For the June 2026 customer snapshot, the baseline forecast starts in `2026-07`. Assets marked as stopped, inactive, excluded, zero-net-value, or without a depreciation code are written to the exclusion audit list and are not calculated. Z802 uses the matching monthly block configuration (production, reserves, and configured depletion rate); Z901 uses the monthly workload configuration by organization.

```powershell
$env:PYTHONPATH="src"
$env:DEEPSEEK_API_KEY="<set outside source control>"
$env:DEEPSEEK_MODEL="deepseek-v4-flash"
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
python -m depreciation_poc.app.demo_server --port 8765
```

You can instead copy `.env.example` to `.env` and fill in `DEEPSEEK_API_KEY`. The server loads `.env` at startup; `.env` is ignored by Git.

`DEEPSEEK_BASE_URL` defaults to `https://api.deepseek.com/v1` and can be set for an internal compatible endpoint. The browser never receives the key. If the service is unavailable, the system keeps the deterministic calculation and evidence trace, then displays a template conclusion.

Start the service from the same PowerShell session in which the three `DEEPSEEK_*` variables are set. Check `GET /api/qa/status` after startup: `configured: true` means the wide-table skill will call DeepSeek after its deterministic calculation and Ontology evidence retrieval. The key is intentionally not persisted in source code, SQLite, or browser storage.

## Neo4j Graph Projection

When local Neo4j Community is available, the application projects all business Ontology objects and relations, persisted asset-month calculation records, aggregate records, rule executions, scenario changes, and attribution records into Neo4j. SQLite remains the transactional calculation store during this POC, while the knowledge-graph API reads object relationships and paths from Neo4j using Cypher over Bolt.

Configure the following values in the ignored `.env` file, then restart the service:

```ini
NEO4J_ENABLED=true
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<local-password>
NEO4J_DATABASE=neo4j
```

Open `http://localhost:7474` to inspect the local graph. The in-app `知识图谱` page displays the active graph engine and projected-record counts so the demo can show the live Neo4j data path.

## End-to-End Demo

On first start, the browser demo builds its baseline from the two controlled Excel files. Later starts reopen the persisted baseline and saved What-if scenarios, then refresh their Neo4j graph projection. Replacing the controlled Excel snapshot requires an explicit runtime-data reset before rebuilding the baseline.

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
- `GET /api/forecast-lines?scenario_id=BASELINE&period_from=2026-06&period_to=2026-08`
- `GET /api/anomalies?scenario_id=BASELINE`
- `GET /api/assets/cards?scenario_id=BASELINE`
- `GET /api/semantic-catalog`
- `POST /api/what-if`
- `POST /api/what-if/planned-asset` for backward compatibility

The customer POC uses DeepSeek for business-language expression only. The rule engine calculates all amounts and saves an execution trace before DeepSeek receives a whitelist evidence package.

## Project Shape

- `src/depreciation_poc/domain`: stable DTOs and value objects.
- `src/depreciation_poc/ports`: interface contracts for data access.
- `src/depreciation_poc/infrastructure`: low-coupling adapters, currently CSV.
- `src/depreciation_poc/infrastructure.graph_store`: embedded graph database for ontology triples and materialized inference.
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
