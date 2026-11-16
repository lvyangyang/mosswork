import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import ResourceMarketEntry from "./ResourceMarketEntry";
import { skillPackagesApi } from "../api/modules/skillPackages";
import { localSkillMarketApi } from "../api/modules/localSkillMarket";
import { useCurrentUser } from "../hooks/useCurrentUser";

const translate = (key: string, values?: Record<string, unknown>) =>
  key === "localSkillMarket.packageOption"
    ? `${values?.name} (${values?.count})`
    : key;
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: translate }) }));
vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: vi.fn() }));
vi.mock("../api/modules/localSkillMarket", () => ({
  localSkillMarketApi: {
    list: vi.fn(),
    detail: vi.fn(),
    install: vi.fn(),
    status: vi.fn(),
    preview: vi.fn(),
    installExisting: vi.fn(),
    refresh: vi.fn(),
  },
}));
vi.mock("../api/modules/skillPackages", () => ({
  skillPackagesApi: { listWritable: vi.fn() },
}));
vi.mock("../utils/antdMessage", () => ({ message: { success: vi.fn() } }));
const release = {
  resource_id: "resource",
  version_id: "v1",
  version: "1.0.0",
  object_sha256: "a".repeat(64),
  name: "Offline inspection",
  description: "A local skill",
  dependencies: [],
};
const newer = { ...release, version_id: "v2", version: "2.0.0" };
const detail = {
  ...release,
  slug: "inspection",
  changelog: "Initial release",
  versions: [newer, release],
  lock: [
    {
      resource_id: "resource",
      version_id: "v1",
      version: "1.0.0",
      object_sha256: release.object_sha256,
    },
  ],
  file_contents: [
    { name: "skills/inspection/SKILL.md", content: "Inspection instructions" },
  ],
  runtime: {},
  configuration: {},
};
function show(props = {}) {
  return render(
    <MemoryRouter>
      <ResourceMarketEntry {...props} />
    </MemoryRouter>,
  );
}
const getComputedStyle = window.getComputedStyle;
beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.mocked(skillPackagesApi.listWritable).mockResolvedValue([]);
  vi.mocked(localSkillMarketApi.status).mockResolvedValue({
    package_id: "existing",
    name: "Existing",
    installed: [],
  });
  vi.mocked(localSkillMarketApi.preview).mockResolvedValue({
    package_id: "existing",
    package_name: "Existing",
    action: "install",
    installed_version: null,
    changes: [{ slug: "inspection", version: "1.0.0", operation: "install" }],
    modified_slugs: [],
    token: "preview-token",
    source: detail,
  });
  vi.mocked(localSkillMarketApi.installExisting).mockResolvedValue({
    package: { id: "existing", name: "Existing", skill_count: 1 },
    source: detail,
  });
  vi.mocked(useCurrentUser).mockReturnValue({
    id: 2,
    role: "user",
    permissions: ["skill_packages"],
  } as ReturnType<typeof useCurrentUser>);
  vi.mocked(localSkillMarketApi.list).mockResolvedValue({ items: [release] });
  vi.mocked(localSkillMarketApi.detail).mockResolvedValue(detail);
  vi.mocked(localSkillMarketApi.install).mockResolvedValue({
    package: { id: "package-v1", name: "Inspection v1", skill_count: 1 },
    source: detail,
  });
});

async function chooseNew() {
  await userEvent.click(
    await screen.findByRole("button", {
      name: "localSkillMarket.createPackage",
    }),
  );
}
const installButton = () =>
  within(
    screen
      .getByText("localSkillMarket.installTitle")
      .closest('[role="dialog"]') as HTMLElement,
  ).getByRole("button", {
    name: /localSkillMarket.confirm(Install|Action\.)/,
  });
async function chooseExisting(name = "Existing (3)") {
  await userEvent.click(
    await screen.findByRole("combobox", { name: "localSkillMarket.target" }),
  );
  await userEvent.click(await screen.findByText(name));
}
const writable = (id = "existing", name = "Existing") => ({
  id,
  name,
  skill_count: 3,
  description: "",
  created_by: "2",
  created_at: "",
  updated_at: "",
});
const openInstall = async () =>
  userEvent.click(
    await screen.findByRole("button", { name: "localSkillMarket.install" }),
  );

