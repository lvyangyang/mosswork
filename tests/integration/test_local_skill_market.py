"""Published local skills must install into usable packages without market management access."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from octop.infra.market.service import ResourceMarket, example_package
from octop.infra.skills.skill_package_store import SkillPackageStore
from octop.infra.users.identity import User
from tests.support.auth import create_user
from tests.unit.market.test_expert_installation import named_skill
from tests.unit.market.test_resource_market import release, rewrite


def publish(server):
    services = server.services
    admin = services.user_repo.get_by_username("admin")
    market = ResourceMarket(services, User(admin.id, "admin", "admin", None))
    leaf, leaf_v1 = release(market, named_skill("leaf-skill"))
    _, leaf_v2 = release(
        market, rewrite(named_skill("leaf-skill"), version="2.0.0"), leaf["resource_id"]
    )
    market.recommend(leaf["resource_id"], leaf_v2["version_id"], leaf_v1["version_id"])
    root, v1 = release(
        market,
        named_skill(
            "root-skill", dependencies=[{"resource_id": leaf["resource_id"], "version": "1.0.0"}]
        ),
    )
    return market, root, v1, leaf_v1


async def test_consumer_can_inspect_and_install_exact_release_without_publishing_access(env):
    client, server, auth = env
    peer = await create_user(client, auth, username="skill_consumer")
    market, root, v1, leaf_v1 = publish(server)
    assert (await client.get("/api/market/resources", headers=peer)).status_code == 403
    response = await client.get("/api/skill-packages/market", headers=peer)
    assert response.status_code == 200, response.text
    assert v1["version_id"] in [v["version_id"] for v in response.json()["items"]]
    route = f"/api/skill-packages/market/{v1['version_id']}"
    detail = await client.get(route, headers=peer)
    assert detail.status_code == 200, detail.text
    assert {v["version_id"] for v in detail.json()["lock"]} == {
        v1["version_id"],
        leaf_v1["version_id"],
    }
    assert len(detail.json()["file_contents"]) == 2
    _, v2 = release(
        market, rewrite(named_skill("root-skill"), version="2.0.0"), root["resource_id"]
    )
    market.recommend(root["resource_id"], v2["version_id"], v1["version_id"])
    result = await client.post(
        route + "/install", headers=peer, json={"name": "Inspection release v1"}
    )
    assert result.status_code == 201, result.text
    package_id = result.json()["package"]["id"]
    assert result.json()["package"]["skill_count"] == 2
    assert result.json()["source"]["version_id"] == v1["version_id"]
    package = (await client.get(f"/api/skill-packages/{package_id}", headers=peer)).json()
    assert package["can_write"] is True
    assert package["market_source"]["lock"] == result.json()["source"]["lock"]
    assert {v["slug"] for v in package["skills"]} == {"root-skill", "leaf-skill"}
    duplicate = await client.post(
        route + "/install", headers=peer, json={"name": "Inspection release v1"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SKILL_PACKAGE_NAME_TAKEN"
    market.listing(root["resource_id"], False)
    assert (await client.get(route, headers=peer)).status_code == 404
    assert (
        await client.post(route + "/install", headers=peer, json={"name": "hidden"})
    ).status_code == 404
    assert (await client.get(f"/api/skill-packages/{package_id}", headers=peer)).status_code == 200


async def test_installed_market_package_mounts_on_agent(env_with_agent):
    client, server, auth, agent_id = env_with_agent
    _, _, v1, _ = publish(server)
    result = await client.post(
        f"/api/skill-packages/market/{v1['version_id']}/install",
        headers=auth,
        json={"name": "Mountable market skills"},
    )
    assert result.status_code == 201, result.text
    package_id = result.json()["package"]["id"]
    mounted = await client.put(
        f"/api/agents/{agent_id}/skill-packages", headers=auth, json={"package_ids": [package_id]}
    )
    assert mounted.status_code == 200, mounted.text
    skills = (await client.get(f"/api/agents/{agent_id}/skills", headers=auth)).json()
    assert {v["slug"] for v in skills if v["kind"] == "package"} == {"root-skill", "leaf-skill"}


async def test_permission_drafts_experts_corruption_and_unsupported_runtime_are_rejected(env):
    client, server, auth = env
    market, root, v1, _ = publish(server)
    peer = await create_user(client, auth, username="no_skills", permissions=[])
    route = f"/api/skill-packages/market/{v1['version_id']}"
    for path in ["/api/skill-packages/market", route]:
        assert (await client.get(path, headers=peer)).status_code == 403
    assert (
        await client.post(route + "/install", headers=peer, json={"name": "forbidden"})
    ).status_code == 403
    draft = market.import_package(rewrite(named_skill("unpublished")))
    _, expert = release(market, example_package("expert"))
    for version_id in [draft["draft_id"], expert["version_id"]]:
        assert (
            await client.get(f"/api/skill-packages/market/{version_id}", headers=auth)
        ).status_code == 404
    _, unsupported = release(
        market,
        rewrite(
            named_skill("root-skill"),
            version="1.1.0",
            updates={"configuration": {"system_url": {"type": "url", "required": True}}},
        ),
        root["resource_id"],
    )
    result = await client.post(
        f"/api/skill-packages/market/{unsupported['version_id']}/install",
        headers=auth,
        json={"name": "unsupported"},
    )
    assert result.status_code == 409, result.text
    assert result.json()["error"]["code"] == "MARKET_INSTALL_UNSUPPORTED"
    # Simulate owner tampering after removing the immutable object's write protection.
    (market.objects / v1["object_sha256"]).chmod(0o600)
    (market.objects / v1["object_sha256"]).unlink()
    (market.objects / v1["object_sha256"]).write_bytes(b"corrupt")
    result = await client.post(route + "/install", headers=auth, json={"name": "corrupt"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "MARKET_CONTENT_CORRUPT"
    assert server.services.skill_package_repo.list_all() == []


async def test_install_failure_rolls_back_package_and_files(env):
    client, server, auth = env
    _, _, v1, _ = publish(server)
    original = SkillPackageStore.write_skill
    writes = 0

    def fail_second(self, *args, **kwargs):
        nonlocal writes
        writes += 1
        assert server.services.skill_package_repo.list_all() == []
        if writes == 2:
            raise OSError("disk full")
        return original(self, *args, **kwargs)

    with (
        patch.object(SkillPackageStore, "write_skill", fail_second),
        pytest.raises(OSError, match="disk full"),
    ):
        await client.post(
            f"/api/skill-packages/market/{v1['version_id']}/install",
            headers=auth,
            json={"name": "partial"},
        )
    assert server.services.skill_package_repo.list_all() == []
    assert list(server.paths.skill_packages_dir.iterdir()) == []


async def test_concurrent_same_name_installs_keep_one_complete_package(env):
    client, server, auth = env
    _, _, version, _ = publish(server)
    route = f"/api/skill-packages/market/{version['version_id']}/install"
    responses = await asyncio.gather(
        *(client.post(route, headers=auth, json={"name": "Same release"}) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    rows = server.services.skill_package_repo.list_all()
    assert len(rows) == 1
    assert rows[0].skill_count == 2
    assert sorted(path.name for path in server.paths.skill_packages_dir.iterdir()) == [rows[0].id]


async def test_existing_package_install_status_and_permission_boundary(env):
    client, server, auth = env
    _, _, version, _ = publish(server)
    created = await client.post("/api/skill-packages", headers=auth, json={"name": "Target"})
    assert created.status_code == 200, created.text
    package_id = created.json()["id"]
    route = f"/api/skill-packages/market/{version['version_id']}"
    status_route = f"/api/skill-packages/market/packages/{package_id}/status"
    plan = await client.post(route + "/preview", headers=auth, json={"package_id": package_id})
    assert plan.status_code == 200, plan.text
    assert plan.json()["action"] == "install"
    result = await client.post(
        route + "/install",
        headers=auth,
        json={"package_id": package_id, "token": plan.json()["token"]},
    )
    assert result.status_code == 201, result.text
    assert result.json()["package"]["id"] == package_id
    assert result.json()["refresh_failed"] is False
    assert len(server.services.skill_package_repo.list_all()) == 1
    status = await client.get(status_route, headers=auth)
    assert {item["slug"] for item in status.json()["installed"]} == {"root-skill", "leaf-skill"}
    again = await client.post(route + "/preview", headers=auth, json={"package_id": package_id})
    assert again.json()["action"] == "reinstall"
    no_token = await client.post(route + "/install", headers=auth, json={"package_id": package_id})
    assert no_token.status_code == 422
    stale = await client.post(
        route + "/install",
        headers=auth,
        json={"package_id": package_id, "token": plan.json()["token"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "MARKET_INSTALL_CHANGED"
    peer = await create_user(client, auth, username="other_package_user")
    assert (await client.get(status_route, headers=peer)).status_code == 403
    for endpoint, body in [
        ("preview", {"package_id": package_id}),
        ("install", {"package_id": package_id, "token": again.json()["token"]}),
    ]:
        assert (
            await client.post(route + "/" + endpoint, headers=peer, json=body)
        ).status_code == 403


async def test_existing_install_does_not_fail_when_agent_refresh_fails(env):
    from unittest.mock import AsyncMock

    client, server, auth = env
    _, _, version, _ = publish(server)
    package = await client.post("/api/skill-packages", headers=auth, json={"name": "Refresh retry"})
    package_id = package.json()["id"]
    route = f"/api/skill-packages/market/{version['version_id']}"
    plan = await client.post(route + "/preview", headers=auth, json={"package_id": package_id})
    with patch.object(
        server.app_runtime.agent_registry,
        "refresh_agents_for_package",
        AsyncMock(side_effect=RuntimeError("runtime unavailable")),
    ):
        result = await client.post(
            route + "/install",
            headers=auth,
            json={"package_id": package_id, "token": plan.json()["token"]},
        )
    assert result.status_code == 201, result.text
    assert result.json()["refresh_failed"] is True
    assert result.json()["package"]["skill_count"] == 2
    retry = await client.post(
        f"/api/skill-packages/market/packages/{package_id}/refresh", headers=auth
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["refresh_failed"] is False


async def test_existing_package_mount_sees_new_market_skill_without_new_agent(env_with_agent):
    client, server, auth, agent_id = env_with_agent
    _, _, version, _ = publish(server)
    package = await client.post(
        "/api/skill-packages", headers=auth, json={"name": "Already mounted"}
    )
    package_id = package.json()["id"]
    mounted = await client.put(
        f"/api/agents/{agent_id}/skill-packages", headers=auth, json={"package_ids": [package_id]}
    )
    assert mounted.status_code == 200, mounted.text
    route = f"/api/skill-packages/market/{version['version_id']}"
    plan = await client.post(route + "/preview", headers=auth, json={"package_id": package_id})
    result = await client.post(
        route + "/install",
        headers=auth,
        json={"package_id": package_id, "token": plan.json()["token"]},
    )
    assert result.status_code == 201, result.text
    skills = (await client.get(f"/api/agents/{agent_id}/skills", headers=auth)).json()
    assert {item["slug"] for item in skills if item["kind"] == "package"} == {
        "root-skill",
        "leaf-skill",
    }
    mounted = await client.get(f"/api/agents/{agent_id}/skill-packages", headers=auth)
    assert mounted.json()["package_ids"] == [package_id]


async def test_consumer_install_reinstall_update_rollback_and_unlist_flow(env):
    """Exercise the target picker contract with real HTTP, receipts and skill files."""
    client, server, auth = env
    peer = await create_user(client, auth, username="market_trial", permissions=["skill_packages"])
    market, root, v1, leaf = publish(server)
    ids = []
    for name in ["Production", "Support"]:
        response = await client.post("/api/skill-packages", headers=peer, json={"name": name})
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])
    production, support = ids
    local_content = "---\nname: local-notes\ndescription: Preserve these notes\n---\nLocal notes"
    extra = await client.post(
        f"/api/skill-packages/{production}/skills",
        headers=peer,
        json={"name": "local-notes", "content": local_content},
    )
    assert extra.status_code == 200, extra.text
    # The dropdown exposes only writable packages; looking and cancelling write nothing.
    available = await client.get("/api/skill-packages?writable_only=true", headers=peer)
    assert {item["id"] for item in available.json()} == set(ids)
    assert (await client.get("/api/market/resources", headers=peer)).status_code == 403

    async def preview(version, package_id, action):
        route = f"/api/skill-packages/market/{version['version_id']}"
        response = await client.post(
            route + "/preview", headers=peer, json={"package_id": package_id}
        )
        assert response.status_code == 200, response.text
        assert response.json()["action"] == action
        return route, response.json()

    async def apply(version, package_id, action):
        route, plan = await preview(version, package_id, action)
        response = await client.post(
            route + "/install",
            headers=peer,
            json={"package_id": package_id, "token": plan["token"]},
        )
        assert response.status_code == 201, response.text
        assert response.json()["package"]["id"] == package_id
        assert response.json()["refresh_failed"] is False
        return response.json()

    await preview(v1, production, "install")
    before = await client.get(f"/api/skill-packages/{production}", headers=peer)
    assert {item["slug"] for item in before.json()["skills"]} == {"local-notes"}
    await apply(v1, production, "install")
    await apply(v1, production, "reinstall")
    await preview(v1, support, "install")
    dependency = {"resource_id": leaf["resource_id"], "version": "1.0.0"}
    _, v2 = release(
        market,
        rewrite(
            named_skill("root-skill", dependencies=[dependency]),
            version="2.0.0",
            files={
                "SKILL.md": b"---\nname: root-skill\ndescription: Updated inspection\n---\nVersion two"
            },
        ),
        root["resource_id"],
    )
    await apply(v2, production, "update")
    updated = await client.get(f"/api/skill-packages/{production}/skills/root-skill", headers=peer)
    assert "Version two" in updated.json()["raw"]
    await apply(v1, production, "rollback")
    await apply(v2, support, "install")
    for package_id, version in [(production, "1.0.0"), (support, "2.0.0")]:
        status = await client.get(
            f"/api/skill-packages/market/packages/{package_id}/status", headers=peer
        )
        assert (
            next(
                item["version"]
                for item in status.json()["installed"]
                if item["resource_id"] == root["resource_id"]
            )
            == version
        )
    preserved = await client.get(
        f"/api/skill-packages/{production}/skills/local-notes", headers=peer
    )
    assert preserved.json()["raw"] == local_content
    assert len(server.services.skill_package_repo.list_all()) == 2
    market.listing(root["resource_id"], False)
    hidden = await client.post(
        f"/api/skill-packages/market/{v1['version_id']}/preview",
        headers=peer,
        json={"package_id": production},
    )
    assert hidden.status_code == 404
    still_available = await client.get(
        f"/api/skill-packages/{production}/skills/root-skill", headers=peer
    )
    assert still_available.status_code == 200
