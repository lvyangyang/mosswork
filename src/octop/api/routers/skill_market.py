"""Read and install published local skills using consumption permissions."""

from __future__ import annotations

import logging
import mimetypes
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator

from octop.api.deps import get_server, require_permission
from octop.infra.market.skill_installation import LocalSkillMarket
from octop.infra.market.skill_package_installation import PackageSkillInstaller
from octop.infra.server import MossworkServer
from octop.infra.users.identity import User
from octop.infra.utils.asyncio import offload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skill-packages/market")


class SkillDependency(BaseModel):
    resource_id: str
    version: str


class SkillRelease(BaseModel):
    resource_id: str
    version_id: str
    version: str
    object_sha256: str
    name: str
    description: str
    dependencies: list[SkillDependency]
    icon_url: str | None = None


class SkillLock(SkillDependency):
    version_id: str
    object_sha256: str


class SkillCatalog(BaseModel):
    items: list[SkillRelease]
    next_offset: int | None = None


class SkillFilePreview(BaseModel):
    name: str
    content: str


class SkillReleaseDetail(SkillRelease):
    slug: str
    changelog: str
    versions: list[SkillRelease]
    lock: list[SkillLock]
    file_contents: list[SkillFilePreview]
    runtime: dict[str, Any]
    configuration: dict[str, Any]


class InstallSkillRelease(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Name for an explicitly new package; omit when targeting an existing package.",
    )
    package_id: str | None = Field(
        default=None, min_length=1, description="Writable existing package receiving this release."
    )
    token: str | None = Field(
        default=None,
        description="Preview token confirming the exact release and current package contents.",
    )

    @model_validator(mode="after")
    def target_or_name(self) -> InstallSkillRelease:
        if self.package_id:
            if self.name is not None or not self.token:
                raise ValueError("Existing packages require a preview token and no name")
        elif not self.name or self.token:
            raise ValueError("New packages require a name and no preview token")
        return self


class SkillTarget(BaseModel):
    package_id: str = Field(min_length=1)


class InstalledSkill(BaseModel):
    resource_id: str
    version_id: str
    version: str
    slug: str


class PackageInstallationStatus(BaseModel):
    package_id: str
    name: str
    installed: list[InstalledSkill]


class SkillChange(BaseModel):
    slug: str
    version: str
    operation: str


class SkillInstallPreview(BaseModel):
    package_id: str
    package_name: str
    action: str
    installed_version: str | None
    changes: list[SkillChange]
    modified_slugs: list[str]
    token: str
    source: SkillInstallationSource


class PackageRefreshResult(BaseModel):
    refresh_failed: bool = False


class InstalledSkillPackage(BaseModel):
    id: str
    name: str
    skill_count: int


class SkillInstallationSource(SkillRelease):
    lock: list[SkillLock]


class SkillInstallationResult(BaseModel):
    package: InstalledSkillPackage
    source: SkillInstallationSource
    refresh_failed: bool = False


def service(server: MossworkServer = Depends(get_server)) -> LocalSkillMarket:
    assert server.services is not None
    return LocalSkillMarket(server.services)


@router.get("", response_model=SkillCatalog, summary="Browse published local skills")
async def catalog(
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=100),
    _user: User = Depends(require_permission("skill_packages")),
    market: LocalSkillMarket = Depends(service),
) -> Any:
    return await offload(market.catalog, offset, limit)


@router.get(
    "/packages/{package_id}/status",
    response_model=PackageInstallationStatus,
    summary="Read per-resource installation versions in a writable package",
)
async def package_status(
    package_id: str,
    user: User = Depends(require_permission("skill_packages")),
    market: LocalSkillMarket = Depends(service),
) -> Any:
    return await offload(
        PackageSkillInstaller(market.store, market.releases).statuses, user, package_id
    )


@router.post(
    "/{version_id}/preview",
    response_model=SkillInstallPreview,
    summary="Preview installation, replacement and exact dependency reuse",
    description="Validates package ownership and conflicts without writing files. The token expires when package contents change.",
)
async def preview(
    version_id: str,
    body: SkillTarget,
    user: User = Depends(require_permission("skill_packages")),
    market: LocalSkillMarket = Depends(service),
) -> Any:
    return await offload(
        PackageSkillInstaller(market.store, market.releases).preview,
        user,
        version_id,
        body.package_id,
    )


async def refresh_package(server: MossworkServer, package_id: str) -> bool:
    try:
        assert server.app_runtime is not None
        await server.app_runtime.agent_registry.refresh_agents_for_package(package_id)
        return False
    except Exception:
        logger.exception("Installed package %s, but Agent refresh needs retry", package_id)
        return True


@router.post(
    "/packages/{package_id}/refresh",
    response_model=PackageRefreshResult,
    summary="Retry Agent refresh after a successful package installation",
)
async def retry_refresh(
    package_id: str,
    user: User = Depends(require_permission("skill_packages")),
    server: MossworkServer = Depends(get_server),
    market: LocalSkillMarket = Depends(service),
) -> Any:
    await offload(PackageSkillInstaller(market.store, market.releases).target, user, package_id)
    return {"refresh_failed": await refresh_package(server, package_id)}


@router.get(
    "/{version_id}",
    response_model=SkillReleaseDetail,
    summary="Inspect a skill release and its exact dependencies",
)
async def detail(
    version_id: str,
    _user: User = Depends(require_permission("skill_packages")),
    market: LocalSkillMarket = Depends(service),
) -> Any:
    return await offload(market.detail, version_id)


@router.get(
    "/{version_id}/icon",
    response_class=Response,
    summary="Serve an offline icon from a published local skill release",
)
async def icon(
    version_id: str,
    _user: User = Depends(require_permission("skill_packages")),
    server: MossworkServer = Depends(get_server),
) -> Response:
    assert server.services is not None
    path, payload = await offload(
        LocalSkillMarket(server.services).releases.icon, version_id, kind="skill"
    )
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(
        payload,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post(
    "/{version_id}/install",
    response_model=SkillInstallationResult,
    status_code=201,
    summary="Install a local skill release into an existing or new skill package",
    description="Requires skill_packages permission, not market publishing access. Copies the exact "
    "release and all pinned dependencies without network access. Existing packages require ownership, an installation preview token and explicit replacement confirmation.",
)
async def install(
    version_id: str,
    body: InstallSkillRelease,
    user: User = Depends(require_permission("skill_packages")),
    market: LocalSkillMarket = Depends(service),
    server: MossworkServer = Depends(get_server),
) -> Any:
    if not body.package_id:
        assert body.name is not None
        return await offload(market.install, user, version_id, body.name)
    assert body.token is not None
    result = await offload(
        PackageSkillInstaller(market.store, market.releases).install,
        user,
        version_id,
        body.package_id,
        body.token,
    )
    result["refresh_failed"] = await refresh_package(server, body.package_id)
    return result
