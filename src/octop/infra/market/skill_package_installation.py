"""Target-aware, offline installation plans and transactional package replacement."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, MossworkError
from octop.infra.market.expert_installation import LocalExpertMarket
from octop.infra.skills.package_lock import package_lock
from octop.infra.skills.skill_package_store import SkillPackageStore
from octop.infra.users.identity import User
from octop.infra.utils.json_file import JsonFileCorruptError, read_json_object, write_json_atomic

RECEIPT = "market-installations.json"


def conflict(slug: str) -> MossworkError:
    return MossworkError.localized(
        ErrorCode.MARKET_SKILL_CONFLICT, slug=slug, details={"slug": slug}
    )


def hashes(directory: Path) -> dict[str, str]:
    if not directory.exists():
        return {}
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise MossworkError.localized(ErrorCode.MARKET_CONTENT_CORRUPT)
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def action(version: str, installed: str | None) -> str:
    if installed is None:
        return "install"
    current = tuple(map(int, installed.split(".")))
    requested = tuple(map(int, version.split(".")))
    return "reinstall" if current == requested else "update" if requested > current else "rollback"


class PackageSkillInstaller:
    def __init__(self, store: SkillPackageStore, releases: LocalExpertMarket) -> None:
        self.store = store
        self.releases = releases

    def target(self, user: User, package_id: str) -> Any:
        row = self.store.repo.get(package_id)
        if row is None:
            raise MossworkError.localized(ErrorCode.SKILL_PACKAGE_NOT_FOUND)
        self.store.assert_can_mutate(row, user)
        return row

    def receipts(self, package_id: str) -> dict[str, Any]:
        """Adapt old package-level receipts without changing published objects."""
        directory = self.store.root / package_id
        try:
            value = read_json_object(directory / RECEIPT)
            if value is not None:
                if (
                    value.get("schema") != 1
                    or not isinstance(value.get("resources"), dict)
                    or not isinstance(value.get("roots"), dict)
                ):
                    raise ValueError("receipt schema")
                return value
            legacy = self.store.market_source(package_id)
            value = {"schema": 1, "resources": {}, "roots": {}}
            if not legacy:
                return value
            for entry in legacy["lock"]:
                row = self.releases.repo.version(entry["version_id"])
                if row is None:
                    raise ValueError("missing legacy release")
                manifest = json.loads(row["manifest_json"])
                if manifest["slug"] in legacy.get("deleted_slugs", []):
                    continue
                package = self.releases._package(row)
                value["resources"][entry["resource_id"]] = {
                    **entry,
                    "slug": manifest["slug"],
                    "files": {
                        path: hashlib.sha256(data).hexdigest()
                        for path, data in package.files.items()
                        if path != "market.json"
                    },
                    "installed_at": "",
                }
            if legacy["resource_id"] in value["resources"]:
                value["roots"][legacy["resource_id"]] = legacy["lock"]
            return value
        except (JsonFileCorruptError, ValueError, KeyError, TypeError) as exc:
            raise MossworkError.localized(ErrorCode.MARKET_CONTENT_CORRUPT) from exc

    def statuses(self, user: User, package_id: str) -> dict[str, Any]:
        self.target(user, package_id)
        with package_lock(self.store.root, package_id):
            row = self.target(user, package_id)
            receipt = self.receipts(package_id)
            skills = self.store.package_skills_dir(package_id)
            return {
                "package_id": row.id,
                "name": row.name,
                "installed": [
                    {key: entry[key] for key in ("resource_id", "version_id", "version", "slug")}
                    for entry in receipt["resources"].values()
                    if (skills / entry["slug"] / "SKILL.md").is_file()
                ],
            }

    def _plan(
        self, user: User, version_id: str, package_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[tuple[str, bytes]]]]:
        row = self.target(user, package_id)
        lock, files = self.releases.installation_content(version_id, kind="skill")
        root = self.releases._version(version_id, "skill")
        source = {**self.releases.card(root, kind="skill"), "lock": lock}
        receipt = self.receipts(package_id)
        skills = self.store.package_skills_dir(package_id)
        resources = receipt["resources"]
        installed = resources.get(root["resource_id"])
        if installed and not (skills / installed["slug"] / "SKILL.md").is_file():
            installed = None
        requested = {entry["resource_id"]: entry for entry in lock}
        # Another installed root owns its exact dependency versions, even if a
        # dependency directory was manually deleted and needs repair.
        for owner, dependencies in receipt["roots"].items():
            own = resources.get(owner)
            if (
                owner == root["resource_id"]
                or not own
                or not (skills / own["slug"] / "SKILL.md").is_file()
            ):
                continue
            for dependency in dependencies:
                new = requested.get(dependency["resource_id"])
                if new and new["version_id"] != dependency["version_id"]:
                    raise conflict(
                        resources.get(dependency["resource_id"], {}).get(
                            "slug", dependency["resource_id"]
                        )
                    )
        grouped: dict[str, list[tuple[str, bytes]]] = {}
        for path, data in files.items():
            _, slug, relative = path.split("/", 2)
            grouped.setdefault(slug, []).append((relative, data))
        changes = []
        modified = []
        for entry in lock:
            release = self.releases.repo.version(entry["version_id"])
            assert release is not None
            slug = json.loads(release["manifest_json"])["slug"]
            previous = resources.get(entry["resource_id"])
            # Case-insensitive collisions matter on Windows/macOS too.
            collisions = (
                [p for p in skills.iterdir() if p.name.casefold() == slug.casefold()]
                if skills.is_dir()
                else []
            )
            if collisions and (
                not previous or previous["slug"] != slug or any(p.name != slug for p in collisions)
            ):
                raise conflict(slug)
            if previous and previous["slug"] != slug:
                raise conflict(slug)
            present = (skills / slug / "SKILL.md").is_file()
            is_root = entry["resource_id"] == root["resource_id"]
            # Compatible dependencies are reused, including their local edits.
            replace = (
                is_root
                or not present
                or not previous
                or previous["version_id"] != entry["version_id"]
            )
            if not replace:
                grouped.pop(slug)
            if replace and collisions and previous and hashes(skills / slug) != previous["files"]:
                modified.append(slug)
            changes.append(
                {
                    "slug": slug,
                    "version": entry["version"],
                    "operation": "replace"
                    if replace and collisions
                    else "install"
                    if replace
                    else "reuse",
                }
            )
        state = {
            "package_id": package_id,
            "files": hashes(self.store.root / package_id),
            "version_id": version_id,
            "lock": lock,
        }
        token = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        plan = {
            "package_id": package_id,
            "package_name": row.name,
            "action": action(source["version"], installed["version"] if installed else None),
            "installed_version": installed["version"] if installed else None,
            "changes": changes,
            "modified_slugs": modified,
            "token": token,
            "source": source,
        }
        return plan, receipt, grouped

    def preview(self, user: User, version_id: str, package_id: str) -> dict[str, Any]:
        self.target(user, package_id)
        with package_lock(self.store.root, package_id):
            return self._plan(user, version_id, package_id)[0]

    def install(self, user: User, version_id: str, package_id: str, token: str) -> dict[str, Any]:
        self.target(user, package_id)
        with package_lock(self.store.root, package_id):
            plan, receipt, grouped = self._plan(user, version_id, package_id)
            if plan["token"] != token:
                raise MossworkError.localized(ErrorCode.MARKET_INSTALL_CHANGED)
            source = plan["source"]
            root = self.store.root / package_id
            with tempfile.TemporaryDirectory(
                prefix=".market-install-", dir=self.store.root
            ) as temporary:
                staging = Path(temporary) / "new"
                backup = Path(temporary) / "old"
                shutil.copytree(root, staging)
                for slug, content in grouped.items():
                    directory = staging / "skills" / slug
                    shutil.rmtree(directory, ignore_errors=True)
                    directory.mkdir(parents=True)
                    for relative, payload in content:
                        path = directory / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(payload)
                for entry in source["lock"]:
                    release = self.releases.repo.version(entry["version_id"])
                    assert release is not None
                    slug = json.loads(release["manifest_json"])["slug"]
                    if slug in grouped:
                        receipt["resources"][entry["resource_id"]] = {
                            **entry,
                            "slug": slug,
                            "files": hashes(staging / "skills" / slug),
                            "installed_at": datetime.now(UTC).isoformat(),
                        }
                receipt["roots"][source["resource_id"]] = source["lock"]
                write_json_atomic(staging / RECEIPT, receipt)
                root.rename(backup)
                try:
                    staging.rename(root)
                    self.store._update_skill_count(package_id)
                except BaseException:
                    if root.exists():
                        shutil.rmtree(root)
                    backup.rename(root)
                    raise
            row = self.store.repo.get(package_id)
            assert row is not None
            package = asdict(row)
            package.pop("pk", None)
            return {"package": package, "source": source}
