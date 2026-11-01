import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Assistant } from "./Assistant";
import { read, request, stream } from "./api";
import type { PageProps } from "./Banking";

vi.mock("./api", () => ({ isPreview: true, read: vi.fn(), request: vi.fn(), stream: vi.fn() }));
const formal14 = "我上个月各类消费的占比，和前一个月比怎么样";
const props = {
  sid: "ses_compare", profile: { sim_now: "2026-10-15T09:00:00+08:00" } as PageProps["profile"],
  accounts: [], revision: 0, busy: false, notify: vi.fn(), onPropose: vi.fn(),
  openApproval: vi.fn(), onNewSession: vi.fn(), onResumeRegister: vi.fn(),
};
const summaries = {
  "2026-09": { month: "2026-09", daily: [], total_cents: 644800, previous_total_cents: 133190, mom_change_cents: 511610, mom_change_bp: 38412, transfer_out_cents: 0, categories: [{ category: "购物", amount_cents: 644800, share_bp: 10000, previous_amount_cents: 133190 }] },
  "2026-08": { month: "2026-08", daily: [], total_cents: 133190, previous_total_cents: 100000, mom_change_cents: 33190, mom_change_bp: 3319, transfer_out_cents: 0, categories: [{ category: "购物", amount_cents: 133190, share_bp: 10000, previous_amount_cents: 100000 }] },
};
beforeEach(() => {
  vi.clearAllMocks(); Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(request).mockResolvedValue({ clarification: null });
  vi.mocked(read).mockImplementation(async (_sid, tool, args) => {
    if (tool === "get_spending_summary") {
      const summary = summaries[args?.month as keyof typeof summaries];
      if (!summary) throw new Error("Unexpected summary month");
      return summary;
    }
    if (tool === "get_transactions") return { items: [], next_cursor: null, income_cents: 0, expense_cents: 0 };
    if (tool === "detect_anomalies") return { items: [] };
    throw new Error(`Unexpected read: ${tool}`);
  });
  vi.mocked(stream).mockImplementation(async (_sid, _body, handle) => handle({ event: "custom", data: { type: "cards", cards: [
    { id: "sep", type: "TRANSACTIONS", source_ref: "src_sep", data: summaries["2026-09"], actions: [] },
    { id: "aug", type: "TRANSACTIONS", source_ref: "src_aug", data: summaries["2026-08"], actions: [] },
    { id: "unexpected", type: "TRANSACTIONS", source_ref: "src_oct", data: { ...summaries["2026-09"], month: "2026-10", total_cents: 55050 }, actions: [] },
  ] } }));
});
async function ask(text = formal14, simNow = props.profile.sim_now) {
  const user = userEvent.setup(); render(<Assistant {...props} profile={{ ...props.profile, sim_now: simNow }} />);
  await user.type(screen.getByRole("textbox", { name: "向小安描述您的需求" }), text);
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  return user;
}
it("shows each formal14 source month independently and still rejects an unrequested month", async () => {
  await ask();
  const sep = screen.getByText("来源 src_sep").closest(".safe-ui-card")!;
  const aug = screen.getByText("来源 src_aug").closest(".safe-ui-card")!;
  const oct = screen.getByText("来源 src_oct").closest(".safe-ui-card")!;
  expect(sep).toHaveTextContent("2026-09"); expect(sep).toHaveTextContent("6,448.00");
  expect(aug).toHaveTextContent("2026-08"); expect(aug).toHaveTextContent("1,331.90");
  expect(within(sep as HTMLElement).queryByRole("alert")).toBeNull();
  expect(within(aug as HTMLElement).queryByRole("alert")).toBeNull();
  expect(oct).toHaveTextContent("月份"); expect(oct).not.toHaveTextContent("550.50");
  expect(props.onPropose).not.toHaveBeenCalled();
});
it("starts inline formal14 details in September without fabricating a combined monthly total", async () => {
  const user = await ask();
  await waitFor(() => expect(read).toHaveBeenCalledWith("ses_compare", "get_spending_summary", { month: "2026-09", account_id: null }));
  const region = await screen.findByRole("region", { name: "账单分析内容" });
  expect(within(region).getByRole("button", { name: "账单月份" })).toHaveTextContent("2026年9月");
  await user.click(within(region).getByRole("button", { name: "展开账单明细" }));
  await waitFor(() => expect(read).toHaveBeenCalledWith("ses_compare", "get_transactions", expect.objectContaining({ from_at: "2026-09-01T00:00:00+08:00", to_at: "2026-10-01T00:00:00+08:00" })));
  expect(region).not.toHaveTextContent("7,779.90");
});
it("updates an already expanded detail view when the inline selected month changes", async () => {
  vi.mocked(stream).mockResolvedValue(undefined);
  const user = await ask("给我出一份9月的账单报告");
  const region = await screen.findByRole("region", { name: "账单分析内容" });
  await user.click(await within(region).findByRole("button", { name: "展开账单明细" }));
  await within(region).findByText("账单与分析");
  await user.click(within(region).getAllByRole("button", { name: "账单月份" })[0]);
  await user.click(screen.getByRole("option", { name: "8月" }));
  await waitFor(() => expect(read).toHaveBeenCalledWith("ses_compare", "get_transactions", expect.objectContaining({ from_at: "2026-08-01T00:00:00+08:00", to_at: "2026-09-01T00:00:00+08:00" })));
  expect(within(region).getAllByRole("button", { name: "账单月份" }).every(button => button.textContent?.includes("2026年8月"))).toBe(true);
});
it("uses the simulated Shanghai year boundary for the two-month comparison", async () => {
  vi.mocked(stream).mockResolvedValue(undefined);
  vi.mocked(read).mockImplementation(async (_sid, tool) => tool === "get_spending_summary" ? { ...summaries["2026-09"], month: "2025-12" } : { items: [] });
  await ask(formal14, "2025-12-31T16:30:00Z");
  await waitFor(() => expect(read).toHaveBeenCalledWith("ses_compare", "get_spending_summary", { month: "2025-12", account_id: null }));
  expect(read).toHaveBeenCalledWith("ses_compare", "detect_anomalies", { from_at: "2025-12-01T00:00:00+08:00", to_at: "2026-01-01T00:00:00+08:00", account_id: null });
});
