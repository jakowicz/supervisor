from pathlib import Path


def test_factory_requires_authoring_to_continue_through_implementation_gates():
    root = Path(__file__).resolve().parents[2] / "runbooks"
    game_design_stage = (root / "F005.md").read_text(encoding="utf-8")
    manifest_stage = (root / "F013.md").read_text(encoding="utf-8")
    authoring_stage = (root / "F014.md").read_text(encoding="utf-8")
    quality_stage = (root / "F015.md").read_text(encoding="utf-8")

    assert "gates block implementation, not runbook authoring" in authoring_stage
    assert "must never block creation of the runbook itself" in manifest_stage
    assert "reason not to create an allocated R contract" in quality_stage
    assert "checkpoint**, not the final manifest audit" in game_design_stage
    assert "reason to stop for user review" in game_design_stage
    assert "must never rewrite its own" in game_design_stage
