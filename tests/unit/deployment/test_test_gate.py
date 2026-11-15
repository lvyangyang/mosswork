"""Actual private Git/pytest histories qualify selection without copying an application cache."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from tools import test_gate as gate

SOURCE = Path(__file__).resolve().parents[3]


@pytest.fixture
def gate_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "real gate"
    for folder in ("src", "tests", "tools", "output", "empty-hooks"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE / "tools/test_gate.py", root / "tools/test_gate.py")
    shutil.copyfile(SOURCE / "Makefile", root / "Makefile")
    shutil.copyfile(SOURCE / "tests/support/testmon_staged_changes.py", root / "staged_changes.py")
    (root / "conftest.py").write_text(
        'import staged_changes\npytest_plugins = ["tools.test_gate"]\n', encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["src"]\nmarkers=["live: explicit paid-model tests"]\naddopts="--maxfail=1"\n',
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(
        ".testmon*\n.pytest_cache/\n__pycache__/\noutput/\n", encoding="utf-8"
    )
    for name, value in (("first", 1), ("second", 2)):
        (root / f"src/{name}.py").write_text(
            f"def value():\n    return {value}\n", encoding="utf-8"
        )
        (root / f"tests/test_{name}.py").write_text(
            f"from {name} import value\nfrom pathlib import Path\n\ndef test_{name}():\n"
            f"    with Path('output/observed').open('a') as f: f.write('{name}\\n')\n"
            f"    assert value() == {value}\n",
            encoding="utf-8",
        )
    (root / "tests/test_live.py").write_text(
        "import pytest\nfrom pathlib import Path\n\n@pytest.mark.live\ndef test_live():\n"
        "    Path('output/LIVE-RAN').touch()\n    raise AssertionError('live body must not execute')\n",
        encoding="utf-8",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "GIT_PREFIX",
            "PYTEST_CURRENT_TEST",
            "PYTEST_ADDOPTS",
        }
    }
    env.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTEST_ADDOPTS="-p testmon.pytest_testmon -p xdist.plugin",
        PYTHONIOENCODING="utf-8",
        OPENAI_API_KEY="synthetic-present-credential",
    )
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Gate Test"),
        ("config", "user.email", "test@example.com"),
        ("config", "core.hooksPath", str(root / "empty-hooks")),
        ("add", "."),
        ("commit", "-qm", "fixture"),
    ):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return root, env


def execute(
    root: Path, env: dict[str, str], label: str, *args: str
) -> tuple[subprocess.CompletedProcess[str], dict, list[str]]:
    marker = root / "output/observed"
    prior = marker.read_text().splitlines() if marker.exists() else []
    result = subprocess.run(
        [sys.executable, str(root / "tools/test_gate.py"), "--workers", "1", *args],
        cwd=root,
        env={
            **env,
            "PYTEST_ADDOPTS": env["PYTEST_ADDOPTS"] + " --junitxml=output/" + label + ".xml",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    (root / f"output/{label}.stdout").write_text(result.stdout, encoding="utf-8")
    (root / f"output/{label}.stderr").write_text(result.stderr, encoding="utf-8")
    plans = [line for line in result.stdout.splitlines() if line.startswith("TEST_GATE: {")]
    plan = json.loads(plans[0].removeprefix("TEST_GATE: ")) if plans else {}
    after = marker.read_text().splitlines() if marker.exists() else []
    assert not (root / "output/LIVE-RAN").exists(), result.stdout + result.stderr
    return result, plan, after[len(prior) :]


def test_actual_graph_reuses_python_changes_and_keeps_nonpython_and_missing_cache_full(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    first, plan, calls = execute(root, env, "first")
    assert first.returncode == 0, first.stdout + first.stderr
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]
    assert Path(plan["graph"]).is_file()
    unchanged, plan, calls = execute(root, env, "unchanged")
    assert unchanged.returncode == 0, unchanged.stdout + unchanged.stderr
    assert plan["mode"] == "affected" and calls == []
    (root / "src/first.py").write_text("def value():\n    return 1 + 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/first.py"], cwd=root, env=env, check=True)
    changed, plan, calls = execute(root, env, "changed")
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert plan["mode"] == "affected" and calls == ["first"]
    (root / "settings.json").write_text('{"new": true}\n', encoding="utf-8")
    nonpython, plan, calls = execute(root, env, "nonpython")
    assert nonpython.returncode == 0, nonpython.stdout + nonpython.stderr
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]
    Path(plan["graph"]).rename(root / "output/preserved-graph")
    missing, plan, calls = execute(root, env, "missing")
    assert missing.returncode == 0, missing.stdout + missing.stderr
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]
    explicit, plan, calls = execute(root, env, "explicit", "--full")
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]
    environment_changed, plan, calls = execute(
        root, {**env, "MOSSWORK_SYNTHETIC_VARIANT": "changed"}, "environment"
    )
    assert environment_changed.returncode == 0, (
        environment_changed.stdout + environment_changed.stderr
    )
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]


def test_real_failure_and_collection_only_cannot_replace_a_passed_baseline(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    initial, plan, _ = execute(root, env, "initial")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    baseline = root / ".git/mosswork-test-gate/baseline.json"
    before = baseline.read_bytes()
    (root / "src/first.py").write_text("def value():\n    return 99\n", encoding="utf-8")
    failed, plan, calls = execute(root, env, "failed")
    assert failed.returncode != 0 and "assert 99 == 1" in failed.stdout
    assert plan["mode"] == "affected" and calls == ["first"]
    assert baseline.read_bytes() == before
    attempted, plan, calls = execute(
        root, {**env, "PYTEST_ADDOPTS": env["PYTEST_ADDOPTS"] + " --collect-only"}, "selection"
    )
    assert attempted.returncode != 0 and "TEST_GATE_REFUSED" in attempted.stderr
    assert not plan and not calls and baseline.read_bytes() == before


@pytest.fixture
def installed_gate_repo(
    gate_repo: tuple[Path, dict[str, str]],
) -> tuple[Path, dict[str, str], Path, Path, Path]:
    root, env = gate_repo
    site = root / "output/installed-fixture"
    package = site / "gate_payload_fixture"
    metadata = site / "gate_payload_fixture-1.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    marker = package / "py.typed"
    module = package / "__init__.py"
    marker.write_bytes(b"")
    module.write_bytes(b"VALUE = 1\n")
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: gate-payload-fixture\nVersion: 1.0\n", encoding="utf-8"
    )
    record = metadata / "RECORD"
    record.write_text(
        "".join(
            f"gate_payload_fixture/{file.name},sha256="
            + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            + f",{len(data)}\n"
            for file, data in ((marker, b""), (module, b"VALUE = 1\n"))
        )
        + "gate_payload_fixture/__pycache__/optional.pyc,,\n"
        "gate_payload_fixture-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    env = {**env, "PYTHONPATH": str(site) + os.pathsep + env.get("PYTHONPATH", "")}
    return root, env, marker, module, metadata


@pytest.mark.parametrize("damage", ["missing", "truncated"])
def test_damaged_installed_files_refuse_before_pytest_without_losing_real_history(
    installed_gate_repo: tuple[Path, dict[str, str], Path, Path, Path], damage: str
) -> None:
    root, env, marker, module, metadata = installed_gate_repo
    record = metadata / "RECORD"
    first, plan, calls = execute(root, env, "intact")
    assert first.returncode == 0, first.stdout + first.stderr
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]
    storage = root / ".git/mosswork-test-gate"
    original_history = {
        name: (storage / name).read_bytes() for name in ("baseline.json", "history.json")
    }
    original_metadata = (metadata / "METADATA").read_bytes(), record.read_bytes()
    if damage == "missing":
        marker.unlink()
    else:
        module.write_bytes(b"VALUE =")
    refused, plan, calls = execute(root, env, damage, "--require-reuse")
    assert refused.returncode != 0 and "TEST_GATE_REFUSED" in refused.stderr
    assert "gate-payload-fixture" in refused.stderr
    assert "uv sync --locked --reinstall" in refused.stderr
    assert not plan and not calls
    full, plan, calls = execute(root, env, damage + "-full", "--full")
    assert full.returncode != 0 and "Installed package payload incomplete" in full.stderr
    assert not plan and not calls
    assert not (storage / "incomplete.json").exists()
    assert {name: (storage / name).read_bytes() for name in original_history} == original_history
    assert ((metadata / "METADATA").read_bytes(), record.read_bytes()) == original_metadata
    marker.write_bytes(b"")
    module.write_bytes(b"VALUE = 1\n")
    repaired, plan, calls = execute(root, env, "repaired", "--require-reuse")
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert plan["mode"] == "affected" and calls == []


def test_installed_payload_damage_during_pytest_cannot_update_real_history(
    installed_gate_repo: tuple[Path, dict[str, str], Path, Path, Path],
) -> None:
    root, env, marker, _, _ = installed_gate_repo
    initial, plan, _ = execute(root, env, "initial")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    storage = root / ".git/mosswork-test-gate"
    original_history = {
        name: (storage / name).read_bytes() for name in ("baseline.json", "history.json")
    }
    first = root / "tests/test_first.py"
    first.write_text(
        first.read_text(encoding="utf-8")
        + f"    Path({marker.relative_to(root).as_posix()!r}).unlink()\n",
        encoding="utf-8",
    )
    result, plan, calls = execute(root, env, "payload-drift")
    assert result.returncode != 0 and "Installed package payload incomplete" in result.stderr
    assert plan["mode"] == "affected" and calls == ["first"]
    receipt = json.loads(Path(plan["receipt"]).read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 0 and not receipt["passed"]
    assert not marker.exists()
    assert {name: (storage / name).read_bytes() for name in original_history} == original_history


def test_source_drift_rejects_an_otherwise_successful_real_pytest_run(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    initial, _, _ = execute(root, env, "initial")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    baseline = root / ".git/mosswork-test-gate/baseline.json"
    before = baseline.read_bytes()
    (root / "tests/test_write.py").write_text(
        "from pathlib import Path\n\ndef test_write():\n"
        "    Path('src/first.py').write_text('def value():\\n    return 1 + 0\\n')\n",
        encoding="utf-8",
    )
    result, plan, _ = execute(root, env, "drift")
    assert result.returncode != 0 and "Source/environment changed" in result.stderr
    receipt = json.loads(Path(plan["receipt"]).read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 0 and not receipt["passed"]
    assert receipt["changed_during_validation"] == ["src/first.py"]
    assert baseline.read_bytes() == before


@pytest.mark.parametrize("with_git_shell", [False, True])
def test_source_archive_without_git_keeps_the_original_full_nonlive_run(
    gate_repo: tuple[Path, dict[str, str]],
    with_git_shell: bool,
) -> None:
    root, env = gate_repo
    if with_git_shell:
        (root / ".githooks").mkdir()
        shutil.copyfile(SOURCE / ".githooks/test-gate", root / ".githooks/test-gate")
    (root / ".git").rename(root / "output/retained-git")
    result, plan, calls = execute(root, env, "archive")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no reusable baseline" in result.stdout and not plan
    assert sorted(calls) == ["first", "second"]


def test_git_shell_zero_without_actual_gate_cannot_accept_validation(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    hooks = root / ".githooks"
    hooks.mkdir()
    shutil.copyfile(SOURCE / ".githooks/test-gate", hooks / "test-gate")
    (hooks / "test-gate").chmod(0o755)
    original, _, calls = execute(root, env, "original-actual-gate")
    assert original.returncode == 0 and sorted(calls) == ["first", "second"]
    storage = root / ".git/mosswork-test-gate"
    baseline = (storage / "baseline.json").read_bytes()
    (hooks / "test-gate").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    refused, _, calls = execute(root, env, "zero-shell")
    assert refused.returncode != 0 and "zero shell exit cannot replace validation" in refused.stderr
    assert calls == [] and (storage / "baseline.json").read_bytes() == baseline
    assert not list(storage.glob("launch-*.json"))


def test_real_make_uses_the_gate_and_explicit_full_target_keeps_every_case(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    make = shutil.which("make") or shutil.which("mingw32-make")
    if make is None and os.name == "nt":
        pytest.skip("GNU Make is required; the Windows publication route supplies it")
    assert make is not None
    root, env = gate_repo
    modes: list[str] = []
    for label, target in (("cold", "test-current"), ("reuse", "test-current"), ("full", "test")):
        result = subprocess.run(
            [
                make,
                target,
                "PYTEST_JOBS=1",
                "TEST_PYTHON=" + shlex.quote(Path(sys.executable).as_posix()),
                "RUN=" + shlex.quote(Path(sys.executable).as_posix()) + " -m",
            ],
            cwd=root,
            env={
                **env,
                "PYTEST_ADDOPTS": env["PYTEST_ADDOPTS"]
                + " --junitxml=output/make-"
                + label
                + ".xml",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        plans = [line for line in result.stdout.splitlines() if line.startswith("TEST_GATE: {")]
        modes.append(
            json.loads(plans[0].removeprefix("TEST_GATE: "))["mode"] if plans else "explicit-full"
        )
    assert modes == ["full", "affected", "explicit-full"]
    assert (root / "output/observed").read_text().splitlines().count("first") == 2
    assert (root / "output/observed").read_text().splitlines().count("second") == 2
    assert not (root / "output/LIVE-RAN").exists()


@pytest.mark.parametrize("inherited_hook_route", [False, True])
def test_fresh_make_and_real_commit_share_history_without_losing_live_or_policy_checks(
    gate_repo: tuple[Path, dict[str, str]],
    inherited_hook_route: bool,
) -> None:
    make = shutil.which("make") or shutil.which("mingw32-make")
    if make is None and os.name == "nt":
        pytest.skip("GNU Make is required; the Windows publication route supplies it")
    assert make is not None
    root, environment = gate_repo
    if inherited_hook_route:
        environment["GIT_CONFIG_PARAMETERS"] = "'core.hooksPath'='.githooks'"
    hooks = root / ".githooks"
    hooks.mkdir()
    shutil.copyfile(SOURCE / ".githooks/test-gate", hooks / "test-gate")
    (hooks / "test-gate").chmod(0o755)
    (root / "tests/test_live.py").write_text(
        "import pytest\nfrom pathlib import Path\n\n@pytest.mark.live\ndef test_live():\n"
        "    with Path('output/observed').open('a') as f: f.write('live\\n')\n",
        encoding="utf-8",
    )
    arguments = [
        make,
        "PYTEST_JOBS=1",
        "TEST_PYTHON=" + shlex.quote(Path(sys.executable).as_posix()),
        "RUN=" + shlex.quote(Path(sys.executable).as_posix()) + " -m",
    ]
    # This private hook isolates the actual Make test boundary; the application's
    # original lint/typecheck/build hook still qualifies publication separately.
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\nexec " + shlex.join([*arguments, "test-affected"]) + "\n",
        encoding="utf-8",
    )
    (hooks / "pre-commit").chmod(0o755)
    subprocess.run(["git", "add", "."], cwd=root, env=environment, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + str(root / "empty-hooks"),
            "commit",
            "-qm",
            "shared entry fixture",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        check=True,
    )
    marker = root / "output/observed"

    def invoke(*argv: str) -> tuple[dict, list[str]]:
        before = marker.read_text().splitlines() if marker.exists() else []
        result = subprocess.run(
            list(argv),
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        receipt = json.loads((root / ".git/mosswork-test-gate/history.json").read_text())
        assert receipt["passed"]
        return receipt, marker.read_text().splitlines()[len(before) :]

    full, observed = invoke(*arguments, "test-current")
    assert full["mode"] == "full" and sorted(observed) == ["first", "second"]
    graph = full["graph"]
    hook, observed = invoke(
        "git", "-c", "core.hooksPath=.githooks", "commit", "--allow-empty", "-qm", "checked"
    )
    assert hook["mode"] == "affected" and observed == ["live"], hook["reason"]
    assert hook["graph"] == graph and hook["scope"] == "including-live"
    assert not (root / ".testmondata").exists()
    unchanged, observed = invoke(
        "git", "-c", "core.hooksPath=.githooks", "commit", "--allow-empty", "-qm", "unchanged"
    )
    assert unchanged["mode"] == "affected" and observed == []
    assert unchanged["pytest_session"]["collected"] == 0
    assert unchanged["pytest_session"]["exit_code"] == 0
    nonlive, observed = invoke(*arguments, "test-current")
    assert nonlive["mode"] == "affected" and observed == []
    unchanged, observed = invoke(
        "git", "-c", "core.hooksPath=.githooks", "commit", "--allow-empty", "-qm", "same scopes"
    )
    assert unchanged["mode"] == "affected" and observed == []

    live = root / "tests/test_live.py"
    live.write_text(
        live.read_text(encoding="utf-8")
        + "\n@pytest.mark.live\ndef test_live_added():\n"
        + "    with Path('output/observed').open('a') as f: f.write('new-live\\n')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "tests/test_live.py"], cwd=root, env=environment, check=True)
    nonlive, observed = invoke(*arguments, "test-current")
    assert nonlive["mode"] == "affected" and observed == []
    eligible, observed = invoke(
        "git", "-c", "core.hooksPath=.githooks", "commit", "-qm", "required new live case"
    )
    assert eligible["mode"] == "affected" and observed.count("new-live") == 1
    assert not {"first", "second"}.intersection(observed)

    (root / "src/first.py").write_text("def value():\n    return 1 + 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/first.py"], cwd=root, env=environment, check=True)
    staged, observed = invoke(
        "git", "-c", "core.hooksPath=.githooks", "commit", "-qm", "staged source"
    )
    assert staged["mode"] == "affected" and observed == ["first"]

    policy, observed = invoke(
        "git",
        "-c",
        "core.hooksPath=.githooks",
        "-c",
        "mosswork.fixture-policy=changed",
        "commit",
        "--allow-empty",
        "-qm",
        "different actual Git policy",
    )
    assert policy["mode"] == "full" and sorted(observed) == ["first", "live", "new-live", "second"]
    assert "GIT_CONFIG_PARAMETERS" in policy["environment_variables"]
    baseline = root / ".git/mosswork-test-gate/baseline.json"
    passed_baseline = baseline.read_bytes()
    (root / "tests/test_live.py").write_text(
        "import pytest\n\n@pytest.mark.live\ndef test_live():\n"
        "    raise AssertionError('controlled live failure must stop the commit')\n",
        encoding="utf-8",
    )
    failed = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=.githooks",
            "commit",
            "--allow-empty",
            "-qm",
            "refused live failure",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert failed.returncode != 0 and "controlled live failure" in failed.stdout + failed.stderr
    assert baseline.read_bytes() == passed_baseline


def test_zero_exit_without_an_actual_pytest_session_cannot_create_a_baseline(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    env = {k: v for k, v in env.items() if k != "PYTHONSAFEPATH"}
    initial, _, _ = execute(root, env, "initial")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    baseline = root / ".git/mosswork-test-gate/baseline.json"
    before = baseline.read_bytes()
    (root / "pytest.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    fake, _, calls = execute(root, env, "no-session")
    assert fake.returncode != 0 and "Actual pytest completion receipt" in fake.stderr
    assert not calls and baseline.read_bytes() == before


def test_changed_or_interrupted_graph_is_never_reused_and_legacy_cache_is_independent(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    first, plan, _ = execute(root, env, "first")
    assert first.returncode == 0, first.stdout + first.stderr
    original_graph = Path(plan["graph"])
    with original_graph.open("ab") as stream:
        stream.write(b"changed outside the gate")
    changed, plan, calls = execute(root, env, "changed-graph")
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]
    assert Path(plan["graph"]) != original_graph and original_graph.is_file()
    storage = root / ".git/mosswork-test-gate"
    (storage / "incomplete.json").write_text('{"interrupted": true}\n', encoding="utf-8")
    previous_graph = Path(plan["graph"])
    interrupted, plan, calls = execute(root, env, "interrupted")
    assert interrupted.returncode == 0, interrupted.stdout + interrupted.stderr
    assert plan["mode"] == "full" and sorted(calls) == ["first", "second"]
    assert Path(plan["graph"]) != previous_graph and previous_graph.is_file()
    # Ordinary pytest/hook output cannot mutate the gate's private graph.
    (root / ".testmondata").write_bytes(b"independent ordinary pytest cache")
    reused, plan, calls = execute(root, env, "independent")
    assert reused.returncode == 0, reused.stdout + reused.stderr
    assert plan["mode"] == "affected" and calls == []


def test_simultaneous_gate_refuses_before_launching_pytest(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    from tools.test_gate import gate_lock

    root, env = gate_repo
    storage = root / ".git/mosswork-test-gate"
    storage.mkdir()
    with gate_lock(storage):
        result, plan, calls = execute(root, env, "contended")
    assert result.returncode != 0 and "already running" in result.stderr
    assert not plan and calls == []


def test_nested_commands_do_not_inherit_the_private_testmon_graph(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    (root / "tests/test_nested.py").write_text(
        "import subprocess, sys\n\ndef test_nested():\n"
        "    value = subprocess.check_output([sys.executable, '-c', "
        "\"import os;print(os.environ.get('TESTMON_DATAFILE', 'unset'))\"], text=True)\n"
        "    assert value.strip() == 'unset'\n",
        encoding="utf-8",
    )
    result, _, _ = execute(root, env, "nested")
    assert result.returncode == 0, result.stdout + result.stderr


def test_linked_worktree_workers_keep_their_own_root_and_private_graph(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    linked = root.parent / "linked gate"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked), "HEAD"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
    )
    (linked / "output").mkdir()
    result, plan, calls = execute(linked, env, "linked")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(calls) == ["first", "second"]
    assert not (root / "output/observed").exists()
    receipt = json.loads(Path(plan["receipt"]).read_text(encoding="utf-8"))
    assert receipt["pytest_session"]["root"] == str(linked.resolve())
    assert "/worktrees/" in Path(plan["graph"]).as_posix()


def test_failed_first_full_retains_passed_failed_and_unexecuted_cases_distinctly(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    (root / "tests/test_third.py").write_text(
        "from pathlib import Path\n\ndef test_third():\n"
        "    with Path('output/observed').open('a') as f: f.write('third\\n')\n"
        "\n",
        encoding="utf-8",
    )
    (root / "src/second.py").write_text("def value():\n    return 99\n", encoding="utf-8")
    failed, plan, calls = execute(root, env, "first-full-failed")
    assert failed.returncode != 0 and "assert 99 == 2" in failed.stdout
    assert plan["mode"] == "full" and calls == ["first", "second"]
    storage = root / ".git/mosswork-test-gate"
    assert not (storage / "baseline.json").exists()
    history = json.loads((storage / "history.json").read_text(encoding="utf-8"))
    assert not history["passed"] and history["trusted_graph"]
    assert history["pytest_session"]["passed"] == 1
    assert history["pytest_session"]["failed"] == 1
    assert not (storage / "incomplete.json").exists()
    (root / "src/second.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    fixed, plan, calls = execute(root, env, "fixed")
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert plan["mode"] == "affected" and plan["prior_passed"] is False
    # Testmon has not checkpointed this short coverage batch. It safely reruns
    # the unrecorded pass too; no saved work is claimed for that case.
    assert sorted(calls) == ["first", "second", "third"]
    baseline = json.loads((storage / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["passed"] and baseline["mode"] == "affected"
    # The first actual field remains failed, never relabeled as whole-source pass.
    assert json.loads(Path(history["attempt"]).read_text(encoding="utf-8"))["passed"] is False


def test_checkpointed_passes_are_reused_after_a_real_first_full_failure(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    from testmon.testmon_core import TEST_BATCH_SIZE

    root, env = gate_repo
    first = root / "tests/test_first.py"
    first.write_text(
        "import pytest\n"
        + first.read_text(encoding="utf-8").replace(
            "def test_first():",
            f"@pytest.mark.parametrize('index', range({TEST_BATCH_SIZE}))\ndef test_first(index):",
        ),
        encoding="utf-8",
    )
    (root / "tests/test_third.py").write_text(
        "from pathlib import Path\n\ndef test_third():\n"
        "    with Path('output/observed').open('a') as f: f.write('third\\n')\n",
        encoding="utf-8",
    )
    (root / "src/second.py").write_text("def value():\n    return 99\n", encoding="utf-8")
    failed, plan, calls = execute(root, env, "checkpointed-first-full-failed")
    assert failed.returncode != 0 and plan["mode"] == "full"
    assert calls == ["first"] * TEST_BATCH_SIZE + ["second"]
    storage = root / ".git/mosswork-test-gate"
    history = json.loads((storage / "history.json").read_text(encoding="utf-8"))
    assert not history["passed"] and history["pytest_session"]["passed"] == TEST_BATCH_SIZE
    assert not (storage / "baseline.json").exists()
    (root / "src/second.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    repaired, plan, calls = execute(root, env, "checkpointed-repaired")
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert plan["mode"] == "affected" and plan["prior_passed"] is False
    assert sorted(calls) == ["second", "third"]
    assert json.loads(Path(history["attempt"]).read_text(encoding="utf-8"))["passed"] is False


def test_crashed_worker_does_not_make_its_partial_graph_trusted(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    initial, _, _ = execute(root, env, "initial")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    storage = root / ".git/mosswork-test-gate"
    previous = (storage / "history.json").read_bytes()
    config = root / "pyproject.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            '--maxfail=1"', '--maxfail=1 --max-worker-restart=0"'
        ),
        encoding="utf-8",
    )
    (root / "tests/test_crash.py").write_text(
        "import os\n\ndef test_crash():\n    os._exit(12)\n", encoding="utf-8"
    )
    result, plan, _ = execute(root, env, "crash")
    assert result.returncode != 0
    receipt = json.loads(Path(plan["receipt"]).read_text(encoding="utf-8"))
    assert receipt["passed"] is False and receipt["pytest_session"]["worker_crashed"]
    assert (storage / "history.json").read_bytes() == previous
    assert (storage / "incomplete.json").exists()


def test_actual_zero_status_early_pytest_exit_cannot_become_a_full_baseline(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    conftest = root / "conftest.py"
    conftest.write_text(
        conftest.read_text(encoding="utf-8")
        + "\nimport pytest\n@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
        "def pytest_collection(session):\n    yield\n"
        "    pytest.exit('synthetic early collection stop', returncode=0)\n",
        encoding="utf-8",
    )
    result, plan, calls = execute(root, env, "early-zero-status")
    assert result.returncode != 0 and "Actual pytest completion receipt" in result.stderr
    assert calls == []
    receipt = json.loads(Path(plan["receipt"]).read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 0 and receipt["passed"] is False
    assert receipt["pytest_session"]["interrupted"]
    storage = root / ".git/mosswork-test-gate"
    assert not (storage / "baseline.json").exists()
    assert not (storage / "history.json").exists()


def test_unexecuted_existing_dependent_is_required_after_an_affected_failure(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    second = root / "tests/test_second.py"
    second.write_text(
        second.read_text(encoding="utf-8")
        .replace("from second import value", "from first import value")
        .replace("value() == 2", "value() == 1"),
        encoding="utf-8",
    )
    initial, _, _ = execute(root, env, "initial")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    (root / "src/first.py").write_text("def value():\n    return 99\n", encoding="utf-8")
    failed, plan, calls = execute(root, env, "shared-dependency-failed")
    assert failed.returncode != 0 and plan["mode"] == "affected"
    assert len(calls) == 1 and calls[0] in {"first", "second"}
    failed_name = calls[0]
    unexecuted_name = "second" if failed_name == "first" else "first"
    repaired_test = root / f"tests/test_{failed_name}.py"
    repaired_test.write_text(
        repaired_test.read_text(encoding="utf-8").replace("value() == 1", "value() == 99"),
        encoding="utf-8",
    )
    still_failed, plan, calls = execute(root, env, "second-still-required")
    assert still_failed.returncode != 0 and "assert 99 == 1" in still_failed.stdout
    assert plan["mode"] == "affected" and unexecuted_name in calls
    checked = set(calls)
    other_test = root / f"tests/test_{unexecuted_name}.py"
    other_test.write_text(
        other_test.read_text(encoding="utf-8").replace("value() == 1", "value() == 99"),
        encoding="utf-8",
    )
    repaired, plan, calls = execute(root, env, "both-repaired")
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert plan["mode"] == "affected" and unexecuted_name in calls
    if failed_name not in checked:
        assert failed_name in calls


def test_tool_call_tracking_changes_do_not_invalidate_real_history(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    first, _, _ = execute(root, {**env, "CODEX_TOOL_CALL_ID": "synthetic-call-one"}, "first")
    assert first.returncode == 0, first.stdout + first.stderr
    reused, plan, calls = execute(
        root,
        {**env, "CODEX_TOOL_CALL_ID": "synthetic-call-two", "MOSSWORK_TEST_REQUIRE_REUSE": "1"},
        "new-tracking-id",
    )
    assert reused.returncode == 0, reused.stdout + reused.stderr
    assert plan["mode"] == "affected" and calls == []
    refused, plan, calls = execute(
        root,
        {
            **env,
            "MOSSWORK_TEST_REQUIRE_REUSE": "1",
            "MOSSWORK_SYNTHETIC_VARIANT": "private-control-value",
        },
        "real-environment-change",
    )
    assert refused.returncode != 0 and not plan and calls == []
    assert "MOSSWORK_SYNTHETIC_VARIANT" in refused.stderr
    assert "private-control-value" not in refused.stdout + refused.stderr


def test_reuse_measurement_refuses_before_an_unplanned_full_run(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    refused, plan, calls = execute(root, {**env, "MOSSWORK_TEST_REQUIRE_REUSE": "1"}, "no-baseline")
    assert refused.returncode != 0 and not plan and calls == []
    assert "No tests ran" in refused.stderr and "make all" in refused.stderr
    assert not (root / ".git/mosswork-test-gate/incomplete.json").exists()


@pytest.mark.parametrize("repetitions", [1, 2, 3])
def test_posix_git_launch_preserves_other_path_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repetitions: int
) -> None:
    from tools import test_gate

    git_exec = str(tmp_path / "git-exec")
    Path(git_exec).mkdir()
    original = [git_exec] * repetitions + ["/custom/tools", git_exec, "/usr/bin", "/custom/tools"]
    monkeypatch.setattr(test_gate.sys, "platform", "linux")
    monkeypatch.setenv("GIT_EXEC_PATH", git_exec)
    monkeypatch.setenv("PATH", os.pathsep.join(original))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath=.githooks' 'fixture.policy=keep'")
    test_gate.normalize_launch_environment()
    assert os.environ["PATH"].split(os.pathsep) == [
        git_exec,
        "/custom/tools",
        git_exec,
        "/usr/bin",
        "/custom/tools",
    ]
    assert os.environ["GIT_CONFIG_PARAMETERS"] == "'core.hooksPath=.githooks' 'fixture.policy=keep'"


def test_posix_git_launch_keeps_interpreter_and_custom_path_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import test_gate

    git_exec = str(tmp_path / "git-exec")
    Path(git_exec).mkdir()
    interpreter = str(Path(sys.executable).parent)
    custom = ["/custom/tools", git_exec, "/usr/bin", "/custom/tools"]
    monkeypatch.setattr(test_gate.sys, "platform", "linux")
    monkeypatch.setenv("GIT_EXEC_PATH", git_exec)
    monkeypatch.setenv(
        "PATH", os.pathsep.join([interpreter, git_exec, interpreter, git_exec, *custom])
    )
    test_gate.normalize_launch_environment()
    assert os.environ["PATH"].split(os.pathsep) == [interpreter, git_exec, interpreter, *custom]


@pytest.mark.parametrize("runtime", ["mingw64", "ucrt64"])
def test_windows_git_launch_uses_the_actual_runtime_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime: str
) -> None:
    from tools import test_gate

    root = tmp_path / "installed Git"
    directories = [
        root / runtime / "libexec/git-core",
        root / runtime / "bin",
        root / "usr/bin",
        root / "bin",
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    (root / "bin/git.exe").touch()
    monkeypatch.setattr(test_gate.sys, "platform", "win32")
    monkeypatch.setenv("GIT_EXEC_PATH", str(directories[0]))
    monkeypatch.setenv(
        "PATH", os.pathsep.join(str(p) for p in [*directories, *directories, root / "custom"])
    )
    test_gate.normalize_launch_environment()
    assert os.environ["PATH"].split(os.pathsep) == [
        os.path.normcase(os.path.normpath(str(p))) for p in [*directories, root / "custom"]
    ]


@pytest.mark.parametrize("folder", ["unit", "integration"])
@pytest.mark.parametrize("staged", [False, True])
def test_actual_graph_runs_added_modules_and_keeps_failed_pending_cases_required(
    gate_repo: tuple[Path, dict[str, str]], folder: str, staged: bool
) -> None:
    root, env = gate_repo
    initial, base, calls = execute(root, env, "added-baseline")
    assert initial.returncode == 0 and sorted(calls) == ["first", "second"]
    graph = base["graph"]
    baseline = root / ".git/mosswork-test-gate/baseline.json"
    original_pass = baseline.read_bytes()
    added = root / "tests" / folder / "test_added_module.py"
    added.parent.mkdir(parents=True)
    added.write_text(
        "from pathlib import Path\n\ndef test_added():\n"
        "    with Path('output/observed').open('a') as f: f.write('added\\n')\n"
        "    assert False\n\ndef test_pending():\n"
        "    with Path('output/observed').open('a') as f: f.write('pending\\n')\n",
        encoding="utf-8",
    )
    if staged:
        subprocess.run(["git", "add", str(added.relative_to(root))], cwd=root, env=env, check=True)
    failed, plan, calls = execute(root, env, "new-module-failed")
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert plan["mode"] == "affected" and plan["graph"] == graph
    assert calls == ["added"]
    assert baseline.read_bytes() == original_pass
    added.write_text(
        added.read_text(encoding="utf-8").replace("assert False", "assert True"), encoding="utf-8"
    )
    repaired, plan, calls = execute(root, env, "new-module-repaired")
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert plan["mode"] == "affected" and plan["graph"] == graph
    assert sorted(calls) == ["added", "pending"]


def test_new_live_module_remains_unexecuted_until_original_live_scope(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    (root / "tests/test_live.py").write_text(
        "import pytest\nfrom pathlib import Path\n\n@pytest.mark.live\ndef test_live():\n"
        "    with Path('output/observed').open('a') as f: f.write('old-live\\n')\n",
        encoding="utf-8",
    )
    initial, base, calls = execute(root, env, "live-module-baseline")
    assert initial.returncode == 0 and sorted(calls) == ["first", "second"]
    added = root / "tests/unit/test_new_live_module.py"
    added.parent.mkdir(parents=True)
    added.write_text(
        "import pytest\nfrom pathlib import Path\n\n@pytest.mark.live\ndef test_new_live():\n"
        "    with Path('output/observed').open('a') as f: f.write('new-live\\n')\n"
        "    assert False\n",
        encoding="utf-8",
    )
    nonlive, plan, calls = execute(root, env, "new-live-excluded")
    assert nonlive.returncode == 0 and plan["mode"] == "affected" and calls == []
    failed, plan, failed_calls = execute(root, env, "new-live-required", "--include-live")
    assert failed.returncode != 0 and plan["mode"] == "affected"
    assert failed_calls.count("new-live") == 1
    assert not {"first", "second"}.intersection(failed_calls)
    added.write_text(
        added.read_text(encoding="utf-8").replace("assert False", "assert True"), encoding="utf-8"
    )
    repaired, plan, calls = execute(root, env, "new-live-repaired", "--include-live")
    assert repaired.returncode == 0 and plan["mode"] == "affected"
    assert calls.count("new-live") == 1 and not {"first", "second"}.intersection(calls)
    assert failed_calls.count("old-live") + calls.count("old-live") == 1
    assert plan["graph"] == base["graph"]


def test_unknown_helpers_and_removed_tests_keep_full_run_and_name_cli_inputs(
    gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = gate_repo
    initial, _, _ = execute(root, env, "blocked-baseline")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    helper = root / "tests/unit/helper.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("UNUSED = True\n", encoding="utf-8")
    changed, plan, calls = execute(root, env, "unknown-helper")
    assert changed.returncode == 0 and plan["mode"] == "full"
    assert sorted(calls) == ["first", "second"]
    assert "tests/unit/helper.py" in plan["reason"]
    (root / "tests/test_second.py").unlink()
    removed, plan, calls = execute(root, env, "removed-test")
    assert removed.returncode == 0 and plan["mode"] == "full" and calls == ["first"]
    assert "tests/test_second.py" in plan["reason"]


_CI_INPUTS = (
    ".github/workflows/mosswork-ci.yml",
    ".github/workflows/mosswork-identity-ci.yml",
    ".github/workflows/mosswork-browser-ci.yml",
    ".github/actions/canonical-product-checks/action.yml",
    "tools/precommit.py",
)
_CI_READERS = (
    "tests/unit/deployment/test_precommit.py",
    "tests/unit/deployment/test_make_format_gate.py",
    "tests/unit/deployment/test_hook_staging.py",
    "tests/unit/deployment/test_frontend_gate.py",
    "tests/unit/deployment/test_export_public_source.py",
    "tests/unit/deployment/test_ci_identity_reuse.py",
    "tests/unit/deployment/test_ci_action_recipe.py",
    "tests/unit/deployment/test_browser_worker_acceptance.py",
    "tests/unit/deployment/test_make_testmon.py",
)


@pytest.fixture
def ci_gate_repo(gate_repo: tuple[Path, dict[str, str]]) -> tuple[Path, dict[str, str]]:
    root, environment = gate_repo
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in _CI_INPUTS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# enabled: true\n", encoding="utf-8")
    for index, name in enumerate(_CI_READERS):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"from first import value\nfrom pathlib import Path\ndef test_ci_{index}():\n"
            f"    with Path('output/observed').open('a') as f: f.write('ci{index}\\n')\n"
            "    assert 'enabled: true' in Path('.github/workflows/mosswork-identity-ci.yml').read_text()\n"
            "    assert value() in (0, 1, 2)\n",
            encoding="utf-8",
        )
    return root, environment


def _ci_attempt(plan: dict) -> dict:
    return json.loads(Path(plan["receipt"]).read_text(encoding="utf-8"))


def _all_ci_fixture_calls() -> Counter:
    return Counter({**{"ci" + str(i): 1 for i in range(9)}, "first": 1, "second": 1})


def test_known_ci_scope_is_exact_and_never_accepts_additions_or_removals() -> None:
    assert frozenset(_CI_INPUTS) == gate.INDEPENDENT_CI_INPUTS
    assert gate.INDEPENDENT_CI_TESTS == _CI_READERS
    for name in _CI_INPUTS:
        assert gate.full_test_inputs({name: "old"}, {name: "new"}) == []
        assert gate.full_test_inputs({}, {name: "new"}) == [name]
        assert gate.full_test_inputs({name: "old"}, {}) == [name]
    for name in ("Makefile", "uv.lock", "pyproject.toml", "conftest.py", "tests/support/helper.py"):
        assert gate.full_test_inputs({name: "old"}, {name: "new"}) == [name]


def test_real_ci_phase_preserves_graph_and_both_junit_records(
    ci_gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = ci_gate_repo
    initial, old, calls = execute(root, env, "ci-initial")
    assert initial.returncode == 0 and Counter(calls) == _all_ci_fixture_calls()
    (root / "src/first.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    test = root / "tests/test_first.py"
    test.write_text(
        test.read_text().replace("assert value() == 1", "assert value() == 2"), encoding="utf-8"
    )
    metadata = root / ".github/workflows/mosswork-identity-ci.yml"
    metadata.write_text(metadata.read_text() + "# revision 2\n", encoding="utf-8")
    result, plan, calls = execute(root, env, "ci-and-app")
    assert result.returncode == 0 and plan["mode"] == "affected" and plan["graph"] == old["graph"]
    assert Counter(calls) == _all_ci_fixture_calls() - Counter({"second": 1})
    attempt = _ci_attempt(plan)
    phase = attempt["independent_ci_phase"]
    assert phase["passed"] and phase["session"]["passed"] == 9
    assert attempt["pytest_session"]["passed"] == 1
    junit = Path(phase["junit_path"])
    assert junit.is_file() and junit != root / "output/ci-and-app.xml"
    assert hashlib.sha256(junit.read_bytes()).hexdigest() == phase["junit_sha256"]
    assert (root / "output/ci-and-app.xml").is_file()


def test_ci_failure_stops_app_and_retains_full_then_failed_graph_recovery(
    ci_gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = ci_gate_repo
    initial, base, _ = execute(root, env, "ci-recovery-base")
    assert initial.returncode == 0
    metadata = root / ".github/workflows/mosswork-identity-ci.yml"
    metadata.write_text("enabled: false\n", encoding="utf-8")
    failed, plan, calls = execute(root, env, "ci-first-failure")
    assert failed.returncode != 0 and calls and all(name.startswith("ci") for name in calls)
    attempt = _ci_attempt(plan)
    assert not attempt["passed"] and not attempt["independent_ci_phase"]["passed"]
    assert (root / ".git/mosswork-test-gate/incomplete.json").exists()
    metadata.write_text("enabled: true\n# fixed\n", encoding="utf-8")
    repaired, new, calls = execute(root, env, "ci-recovery-full")
    assert repaired.returncode == 0 and new["mode"] == "full" and new["graph"] != base["graph"]
    assert Counter(calls) == _all_ci_fixture_calls()
    (root / "src/first.py").write_text("def value():\n    return 0\n", encoding="utf-8")
    metadata.write_text(metadata.read_text() + "# revision\n", encoding="utf-8")
    app_failed, plan, calls = execute(root, env, "ci-then-app-failure")
    assert app_failed.returncode != 0 and "first" in calls and "second" not in calls
    attempt = _ci_attempt(plan)
    assert attempt["independent_ci_phase"]["passed"] and attempt["pytest_session"]["failed"] == 1
    assert attempt["trusted_graph"] and not attempt["passed"]
    (root / "src/first.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    result, plan, calls = execute(root, env, "ci-original-failed-recovery")
    assert result.returncode == 0 and plan["mode"] == "affected" and plan["graph"] == new["graph"]
    assert Counter(calls) == _all_ci_fixture_calls() - Counter({"second": 1})
    assert "independent_ci_phase" not in _ci_attempt(plan)


def test_ci_source_drift_refuses_before_app_cases(
    ci_gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = ci_gate_repo
    reader = root / _CI_READERS[0]
    initial, _, _ = execute(root, env, "ci-drift-base")
    assert initial.returncode == 0
    reader.write_text(
        reader.read_text() + "    Path('src/second.py').write_text('DRIFT = True')\n",
        encoding="utf-8",
    )
    metadata = root / ".github/workflows/mosswork-identity-ci.yml"
    metadata.write_text(metadata.read_text() + "# revision\n", encoding="utf-8")
    result, plan, calls = execute(root, env, "ci-source-drift")
    assert result.returncode != 0 and all(name.startswith("ci") for name in calls)
    assert "Source/environment changed during independent CI" in _ci_attempt(plan)["error"]
    assert (root / ".git/mosswork-test-gate/incomplete.json").exists()


@pytest.mark.parametrize("completion", ["missing", "malformed-count"])
def test_ci_shell_zero_without_actual_completion_refuses_before_app(
    ci_gate_repo: tuple[Path, dict[str, str]], completion: str
) -> None:
    root, env = ci_gate_repo
    path = root / "tools/test_gate.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    class ControlledCIExecutor(ast.NodeTransformer):
        changed = 0

        def visit_Assign(self, node: ast.Assign):
            if not any(
                isinstance(target, ast.Name) and target.id == "ci_process"
                for target in node.targets
            ):
                return self.generic_visit(node)
            self.changed += 1
            node.value = ast.parse("subprocess.CompletedProcess(ci_argv, 0)", mode="eval").body
            if completion == "missing":
                return node
            fake = ast.parse(
                'ci_report.write_text(json.dumps(dict(nonce=nonce, root=str(root), exit_code=0, collect_only=False, collected=1, passed="wrong type", failed=0, errors=0, worker_crashed=False, interrupted=False, maxfail_stop=False)), encoding="utf-8")\n'
                'ci_junit.write_text("explicit malformed private negative control", encoding="utf-8")\n'
            ).body
            return [*fake, node]

    controlled = ControlledCIExecutor()
    tree = controlled.visit(tree)
    assert controlled.changed == 1
    path.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n", encoding="utf-8")
    initial, _, _ = execute(root, env, "ci-zero-base")
    assert initial.returncode == 0
    metadata = root / ".github/workflows/mosswork-identity-ci.yml"
    metadata.write_text(metadata.read_text() + "# revision\n", encoding="utf-8")
    result, plan, calls = execute(root, env, "ci-zero-no-report")
    assert result.returncode != 0 and calls == []
    assert _ci_attempt(plan)["independent_ci_phase"]["exit_code"] == 0
    assert (
        "Independent CI validation failed or lacks actual completion" in _ci_attempt(plan)["error"]
    )
    assert (root / ".git/mosswork-test-gate/incomplete.json").exists()


def test_missing_independent_reader_cannot_accept_ci_metadata(
    ci_gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = ci_gate_repo
    (root / _CI_READERS[-1]).unlink()
    initial, _, _ = execute(root, env, "ci-missing-base")
    assert initial.returncode == 0
    metadata = root / ".github/workflows/mosswork-identity-ci.yml"
    metadata.write_text(metadata.read_text() + "# revision\n", encoding="utf-8")
    result, plan, calls = execute(root, env, "ci-missing-reader")
    assert result.returncode != 0 and calls == []
    assert "Independent CI validators missing" in _ci_attempt(plan)["error"]


def test_ci_metadata_with_global_input_still_runs_full(
    ci_gate_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = ci_gate_repo
    initial, _, _ = execute(root, env, "ci-global-base")
    assert initial.returncode == 0
    for index, name in enumerate(
        ("Makefile", "uv.lock", "pyproject.toml", "conftest.py", "tests/support/new_helper.py")
    ):
        metadata = root / ".github/workflows/mosswork-identity-ci.yml"
        metadata.write_text(metadata.read_text() + "# revision\n", encoding="utf-8")
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(original + "\n# unrelated global input\n", encoding="utf-8")
        result, plan, calls = execute(root, env, f"ci-global-required-{index}")
        assert (
            result.returncode == 0
            and plan["mode"] == "full"
            and Counter(calls) == _all_ci_fixture_calls()
        )
        assert name in plan["reason"]
        assert "independent_ci_phase" not in _ci_attempt(plan)
