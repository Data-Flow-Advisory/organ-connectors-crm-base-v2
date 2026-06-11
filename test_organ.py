#!/usr/bin/env python3
"""Tests for the CRM-connector-base organ."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from organ import (
    decide,
    run_organ,
    derive_schema,
    resolve_sample_plan,
    _coerce_int,
    _resolve_source_name,
    _resolve_column_data_type,
    _CRM_ENTITIES,
    _DEFAULT_SAMPLE_SIZE,
    _DEFAULT_SOURCE_NAME,
)

HERE = Path(__file__).parent


# --------------------------------------------------------------------------- #
# derive_schema — faithful port of get_schema()
# --------------------------------------------------------------------------- #

def test_schema_from_populated_contacts():
    contacts = {
        "columns": ["email", "first_name", "last_name"],
        "rows": [["a@b.com", "Ada", "Lovelace"]],
        "row_count": 4210,
    }
    schema = derive_schema(contacts, "HubSpotConnector", "string")
    assert schema["row_count"] == 4210
    assert schema["source_name"] == "HubSpotConnector"
    assert schema["columns"] == [
        {"name": "email", "data_type": "string"},
        {"name": "first_name", "data_type": "string"},
        {"name": "last_name", "data_type": "string"},
    ]


def test_no_rows_yields_empty_schema_even_with_columns():
    # Faithful invariant: `if not contacts.rows: return empty schema` — column
    # names present but zero rows still produces an EMPTY schema.
    contacts = {"columns": ["email", "name"], "rows": [], "row_count": 0}
    schema = derive_schema(contacts, "SalesforceConnector", "string")
    assert schema == {
        "columns": [],
        "row_count": 0,
        "source_name": "SalesforceConnector",
    }


def test_missing_rows_key_yields_empty_schema():
    schema = derive_schema({"columns": ["x"]}, "Src", "string")
    assert schema["columns"] == []
    assert schema["row_count"] == 0


def test_non_dict_contacts_yields_empty_schema():
    assert derive_schema(None, "Src", "string")["columns"] == []
    assert derive_schema("nope", "Src", "string")["row_count"] == 0


def test_column_data_type_override_applied_to_all_columns():
    contacts = {"columns": ["a", "b"], "rows": [[1, 2]], "row_count": 2}
    schema = derive_schema(contacts, "Src", "number")
    assert all(c["data_type"] == "number" for c in schema["columns"])


def test_row_count_coerced_from_string():
    contacts = {"columns": ["a"], "rows": [[1]], "row_count": "99"}
    assert derive_schema(contacts, "Src", "string")["row_count"] == 99


def test_malformed_columns_not_a_list_becomes_empty():
    contacts = {"columns": "email", "rows": [["x"]], "row_count": 1}
    schema = derive_schema(contacts, "Src", "string")
    assert schema["columns"] == []
    # rows present, so row_count still carried
    assert schema["row_count"] == 1


# --------------------------------------------------------------------------- #
# resolve_sample_plan — faithful port of sample(n=5)
# --------------------------------------------------------------------------- #

def test_sample_plan_default_size():
    plan = resolve_sample_plan({})
    assert plan == {"entity": "contacts", "limit": _DEFAULT_SAMPLE_SIZE}


def test_sample_plan_explicit_size():
    assert resolve_sample_plan({"sample_size": 25})["limit"] == 25


def test_sample_plan_floors_non_positive_at_one():
    assert resolve_sample_plan({"sample_size": 0})["limit"] == 1
    assert resolve_sample_plan({"sample_size": -7})["limit"] == 1


def test_sample_plan_always_previews_contacts():
    assert resolve_sample_plan({"sample_size": 3})["entity"] == "contacts"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def test_coerce_int_rejects_bool():
    assert _coerce_int(True, 5) == 5
    assert _coerce_int(False, 5) == 5


def test_coerce_int_passthrough_and_fallback():
    assert _coerce_int(12, 5) == 12
    assert _coerce_int("8", 5) == 8
    assert _coerce_int(None, 5) == 5
    assert _coerce_int("xyz", 5) == 5


def test_resolve_source_name_default():
    assert _resolve_source_name({}) == _DEFAULT_SOURCE_NAME
    assert _resolve_source_name({"source_name": "   "}) == _DEFAULT_SOURCE_NAME


def test_resolve_source_name_passthrough():
    assert _resolve_source_name({"source_name": "PipedriveConnector"}) == "PipedriveConnector"


def test_resolve_column_data_type_default_and_override():
    assert _resolve_column_data_type({}) == "string"
    assert _resolve_column_data_type({"column_data_type": "date"}) == "date"


# --------------------------------------------------------------------------- #
# decide — full contract
# --------------------------------------------------------------------------- #

def test_decide_contract_shape():
    result = decide(
        {"source_name": "Src", "contacts": {"columns": ["a"], "rows": [[1]], "row_count": 1}},
        {},
    )
    assert set(result.keys()) == {"output", "rationale", "self_metric"}
    out = result["output"]
    for key in ("schema", "schema_empty", "sample_plan", "entities", "capabilities"):
        assert key in out
    assert out["entities"] == _CRM_ENTITIES
    assert out["capabilities"] == {"search": True, "write_back": True}
    assert isinstance(result["self_metric"]["confidence"], float)
    assert 0.0 <= result["self_metric"]["confidence"] <= 1.0


def test_decide_populated_high_confidence():
    result = decide(
        {"source_name": "HubSpotConnector",
         "contacts": {"columns": ["email"], "rows": [["a@b.com"]], "row_count": 10}},
        {"sample_size": 5},
    )
    assert result["self_metric"]["confidence"] == 1.0
    assert result["output"]["schema_empty"] is False
    assert result["output"]["schema"]["row_count"] == 10


def test_decide_empty_rows_mid_confidence():
    result = decide(
        {"source_name": "Src", "contacts": {"columns": ["x"], "rows": [], "row_count": 0}},
        {},
    )
    assert result["self_metric"]["confidence"] == 0.5
    assert result["output"]["schema_empty"] is True
    assert result["output"]["schema"]["columns"] == []


def test_decide_empty_state_fail_safe_zero_confidence():
    result = decide({}, {})
    assert result["self_metric"]["confidence"] == 0.0
    assert result["output"]["schema_empty"] is True
    assert result["output"]["schema"]["source_name"] == _DEFAULT_SOURCE_NAME


def test_decide_non_dict_inputs_fail_safe():
    result = decide(None, "garbage")
    assert result["output"]["schema_empty"] is True
    assert result["self_metric"]["confidence"] == 0.0


def test_decide_is_deterministic():
    state = {"source_name": "Src",
             "contacts": {"columns": ["a", "b"], "rows": [[1, 2]], "row_count": 3}}
    ctx = {"sample_size": 7}
    results = [decide(state, ctx) for _ in range(5)]
    assert all(r == results[0] for r in results[1:])


def test_decide_does_not_mutate_module_constants():
    decide({"contacts": {"columns": ["a"], "rows": [[1]], "row_count": 1}}, {})
    # output.entities / capabilities must be copies, not the shared constants
    r = decide({"contacts": {"columns": ["a"], "rows": [[1]], "row_count": 1}}, {})
    r["output"]["entities"].append("mutated")
    r["output"]["capabilities"]["search"] = False
    assert _CRM_ENTITIES == ["contacts", "deals", "companies"]
    fresh = decide({}, {})
    assert fresh["output"]["capabilities"] == {"search": True, "write_back": True}


# --------------------------------------------------------------------------- #
# run_organ
# --------------------------------------------------------------------------- #

def test_run_organ_unpacks_state_and_context():
    result = run_organ({
        "state": {"source_name": "Src",
                  "contacts": {"columns": ["a"], "rows": [[1]], "row_count": 1}},
        "context": {"sample_size": 9},
    })
    assert result["output"]["sample_plan"]["limit"] == 9


def test_run_organ_handles_non_dict():
    assert run_organ("nope")["self_metric"]["confidence"] == 0.0


def test_run_organ_handles_missing_keys():
    result = run_organ({})
    assert result["output"]["schema_empty"] is True


# --------------------------------------------------------------------------- #
# CLI harness + samples
# --------------------------------------------------------------------------- #

def _run_cli(payload):
    env = os.environ.copy()
    env["ORGAN_INPUT"] = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run(
        [sys.executable, str(HERE / "organ.py")],
        env=env, capture_output=True, text=True,
    )
    return proc


def test_cli_emits_contract_json():
    proc = _run_cli({"state": {"source_name": "Src",
                               "contacts": {"columns": ["a"], "rows": [[1]], "row_count": 1}}})
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "output" in data and "rationale" in data and "self_metric" in data


def test_cli_invalid_json_fails_safe():
    proc = _run_cli("{not json")
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["self_metric"]["confidence"] == 0.0
    assert data["output"]["schema_empty"] is True


def test_cli_empty_input():
    proc = _run_cli("")
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["self_metric"]["confidence"] == 0.0


@pytest.mark.parametrize("sample_path", sorted((HERE / "samples").glob("*.json")))
def test_samples_conform(sample_path):
    proc = _run_cli(str(sample_path))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert set(data.keys()) == {"output", "rationale", "self_metric"}
    conf = data["self_metric"]["confidence"]
    assert isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0
