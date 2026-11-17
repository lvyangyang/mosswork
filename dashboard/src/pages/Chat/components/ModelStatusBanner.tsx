import { Alert } from "antd";
import { useTranslation } from "react-i18next";
import type { ModelStatus } from "../hooks/sseHelpers";

export default function ModelStatusBanner({
  modelStatus,
  streamStalled,
}: {
  modelStatus: ModelStatus | null;
  streamStalled: boolean;
}) {
  const { t } = useTranslation();
  if (modelStatus) {
    return (
      <Alert
        type="info"
        showIcon
        message={t("chat.modelRetrying", {
          attempt: modelStatus.attempt,
          maxAttempts: modelStatus.maxAttempts,
        })}
      />
    );
  }
  if (!streamStalled) return null;
  return <Alert type="warning" showIcon message={t("chat.localStalled")} />;
}
