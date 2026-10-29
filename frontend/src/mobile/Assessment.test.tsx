import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Wealth } from "./Services";
import { read } from "./api";
import type { PageProps } from "./Banking";
vi.mock("./api", () => ({ read: vi.fn() }));
const props = (): PageProps => ({
  sid: "ses_test",
  profile: {
    customer_id: "cus_test",
    display_name: "测试客户",
    phone_masked: "138 **** 0000",
    sim_now: "2026-10-28T10:00:00+08:00",
    risk_profile: {
      level: 2,
      is_valid: true,
      valid_until: "2027-09-01T00:00:00+08:00",
    },
  },
  accounts: [],
  revision: 0,
  busy: false,
  notify: vi.fn(),
  onPropose: vi.fn().mockResolvedValue(undefined),
});
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(read).mockImplementation(async (_sid, tool) => {
    if (tool === "list_products") return { items: [] };
    if (tool === "get_holdings") return { holdings: [], orders: [] };
    return {
      questionnaire_id: "rq_v1",
      questions: Array.from({ length: 6 }, (_, i) => ({
        id: `q${i + 1}`,
        prompt: `问题 ${i + 1}`,
        options: [
          { id: "o1", label: "选项一" },
          { id: "o2", label: "选项二" },
        ],
      })),
    };
  });
});
it("existing assessment shows result and update entry instead of asking the six questions again", async () => {
  const user = userEvent.setup();
  render(<Wealth {...props()} />);
  await user.click(screen.getByRole("tab", { name: "风险测评" }));
  expect(
    await screen.findByRole("heading", { name: "风险测评已完成" }),
  ).toBeInTheDocument();
  expect(screen.getByText("R2")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "更新风险测评" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("radio")).toBeNull();
});
it("updating starts a blank questionnaire and can return without changing the recorded result", async () => {
  const user = userEvent.setup();
  const p = props();
  render(<Wealth {...p} />);
  await user.click(screen.getByRole("tab", { name: "风险测评" }));
  await user.click(await screen.findByRole("button", { name: "更新风险测评" }));
  expect(screen.getAllByRole("radio")).toHaveLength(12);
  expect(
    screen.getAllByRole("radio").every((r) => !(r as HTMLInputElement).checked),
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "返回测评结果" }));
  expect(
    screen.getByRole("heading", { name: "风险测评已完成" }),
  ).toBeInTheDocument();
  expect(p.onPropose).not.toHaveBeenCalled();
});
it("submission remains a questionnaire until authoritative completion refreshes the profile", async () => {
  const user = userEvent.setup();
  const p = props();
  p.profile = {
    ...p.profile,
    risk_profile: { level: null, is_valid: false, valid_until: null },
  };
  const { rerender } = render(<Wealth {...p} />);
  await user.click(screen.getByRole("tab", { name: "风险测评" }));
  await screen.findAllByRole("radio", { name: "选项一" });
  await waitFor(() =>
    expect(screen.getAllByRole("radio", { name: "选项一" })).toHaveLength(6),
  );
  for (const option of screen.getAllByRole("radio", { name: "选项一" }))
    await user.click(option);
  await user.click(screen.getByRole("button", { name: "提交测评，查看预演" }));
  expect(p.onPropose).toHaveBeenCalledWith(
    "ASSESSMENT_SUBMIT",
    expect.objectContaining({
      questionnaire_id: "rq_v1",
      answers: expect.arrayContaining([{ question_id: "q1", option_id: "o1" }]),
    }),
  );
  expect(screen.queryByRole("heading", { name: "风险测评已完成" })).toBeNull();
  rerender(
    <Wealth
      {...p}
      revision={1}
      profile={{
        ...p.profile,
        risk_profile: {
          level: 1,
          is_valid: true,
          valid_until: "2027-10-28T00:00:00+08:00",
        },
      }}
    />,
  );
  expect(
    await screen.findByRole("heading", { name: "风险测评已完成" }),
  ).toBeInTheDocument();
  expect(screen.getByText("R1")).toBeInTheDocument();
  expect(screen.queryByRole("radio")).toBeNull();
});
it("expired assessments display the recorded level and an update button", async () => {
  const user = userEvent.setup();
  const p = props();
  p.profile = {
    ...p.profile,
    risk_profile: {
      level: 2,
      is_valid: false,
      valid_until: "2025-10-28T00:00:00+08:00",
    },
  };
  render(<Wealth {...p} />);
  await user.click(screen.getByRole("tab", { name: "风险测评" }));
  expect(
    await screen.findByRole("heading", { name: "风险测评已过期" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "更新风险测评" }),
  ).toBeInTheDocument();
});
