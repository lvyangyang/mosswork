import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Typography,
} from "antd";
import { Package } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  localSkillMarketApi,
  type LocalSkillDetail,
  type LocalSkillInstallation,
  type LocalSkillRelease,
  type PackageMarketStatus,
  type SkillInstallPreview,
} from "../api/modules/localSkillMarket";
import { skillPackagesApi } from "../api/modules/skillPackages";
import type { SkillPackage } from "../api/types/skillPackage";
import { apiErrorMessage } from "../utils/apiError";
import MarketIcon from "./MarketIcon";
import styles from "./ResourceMarketEntry.module.less";

export type MarketTargetPackage = { id: string; name: string };

export function skillInstallAction(
  item: LocalSkillRelease,
  status: PackageMarketStatus | null,
): "install" | "reinstall" | "update" | "rollback" {
  const installed = status?.installed.find(
    (entry) => entry.resource_id === item.resource_id,
  );
  if (!installed) return "install";
  const requested = item.version.split(".").map(Number);
  const current = installed.version.split(".").map(Number);
  const difference = requested.findIndex(
    (part, index) => part !== current[index],
  );
  return difference < 0
    ? "reinstall"
    : requested[difference] > current[difference]
    ? "update"
    : "rollback";
}

type Props = {
  versionId: string;
  targetPackage?: MarketTargetPackage | null;
  onClose: () => void;
  onInstalled: (result: LocalSkillInstallation) => void;
  onBusyChange?: (busy: boolean) => void;
};

