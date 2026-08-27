import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuditLogPage } from "./pages/AuditLogPage";
import { PermissionPage } from "./pages/PermissionPage";
import type { AuditItem, AuditResponse, PermissionMatrix } from "./types";

const matrix: PermissionMatrix = {
  currentUserId: "super-demo",
  users: [
    { id: "super-demo", name: "超级演示用户", mode: "local_demo", roles: ["企业管理员", "内容创作者"], permissions: ["user.manage", "audit.view"] },
    { id: "viewer-demo", name: "只读演示用户", mode: "local_demo", roles: ["只读访客"], permissions: [] },
  ],
  roles: [
    { name: "企业管理员", permissions: ["user.manage", "audit.view"] },
    { name: "只读访客", permissions: [] },
  ],
  permissions: [
    { code: "user.manage", description: "查看演示权限矩阵" },
    { code: "audit.view", description: "查看不可变操作记录" },
  ],
  mode: "local_demo",
  mutable: false,
};

const auditItem: AuditItem = {
  id: 7,
  occurred_at: "2026-08-27T16:20:00+08:00",
  actor_id: "super-demo",
  action: "product.create",
  object_type: "product",
  object_id: "product-stage-10",
  detail: "创建产品草稿",
};

function audit(items: AuditItem[], total = items.length): AuditResponse {
  return { items, total, limit: 100, offset: 0, facets: { actors: ["super-demo", "viewer-demo"], actions: ["permission.denied", "product.create"], objectTypes: ["permission", "product"] } };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("Stage 10 permissions and audit", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("renders a read-only permission matrix without mutation controls", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(matrix)));
    render(<MemoryRouter><PermissionPage /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "用户与权限" })).toBeInTheDocument();
    expect(screen.getByText("只读演示模式")).toBeInTheDocument();
    expect(screen.getByText("超级演示用户")).toBeInTheDocument();
    expect(screen.getByText("只读演示用户")).toBeInTheDocument();
    const roleTable = screen.getByRole("table", { name: "角色权限矩阵" });
    expect(within(roleTable).getByText("audit.view")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /新增|编辑|删除|邀请|保存/ })).not.toBeInTheDocument();
  });

  it("shows a recoverable permission loading error", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ error: { code: "PERMISSION_DENIED", message: "当前演示用户没有此操作权限" } }, 403))
      .mockResolvedValueOnce(json(matrix));
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><PermissionPage /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("当前演示用户没有此操作权限");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByRole("heading", { name: "用户与权限" })).toBeInTheDocument();
  });

  it("filters audit records, opens immutable detail and resets", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/audit-logs/7")) return json(auditItem);
      if (url.includes("actorId=super-demo") && url.includes("action=product.create")) return json(audit([auditItem]));
      return json(audit([auditItem]));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><AuditLogPage /></MemoryRouter>);
    await screen.findByText("创建产品草稿");
    fireEvent.change(screen.getByLabelText("审计操作人"), { target: { value: "super-demo" } });
    fireEvent.change(screen.getByLabelText("审计动作"), { target: { value: "product.create" } });
    fireEvent.change(screen.getByLabelText("审计对象 ID"), { target: { value: "product-stage-10" } });
    fireEvent.click(screen.getByRole("button", { name: "查询记录" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = String(input); return url.includes("actorId=super-demo") && url.includes("action=product.create") && url.includes("objectId=product-stage-10");
    })).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /product.create/ }));
    const dialog = await screen.findByRole("dialog", { name: "操作记录详情" });
    expect(dialog).toHaveTextContent("该记录由服务端追加，不提供编辑或删除接口");
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭详情" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置筛选" }));
    expect(screen.getByLabelText("审计对象 ID")).toHaveValue("");
  });

  it("shows the filtered empty state and can recover by resetting", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      calls += 1;
      const url = String(input);
      if (url.includes("objectId=missing")) return json(audit([], 0));
      return json(audit([auditItem]));
    }));
    render(<MemoryRouter><AuditLogPage /></MemoryRouter>);
    await screen.findByText("创建产品草稿");
    fireEvent.change(screen.getByLabelText("审计对象 ID"), { target: { value: "missing" } });
    fireEvent.click(screen.getByRole("button", { name: "查询记录" }));
    expect(await screen.findByText("当前筛选没有记录")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "重置筛选" })[1]);
    expect(await screen.findByText("创建产品草稿")).toBeInTheDocument();
    expect(calls).toBeGreaterThanOrEqual(3);
  });
});
