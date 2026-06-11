#!/usr/bin/env python3
"""
CRM-Connector-Base Organ — pure schema/preview policy extracted from
discovery-engine `lib/dataflow_core/connectors/crm_base.py`
(the orchestrator directive's `app/services/connectors.crm_base.py`).

A pure, stdlib-only decider that reads {state, context} JSON on stdin (or via
the ORGAN_INPUT env var) and writes {output, rationale, self_metric} on stdout.

It performs NO network/API calls. The original `AbstractCRMConnector` mixes two
concerns:
  1. side-effecting CRM I/O — the abstract `get_contacts` / `get_deals` /
     `get_companies` / `search` / `write_back` methods that hit a live CRM, and
  2. pure connector *policy* — the decisions taken AROUND that I/O: how a schema
     is derived from a sampled contacts result, how a preview is planned, and
     which CRM entities/capabilities the connector exposes.

This organ extracts only (2). Every input arrives as JSON; nothing is fetched.

Faithful mapping from crm_base.py:

    AbstractCRMConnector                              organ decision
    ------------------------------------------------  ------------------------------
    get_schema(): contacts = get_contacts(limit=1)    state.contacts is the handed-in
                                                      sample (no fetch here)
      if not contacts.rows:                           rows falsy  -> empty schema
        DataSchema(columns=[], row_count=0, src)        (columns=[], row_count=0)
      else:                                           rows present-> derive columns
        columns=[ColumnInfo(name=c,                     [{name:c, data_type:"string"}
                  data_type="string")                    for c in contacts.columns]
                 for c in contacts.columns]
        row_count=contacts.row_count                   schema.row_count = contacts.row_count
        source_name=self._source_name                  schema.source_name = state.source_name
    sample(n=5): return get_contacts(limit=n)         sample_plan {entity:"contacts",
                                                                   limit: sample_size}
    _source_name -> self.__class__.__name__           state.source_name (default "unknown")
    abstract get_contacts/get_deals/get_companies     entities: ["contacts","deals",
                                                                  "companies"]
    abstract search / write_back                      capabilities: search + write_back

Key faithful invariant (preserved exactly): a contacts sample with NO rows yields
an EMPTY schema even when column names are present — mirroring `if not
contacts.rows: return DataSchema(columns=[], row_count=0, ...)`. Column
data_type is "string" for every derived column, matching the source's
`ColumnInfo(name=c, data_type="string")`.

Contract:
  INPUT:  {
    "state": {
      "source_name": "HubSpotConnector",      # connector identity (_source_name)
      "contacts": {                            # result of get_contacts(limit=1)
        "columns": ["email", "first_name"],
        "rows": [["a@b.com", "Ada"]],
        "row_count": 4210
      }
    },
    "context": {
      "sample_size": 5,                        # n for sample() (default 5)
      "column_data_type": "string"             # data_type for derived columns
    }
  }

  OUTPUT: {
    "output": {
      "schema": {
        "columns": [{"name": "email", "data_type": "string"}, ...],
        "row_count": 4210,
        "source_name": "HubSpotConnector"
      },
      "schema_empty": false,
      "sample_plan": {"entity": "contacts", "limit": 5},
      "entities": ["contacts", "deals", "companies"],
      "capabilities": {"search": true, "write_back": true}
    },
    "rationale": "<why>",
    "self_metric": {"confidence": 1.0, ...}
  }

The organ is pure, deterministic, stdlib-only (Python 3.12+), and fails safe to
an EMPTY schema (never a confidently-populated false schema) on malformed or
empty state.
"""

import json
import os
import sys
from typing import Any, Dict


# CRM read primitives — the abstract get_* methods every CRM connector exposes.
_CRM_ENTITIES = ["contacts", "deals", "companies"]
# CRM capabilities — the abstract search() / write_back() methods.
_CRM_CAPABILITIES = {"search": True, "write_back": True}

# crm_base.py assigns every derived column data_type="string".
_DEFAULT_COLUMN_DATA_TYPE = "string"
# sample(n=5) default.
_DEFAULT_SAMPLE_SIZE = 5
# _source_name falls back to the connector class name; "unknown" when absent.
_DEFAULT_SOURCE_NAME = "unknown"


def _coerce_int(value: Any, default: int) -> int:
    """Best-effort int coercion that never raises (fail-safe)."""
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _resolve_source_name(state: Dict[str, Any]) -> str:
    """Mirror _source_name: the connector identity, defaulting to 'unknown'."""
    name = state.get("source_name")
    if isinstance(name, str) and name.strip():
        return name
    return _DEFAULT_SOURCE_NAME


def _resolve_column_data_type(context: Dict[str, Any]) -> str:
    """Data type assigned to every derived column (source uses 'string')."""
    dt = context.get("column_data_type")
    if isinstance(dt, str) and dt.strip():
        return dt
    return _DEFAULT_COLUMN_DATA_TYPE


