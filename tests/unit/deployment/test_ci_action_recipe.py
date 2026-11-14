"""A checked merge must bind the actual local action, including base advances."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
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
        [arg for arg in shlex.split(line.strip()) if arg != "${fixture_args[@]}"]
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
    assert parallel[parallel.index("-n") + 1] == "2"
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


def _functional_storage_program(
    steps: list[dict], identifier: str = "functional_fixture_storage"
) -> str:
    (setup,) = [step for step in steps if step.get("id") == identifier]
    condition = "runner.os == 'Windows'" + (
        " && matrix.suite == 'interop'" if identifier == "interop_fixture_storage" else ""
    )
    assert setup["if"] == condition and setup["shell"] == "bash"
    return setup["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def test_windows_functional_storage_program_is_shared_and_every_cohort_is_separate() -> None:
    root = Path(__file__).resolve().parents[3]
    action = yaml.safe_load(
        (root / ".github/actions/canonical-product-checks/action.yml").read_text("utf-8")
    )
    identity = yaml.safe_load(
        (root / ".github/workflows/mosswork-identity-ci.yml").read_text("utf-8")
    )
    action_steps = action["runs"]["steps"]
    identity_steps = identity["jobs"]["identity"]["steps"]
    assert _functional_storage_program(action_steps) == _functional_storage_program(identity_steps)
    for steps, expected in [(action_steps, 3), (identity_steps, 4)]:
        scripts = [step.get("run", "") for step in steps]
        commands = [
            line.strip()
            for script in scripts
            for line in script.replace("\\\n", " ").splitlines()
            if line.strip().startswith("uv run --frozen pytest ")
        ]
        assert len(commands) == expected
        assert all(line.endswith('"${fixture_args[@]}"') for line in commands)
        for index in range(1, expected + 1):
            token = (
                'fixture_args=(--basetemp "${{ steps.functional_fixture_storage.outputs.base }}/cohort-'
                + str(index)
                + '")'
            )
            assert sum(script.count(token) for script in scripts) == 1
        assert sum(script.count("fixture_args=()") for script in scripts) == expected
        assert (
            sum(
                script.count(
                    'if [[ -n "${{ steps.functional_fixture_storage.outputs.base }}" ]]; then'
                )
                for script in scripts
            )
            == expected
        )
        uploads = [
            step.get("with", {}).get("path", "")
            for step in steps
            if step.get("uses", "").startswith("actions/upload-artifact")
        ]
        assert any("functional-fixture-storage.json" in paths for paths in uploads)


@pytest.mark.parametrize("scope", ["canonical", "interop"])
def test_real_storage_selection_keeps_default_and_explicit_parents_intact(
    tmp_path: Path, scope: str
) -> None:
    root = Path(__file__).resolve().parents[3]
    action = yaml.safe_load(
        (root / ".github/actions/canonical-product-checks/action.yml").read_text("utf-8")
    )
    if scope == "interop":
        workflow = yaml.safe_load((root / ".github/workflows/mosswork-ci.yml").read_text("utf-8"))
        program = _functional_storage_program(
            workflow["jobs"]["release-checks"]["steps"], "interop_fixture_storage"
        )
    else:
        program = _functional_storage_program(action["runs"]["steps"])
    metadata_name = (
        "interop-fixture-storage.json" if scope == "interop" else "functional-fixture-storage.json"
    )

    def run(label: str, *, same_volume: bool = False, explicit: bool = False) -> dict:
        runner = tmp_path / label
        runner.mkdir()
        sentinel = runner / "unrelated-sentinel"
        sentinel.write_text("retain", "utf-8")
        output = runner / "github-output"
        environment = {**os.environ, "RUNNER_TEMP": str(runner), "GITHUB_OUTPUT": str(output)}
        environment.pop("PYTEST_ADDOPTS", None)
        if same_volume:
            system = runner / "system-temp"
            system.mkdir()
            environment.update(TEMP=str(system), TMP=str(system), TMPDIR=str(system))
        if explicit:
            environment["PYTEST_ADDOPTS"] = '--basetemp="original explicit selection" -q'
        result = subprocess.run(
            [sys.executable, "-c", program], env=environment, capture_output=True, timeout=30
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        record = json.loads((runner / metadata_name).read_text("utf-8"))
        selected = record["fresh_fixture_parent"]
        assert output.read_text("utf-8") == "base=" + selected.replace("\\", "/") + "\n"
        expected = (
            sys.platform == "win32"
            and not explicit
            and Path(record["runner_temp"]).drive.casefold()
            != Path(record["system_temp"]).drive.casefold()
        )
        assert bool(selected) == expected
        if selected:
            directory = Path(selected)
            assert directory.parent == runner.resolve() and directory != runner.resolve()
            assert directory.is_dir() and list(directory.iterdir()) == []
            assert directory.name.startswith("mosswork-functional-")
        assert sentinel.read_text("utf-8") == "retain"
        assert record["existing_explicit_basetemp"] is explicit
        return record

    first, second = run("first"), run("second")
    if first["fresh_fixture_parent"]:
        assert first["fresh_fixture_parent"] != second["fresh_fixture_parent"]
    assert run("same", same_volume=True)["fresh_fixture_parent"] == ""
    assert run("explicit", explicit=True)["fresh_fixture_parent"] == ""


def test_interop_storage_only_covers_original_functional_cohorts() -> None:
    root = Path(__file__).resolve().parents[3]
    core = yaml.safe_load((root / ".github/workflows/mosswork-ci.yml").read_text("utf-8"))
    action = yaml.safe_load(
        (root / ".github/actions/canonical-product-checks/action.yml").read_text("utf-8")
    )
    steps = core["jobs"]["release-checks"]["steps"]
    interop = _functional_storage_program(steps, "interop_fixture_storage")
    assert interop.replace(
        "interop-fixture-storage.json", "functional-fixture-storage.json"
    ) == _functional_storage_program(action["runs"]["steps"])
    selected = {
        "Check registered JSON HTTP, TLS and MCP boundaries",
        "Check shared fixture, cold CLI and offline catalog isolation",
        "Check isolated dependencies and live worker fixtures",
        "Check connector regressions and portable source exports",
    }
    count = 0
    for step in steps:
        body = step.get("run", "")
        if step.get("name") not in selected:
            assert '"${fixture_args[@]}"' not in body
            continue
        count += body.count('"${fixture_args[@]}"')
        assert body.count("fixture_args=()") == body.count('"${fixture_args[@]}"')
    assert count == 7
    for index in range(1, 8):
        token = (
            'fixture_args=(--basetemp "${{ steps.interop_fixture_storage.outputs.base }}/cohort-'
            + str(index)
            + '")'
        )
        assert sum(step.get("run", "").count(token) for step in steps) == 1
    (domain,) = [
        step
        for step in steps
        if step.get("name") == "Check domain client with the real installed worker"
    ]
    assert '--basetemp "$RUNNER_TEMP/sudograph-domain-client"' in domain["run"]
    assert any(
        "interop-fixture-storage.json" in step.get("with", {}).get("path", "") for step in steps
    )
