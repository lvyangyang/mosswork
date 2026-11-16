import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Drawer,
  Empty,
  Input,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Package, RefreshCw } from "lucide-react";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { userCan } from "../utils/permissions";
import { apiErrorMessage } from "../utils/apiError";
import {
  localSkillMarketApi,
  type LocalSkillDetail,
  type LocalSkillRelease,
  type PackageMarketStatus,
  type LocalSkillInstallation,
} from "../api/modules/localSkillMarket";
import styles from "./ResourceMarketEntry.module.less";
import MarketIcon from "./MarketIcon";
import SkillMarketInstallModal, {
  SkillMarketInstallFeedback,
  skillInstallAction,
} from "./SkillMarketInstallModal";

type Props = {
  targetPackage?: { id: string; name: string } | null;
  initialVersionId?: string | null;
  onBusyChange?: (busy: boolean) => void;
  onInstallOpenChange?: (open: boolean) => void;
  onInstalled?: (packageId: string) => void;
};

/** Local catalog consumption is independent of market publication permissions. */
export default function ResourceMarketEntry(props: Props) {
  const { t } = useTranslation();
  const user = useCurrentUser();
  if (!userCan(user, "skill_packages")) {
    return <Empty description={t("resourceMarket.contactAdmin")} />;
  }
  return <LocalSkillCatalog {...props} />;
}

