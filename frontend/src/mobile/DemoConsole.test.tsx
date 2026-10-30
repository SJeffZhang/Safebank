import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DemoConsole } from "./DemoConsole";
import { consoleRequest, consoleStatus } from "./api";
vi.mock("./api", () => ({ consoleLogin: vi.fn(), consoleRequest: vi.fn(), consoleStatus: vi.fn() }));
const job = { id: "job_clock_1", request_id: "req_original", target_at: "2026-10-15T09:40:00+08:00" };
beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear();
  sessionStorage.setItem("safebank-demo-clock", JSON.stringify(job));
  vi.mocked(consoleStatus).mockResolvedValue({ signed_in: true });
});
afterEach(() => vi.useRealTimers());
it("restores the job and queries it without submitting another advance", async () => {
  vi.mocked(consoleRequest).mockResolvedValue({ status: "DONE", remaining: 0 });
  render(<DemoConsole onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "查询进度" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "查询进度" }));
  await screen.findByText(/状态 DONE/);
  expect(consoleRequest).toHaveBeenCalledTimes(1);
  expect(consoleRequest).toHaveBeenCalledWith("/clock/advances/job_clock_1");
  expect(screen.getByText(/job_clock_1/)).toBeInTheDocument();
});
it("resumes RETRYING with the original request id and target", async () => {
  vi.mocked(consoleRequest).mockResolvedValue({ status: "RETRYING", remaining: 1 });
  render(<DemoConsole onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查询进度" }));
  const resume = await screen.findByRole("button", { name: "继续原作业" });
  vi.mocked(consoleRequest).mockResolvedValueOnce({ advance_job_id: job.id })
    .mockResolvedValue({ status: "DONE", remaining: 0 });
  fireEvent.click(resume);
  await screen.findByText(/状态 DONE/);
  expect(consoleRequest).toHaveBeenCalledWith("/clock/advance", { request_id: job.request_id, target_at: job.target_at });
});
it("poll timeout keeps the job and never creates a replacement", async () => {
  vi.mocked(consoleRequest).mockResolvedValue({ status: "RUNNING", remaining: 1 });
  render(<DemoConsole onClose={vi.fn()} />);
  const query = await screen.findByRole("button", { name: "查询进度" });
  vi.useFakeTimers(); fireEvent.click(query);
  await act(async () => { await vi.advanceTimersByTimeAsync(31000); });
  expect(screen.getByText(/仍在处理/)).toBeInTheDocument();
  expect(JSON.parse(sessionStorage.getItem("safebank-demo-clock")!).id).toBe(job.id);
  expect(vi.mocked(consoleRequest).mock.calls.every(([path]) => path.includes("/clock/advances/"))).toBe(true);
});
