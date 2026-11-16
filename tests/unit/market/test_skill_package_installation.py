"""Existing-package installs preserve unrelated skills and reject unsafe replacement."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from tests.unit.market.test_expert_installation import named_skill
from tests.unit.market.test_resource_market import market_env, release, rewrite  # noqa: F401

from octop.infra.errors import MossworkError
from octop.infra.market.service import ResourceMarket
from octop.infra.market.skill_installation import LocalSkillMarket
from octop.infra.market.skill_package_installation import PackageSkillInstaller, hashes


@contextmanager
def raises_code(code):
    with pytest.raises(MossworkError) as caught:
        yield
    assert caught.value.code == code


@pytest.fixture
def setup(market_env):  # noqa: F811
    services, users = market_env
    market = ResourceMarket(services, users["admin"])
    local = LocalSkillMarket(services)
    installer = PackageSkillInstaller(local.store, local.releases)
    package = local.store.create(
        name="Existing", description="Keep me", created_by=str(users["developer"].id)
    )
    return market, local, installer, package, users


def install(installer, user, version, package):
    plan = installer.preview(user, version["version_id"], package.id)
    return installer.install(user, version["version_id"], package.id, plan["token"])


def test_multiple_skills_reinstall_updates_and_rollback_keep_same_package(setup):
    market, local, installer, package, users = setup
    user = users["developer"]
    a, v1 = release(market, rewrite(named_skill("alpha"), files={"assets/old.txt": b"old"}))
    _, vb = release(market, named_skill("bravo"))
    result = install(installer, user, v1, package)
    assert result["package"]["id"] == package.id
    install(installer, user, vb, package)
    before = hashes(local.store.package_skills_dir(package.id) / "bravo")
    assert len(local.store.repo.list_all()) == 1
    plan = installer.preview(user, v1["version_id"], package.id)
    assert plan["action"] == "reinstall"
    (local.store.package_skills_dir(package.id) / "alpha" / "local.txt").write_text("edits")
    plan = installer.preview(user, v1["version_id"], package.id)
    assert plan["modified_slugs"] == ["alpha"]
    installer.install(user, v1["version_id"], package.id, plan["token"])
    assert not (local.store.package_skills_dir(package.id) / "alpha" / "local.txt").exists()
    _, v2 = release(market, rewrite(named_skill("alpha"), version="2.0.0"), a["resource_id"])
    assert installer.preview(user, v2["version_id"], package.id)["action"] == "update"
    install(installer, user, v2, package)
    assert not (local.store.package_skills_dir(package.id) / "alpha" / "assets/old.txt").exists()
    assert installer.preview(user, v1["version_id"], package.id)["action"] == "rollback"
    install(installer, user, v1, package)
    assert (
        local.store.package_skills_dir(package.id) / "alpha" / "assets/old.txt"
    ).read_bytes() == b"old"
    assert hashes(local.store.package_skills_dir(package.id) / "bravo") == before
    assert local.store.repo.get(package.id).description == "Keep me"
    assert local.store.repo.get(package.id).skill_count == 2
    receipt = json.loads((local.store.root / package.id / "market-installations.json").read_text())
    assert len(receipt["resources"]) == 2
    assert all(entry["installed_at"] for entry in receipt["resources"].values())


def test_status_is_per_package_and_deleting_skill_clears_receipt(setup):
    market, local, installer, package, users = setup
    _, v1 = release(market, named_skill("alpha"))
    user = users["developer"]
    install(installer, user, v1, package)
    other = local.store.create(name="Other", description="", created_by=str(user.id))
    assert installer.statuses(user, other.id)["installed"] == []
    assert len(installer.statuses(user, package.id)["installed"]) == 1
    local.store.delete_skill(package.id, "alpha")
    assert installer.statuses(user, package.id)["installed"] == []
    assert installer.preview(user, v1["version_id"], package.id)["action"] == "install"
    local.store.write_skill(package.id, "alpha", [("SKILL.md", b"local manual skill")])
    with raises_code("MARKET_SKILL_CONFLICT"):
        installer.preview(user, v1["version_id"], package.id)


def test_legacy_receipts_recognized_and_deleted_identity_not_reused(setup):
    market, local, installer, _, users = setup
    _, v1 = release(market, named_skill("alpha"))
    created = local.install(users["developer"], v1["version_id"], "Legacy")
    package_id = created["package"]["id"]
    assert (
        installer.preview(users["developer"], v1["version_id"], package_id)["action"] == "reinstall"
    )
    local.store.delete_skill(package_id, "alpha")
    local.store.write_skill(package_id, "alpha", [("SKILL.md", b"unrelated manual content")])
    assert installer.statuses(users["developer"], package_id)["installed"] == []
    with raises_code("MARKET_SKILL_CONFLICT"):
        installer.preview(users["developer"], v1["version_id"], package_id)


def test_dependencies_reused_and_shared_version_conflict_rejected(setup):
    market, local, installer, package, users = setup
    dep, d1 = release(market, named_skill("dependency"))
    _, d2 = release(market, rewrite(named_skill("dependency"), version="2.0.0"), dep["resource_id"])
    pinned = [{"resource_id": dep["resource_id"], "version": "1.0.0"}]
    a, a1 = release(market, named_skill("alpha", dependencies=pinned))
    _, b1 = release(market, named_skill("bravo", dependencies=pinned))
    user = users["developer"]
    install(installer, user, a1, package)
    directory = local.store.package_skills_dir(package.id) / "dependency"
    (directory / "note.txt").write_text("keep local dependency edits")
    before = hashes(directory)
    preview = installer.preview(user, b1["version_id"], package.id)
    assert (
        next(change for change in preview["changes"] if change["slug"] == "dependency")["operation"]
        == "reuse"
    )
    install(installer, user, b1, package)
    assert hashes(directory) == before
    _, a2 = release(
        market,
        rewrite(
            named_skill(
                "alpha", dependencies=[{"resource_id": dep["resource_id"], "version": "2.0.0"}]
            ),
            version="2.0.0",
        ),
        a["resource_id"],
    )
    with raises_code("MARKET_SKILL_CONFLICT"):
        installer.preview(user, a2["version_id"], package.id)
    with raises_code("MARKET_SKILL_CONFLICT"):
        installer.preview(user, d2["version_id"], package.id)
    # A missing dependency keeps its owning roots' pins; reinstall repairs it.
    local.store.delete_skill(package.id, "dependency")
    with raises_code("MARKET_SKILL_CONFLICT"):
        installer.preview(user, a2["version_id"], package.id)
    install(installer, user, a1, package)
    assert installer.statuses(user, package.id)["installed"][0]
    assert (directory / "SKILL.md").is_file()


def test_sole_owner_can_upgrade_dependency(setup):
    market, local, installer, package, users = setup
    dep, _ = release(market, named_skill("dependency"))
    release(market, rewrite(named_skill("dependency"), version="2.0.0"), dep["resource_id"])
    a, a1 = release(
        market,
        named_skill(
            "alpha", dependencies=[{"resource_id": dep["resource_id"], "version": "1.0.0"}]
        ),
    )
    _, a2 = release(
        market,
        rewrite(
            named_skill(
                "alpha", dependencies=[{"resource_id": dep["resource_id"], "version": "2.0.0"}]
            ),
            version="2.0.0",
        ),
        a["resource_id"],
    )
    user = users["developer"]
    install(installer, user, a1, package)
    install(installer, user, a2, package)
    versions = {
        row["slug"]: row["version"] for row in installer.statuses(user, package.id)["installed"]
    }
    assert versions == {"dependency": "2.0.0", "alpha": "2.0.0"}


def test_permissions_and_same_slug_conflict(setup):
    market, local, installer, package, users = setup
    _, v1 = release(market, named_skill("alpha"))
    for operation in (installer.statuses,):
        with raises_code("FORBIDDEN"):
            operation(users["other"], package.id)
    with raises_code("FORBIDDEN"):
        installer.preview(users["other"], v1["version_id"], package.id)
    with raises_code("FORBIDDEN"):
        installer.install(users["other"], v1["version_id"], package.id, "fake")
    local.store.write_skill(package.id, "alpha", [("SKILL.md", b"existing manual skill")])
    before = hashes(local.store.root / package.id)
    with raises_code("MARKET_SKILL_CONFLICT"):
        installer.preview(users["admin"], v1["version_id"], package.id)
    assert hashes(local.store.root / package.id) == before


def test_changed_preview_and_concurrent_installs_do_not_lose_content(setup):
    market, local, installer, package, users = setup
    _, a1 = release(market, named_skill("alpha"))
    _, b1 = release(market, named_skill("bravo"))
    user = users["developer"]
    pa = installer.preview(user, a1["version_id"], package.id)
    pb = installer.preview(user, b1["version_id"], package.id)

    def apply(version, plan):
        try:
            installer.install(user, version["version_id"], package.id, plan["token"])
            return "success"
        except MossworkError as exc:
            assert str(exc.code) == "MARKET_INSTALL_CHANGED"
            return "changed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda pair: apply(*pair), [(a1, pa), (b1, pb)]))
    assert sorted(results) == ["changed", "success"]
    install(installer, user, a1, package)
    install(installer, user, b1, package)
    assert local.store.repo.get(package.id).skill_count == 2


@pytest.mark.parametrize("failure", ["staging", "commit"])
def test_failure_preserves_original_files_receipts_and_count(setup, failure):
    market, local, installer, package, users = setup
    _, a1 = release(market, named_skill("alpha"))
    _, b1 = release(market, named_skill("bravo"))
    user = users["developer"]
    install(installer, user, a1, package)
    before = hashes(local.store.root / package.id)
    count = local.store.repo.get(package.id).skill_count
    preview = installer.preview(user, b1["version_id"], package.id)
    target = (
        "octop.infra.market.skill_package_installation.write_json_atomic"
        if failure == "staging"
        else "octop.infra.skills.skill_package_store.SkillPackageStore._update_skill_count"
    )
    with patch(target, side_effect=OSError("disk full")), pytest.raises(OSError, match="disk full"):
        installer.install(user, b1["version_id"], package.id, preview["token"])
    assert hashes(local.store.root / package.id) == before
    assert local.store.repo.get(package.id).skill_count == count
    assert not list(local.store.root.glob(".market-install-*"))


def test_manual_create_rechecks_overwrite_under_the_same_package_lock(setup):
    market, local, installer, package, users = setup
    _, version = release(market, named_skill("alpha"))
    install(installer, users["developer"], version, package)
    before = hashes(local.store.root / package.id)
    with raises_code("SKILL_ALREADY_EXISTS"):
        local.store.write_skill(package.id, "alpha", [("SKILL.md", b"manual")], overwrite=False)
    assert hashes(local.store.root / package.id) == before
    local.store.delete_package(package.id)
    with raises_code("SKILL_PACKAGE_NOT_FOUND"):
        local.store.write_skill(package.id, "alpha", [("SKILL.md", b"manual")])
    assert not (local.store.root / package.id).exists()
