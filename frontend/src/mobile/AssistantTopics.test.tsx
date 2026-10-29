import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Assistant } from "./Assistant";
import { read, request, stream } from "./api";
import type { PageProps } from "./Banking";

vi.mock("./api", () => ({ isPreview: true, read: vi.fn(), request: vi.fn(), stream: vi.fn() }));
const props = {
  sid: "ses_topics", profile: {sim_now:"2026-10-28T10:00:00+08:00"} as PageProps["profile"], accounts: [], revision: 0,
  busy: false, notify: vi.fn(), onPropose: vi.fn(), openApproval: vi.fn(),
  onNewSession: vi.fn(), onResumeRegister: vi.fn(),
};
beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(request).mockResolvedValue({ clarification: null });
  vi.mocked(read).mockImplementation(async (_sid,tool)=>tool === "get_spending_summary" ? {total_cents:12345,categories:[{category:"餐饮",amount_cents:12345,share_bp:10000}]} : tool === "get_accounts" ? {accounts:[]} : {items:[],reserve_total_cents:4500});
  vi.mocked(stream).mockResolvedValue(undefined);
});

it.each(["账单分析", "资金安排", "跨场景联动"])("%s sends the selected request and reveals inline business content without navigation", async (name) => {
  const user = userEvent.setup();
  render(<Assistant {...props} />);
  await user.click(screen.getByRole("button", { name }));
  expect(await screen.findByRole("region", {name: `${name}内容`})).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("");
  expect(screen.getByRole("textbox")).toHaveFocus();
  expect(vi.mocked(stream).mock.calls[0][1]).toMatchObject({kind:"typed",text:name});
  expect(props.onPropose).not.toHaveBeenCalled();
});

it("supports keyboard activation and sends only the user's own input through the existing stream", async () => {
  const user = userEvent.setup();
  render(<Assistant {...props} />);
  screen.getByRole("button", { name: "账单分析" }).focus();
  await user.keyboard("{Enter}");
  await screen.findByRole("region", {name:"账单分析内容"});
  await user.type(screen.getByRole("textbox"), "分析十月份账单");
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  expect(stream).toHaveBeenCalledTimes(2);
  expect(vi.mocked(stream).mock.calls[1][1]).toMatchObject({ kind: "typed", text: "分析十月份账单", hint_id: null, action_id: null });
});

it("typed requests display summary data from the read API without changing route", async () => {
  const user=userEvent.setup();render(<Assistant {...props}/>);
  const hash=location.hash;
  await user.type(screen.getByRole("textbox"),"账单分析");
  await user.click(screen.getByRole("button",{name:"发送消息"}));
  await screen.findByRole("region",{name:"账单分析内容"});
  await waitFor(()=>expect(screen.getAllByText("123.45")).toHaveLength(2));
  expect(read).toHaveBeenCalledWith("ses_topics","get_spending_summary",{month:"2026-10",account_id:null});
  expect(location.hash).toBe(hash);
});

it("keeps progress visible until completion and does not display business results after failure", async () => {
  let fail:(e:Error)=>void=()=>{};
  vi.mocked(stream).mockImplementation((_sid,_body,handle)=>new Promise((_resolve,reject)=>{
    fail=reject;handle({event:"updates",data:{progress:{label:"读取账户信息"}}});
  }));
  const user=userEvent.setup();render(<Assistant {...props}/>);
  await user.click(screen.getByRole("button",{name:"资金安排"}));
  expect(screen.getByRole("status",{name:"小安处理进度"})).toHaveTextContent("读取账户信息");
  expect(screen.queryByRole("region",{name:"资金安排内容"})).toBeNull();
  fail(new Error("测试连接失败"));
  await screen.findByText("测试连接失败");
  expect(screen.queryByRole("region",{name:"资金安排内容"})).toBeNull();
  expect(read).not.toHaveBeenCalled();
});

it("ignores a stopped request that finishes after a newer request starts", async () => {
  const complete: (()=>void)[]=[];
  vi.mocked(stream).mockImplementation(()=>new Promise<void>(resolve=>complete.push(resolve)));
  const user=userEvent.setup();render(<Assistant {...props}/>);
  await user.click(screen.getByRole("button",{name:"账单分析"}));
  await user.click(screen.getByRole("button",{name:"停止接收回复"}));
  await user.type(screen.getByRole("textbox"),"资金安排");
  await user.click(screen.getByRole("button",{name:"发送消息"}));
  complete[0]();
  await waitFor(()=>expect(complete).toHaveLength(2));
  await new Promise(resolve=>setTimeout(resolve,0));
  expect(screen.queryByRole("region",{name:"账单分析内容"})).toBeNull();
  expect(screen.getByRole("button",{name:"停止接收回复"})).toBeInTheDocument();
  complete[1]();
  expect(await screen.findByRole("region",{name:"资金安排内容"})).toBeInTheDocument();
});
