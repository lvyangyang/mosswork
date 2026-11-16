import { request, requestBlob } from "../request";

export type LocalSkillRelease = {
  resource_id: string;
  version_id: string;
  version: string;
  object_sha256: string;
  name: string;
  description: string;
  dependencies: { resource_id: string; version: string }[];
  icon_url?: string | null;
};
export type LocalSkillSource = LocalSkillRelease & {
  lock: {
    resource_id: string;
    version: string;
    version_id: string;
    object_sha256: string;
  }[];
};
export type LocalSkillDetail = LocalSkillSource & {
  slug: string;
  changelog: string;
  versions: LocalSkillRelease[];
  file_contents: { name: string; content: string }[];
  runtime: Record<string, unknown>;
  configuration: Record<string, unknown>;
};
export type LocalSkillCatalog = {
  items: LocalSkillRelease[];
  next_offset?: number | null;
};
export type LocalSkillInstallation = {
  package: { id: string; name: string; skill_count: number };
  source: LocalSkillSource;
  refresh_failed?: boolean;
};
export type PackageMarketStatus = {
  package_id: string;
  name: string;
  installed: {
    resource_id: string;
    version_id: string;
    version: string;
    slug: string;
  }[];
};
export type SkillInstallPreview = {
  package_id: string;
  package_name: string;
  action: "install" | "reinstall" | "update" | "rollback";
  installed_version: string | null;
  changes: {
    slug: string;
    version: string;
    operation: "install" | "replace" | "reuse";
  }[];
  modified_slugs: string[];
  token: string;
  source: LocalSkillSource;
};
export const localSkillMarketApi = {
  list: (offset = 0, limit = 60) =>
    request<LocalSkillCatalog>(
      `/skill-packages/market?offset=${offset}&limit=${limit}`,
    ),
  status: (packageId: string) =>
    request<PackageMarketStatus>(
      `/skill-packages/market/packages/${encodeURIComponent(packageId)}/status`,
    ),
  preview: (versionId: string, packageId: string) =>
    request<SkillInstallPreview>(
      `/skill-packages/market/${encodeURIComponent(versionId)}/preview`,
      { method: "POST", body: JSON.stringify({ package_id: packageId }) },
    ),
  installExisting: (versionId: string, packageId: string, token: string) =>
    request<LocalSkillInstallation>(
      `/skill-packages/market/${encodeURIComponent(versionId)}/install`,
      {
        method: "POST",
        body: JSON.stringify({ package_id: packageId, token }),
      },
    ),
  refresh: (packageId: string) =>
    request<{ refresh_failed: boolean }>(
      `/skill-packages/market/packages/${encodeURIComponent(
        packageId,
      )}/refresh`,
      { method: "POST" },
    ),
  detail: (versionId: string) =>
    request<LocalSkillDetail>(
      `/skill-packages/market/${encodeURIComponent(versionId)}`,
    ),
  icon: (versionId: string) =>
    requestBlob(`/skill-packages/market/${encodeURIComponent(versionId)}/icon`),
  install: (versionId: string, name: string) =>
    request<LocalSkillInstallation>(
      `/skill-packages/market/${encodeURIComponent(versionId)}/install`,
      { method: "POST", body: JSON.stringify({ name }) },
    ),
};
