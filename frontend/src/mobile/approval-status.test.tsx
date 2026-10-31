import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  vi.stubEnv("VITE_BANK_MODE", "live");
});

import { Approval } from "./Approval";
import { getView } from "./api";
import type { PlanView } from "./types";

// G9 overlays ApprovalStatus, not the stored PlanStatus, onto this envelope.
const pendingPanel = (): PlanView => ({
  approval_id: "apv_pending",
  plan_id: "pln_pending",
  evaluation_id: "evl_pending",
  session_id: "ses_pending",
  status: "PENDING",
  origin: "app_form",
  risk: "YELLOW",
  steps: [
    {
      step_id: "step_transfer",
      action_kind: "TRANSFER_EXTERNAL",
      title: "转账给王五",
      irreversible: false,
      fields: [{ key: "amount", label: "金额", value: "100.00", unit: "元" }],
    },
  ],
  preflight: null,
  findings: [],
  disclosures: [],
  risk_ack_required: false,
  otp_required: false,
  expires_at_wall: new Date(Date.now() + 300000).toISOString(),
  view_hash: "unchanged-bound-view-hash",
});

function reply(panel: PlanView) {
  const fetcher = vi.fn(async (path: RequestInfo | URL, options?: RequestInit) => {
    if (path !== "/api/approvals/apv_pending" || options?.method !== "GET")
      throw new Error("unexpected approval request");
    return new Response(JSON.stringify(panel), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

async function mount() {
  render(
    <Approval
      id="apv_pending"
      onClose={vi.fn()}
      onComplete={vi.fn()}
      onDecision={vi.fn()}
      onResume={vi.fn()}
    />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const confirm = screen.getByRole("button", { name: "确认操作" });
  expect(confirm).toBeDisabled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1100);
  });
  return confirm;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-31T12:00:00Z"));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
afterAll(() => vi.unstubAllEnvs());

describe("live G9 approval status boundary", () => {
  it("enables an actual PENDING panel after the reading delay without automatically confirming", async () => {
    const fetcher = reply(pendingPanel());
    const confirm = await mount();
    expect(screen.getByText("100.00 元")).toBeInTheDocument();
    expect(confirm).toBeEnabled();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith(
      "/api/approvals/apv_pending",
      expect.objectContaining({ method: "GET", credentials: "same-origin" }),
    );
  });

  it.each([
    "CONFIRMED",
    "USED",
    "REJECTED",
    "EXPIRED",
    "SUPERSEDED",
    "LOCKED",
    "UNKNOWN_STATUS",
    "pending",
    "PENDING ",
  ])(
    "does not make %s confirmable",
    async (status) => {
      const fetcher = reply({ ...pendingPanel(), status });
      expect(await mount()).toBeDisabled();
      expect(fetcher).toHaveBeenCalledOnce();
    },
  );

  it("keeps an expired PENDING envelope disabled", async () => {
    reply({
      ...pendingPanel(),
      expires_at_wall: new Date(Date.now() - 1000).toISOString(),
    });
    expect(await mount()).toBeDisabled();
    expect(screen.getByText("授权已过期，请重新发起操作。")).toBeInTheDocument();
  });

  it("still requires explicit risk acknowledgement and six OTP digits for a RED PENDING panel", async () => {
    const fetcher = reply({
      ...pendingPanel(),
      risk: "RED",
      otp_required: true,
      risk_ack_required: true,
      disclosures: [
        { product_id: "prd_pending", version: "v1", text: "请核对风险揭示。" },
      ],
    });
    const confirm = await mount();
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("短信验证码"), {
      target: { value: "12345" },
    });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("短信验证码"), {
      target: { value: "123456" },
    });
    expect(confirm).toBeEnabled();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("preserves the server identity, binding hash, expiry and risk requirements", async () => {
    const panel: PlanView = {
      ...pendingPanel(),
      risk: "RED",
      risk_ack_required: true,
      otp_required: true,
    };
    reply(panel);
    const view = await getView("apv_pending");
    expect(view).toMatchObject({
      approval_id: "apv_pending",
      plan_id: "pln_pending",
      evaluation_id: "evl_pending",
      session_id: "ses_pending",
      view_hash: "unchanged-bound-view-hash",
      expires_at_wall: "2026-10-31T12:05:00.000Z",
      risk_ack_required: true,
      otp_required: true,
    });
  });
});