/** Mount for one release; selection, preview and confirmation share one dialog. */
export default function SkillMarketInstallModal({
  versionId,
  targetPackage,
  onClose,
  onInstalled,
  onBusyChange,
}: Props) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<LocalSkillDetail | null>(null);
  const [packages, setPackages] = useState<SkillPackage[]>([]);
  const [target, setTarget] = useState<string | undefined>(targetPackage?.id);
  const [newPackage, setNewPackage] = useState(false);
  const [packageName, setPackageName] = useState("");
  const [detailLoading, setDetailLoading] = useState(true);
  const [packagesLoading, setPackagesLoading] = useState(!targetPackage);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [packagesError, setPackagesError] = useState("");
  const [installError, setInstallError] = useState("");
  const [preview, setPreview] = useState<SkillInstallPreview | null>(null);
  const [installing, setInstalling] = useState(false);
  const [detailRetry, setDetailRetry] = useState(0);
  const [packagesRetry, setPackagesRetry] = useState(0);
  const [previewRetry, setPreviewRetry] = useState(0);

  useEffect(() => {
    onBusyChange?.(installing);
    return () => onBusyChange?.(false);
  }, [installing, onBusyChange]);

  useEffect(() => {
    let cancelled = false;
    setDetailLoading(true);
    setDetailError("");
    localSkillMarketApi.detail(versionId).then(
      (result) => {
        if (!cancelled) {
          setDetail(result);
          setPackageName(
            (current) => current || `${result.name} v${result.version}`,
          );
          setDetailLoading(false);
        }
      },
      (err) => {
        if (!cancelled) {
          setDetailError(
            apiErrorMessage(err, t("localSkillMarket.loadFailed"), t),
          );
          setDetailLoading(false);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [versionId, detailRetry, t]);

  useEffect(() => {
    if (targetPackage) return;
    let cancelled = false;
    setPackagesLoading(true);
    setPackagesError("");
    skillPackagesApi.listWritable().then(
      (result) => {
        if (!cancelled) {
          setPackages(result);
          setPackagesLoading(false);
        }
      },
      (err) => {
        if (!cancelled) {
          setPackagesError(
            apiErrorMessage(err, t("localSkillMarket.packagesFailed"), t),
          );
          setPackagesLoading(false);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [targetPackage, packagesRetry, t]);

  useEffect(() => {
    let cancelled = false;
    setPreview(null);
    setInstallError("");
    if (!target || newPackage) {
      setPreviewLoading(false);
      return;
    }
    setPreviewLoading(true);
    localSkillMarketApi.preview(versionId, target).then(
      (result) => {
        if (!cancelled) {
          setPreview(result);
          setPreviewLoading(false);
        }
      },
      (err) => {
        if (!cancelled) {
          setInstallError(
            apiErrorMessage(err, t("localSkillMarket.installFailed"), t),
          );
          setPreviewLoading(false);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [target, newPackage, versionId, previewRetry, t]);

  const readyPreview =
    preview?.package_id === target && preview?.source.version_id === versionId
      ? preview
      : null;
  const ready = Boolean(
    detail &&
      !detailLoading &&
      (newPackage ? packageName.trim() : readyPreview && !previewLoading),
  );
  const confirm = async () => {
    if (!ready || installing) return;
    setInstalling(true);
    setInstallError("");
    try {
      const result = newPackage
        ? await localSkillMarketApi.install(versionId, packageName.trim())
        : await localSkillMarketApi.installExisting(
            versionId,
            readyPreview!.package_id,
            readyPreview!.token,
          );
      onInstalled(result);
      onClose();
    } catch (err) {
      setPreview(null);
      setInstallError(
        apiErrorMessage(err, t("localSkillMarket.installFailed"), t),
      );
    } finally {
      setInstalling(false);
    }
  };
  const retryButton = (reload: () => void) => (
    <Button disabled={installing} onClick={reload}>
      {t("common.retry")}
    </Button>
  );

  return (
    <Modal
      open
      title={t("localSkillMarket.installTitle", { name: detail?.name ?? "…" })}
      width={620}
      onCancel={() => {
        if (!installing) onClose();
      }}
      onOk={() => void confirm()}
      confirmLoading={installing}
      okText={t(
        newPackage || !readyPreview
          ? "localSkillMarket.confirmInstall"
          : `localSkillMarket.confirmAction.${readyPreview.action}`,
      )}
      okButtonProps={{ disabled: !ready }}
      cancelText={t("common.cancel")}
      cancelButtonProps={{ disabled: installing }}
      closable={!installing}
      maskClosable={!installing}
      keyboard={!installing}
    >
      <div className={styles.details}>
        {detailLoading ? <Spin /> : null}
        {detailError ? (
          <Alert
            type="error"
            message={detailError}
            action={retryButton(() => setDetailRetry((v) => v + 1))}
          />
        ) : null}
        {detail ? (
          <Space>
            <MarketIcon
              src={detail.icon_url}
              size={36}
              fallback={<Package size={22} />}
            />
            <div>
              <Typography.Text strong>
                {detail.name} · v{detail.version}
              </Typography.Text>
              <div>{detail.description}</div>
            </div>
          </Space>
        ) : null}
        {targetPackage ? (
          <Typography.Text strong>
            {t("localSkillMarket.targetFixed", { name: targetPackage.name })}
          </Typography.Text>
        ) : (
          <>
            {!newPackage ? (
              <label className={styles.field}>
                <span>{t("localSkillMarket.target")}</span>
                <Select
                  aria-label={t("localSkillMarket.target")}
                  placeholder={t("localSkillMarket.chooseTarget")}
                  showSearch
                  optionFilterProp="label"
                  value={target}
                  disabled={installing}
                  loading={packagesLoading}
                  onChange={(value) => {
                    setTarget(value);
                    setPreview(null);
                    setInstallError("");
                  }}
                  options={packages.map((pack) => ({
                    value: pack.id,
                    label: t("localSkillMarket.packageOption", {
                      name: pack.name,
                      count: pack.skill_count,
                    }),
                  }))}
                />
              </label>
            ) : (
              <label className={styles.field}>
                <span>{t("localSkillMarket.packageName")}</span>
                <Input
                  aria-label={t("localSkillMarket.packageName")}
                  value={packageName}
                  maxLength={200}
                  disabled={installing}
                  onChange={(event) => setPackageName(event.target.value)}
                />
              </label>
            )}
            {packagesError ? (
              <Alert
                type="error"
                message={packagesError}
                action={retryButton(() => setPackagesRetry((v) => v + 1))}
              />
            ) : null}
            {!packagesLoading &&
            !packagesError &&
            !packages.length &&
            !newPackage ? (
              <Alert
                type="info"
                message={t("localSkillMarket.noWritablePackages")}
              />
            ) : null}
            <Button
              type="link"
              className={styles.secondaryAction}
              disabled={installing}
              onClick={() => {
                setNewPackage(!newPackage);
                setPreview(null);
                setInstallError("");
              }}
            >
              {t(
                newPackage
                  ? "localSkillMarket.useExisting"
                  : "localSkillMarket.createPackage",
              )}
            </Button>
          </>
        )}
        {newPackage ? (
          <Alert type="info" message={t("localSkillMarket.snapshotHint")} />
        ) : null}
        {previewLoading ? (
          <Spin tip={t("localSkillMarket.checking")}>
            <div style={{ minHeight: 32 }} />
          </Spin>
        ) : null}
        {installError ? (
          <Alert
            type="error"
            showIcon
            message={installError}
            action={
              newPackage
                ? undefined
                : retryButton(() => setPreviewRetry((v) => v + 1))
            }
          />
        ) : null}
        {readyPreview ? (
          <>
            <Typography.Text>
              {t("localSkillMarket.confirmTarget", {
                name: readyPreview.package_name,
                skill: readyPreview.source.name,
                version: readyPreview.source.version,
              })}
            </Typography.Text>
            <Typography.Text type="secondary">
              {readyPreview.installed_version
                ? t("localSkillMarket.installedVersion", {
                    version: readyPreview.installed_version,
                  })
                : t("localSkillMarket.notInstalled")}
            </Typography.Text>
            {readyPreview.action === "rollback" ? (
              <Alert
                type="warning"
                showIcon
                message={t("localSkillMarket.rollbackWarning")}
              />
            ) : null}
            {readyPreview.changes.some(
              (change) => change.operation === "replace",
            ) ? (
              <Alert
                type="warning"
                showIcon
                message={t("localSkillMarket.replaceWarning")}
              />
            ) : null}
            {readyPreview.modified_slugs.length ? (
              <Alert
                type="warning"
                showIcon
                message={t("localSkillMarket.modifiedWarning", {
                  slugs: readyPreview.modified_slugs.join(", "),
                })}
              />
            ) : null}
            {readyPreview.changes.map((change) => (
              <Typography.Text key={change.slug}>
                {change.slug} · v{change.version} ·{" "}
                {t(`localSkillMarket.operation.${change.operation}`)}
              </Typography.Text>
            ))}
            <Typography.Text type="secondary">
              {t("localSkillMarket.existingHint")}
            </Typography.Text>
          </>
        ) : null}
      </div>
    </Modal>
  );
}

export function SkillMarketInstallFeedback({
  result,
}: {
  result: LocalSkillInstallation;
}) {
  const { t } = useTranslation();
  const [refreshFailed, setRefreshFailed] = useState(
    Boolean(result.refresh_failed),
  );
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setRefreshFailed(Boolean(result.refresh_failed));
    setError("");
  }, [result]);
  return (
    <div className={styles.details}>
      <Alert
        type="success"
        showIcon
        message={t("localSkillMarket.installedSkill", {
          skill: result.source.name,
          version: result.source.version,
          name: result.package.name,
        })}
        action={
          <Link
            to={`/skill-packages?packageId=${encodeURIComponent(
              result.package.id,
            )}`}
          >
            {t("localSkillMarket.viewPackage")}
          </Link>
        }
      />
      {refreshFailed ? (
        <Alert
          type="warning"
          showIcon
          message={t("localSkillMarket.refreshFailed")}
          action={
            <Button
              loading={refreshing}
              onClick={async () => {
                setRefreshing(true);
                setError("");
                try {
                  const refreshed = await localSkillMarketApi.refresh(
                    result.package.id,
                  );
                  setRefreshFailed(refreshed.refresh_failed);
                } catch (err) {
                  setError(
                    apiErrorMessage(err, t("localSkillMarket.loadFailed"), t),
                  );
                } finally {
                  setRefreshing(false);
                }
              }}
            >
              {t("localSkillMarket.retryRefresh")}
            </Button>
          }
        />
      ) : null}
      {error ? <Alert type="error" message={error} /> : null}
    </div>
  );
}
