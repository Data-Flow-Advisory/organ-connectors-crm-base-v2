# organ-connectors-crm-base-v2

A pure, stdlib-only **organ** that decides a CRM connector's **schema** and
**preview** policy: how a data schema is derived from a sampled contacts result,
how a contacts preview is planned, and which CRM entities/capabilities the
connector exposes.

> Clean re-extraction of `organ-connectors-crm-base` (which went RED on its
> conformance Action). Per *"a failed organ can be forked"*, this is a fresh,
> from-source rebuild rather than an in-place patch.

## What is an Organ?

An organ is a small, self-contained decision-maker that conforms to the
orchestrator protocol:

- **Input**: reads `{state, context}` JSON on stdin or via `$ORGAN_INPUT`
  (a JSON value *or* a path to a JSON file).
- **Output**: writes `{output, rationale, self_metric}` JSON to stdout, where
  `self_metric.confidence` is a float in `[0.0, 1.0]`.
- **Pure**: no database calls, no network/CRM I/O, no side effects — every input
  arrives as JSON.
- **Deterministic**: identical input → identical output, every run.
- **Stdlib-only**: depends only on the Python standard library (Python 3.12+).
- **Fail-safe**: never crashes on bad input; on malformed/empty `state` it falls
  back to an **empty schema** (never a confidently-populated false schema).

## What was extracted

The source — discovery-engine
`lib/dataflow_core/connectors/crm_base.py` (`AbstractCRMConnector`) — mixes two
concerns:

1. **side-effecting CRM I/O** — the abstract `get_contacts` / `get_deals` /
   `get_companies` / `search` / `write_back` methods that hit a live CRM, and
2. **pure connector policy** — the decisions taken *around* that I/O.

This organ extracts only (2). It never calls a CRM. Faithful mapping:

| `AbstractCRMConnector`                                     | organ decision |
|------------------------------------------------------------|----------------|
| `get_schema()` builds a `DataSchema` from `get_contacts(limit=1)` | `derive_schema()` over the handed-in `state.contacts` sample |
| `if not contacts.rows: DataSchema(columns=[], row_count=0)` | empty `rows` → **empty schema** (even if column names are present) |
| `[ColumnInfo(name=c, data_type="string") for c in cols]`   | `[{"name": c, "data_type": "string"}, ...]` |
| `row_count=contacts.row_count`                             | `schema.row_count` carried from the sample |
| `_source_name` (`self.__class__.__name__`)                | `state.source_name` (default `"unknown"`) |
| `sample(n=5)` → `get_contacts(limit=n)`                   | `sample_plan = {"entity": "contacts", "limit": n}` |
| abstract `get_contacts` / `get_deals` / `get_companies`   | `entities = ["contacts", "deals", "companies"]` |
| abstract `search` / `write_back`                          | `capabilities = {"search": true, "write_back": true}` |

### Key faithful invariant

A contacts sample with **no rows** yields an **empty schema** even when column
names are present — mirroring the source's
`if not contacts.rows: return DataSchema(columns=[], row_count=0, ...)`. This is
the central, plannable behavioural claim and is pinned by tests.

## Contract

### Input

```json
{
  "state": {
    "source_name": "HubSpotConnector",
    "contacts": {
      "columns": ["email", "first_name"],
      "rows": [["ada@example.com", "Ada"]],
      "row_count": 4210
    }
  },
  "context": {
    "sample_size": 5,
    "column_data_type": "string"
  }
}
```

### Output

```json
{
  "output": {
    "schema": {
      "columns": [{"name": "email", "data_type": "string"}, {"name": "first_name", "data_type": "string"}],
      "row_count": 4210,
      "source_name": "HubSpotConnector"
    },
    "schema_empty": false,
    "sample_plan": {"entity": "contacts", "limit": 5},
    "entities": ["contacts", "deals", "companies"],
    "capabilities": {"search": true, "write_back": true}
  },
  "rationale": "Derived a 2-column schema for 'HubSpotConnector' ...",
  "self_metric": {"confidence": 1.0, "schema_empty": false, "column_count": 2, "source_name": "HubSpotConnector"}
}
```

### Confidence

| situation | `confidence` |
|-----------|--------------|
| populated contacts sample → full schema derived | `1.0` |
| connected but zero-row sample → faithful empty schema | `0.5` |
| empty / malformed state → fail-safe empty schema | `0.0` |

## Run it

```bash
# from a file
ORGAN_INPUT=samples/hubspot_populated.json python3 organ.py

# from a JSON value
echo '{"state": {"contacts": {"columns": ["a"], "rows": [[1]], "row_count": 1}}}' | python3 organ.py
```

## Develop

```bash
python -m pytest -q        # unit + CLI + sample tests
python3 check_contract.py  # contract gate (same as CI)
```

The `conformance` GitHub Action runs both on every push/PR.
