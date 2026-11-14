"""A checked merge must bind the actual local action, including base advances."""

import hashlib
import json
import shlex
from pathlib import Path

import pytest
import yaml
from tests.unit.deployment.test_precommit import (
    ci_commit,
    gate,
    git,
    seed_windows_validation_workflow,
    stage,
)
from tests.unit.deployment.test_precommit import (
    repo as repo,
)


@pytest.mark.parametrize(
    "action_state", ["complete", "missing", "modified", "nonregular", "feature-modified"]
)
def test_tool_scope_binds_the_checked_local_action_revision(repo: Path, action_state: str) -> None:
    seed_windows_validation_workflow(repo)
    common = git(repo, "rev-parse", "HEAD")
    action = gate.WINDOWS_VALIDATION_RECIPE_ACTIONS[0]
    original = (repo / action).read_text(encoding="utf-8")
    git(repo, "checkout", "-qb", "accepted-base")
    if action_state == "missing":
        git(repo, "rm", action)
    elif action_state == "modified":
        stage(repo, action, original + "\n# Different accepted-base implementation.\n")
    elif action_state == "nonregular":
        # Test the committed mode on every platform without requiring OS symlink privileges.
        git(repo, "config", "core.symlinks", "false")
        blob = git(repo, "rev-parse", f"HEAD:{action}")
        git(repo, "update-index", "--cacheinfo", f"120000,{blob},{action}")
    else:
        stage(repo, "src/octop/accepted.py", "ACCEPTED = True\n")
    ci_commit(repo, "accepted base advancement")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "tool-feature", common)
    stage(repo, "tools/check_public_source.py", "# Independent tool payload.\n")
    if action_state == "feature-modified":
        stage(repo, action, original + "\n# Action changed by the feature itself.\n")
    ci_commit(repo, "tool feature")
    head = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "-c",
        "core.hooksPath=" + str(repo / "fixture-hooks"),
        "merge",
        "--no-edit",
        "accepted-base",
    )
    checked = git(repo, "rev-parse", "HEAD")
    plan = gate.ci_plan(repo, base, head)
    assert plan["checked_head"] == checked
    assert plan["runtime_matrix"] is (action_state != "complete")
    assert plan["windows_validation"] is (action_state == "complete")
    if action_state in {"missing", "modified", "nonregular"}:
        assert plan["paths"] == ["tools/check_public_source.py"]
        assert "recipe" in plan["reason"]
    elif action_state == "feature-modified":
        assert action in plan["paths"]


def test_browser_matrices_stop_siblings_after_first_failure() -> None:
    source = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load(
        (source / ".github/workflows/mosswork-browser-ci.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    assert set(jobs) == {"native-host-regressions", "browser"}
    native, browser = jobs["native-host-regressions"], jobs["browser"]
    for job in (native, browser):
        assert job["strategy"]["fail-fast"] is True
        assert job["strategy"]["matrix"]["os"] == ["ubuntu-24.04", "windows-2022"]
        assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job["steps"])
    assert native["strategy"]["matrix"]["suite"] == [
        "transport",
        "review",
        "pages",
        "product",
        "startup",
    ]
    assert browser["needs"] == "native-host-regressions"


def _pytest_commands(body: str) -> list[list[str]]:
    return [
        shlex.split(line.strip())
        for line in body.replace("\\\n", " ").splitlines()
        if line.strip().startswith("uv run --frozen pytest ")
    ]


def test_canonical_ci_platform_workers_preserve_all_original_commands() -> None:
    source = Path(__file__).resolve().parents[3]
    action = yaml.safe_load(
        (source / ".github/actions/canonical-product-checks/action.yml").read_text(encoding="utf-8")
    )
    actual = [
        argv for step in action["runs"]["steps"] for argv in _pytest_commands(step.get("run", ""))
    ]
    assert len(actual) == 3
    original = []
    for argv in actual:
        assert argv.count("-n") == 1
        position = argv.index("-n")
        assert argv[position + 1] == "${{ runner.os == 'Windows' && '0' || '2' }}"
        original.append(argv[:position] + argv[position + 2 :])
    assert (
        hashlib.sha256(json.dumps(original, separators=(",", ":")).encode()).hexdigest()
        == "66e4ca8bdd18e1e2e3d692b7c15f0e3609a5ba0fe2548824864c1312d5e2aad1"
    )


def test_identity_ci_keeps_original_paths_markers_and_serial_startup_boundaries() -> None:
    source = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load(
        (source / ".github/workflows/mosswork-identity-ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["identity"]["steps"]
    main = next(
        step
        for step in steps
        if step.get("name") == "Verify current membership, revocation and original IM identity"
    )
    actual = _pytest_commands(main["run"])
    assert len(actual) == 3
    parallel, serial, gateway = actual
    assert parallel[parallel.index("-n") + 1] == "${{ runner.os == 'Windows' && '0' || '2' }}"
    assert serial[serial.index("-n") + 1] == "0"
    sensitive = [
        "tests/unit/company_runtime/test_local_process.py",
        "tests/unit/infra/test_harness_config_imports.py",
        "tests/unit/company_runtime/test_desktop_capacity.py",
    ]
    assert [arg for arg in serial if arg.startswith("tests/")] == sensitive
    assert not set(sensitive) & set(parallel)
    original_paths = sorted(
        arg for argv in [parallel, serial] for arg in argv if arg.startswith("tests/")
    )
    assert (
        hashlib.sha256(json.dumps(original_paths, separators=(",", ":")).encode()).hexdigest()
        == "811a3fac024b78158f9be5abab66bdb6490aa3540a1a1c451a2a052bfde44dcf"
    )
    for argv in [parallel, serial, gateway]:
        assert argv[argv.index("-m") + 1] == "not integration and not live"
        assert "-q" in argv
    assert (
        hashlib.sha256(json.dumps(gateway, separators=(",", ":")).encode()).hexdigest()
        == "4891cfac70176dc57ddd4f3ed49a659101b2f3d68710362073d6939386a52a31"
    )
    assert "--junitxml=identity-results.xml" in parallel
    assert "--junitxml=identity-startup-results.xml" in serial
    uploads = [
        step["with"]["path"]
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert any(
        "identity-results.xml" in paths and "identity-startup-results.xml" in paths
        for paths in uploads
    )
