import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import SkillPackagesPage from "./index";
import { skillPackagesApi } from "../../api/modules/skillPackages";
import { localSkillMarketApi } from "../../api/modules/localSkillMarket";
import { authApi } from "../../api/modules/auth";

vi.mock("../Agent/Skills/components/SkillDrawer", () => ({
  SkillDrawer: () => null,
}));
vi.mock("../Agent/Skills/components/SkillImportModal", () => ({
  SkillImportModal: () => null,
}));

const translate = (key: string) => key;
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: translate }) }));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 1, role: "admin" }),
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../layouts/PageShell", () => ({
  default: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("../../api/modules/auth", () => ({ authApi: { me: vi.fn() } }));
vi.mock("../../api/modules/skillPackages", () => ({
  skillPackagesApi: { list: vi.fn(), get: vi.fn(), listWritable: vi.fn() },
}));
vi.mock("../../api/modules/localSkillMarket", () => ({
  localSkillMarketApi: {
    list: vi.fn(),
    status: vi.fn(),
    detail: vi.fn(),
    preview: vi.fn(),
    install: vi.fn(),
    installExisting: vi.fn(),
  },
}));

const pack = (id: string) => ({
  id,
  name: `Package ${id}`,
  description: "",
  created_by: "1",
  can_write: true,
  skill_count: 0,
  created_at: "",
  updated_at: "",
  skills: [],
});
const release = {
  resource_id: "resource",
  version_id: "v1",
  version: "1.0.0",
  object_sha256: "a".repeat(64),
  name: "Inspection",
  description: "Inspection instructions",
  dependencies: [],
  lock: [],
};
const getComputedStyle = window.getComputedStyle;

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(window.matchMedia).mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.mocked(authApi.me).mockResolvedValue({ id: 1, role: "admin" } as Awaited<
    ReturnType<typeof authApi.me>
  >);
  vi.mocked(skillPackagesApi.list).mockResolvedValue([
    pack("first"),
    pack("target"),
  ]);
  vi.mocked(skillPackagesApi.get).mockImplementation(async (id) => pack(id));
  vi.mocked(localSkillMarketApi.list).mockResolvedValue({ items: [release] });
  vi.mocked(localSkillMarketApi.status).mockResolvedValue({
    package_id: "target",
    name: "Package target",
    installed: [],
  });
  vi.mocked(localSkillMarketApi.detail).mockResolvedValue({
    ...release,
    slug: "inspection",
    changelog: "",
    versions: [release],
    file_contents: [],
    runtime: {},
    configuration: {},
  });
  vi.mocked(localSkillMarketApi.preview).mockResolvedValue({
    package_id: "target",
    package_name: "Package target",
    action: "install",
    installed_version: null,
    token: "token",
    changes: [],
    modified_slugs: [],
    source: release,
  });
  vi.mocked(localSkillMarketApi.installExisting).mockResolvedValue({
    package: { id: "target", name: "Package target", skill_count: 1 },
    source: release,
  });
});

it("opens the linked package, installs from its right-side market and refreshes the same package", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/skill-packages?packageId=target"]}>
      <SkillPackagesPage />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(skillPackagesApi.get).toHaveBeenCalledWith("target"),
  );
  expect(skillPackagesApi.get).not.toHaveBeenCalledWith("first");
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", { name: "nav.resourceMarket" }),
    ).toHaveLength(2),
  );
  const marketButtons = screen.getAllByRole("button", {
    name: "nav.resourceMarket",
  });
  await user.click(marketButtons.at(-1)!);
  await user.click(
    await screen.findByRole("button", { name: "localSkillMarket.install" }),
  );
  const modal = (
    await screen.findByText("localSkillMarket.installTitle")
  ).closest('[role="dialog"]') as HTMLElement;
  expect(
    within(modal).queryByRole("combobox", { name: "localSkillMarket.target" }),
  ).not.toBeInTheDocument();
  const confirm = await within(modal).findByRole("button", {
    name: "localSkillMarket.confirmAction.install",
  });
  await waitFor(() => expect(confirm).toBeEnabled());
  await user.click(confirm);
  await screen.findByText("localSkillMarket.installedSkill");
  expect(localSkillMarketApi.installExisting).toHaveBeenCalledWith(
    "v1",
    "target",
    "token",
  );
  expect(localSkillMarketApi.install).not.toHaveBeenCalled();
  await waitFor(() => expect(skillPackagesApi.get).toHaveBeenCalledTimes(2));
  expect(skillPackagesApi.get).toHaveBeenLastCalledWith("target");
  expect(skillPackagesApi.listWritable).not.toHaveBeenCalled();
  expect(screen.getByText("Inspection")).toBeInTheDocument();
});

it("opens an old marketVersion link as a focused install dialog without opening a catalog", async () => {
  vi.mocked(skillPackagesApi.listWritable).mockResolvedValue([pack("target")]);
  render(
    <MemoryRouter initialEntries={["/skill-packages?marketVersion=v1"]}>
      <SkillPackagesPage />
    </MemoryRouter>,
  );
  await screen.findByText("localSkillMarket.installTitle");
  expect(
    await screen.findByRole("combobox", { name: "localSkillMarket.target" }),
  ).toBeInTheDocument();
  expect(localSkillMarketApi.list).not.toHaveBeenCalled();
  expect(localSkillMarketApi.installExisting).not.toHaveBeenCalled();
});