it("lets a consumer without publishing access inspect and install a fixed release", async () => {
  const onInstalled = vi.fn();
  show({ onInstalled });
  expect(await screen.findByText("Offline inspection")).toBeInTheDocument();
  expect(screen.queryByText("localSkillMarket.manage")).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "localSkillMarket.details" }),
  );
  expect(
    await screen.findByText("skills/inspection/SKILL.md"),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByText("skills/inspection/SKILL.md"));
  expect(
    await screen.findByText("Inspection instructions"),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getAllByRole("button", { name: "localSkillMarket.install" }).at(-1)!,
  );
  await chooseNew();
  const name = screen.getByRole("textbox", {
    name: "localSkillMarket.packageName",
  });
  await userEvent.clear(name);
  await userEvent.type(name, "My offline skills");
  await userEvent.click(installButton());
  await waitFor(() => {
    expect(localSkillMarketApi.install).toHaveBeenCalledWith(
      "v1",
      "My offline skills",
    );
    expect(onInstalled).toHaveBeenCalledWith("package-v1");
  });
});

it("installs the explicitly selected historical version", async () => {
  vi.mocked(localSkillMarketApi.detail).mockImplementation(async (id) =>
    id === "v2" ? { ...detail, ...newer } : detail,
  );
  show();
  await userEvent.click(
    await screen.findByRole("button", { name: "localSkillMarket.details" }),
  );
  await userEvent.click(
    screen.getByRole("combobox", { name: "resourceMarket.version" }),
  );
  const choices = await screen.findAllByText("v1.0.0");
  await userEvent.click(choices[choices.length - 1]);
  await userEvent.click(
    screen.getAllByRole("button", { name: "localSkillMarket.install" }).at(-1)!,
  );
  await chooseNew();
  expect(
    await screen.findByDisplayValue("Offline inspection v1.0.0"),
  ).toBeInTheDocument();
  await userEvent.click(installButton());
  await waitFor(() =>
    expect(localSkillMarketApi.install).toHaveBeenCalledWith(
      "v1",
      "Offline inspection v1.0.0",
    ),
  );
});

it("retains the detail and package name when installation fails", async () => {
  const onInstalled = vi.fn();
  vi.mocked(localSkillMarketApi.install).mockRejectedValue(
    new Error("Name already exists"),
  );
  show({ initialVersionId: "v1", onInstalled });
  await chooseNew();
  await screen.findByDisplayValue("Offline inspection v1.0.0");
  await userEvent.click(installButton());
  expect(await screen.findByText("Name already exists")).toBeInTheDocument();
  expect(
    screen.getByDisplayValue("Offline inspection v1.0.0"),
  ).toBeInTheDocument();
  expect(onInstalled).not.toHaveBeenCalled();
});

it("reports catalog failures and supports refresh", async () => {
  vi.mocked(localSkillMarketApi.list).mockRejectedValueOnce(
    new Error("Unavailable"),
  );
  show();
  expect(await screen.findByText("Unavailable")).toBeInTheDocument();
  await userEvent.click(
    await screen.findByRole("button", { name: "common.refresh" }),
  );
  expect(await screen.findByText("Offline inspection")).toBeInTheDocument();
});

it("does not query the market without skill package access", () => {
  vi.mocked(useCurrentUser).mockReturnValue({
    id: 3,
    role: "user",
    permissions: [],
  } as unknown as ReturnType<typeof useCurrentUser>);
  show();
  expect(screen.getByText("resourceMarket.contactAdmin")).toBeInTheDocument();
  expect(localSkillMarketApi.list).not.toHaveBeenCalled();
});

