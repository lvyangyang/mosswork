import { PRODUCT_NAME } from "../../utils/branding";
import AppVersionBadge from "../AppVersionBadge";
import EnterpriseBadge from "../EnterpriseBadge";
import styles from "./index.module.less";

interface BrandLockupProps {
  compact?: boolean;
  showEnterprise?: boolean;
  showVersion?: boolean;
  className?: string;
}

/** Split brand mark and wordmark so their proportions can be tuned independently. */
export default function BrandLockup({
  compact = false,
  showEnterprise = false,
  showVersion = false,
  className,
}: BrandLockupProps) {
  return (
    <div
      className={`${styles.lockup} ${compact ? styles.compact : ""} ${
        className ?? ""
      }`}
    >
      <img
        className={styles.mark}
        // Preserve the original orange mark in both light and dark modes.
        src={compact ? "/pwa-192.png" : "/logo_vertical_dark.svg"}
        alt=""
        aria-hidden="true"
      />
      {!compact && <span className={styles.wordmark}>{PRODUCT_NAME}</span>}
      {showEnterprise && <EnterpriseBadge />}
      {showVersion && <AppVersionBadge />}
    </div>
  );
}
