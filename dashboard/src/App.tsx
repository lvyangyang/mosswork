import { createGlobalStyle } from "antd-style";
import { ConfigProvider, theme as antdTheme } from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import { lazy, Suspense, useEffect } from "react";
import DesktopWindowControls from "./components/DesktopWindowControls";
import {
  DesktopChromeProvider,
  useDesktopChrome,
} from "./hooks/useDesktopChrome";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CommunicationProvider } from "./pages/Messaging/hooks/CommunicationContext";
import { ConversationNavigationProvider } from "./layouts/ConversationNavigation";
import MainLayout from "./layouts/MainLayout";
import LoginPage, { CanonicalMossLoginPage } from "./pages/Login";
import OidcComplete from "./pages/Login/OidcComplete";
import SetupPage from "./pages/Setup";
import InvitePage from "./pages/Invite";
import AuthGuard from "./components/AuthGuard";
import MossworkSpinner from "./components/MossworkSpinner";
import { AntdAppProvider } from "./components/AntdAppProvider";
import GlobalErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider, useTheme } from "./context/ThemeContext";
import { AgentProvider } from "./context/AgentContext";
import { DesktopWorkspaceProvider } from "./context/DesktopWorkspaceContext";
import { LayoutModeProvider } from "./context/LayoutModeContext";
import { VoiceOutputProvider } from "./context/VoiceOutputContext";
import { useIsMobile } from "./hooks/useIsMobile";
import { useUnauthorizedRedirect } from "./hooks/useUnauthorizedRedirect";
import { installDesktopExternalLinks } from "./utils/desktopExternalLinks";
import { brandTokensFor } from "./styles/themePalettes";
import "./styles/theme-vars.css";
import "./styles/layout.css";
import "./styles/form-override.css";
import "./styles/spin-override.css";

const GlobalStyle = createGlobalStyle`
* {
  margin: 0;
  box-sizing: border-box;
}
`;

const HumanReviewPage = lazy(() => import("./pages/Review"));

