import { render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Approval } from "./Approval";
import { getView } from "./api";
import type { PlanView } from "./types";

vi.mock("./api", () => ({
  isPreview: false, getView: vi.fn(), getPlan: vi.fn(), request: vi.fn(), demoSms: vi.fn(),
  BankError: class extends Error {},
}));

function view(): PlanView {
  return {
    approval_id: "apv_material", plan_id: "pln_material", evaluation_id: "evl_material",
    session_id: "ses_material", status: "PENDING_APPROVAL", origin: "app_form", risk: "YELLOW",
    steps: [{ step_id: "step_material", action_kind: "FUND_SUBSCRIBE", title: "申购与订阅计划", irreversible: false,
      fields: [
        { key: "monthly_saving", label: "预计每月节省", value: "199.00", unit: "元" },
        { key: "expected_confirm_at", label: "预计确认时间", value: "2026-10-20T01:00:00+00:00", unit: "datetime" },
        { key: "expected_settle_at", label: "预计到账时间", value: "2026-10-21T09:00:00+08:00", unit: "datetime" },
      ] }],
    preflight: { executable: true, balances: [{ account_id: "acc_material", balance_before_cents: 600000,
      balance_after_cents: 520000, available_before_cents: 600000, available_after_cents: 520000,
      daily_used_before_cents: 10000, daily_used_after_cents: 90000 }],
      scheduled_reserve_cents: 4500, reserve_projection_available: true, steps: [], holdings: [] },
    findings: [], disclosures: [], risk_ack_required: false, otp_required: false,
    expires_at_wall: new Date(Date.now() + 300000).toISOString(), view_hash: "material-bound-hash",
  };
}

function mount() {
  return render(<Approval id="apv_material" onClose={vi.fn()} onComplete={vi.fn()} onDecision={vi.fn()} onResume={vi.fn()} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getView).mockResolvedValue(view());
});

it("shows daily limit usage before/after separately from available balance and reserve", async () => {
  mount();
  const label = await screen.findByText("当日限额占用");
  const row = label.closest(".balance-diff") as HTMLElement;
  expect(within(row).getByText("100.00")).toBeInTheDocument();
  expect(within(row).getByText("900.00")).toBeInTheDocument();
  expect(screen.getByText("可用余额")).toBeInTheDocument();
  expect(screen.getByText("计划前未来 7 天预留")).toBeInTheDocument();
});

it("renders hashed monthly saving and expected fund datetimes in Shanghai time", async () => {
  mount();
  expect(await screen.findByText("199.00 元")).toBeInTheDocument();
  expect(screen.getByText(/2026\/10\/20.*09:00/)).toBeInTheDocument();
  expect(screen.getByText(/2026\/10\/21.*09:00/)).toBeInTheDocument();
  expect(screen.queryByText(/datetime/)).toBeNull();
  expect(screen.queryByText(/2026-10-20T/)).toBeNull();
});

it.each(["not-a-date", "", "<img src=x onerror=alert(1)>", null, {}])("invalid datetime values stay conservative and cannot crash or inject HTML: %s", async (value) => {
  const sample = view();
  sample.steps[0].fields = [{ key: "expected_confirm_at", label: "预计确认时间", value, unit: "datetime" }];
  vi.mocked(getView).mockResolvedValue(sample);
  const { container } = mount();
  expect(await screen.findByText("时间待核实")).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.queryByText(/Invalid Date/)).toBeNull();
});

it("keeps unavailable savings conservative without appending a currency unit", async () => {
  const sample = view();
  sample.steps[0].fields = [{ key: "monthly_saving", label: "预计每月节省", value: "暂无法确定", unit: null }];
  vi.mocked(getView).mockResolvedValue(sample);
  mount();
  expect(await screen.findByText("暂无法确定")).toBeInTheDocument();
  expect(screen.queryByText("暂无法确定 元")).toBeNull();
});
