import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Approval } from "./Approval";
import { getView, request } from "./api";
import type { PlanView } from "./types";
vi.mock("./api", () => ({
  isPreview: false,
  getView: vi.fn(),
  getPlan: vi.fn(),
  request: vi.fn(),
}));
const sample = (): PlanView => ({
  approval_id: "apv_test",
  plan_id: "pln_test",
  evaluation_id: "evl_test",
  session_id: "ses_test",
  status: "PENDING_APPROVAL",
  origin: "app_form",
  risk: "YELLOW",
  steps: [
    {
      step_id: "step_one",
      action_kind: "TRANSFER_EXTERNAL",
      title: "转账给王五",
      irreversible: false,
      fields: [
        { key: "amount", label: "金额", value: "800.00", unit: "元" },
        {
          key: "memo",
          label: "备注",
          value: "<img src=x onerror=alert(1)>",
          unit: null,
        },
      ],
    },
  ],
  preflight: {
    executable: true,
    balances: [
      {
        account_id: "acc_test",
        balance_before_cents: 600000,
        balance_after_cents: 520000,
        available_before_cents: 600000,
        available_after_cents: 520000,
        daily_used_before_cents: 0,
        daily_used_after_cents: 80000,
      },
    ],
    scheduled_reserve_cents: 4500,
    steps: [],
    holdings: [],
  },
  findings: [],
  disclosures: [],
  risk_ack_required: false,
  otp_required: false,
  expires_at_wall: new Date(Date.now() + 300000).toISOString(),
  view_hash: "bound-hash",
});
function mount() {
  return render(
    <Approval
      id="apv_test"
      onClose={vi.fn()}
      onComplete={vi.fn()}
      onDecision={vi.fn()}
      onResume={vi.fn()}
    />,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getView).mockResolvedValue(sample());
  vi.mocked(request).mockResolvedValue({ status: "REJECTED" });
});
describe("L2 sourced approval sheet", () => {
  it.each([true, false, undefined])("distinguishes computed reserve from missing projection (%s)", async (available) => {
    const view = sample();
    Object.assign(view.preflight!, { reserve_projection_available: available });
    vi.mocked(getView).mockResolvedValue(view);
    mount();
    await screen.findByText("800.00 元");
    if (available === true) {
      expect(screen.getByText(/计划前未来 7 天预留/)).toBeInTheDocument();
      expect(screen.getByText(/45\.00/)).toBeInTheDocument();
      expect(screen.queryByText(/预留未计算/)).not.toBeInTheDocument();
    } else {
      expect(screen.getByText(/未来 7 天预留未计算/)).toBeInTheDocument();
      expect(screen.queryByText(/45\.00/)).not.toBeInTheDocument();
    }
    expect(request).not.toHaveBeenCalled();
  });
  it("uses actual PlanStatus PENDING_APPROVAL, delays confirm and renders untrusted text safely", async () => {
    const { container } = mount();
    expect(await screen.findByText("800.00 元")).toBeInTheDocument();
    expect(
      screen.getByText("<img src=x onerror=alert(1)>"),
    ).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    const confirm = screen.getByRole("button", { name: "确认操作" });
    expect(confirm).toBeDisabled();
    await waitFor(() => expect(confirm).toBeEnabled(), { timeout: 1800 });
    await userEvent.click(confirm);
    expect(request).not.toHaveBeenCalled();
  });
  it("red plan requires both explicit risk acknowledgement and six digit OTP", async () => {
    vi.mocked(getView).mockResolvedValue({
      ...sample(),
      risk: "RED",
      otp_required: true,
      risk_ack_required: true,
      disclosures: [
        {
          product_id: "prd_a",
          version: "v1",
          text: "本产品不保证本金及收益。",
        },
      ],
    });
    mount();
    expect(
      await screen.findByText("本产品不保证本金及收益。"),
    ).toBeInTheDocument();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100));
    });
    expect(screen.getByRole("button", { name: "确认操作" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.type(screen.getByLabelText("短信验证码"), "12345");
    expect(screen.getByRole("button", { name: "确认操作" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("短信验证码"), "6");
    expect(screen.getByRole("button", { name: "确认操作" })).toBeEnabled();
  });
  it("expired approvals remain disabled", async () => {
    vi.mocked(getView).mockResolvedValue({
      ...sample(),
      expires_at_wall: new Date(Date.now() - 1000).toISOString(),
    });
    mount();
    await screen.findByText("授权已过期，请重新发起操作。");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100));
    });
    expect(screen.getByRole("button", { name: "确认操作" })).toBeDisabled();
  });
  it("rejection uses the exact bound view_hash and never executes", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("800.00 元");
    await user.click(screen.getByRole("button", { name: "拒绝此计划" }));
    await waitFor(() => expect(request).toHaveBeenCalledOnce());
    expect(vi.mocked(request).mock.calls[0][0]).toBe(
      "/approvals/apv_test/reject",
    );
    expect(vi.mocked(request).mock.calls[0][1]).toMatchObject({
      view_hash: "bound-hash",
    });
    expect(
      vi.mocked(request).mock.calls.some(([path]) => path.endsWith("/execute")),
    ).toBe(false);
  });
  it("failed panel fetch offers no confirmation control", async () => {
    vi.mocked(getView).mockRejectedValue(new Error("审批已失效"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("审批已失效");
    expect(screen.queryByRole("button", { name: "确认操作" })).toBeNull();
  });
});