def derive_schema(
    contacts: Any, source_name: str, column_data_type: str
) -> Dict[str, Any]:
    """Pure port of AbstractCRMConnector.get_schema().

    Given a sampled contacts result (the handed-in `get_contacts(limit=1)`
    output), produce the DataSchema-shaped dict. Faithful behaviour:

      * `if not contacts.rows` (missing/empty/malformed) -> EMPTY schema:
        columns=[], row_count=0.
      * otherwise -> one ColumnInfo per contacts column, all data_type=string,
        row_count carried from contacts.row_count.

    Crucially, empty `rows` yields an empty schema EVEN when column names are
    present — this is the exact source behaviour.
    """
    empty = {"columns": [], "row_count": 0, "source_name": source_name}

    if not isinstance(contacts, dict):
        return empty

    rows = contacts.get("rows")
    if not rows:  # None, [], or absent -> empty schema (mirrors `if not contacts.rows`)
        return empty

    columns = contacts.get("columns")
    if not isinstance(columns, list):
        columns = []

    return {
        "columns": [
            {"name": c, "data_type": column_data_type} for c in columns
        ],
        "row_count": _coerce_int(contacts.get("row_count"), 0),
        "source_name": source_name,
    }


def resolve_sample_plan(context: Dict[str, Any]) -> Dict[str, Any]:
    """Pure port of AbstractCRMConnector.sample(n): preview contacts at limit n."""
    size = _coerce_int(context.get("sample_size"), _DEFAULT_SAMPLE_SIZE)
    if size < 1:
        size = 1  # a non-positive preview is meaningless; floor at 1 row
    return {"entity": "contacts", "limit": size}


def decide(state: Any, context: Any) -> Dict[str, Any]:
    """Decide a CRM connector's schema + preview policy from sampled state.

    Pure, deterministic, side-effect-free. Fails safe to an empty schema with
    confidence 0.0 on malformed/empty input.
    """
    if not isinstance(state, dict):
        state = {}
    if not isinstance(context, dict):
        context = {}

    source_name = _resolve_source_name(state)
    column_data_type = _resolve_column_data_type(context)
    contacts = state.get("contacts")

    schema = derive_schema(contacts, source_name, column_data_type)
    sample_plan = resolve_sample_plan(context)
    schema_empty = len(schema["columns"]) == 0

    output = {
        "schema": schema,
        "schema_empty": schema_empty,
        "sample_plan": sample_plan,
        "entities": list(_CRM_ENTITIES),
        "capabilities": dict(_CRM_CAPABILITIES),
    }

    # Confidence reflects how much we actually learned about the schema.
    if not isinstance(state, dict) or not state:
        confidence = 0.0
        rationale = (
            "Empty/malformed state: failing safe to an empty schema "
            f"(source '{source_name}'). No contacts sample to derive columns from."
        )
    elif schema_empty:
        # Faithful: a no-row contacts sample produces an empty schema. We are
        # confident the schema IS empty (the source returns it verbatim), but
        # the connector taught us nothing about the column shape.
        confidence = 0.5
        rationale = (
            f"Contacts sample for '{source_name}' has no rows; per get_schema() "
            "this yields an empty schema (columns=[], row_count=0) even if column "
            "names were present."
        )
    else:
        confidence = 1.0
        rationale = (
            f"Derived a {len(schema['columns'])}-column schema for '{source_name}' "
            f"from the contacts sample (row_count={schema['row_count']}); all "
            f"columns typed '{column_data_type}'. Preview plans "
            f"{sample_plan['limit']} contacts."
        )

    return {
        "output": output,
        "rationale": rationale,
        "self_metric": {
            "confidence": confidence,
            "schema_empty": schema_empty,
            "column_count": len(schema["columns"]),
            "source_name": source_name,
        },
    }


def run_organ(input_data: Any) -> Dict[str, Any]:
    """Unpack {state, context} and delegate to decide()."""
    if not isinstance(input_data, dict):
        input_data = {}
    return decide(input_data.get("state", {}), input_data.get("context", {}))


def _fail_safe_result(reason: str) -> Dict[str, Any]:
    """The empty-schema fail-safe used when input can't even be parsed."""
    return {
        "output": {
            "schema": {
                "columns": [],
                "row_count": 0,
                "source_name": _DEFAULT_SOURCE_NAME,
            },
            "schema_empty": True,
            "sample_plan": {"entity": "contacts", "limit": _DEFAULT_SAMPLE_SIZE},
            "entities": list(_CRM_ENTITIES),
            "capabilities": dict(_CRM_CAPABILITIES),
        },
        "rationale": reason,
        "self_metric": {"confidence": 0.0},
    }


def main() -> None:
    """CLI entry: read JSON from ORGAN_INPUT (value or file path) or stdin."""
    try:
        input_str = os.environ.get("ORGAN_INPUT")
        if input_str:
            if os.path.isfile(input_str):
                with open(input_str, "r") as f:
                    input_str = f.read()
        else:
            input_str = sys.stdin.read()

        input_data = json.loads(input_str) if input_str.strip() else {}
        result = run_organ(input_data)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    except json.JSONDecodeError as exc:
        json.dump(_fail_safe_result(f"Invalid JSON input: {exc}"), sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
