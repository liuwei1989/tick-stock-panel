from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.research import router
from app.services.research_skills import ResearchSkill, load_skills, select_skills

BUILTINS = Path(__file__).parents[1] / "app" / "research_skills"


def test_builtin_files_match_pinned_upstream_manifest():
    manifest = json.loads((BUILTINS / "upstream.json").read_text())
    assert manifest["revision"] == "d3fee51a9e5ebec756fe8184c1d13b2accf5e39f"
    assert len(manifest["files"]) == 15
    assert {p.name for p in BUILTINS.glob("*.yaml")} == set(manifest["files"])
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((BUILTINS / name).read_bytes()).hexdigest() == digest


def test_builtin_metadata_survives_loading(tmp_path):
    skills, errors = load_skills(tmp_path)
    assert not errors
    for path in BUILTINS.glob("*.yaml"):
        original = yaml.safe_load(path.read_text())
        loaded = skills[original["name"]].model_dump()
        for key, value in original.items():
            assert loaded[key] == value, f"{path.name}: {key}"
        assert loaded["source"] == "builtin"
    assert skills["bull_trend"].default_active
    assert skills["shrink_pullback"].default_router


def test_skill_catalog_api_has_details_and_no_private_paths(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    client = TestClient(app)
    listing = client.get("/api/research/skills").json()
    assert listing["builtin_count"] == 15 and listing["custom_count"] == 0
    assert listing["skills"][0]["name"] == "bull_trend"
    assert all("instructions" not in row for row in listing["skills"])
    detail = client.get("/api/research/skills/bull_trend")
    assert detail.status_code == 200
    assert "MA5" in detail.json()["instructions"]
    assert detail.json()["required_tools"] == ["get_daily_history", "analyze_trend"]
    assert str(tmp_path) not in detail.text
    assert client.get("/api/research/skills/absent").status_code == 404


def skill(name, **kwargs):
    return ResearchSkill(
        name=name, display_name=name, category="framework", instructions="测试框架", **kwargs
    )


def test_auto_selection_uses_catalog_defaults_and_honors_invocation_flags():
    catalog = {
        s.name: s
        for s in [
            skill("preferred", default_active=True),
            skill(
                "manual_only", aliases=["事件"], disable_model_invocation=True, default_priority=1
            ),
            skill("hidden", aliases=["事件"], user_invocable=False, default_priority=1),
            skill("public", aliases=["事件"], default_priority=10),
        ]
    }
    assert [s.name for s in select_skills(catalog, ["auto"], "")] == ["preferred"]
    assert [s.name for s in select_skills(catalog, ["auto"], "事件")] == ["public"]
    assert [s.name for s in select_skills(catalog, ["manual_only"], "")] == ["manual_only"]
    with pytest.raises(ValueError):
        select_skills(catalog, ["hidden"], "")
    assert select_skills(catalog, [], "") == []


def test_bad_metadata_and_duplicate_custom_id_are_isolated(tmp_path):
    folder = tmp_path / "research_skills"
    folder.mkdir()
    (folder / "invalid.yaml").write_text(
        "name: bad\ndisplay_name: bad\ncategory: framework\ninstructions: text\nrequired_tools: 123\n"
    )
    (folder / "duplicate.yaml").write_bytes((BUILTINS / "bull_trend.yaml").read_bytes())
    skills, errors = load_skills(tmp_path)
    assert len(skills) == 15 and len(errors) == 2
    assert skills["bull_trend"].source == "builtin"