function LocalSkillCatalog({
  initialVersionId,
  targetPackage,
  onBusyChange,
  onInstallOpenChange,
  onInstalled,
}: Props) {
  const { t } = useTranslation();
  const user = useCurrentUser();
  const [items, setItems] = useState<LocalSkillRelease[]>([]);
  const itemsRef = useRef<LocalSkillRelease[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [versionId, setVersionId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LocalSkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [installVersionId, setInstallVersionId] = useState<string | null>(
    initialVersionId ?? null,
  );
  const [installation, setInstallation] =
    useState<LocalSkillInstallation | null>(null);
  const [status, setStatus] = useState<PackageMarketStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [statusRevision, setStatusRevision] = useState(0);
  const loadSequence = useRef(0);
  useEffect(() => {
    onInstallOpenChange?.(Boolean(installVersionId));
    return () => onInstallOpenChange?.(false);
  }, [installVersionId, onInstallOpenChange]);
  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    setStatusError("");
    if (!targetPackage) return;
    localSkillMarketApi.status(targetPackage.id).then(
      (result) => {
        if (!cancelled) setStatus(result);
      },
      (err) => {
        if (!cancelled)
          setStatusError(
            apiErrorMessage(err, t("localSkillMarket.loadFailed"), t),
          );
      },
    );
    return () => {
      cancelled = true;
    };
  }, [targetPackage, statusRevision, t]);
  const currentStatus =
    status?.package_id === targetPackage?.id ? status : null;
  const actionLabel = (item: LocalSkillRelease) =>
    t(`localSkillMarket.${skillInstallAction(item, currentStatus)}`);
  const load = useCallback(
    async (append = false) => {
      const sequence = ++loadSequence.current;
      setLoading(true);
      setError("");
      try {
        const offset = append ? itemsRef.current.length : 0;
        const result = await localSkillMarketApi.list(offset);
        if (loadSequence.current === sequence) {
          const nextItems = append
            ? [...itemsRef.current, ...result.items]
            : result.items;
          itemsRef.current = nextItems;
          setItems(nextItems);
          setNextOffset(result.next_offset ?? null);
        }
      } catch (err) {
        if (loadSequence.current === sequence)
          setError(apiErrorMessage(err, t("localSkillMarket.loadFailed"), t));
      } finally {
        if (loadSequence.current === sequence) setLoading(false);
      }
    },
    [t],
  );
  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
    };
  }, [load]);
  useEffect(() => {
    if (initialVersionId) setInstallVersionId(initialVersionId);
  }, [initialVersionId]);
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setDetailError("");
    if (!versionId) return;
    setDetailLoading(true);
    localSkillMarketApi
      .detail(versionId)
      .then((result) => {
        if (cancelled) return;
        setDetail(result);
      })
      .catch((err) => {
        if (!cancelled)
          setDetailError(
            apiErrorMessage(err, t("localSkillMarket.loadFailed"), t),
          );
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, t]);

  const search = query.trim().toLocaleLowerCase();
  const visible = items.filter((item) =>
    `${item.name} ${item.description}`.toLocaleLowerCase().includes(search),
  );
  return (
    <div className={styles.container}>
      <Alert type="info" showIcon message={t("localSkillMarket.hint")} />
      {targetPackage ? (
        <Typography.Text strong>
          {t("localSkillMarket.targetFixed", { name: targetPackage.name })}
        </Typography.Text>
      ) : null}
      {!versionId && installation ? (
        <SkillMarketInstallFeedback
          key={installation.package.id}
          result={installation}
        />
      ) : null}
      {statusError ? (
        <Alert
          type="error"
          message={statusError}
          action={
            <Button onClick={() => setStatusRevision((v) => v + 1)}>
              {t("common.refresh")}
            </Button>
          }
        />
      ) : null}
      <div className={styles.toolbar}>
        <Input.Search
          aria-label={t("localSkillMarket.search")}
          placeholder={t("localSkillMarket.search")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          allowClear
        />
        <Button
          icon={<RefreshCw size={14} />}
          onClick={() => {
            setStatusRevision((v) => v + 1);
            void load();
          }}
          loading={loading}
        >
          {t("common.refresh")}
        </Button>
        {userCan(user, "resource_market") ? (
          <Link to="/resource-market">{t("localSkillMarket.manage")}</Link>
        ) : null}
      </div>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {loading ? (
        <Spin />
      ) : !error && visible.length === 0 ? (
        <Empty description={t("localSkillMarket.empty")} />
      ) : (
        <div className={styles.grid}>
          {visible.map((item) => (
            <Card
              key={item.resource_id}
              size="small"
              title={
                <Space>
                  <MarketIcon
                    src={item.icon_url}
                    size={28}
                    fallback={<Package size={18} />}
                  />
                  <Typography.Link
                    onClick={() => setVersionId(item.version_id)}
                  >
                    {item.name}
                  </Typography.Link>
                </Space>
              }
              extra={<Tag>v{item.version}</Tag>}
            >
              <Typography.Paragraph type="secondary" ellipsis={{ rows: 3 }}>
                {item.description}
              </Typography.Paragraph>
              <Space wrap>
                <Button onClick={() => setVersionId(item.version_id)}>
                  {t("localSkillMarket.details")}
                </Button>
                <Button
                  type="primary"
                  onClick={() => setInstallVersionId(item.version_id)}
                >
                  {actionLabel(item)}
                </Button>
                {currentStatus?.installed.find(
                  (entry) => entry.resource_id === item.resource_id,
                ) ? (
                  <Tag>
                    {t("localSkillMarket.installedVersion", {
                      version: currentStatus.installed.find(
                        (entry) => entry.resource_id === item.resource_id,
                      )?.version,
                    })}
                  </Tag>
                ) : null}
              </Space>
            </Card>
          ))}
        </div>
      )}
      {!loading && !error && nextOffset !== null ? (
        <Button loading={loading} onClick={() => void load(true)}>
          {t("resourceMarket.loadMore")}
        </Button>
      ) : null}
      <Drawer
        title={detail?.name ?? t("localSkillMarket.details")}
        open={Boolean(versionId)}
        width={760}
        mask={!installVersionId}
        onClose={() => setVersionId(null)}
        footer={
          <Button
            type="primary"
            disabled={!detail || detailLoading}
            onClick={() => {
              if (detail) setInstallVersionId(detail.version_id);
            }}
          >
            {detail ? actionLabel(detail) : t("localSkillMarket.install")}
          </Button>
        }
      >
        {detailLoading ? <Spin /> : null}
        {detailError ? (
          <Alert type="error" showIcon message={detailError} />
        ) : null}
        {detail ? (
          <Space direction="vertical" size="middle" className={styles.details}>
            {installation ? (
              <SkillMarketInstallFeedback result={installation} />
            ) : null}
            <Typography.Paragraph>{detail.description}</Typography.Paragraph>
            <label className={styles.field}>
              <span>{t("resourceMarket.version")}</span>
              <Select
                aria-label={t("resourceMarket.version")}
                value={detail.version_id}
                onChange={setVersionId}
                options={detail.versions.map((version) => ({
                  value: version.version_id,
                  label: `v${version.version}`,
                }))}
              />
            </label>
            <Typography.Text>{detail.changelog}</Typography.Text>
            <Typography.Text type="secondary" copyable>
              {detail.object_sha256}
            </Typography.Text>
            <Typography.Text strong>
              {t("localSkillMarket.dependencies")}
            </Typography.Text>
            {detail.lock.map((entry) => (
              <Typography.Text key={entry.resource_id}>
                {entry.resource_id} · v{entry.version}
              </Typography.Text>
            ))}
            <Collapse
              items={detail.file_contents.map((file) => ({
                key: file.name,
                label: file.name,
                children: <pre className={styles.preview}>{file.content}</pre>,
              }))}
            />
          </Space>
        ) : null}
      </Drawer>
      {installVersionId ? (
        <SkillMarketInstallModal
          key={installVersionId}
          versionId={installVersionId}
          targetPackage={targetPackage}
          onClose={() => setInstallVersionId(null)}
          onBusyChange={onBusyChange}
          onInstalled={(result) => {
            setInstallation(result);
            setStatusRevision((value) => value + 1);
            onInstalled?.(result.package.id);
          }}
        />
      ) : null}
    </div>
  );
}