it("installs into the fixed current package, stays open and shows reinstall after refresh", async () => {
  const onInstalled = vi.fn();
  vi.mocked(localSkillMarketApi.status)
    .mockResolvedValueOnce({
      package_id: "existing",
      name: "Existing",
      installed: [],
    })
    .mockResolvedValue({
      package_id: "existing",
      name: "Existing",
      installed: [
        {
          resource_id: "resource",
          version_id: "v1",
          version: "1.0.0",
          slug: "inspection",
        },
      ],
    });
  show({ targetPackage: { id: "existing", name: "Existing" }, onInstalled });
  await userEvent.click(
    await screen.findByRole("button", { name: "localSkillMarket.install" }),
  );
  expect(
    await screen.findByText("localSkillMarket.confirmTarget"),
  ).toBeInTheDocument();
  await waitFor(() => expect(installButton()).toBeEnabled());
  await userEvent.click(installButton());
  await waitFor(() =>
    expect(localSkillMarketApi.installExisting).toHaveBeenCalledWith(
      "v1",
      "existing",
      "preview-token",
    ),
  );
  expect(onInstalled).toHaveBeenCalledWith("existing");
  expect(localSkillMarketApi.install).not.toHaveBeenCalled();
  expect(
    await screen.findByRole("button", { name: "localSkillMarket.reinstall" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Offline inspection")).toBeInTheDocument();
  expect(
    screen.queryByRole("combobox", { name: "localSkillMarket.target" }),
  ).not.toBeInTheDocument();
});

it("shows rollback and local-edit warnings and does not replace without confirmation", async () => {
  vi.mocked(localSkillMarketApi.preview).mockResolvedValue({
    package_id: "existing",
    package_name: "Existing",
    action: "rollback",
    installed_version: "2.0.0",
    changes: [{ slug: "inspection", version: "1.0.0", operation: "replace" }],
    modified_slugs: ["inspection"],
    token: "token",
    source: detail,
  });
  show({ targetPackage: { id: "existing", name: "Existing" } });
  await userEvent.click(
    await screen.findByRole("button", { name: "localSkillMarket.install" }),
  );
  expect(
    await screen.findByText("localSkillMarket.rollbackWarning"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("localSkillMarket.modifiedWarning"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("localSkillMarket.replaceWarning"),
  ).toBeInTheDocument();
  expect(localSkillMarketApi.installExisting).not.toHaveBeenCalled();
});

it("separates installation success from Agent refresh failure and offers refresh retry", async () => {
  vi.mocked(localSkillMarketApi.installExisting).mockResolvedValue({
    package: { id: "existing", name: "Existing", skill_count: 1 },
    source: detail,
    refresh_failed: true,
  });
  vi.mocked(localSkillMarketApi.refresh).mockResolvedValue({
    refresh_failed: false,
  });
  show({ targetPackage: { id: "existing", name: "Existing" } });
  await userEvent.click(
    await screen.findByRole("button", { name: "localSkillMarket.install" }),
  );
  await userEvent.click(
    await screen.findByRole("button", {
      name: "localSkillMarket.confirmAction.install",
    }),
  );
  expect(
    await screen.findByText("localSkillMarket.refreshFailed"),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "localSkillMarket.retryRefresh" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByText("localSkillMarket.refreshFailed"),
    ).not.toBeInTheDocument(),
  );
  expect(localSkillMarketApi.refresh).toHaveBeenCalledWith("existing");
  expect(localSkillMarketApi.installExisting).toHaveBeenCalledTimes(1);
});

it("opens a target picker directly from an enabled card and installs into the selected existing package", async () => {
  const onInstalled = vi.fn();
  vi.mocked(skillPackagesApi.listWritable).mockResolvedValue([writable()]);
  show({ onInstalled });
  const button = await screen.findByRole("button", {
    name: "localSkillMarket.install",
  });
  expect(button).toBeEnabled();
  await userEvent.click(button);
  await screen.findByRole("dialog");
  expect(installButton()).toBeDisabled();
  expect(
    screen.queryByText("skills/inspection/SKILL.md"),
  ).not.toBeInTheDocument();
  await chooseExisting();
  await waitFor(() => expect(installButton()).toBeEnabled());
  await userEvent.click(installButton());
  await screen.findByText("localSkillMarket.installedSkill");
  expect(localSkillMarketApi.installExisting).toHaveBeenCalledWith(
    "v1",
    "existing",
    "preview-token",
  );
  expect(localSkillMarketApi.install).not.toHaveBeenCalled();
  expect(onInstalled).toHaveBeenCalledWith("existing");
  expect(
    screen.getByRole("link", { name: "localSkillMarket.viewPackage" }),
  ).toHaveAttribute("href", "/skill-packages?packageId=existing");
});

it("preserves the target on failure and requires a fresh preview before retry", async () => {
  vi.mocked(skillPackagesApi.listWritable).mockResolvedValue([writable()]);
  vi.mocked(localSkillMarketApi.installExisting).mockRejectedValueOnce(
    new Error("Package changed"),
  );
  show();
  await openInstall();
  await chooseExisting();
  await waitFor(() => expect(installButton()).toBeEnabled());
  await userEvent.click(installButton());
  await screen.findByText("Package changed");
  expect(installButton()).toBeDisabled();
  expect(
    screen
      .getByRole("combobox", { name: "localSkillMarket.target" })
      .closest(".ant-select"),
  ).toHaveTextContent("Existing (3)");
  await userEvent.click(screen.getByRole("button", { name: "common.retry" }));
  await waitFor(() => expect(installButton()).toBeEnabled());
  expect(localSkillMarketApi.installExisting).toHaveBeenCalledTimes(1);
  expect(localSkillMarketApi.preview).toHaveBeenCalledTimes(2);
  await userEvent.click(installButton());
  await screen.findByText("localSkillMarket.installedSkill");
});

it("reports conflicts without writing and allows switching the target", async () => {
  vi.mocked(skillPackagesApi.listWritable).mockResolvedValue([
    writable(),
    writable("other", "Other"),
  ]);
  vi.mocked(localSkillMarketApi.preview)
    .mockRejectedValueOnce(new Error("Dependency conflict"))
    .mockResolvedValue({
      package_id: "other",
      package_name: "Other",
      action: "install",
      installed_version: null,
      changes: [],
      modified_slugs: [],
      token: "other-token",
      source: detail,
    });
  show();
  await openInstall();
  await chooseExisting();
  await screen.findByText("Dependency conflict");
  expect(installButton()).toBeDisabled();
  expect(localSkillMarketApi.installExisting).not.toHaveBeenCalled();
  await chooseExisting("Other (3)");
  await waitFor(() => expect(installButton()).toBeEnabled());
  await userEvent.click(installButton());
  await waitFor(() =>
    expect(localSkillMarketApi.installExisting).toHaveBeenCalledWith(
      "v1",
      "other",
      "other-token",
    ),
  );
});

it("ignores a late preview for a previously selected package", async () => {
  let resolveFirst!: (
    result: Awaited<ReturnType<typeof localSkillMarketApi.preview>>,
  ) => void;
  const first = new Promise<
    Awaited<ReturnType<typeof localSkillMarketApi.preview>>
  >((resolve) => {
    resolveFirst = resolve;
  });
  vi.mocked(skillPackagesApi.listWritable).mockResolvedValue([
    writable(),
    writable("other", "Other"),
  ]);
  vi.mocked(localSkillMarketApi.preview).mockImplementation(async (_, id) =>
    id === "existing"
      ? first
      : {
          package_id: "other",
          package_name: "Other",
          action: "install",
          installed_version: null,
          changes: [],
          modified_slugs: [],
          token: "other-token",
          source: detail,
        },
  );
  show();
  await openInstall();
  await chooseExisting();
  expect(installButton()).toBeDisabled();
  await chooseExisting("Other (3)");
  await waitFor(() => expect(installButton()).toBeEnabled());
  await act(async () =>
    resolveFirst({
      package_id: "existing",
      package_name: "Existing",
      action: "reinstall",
      installed_version: "1.0.0",
      changes: [],
      modified_slugs: [],
      token: "stale-token",
      source: detail,
    }),
  );
  expect(installButton()).toHaveTextContent(
    "localSkillMarket.confirmAction.install",
  );
  await userEvent.click(installButton());
  await waitFor(() =>
    expect(localSkillMarketApi.installExisting).toHaveBeenCalledWith(
      "v1",
      "other",
      "other-token",
    ),
  );
});

it("retries package loading, offers explicit creation and cancels without installation", async () => {
  vi.mocked(skillPackagesApi.listWritable).mockRejectedValueOnce(
    new Error("Packages unavailable"),
  );
  show();
  await openInstall();
  await screen.findByText("Packages unavailable");
  await userEvent.click(screen.getByRole("button", { name: "common.retry" }));
  await screen.findByText("localSkillMarket.noWritablePackages");
  expect(installButton()).toBeDisabled();
  await chooseNew();
  await screen.findByRole("textbox", { name: "localSkillMarket.packageName" });
  await userEvent.click(screen.getByRole("button", { name: "common.cancel" }));
  expect(localSkillMarketApi.install).not.toHaveBeenCalled();
  expect(localSkillMarketApi.installExisting).not.toHaveBeenCalled();
});

it.each([
  ["1.0.0", "reinstall"],
  ["0.9.0", "update"],
  ["2.0.0", "rollback"],
])("shows %s as %s only in the selected package", async (version, action) => {
  vi.mocked(localSkillMarketApi.status).mockImplementation(async (id) => ({
    package_id: id,
    name: id,
    installed:
      id === "existing"
        ? [
            {
              resource_id: "resource",
              version_id: "installed",
              version,
              slug: "inspection",
            },
          ]
        : [],
  }));
  const { rerender } = show({
    targetPackage: { id: "existing", name: "Existing" },
  });
  await screen.findByRole("button", { name: `localSkillMarket.${action}` });
  rerender(
    <MemoryRouter>
      <ResourceMarketEntry targetPackage={{ id: "other", name: "Other" }} />
    </MemoryRouter>,
  );
  await screen.findByRole("button", { name: "localSkillMarket.install" });
  expect(
    screen.queryByRole("button", { name: `localSkillMarket.${action}` }),
  ).not.toBeInTheDocument();
});