function ThemedApp() {
  const { isDark, palette, customColor } = useTheme();
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const desktopChrome = useDesktopChrome();
  const brandTokens = brandTokensFor(palette, isDark, customColor);
  // Make antd built-ins (Popconfirm OK/Cancel, Modal default footer, Empty,
  // Pagination, DatePicker, Table… ) follow the current UI language.
  // DatePicker month/weekday labels come from dayjs — keep it in sync too.
  const isZh = i18n.language?.toLowerCase().startsWith("zh") ?? false;
  const antdLocale = isZh ? zhCN : enUS;
  dayjs.locale(isZh ? "zh-cn" : "en");

  useUnauthorizedRedirect();

  useEffect(() => installDesktopExternalLinks(), []);

  // Set document title based on current language
  useEffect(() => {
    document.title = t("app.pageTitle");
  }, [t]);

  const themeConfig = {
    algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      borderRadius: 8,
      fontFamily: "var(--fn-font-family)",
      fontSize: 14,
      lineHeight: 1.5,
      ...(isMobile
        ? {
            fontSize: 15,
            fontSizeSM: 13,
            fontSizeLG: 17,
            fontSizeXL: 22,
            controlHeight: 36,
          }
        : {}),
      ...(isDark
        ? {
            colorBgBase: "var(--fn-bg-primary)",
            colorTextBase: "var(--fn-text-primary)",
            colorBgContainer: "var(--fn-bg-container)",
            colorBgElevated: "var(--fn-bg-elevated)",
            colorBgLayout: "var(--fn-bg-layout)",
            colorBgSpotlight: "rgba(0, 0, 0, 0.85)",
            colorBgMask: "rgba(5, 5, 8, 0.80)",
            colorBorder: "rgba(255,255,255,0.08)",
            colorBorderSecondary: "rgba(255,255,255,0.05)",
            colorText: "var(--fn-text-primary)",
            colorTextSecondary: "var(--fn-text-secondary)",
            colorTextTertiary: "var(--fn-text-tertiary)",
            colorTextQuaternary: "rgba(255, 255, 255, 0.22)",
            colorFill: "rgba(255, 255, 255, 0.06)",
            colorFillSecondary: "rgba(255, 255, 255, 0.04)",
            colorFillTertiary: "rgba(255, 255, 255, 0.03)",
            colorFillQuaternary: "rgba(255, 255, 255, 0.02)",
            colorBgTextHover: "rgba(255, 255, 255, 0.06)",
            ...brandTokens,
            boxShadow: "0px 4px 6px 0px rgba(0, 0, 0, 0.3)",
            boxShadowSecondary:
              "0px 12px 24px -16px rgba(0, 0, 0, 0.2), 0px 8px 40px 0px rgba(0, 0, 0, 0.3)",
          }
        : {
            ...brandTokens,
          }),
    },
    components: {
      Switch: {
        colorPrimary: "var(--fn-color-brand)",
        colorPrimaryHover: "var(--fn-color-brand-hover)",
      },
      Checkbox: {
        colorPrimary: "var(--fn-color-brand)",
        colorPrimaryHover: "var(--fn-color-brand-hover)",
      },
      Segmented: {
        itemSelectedBg: "var(--fn-color-brand-light)",
        itemSelectedColor: "var(--fn-color-brand-soft)",
        itemHoverBg: "var(--fn-color-brand-bg)",
      },
      ...(isDark
        ? {
            Modal: {
              headerBg: "var(--fn-bg-elevated)",
              contentBg: "var(--fn-bg-elevated)",
            },
            Input: { colorBgBase: "var(--fn-bg-primary)" },
            InputNumber: { colorBgBase: "var(--fn-bg-primary)" },
            Select: {
              colorBgBase: "var(--fn-bg-primary)",
              selectorBg: "var(--fn-bg-primary)",
            },
            DatePicker: { colorBgBase: "var(--fn-bg-primary)" },
            Segmented: {
              itemSelectedBg: "var(--fn-color-brand-bg)",
              itemSelectedColor: "var(--fn-text-primary)",
            },
            Card: { colorBgContainer: "var(--fn-bg-secondary)" },
            Tooltip: { colorBgSpotlight: "#424242" },
          }
        : {}),
    },
  };

  return (
    <ConfigProvider
      theme={themeConfig}
      prefixCls="mosswork"
      locale={antdLocale}
      spin={{ indicator: <MossworkSpinner /> }}
    >
      <AntdAppProvider>
        <DesktopChromeProvider value={desktopChrome}>
          {desktopChrome ? (
            <DesktopWindowControls chrome={desktopChrome} />
          ) : null}
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/login/moss" element={<CanonicalMossLoginPage />} />
            <Route path="/login/oidc/complete" element={<OidcComplete />} />
            <Route path="/setup" element={<SetupPage />} />
            <Route path="/invite" element={<InvitePage />} />
            <Route
              path="/reviews/:projectId/:reviewId"
              element={
                <AuthGuard canonicalReview>
                  <Suspense fallback={<MossworkSpinner />}>
                    <HumanReviewPage />
                  </Suspense>
                </AuthGuard>
              }
            />
            <Route
              path="/*"
              element={
                <AuthGuard>
                  <DesktopWorkspaceProvider>
                    <AgentProvider>
                      <LayoutModeProvider>
                        <VoiceOutputProvider>
                          <CommunicationProvider>
                            <ConversationNavigationProvider>
                              <MainLayout />
                            </ConversationNavigationProvider>
                          </CommunicationProvider>
                        </VoiceOutputProvider>
                      </LayoutModeProvider>
                    </AgentProvider>
                  </DesktopWorkspaceProvider>
                </AuthGuard>
              }
            />
          </Routes>
        </DesktopChromeProvider>
      </AntdAppProvider>
    </ConfigProvider>
  );
}

function App() {
  return (
    // `useTransitions` off: with router transitions on, React keeps the old
    // page mounted while a lazy route chunk downloads and never renders the
    // Suspense fallback, so a nav click looks like it did nothing.
    <BrowserRouter useTransitions={false}>
      <GlobalErrorBoundary>
        <GlobalStyle />
        <ThemeProvider>
          <ThemedApp />
        </ThemeProvider>
      </GlobalErrorBoundary>
    </BrowserRouter>
  );
}

export default App;
