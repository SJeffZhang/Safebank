import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ChoiceSelect, MonthPicker, Modal } from "./ui";

it("opens an App sheet with current choice, then changes value only on selection", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  render(
    <ChoiceSelect
      label="付款账户"
      value="current"
      onChange={change}
      options={[
        {
          value: "current",
          label: "日常活期",
          description: "尾号0818 · 可用 ¥6,000.00",
        },
        { value: "savings", label: "安心储蓄", description: "尾号0926" },
      ]}
    />,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "付款账户" }));
  const sheet = screen.getByRole("dialog", { name: "付款账户" });
  expect(
    within(sheet).getByRole("option", { name: /日常活期/ }),
  ).toHaveAttribute("aria-selected", "true");
  expect(change).not.toHaveBeenCalled();
  await user.click(within(sheet).getByRole("option", { name: /安心储蓄/ }));
  expect(change).toHaveBeenCalledTimes(1);
  expect(change).toHaveBeenLastCalledWith("savings");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "付款账户" })).toHaveFocus();
});
it("Escape closes only the upper selection sheet, preserving the trade form beneath it", async () => {
  const user = userEvent.setup();
  const parentClose = vi.fn();
  render(
    <Modal title="申购理财产品" onClose={parentClose}>
      <ChoiceSelect
        label="交易账户"
        value=""
        onChange={vi.fn()}
        options={[{ value: "one", label: "日常活期" }]}
      />
    </Modal>,
  );
  await user.click(screen.getByRole("button", { name: "交易账户" }));
  expect(screen.getAllByRole("dialog")).toHaveLength(2);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "交易账户" })).toBeNull();
  expect(
    screen.getByRole("dialog", { name: "申购理财产品" }),
  ).toBeInTheDocument();
  expect(parentClose).not.toHaveBeenCalled();
});
it("month selection uses the same App sheet and does not apply year navigation alone", async () => {
  const user = userEvent.setup();
  const change = vi.fn();
  render(<MonthPicker value="2026-10" onChange={change} />);
  await user.click(screen.getByRole("button", { name: "账单月份" }));
  expect(screen.getByRole("option", { name: "10月" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await user.click(screen.getByRole("button", { name: "上一年" }));
  expect(change).not.toHaveBeenCalled();
  await user.click(screen.getByRole("option", { name: "9月" }));
  expect(change).toHaveBeenCalledTimes(1);
  expect(change).toHaveBeenLastCalledWith("2025-09");
  expect(screen.queryByRole("dialog")).toBeNull();
});
