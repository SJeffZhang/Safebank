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
  vi.mocked(read).mockImplementation(async (_sid,tool,args)=>tool === "get_spending_summary" ? {month:args?.month,total_cents:12345,categories:[{category:"餐饮",amount_cents:12345,share_bp:10000}]} : tool === "get_accounts" ? {accounts:[]} : {items:[],reserve_total_cents:4500});
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

it.each([
  ['分析9月账单', '2026-09'],
  ['生成上月月报', '2026-09'],
  ['分析上月账单，冻结可疑交易对应的卡', '2026-09'],
  ['生成2026年8月月报', '2026-08'],
])('binds %s to actual summary and anomaly reads for %s', async (text, month) => {
  vi.mocked(read).mockImplementation(async (_sid, tool, args) => tool === 'get_spending_summary'
    ? {month: args?.month, total_cents: 12345, categories: []}
    : {items: []});
  const user = userEvent.setup(); render(<Assistant {...props}/>);
  await user.type(screen.getByRole('textbox'), text);
  await user.click(screen.getByRole('button', {name:'发送消息'}));
  await waitFor(() => expect(read).toHaveBeenCalledWith('ses_topics', 'get_spending_summary', {month,account_id:null}));
  expect(await screen.findByText('123.45')).toBeInTheDocument();
  expect(read).toHaveBeenCalledWith('ses_topics', 'detect_anomalies', {
    from_at:`${month}-01T00:00:00+08:00`, to_at: `${month === '2026-09' ? '2026-10' : '2026-09'}-01T00:00:00+08:00`,account_id:null,
  });
});

it('rejects a summary for a different month rather than displaying its amount', async () => {
  vi.mocked(read).mockResolvedValue({month:'2026-10',total_cents:55050,categories:[]});
  const user=userEvent.setup();render(<Assistant {...props}/>);
  await user.type(screen.getByRole('textbox'),'分析9月账单');
  await user.click(screen.getByRole('button',{name:'发送消息'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('月份');
  expect(screen.queryByText('550.50')).toBeNull();
});

it('renders the actual structured monthly summary instead of an empty transaction list', async () => {
  vi.mocked(stream).mockImplementation(async (_sid,_body,handle) => {
    handle({event:'custom',data:{type:'cards',cards:[{id:'card_month',type:'TRANSACTIONS',source_ref:'src_summary',data:{month:'2026-09',total_cents:43210,categories:[{category:'餐饮',amount_cents:43210,share_bp:10000}]},actions:[]}]}});
  });
  const user=userEvent.setup();render(<Assistant {...props}/>);
  await user.type(screen.getByRole('textbox'),'生成9月月报');
  await user.click(screen.getByRole('button',{name:'发送消息'}));
  const card=screen.getByText('来源 src_summary').closest('.safe-ui-card')!;
  expect(card).toHaveTextContent('2026-09');
  expect(card).toHaveTextContent('432.10');
  expect(card).toHaveTextContent('餐饮');
  expect(card).not.toHaveTextContent('当前没有可展示的记录');
});

it('uses Shanghai simulated calendar boundaries for relative months and rejects stale structured data', async () => {
  vi.mocked(read).mockImplementation(async (_sid,tool,args)=>tool==='get_spending_summary'?{month:args?.month,total_cents:10000,categories:[]}:{items:[]});
  vi.mocked(stream).mockImplementation(async(_sid,_body,handle)=>handle({event:'custom',data:{type:'cards',cards:[{id:'stale',type:'TRANSACTIONS',source_ref:'src_stale',data:{month:'2026-09',total_cents:55050,categories:[]},actions:[]}]}}));
  const user=userEvent.setup();render(<Assistant {...props} profile={{...props.profile,sim_now:'2026-10-31T16:30:00Z'}}/>);
  await user.type(screen.getByRole('textbox'),'生成上月月报');await user.click(screen.getByRole('button',{name:'发送消息'}));
  await waitFor(()=>expect(read).toHaveBeenCalledWith('ses_topics','get_spending_summary',{month:'2026-10',account_id:null}));
  expect(screen.getByText('来源 src_stale').closest('.safe-ui-card')).toHaveTextContent('月份');expect(screen.queryByText('550.50')).toBeNull();
});

it('keeps a cross-scene X1 request bound to the actual prior-month summary and anomaly details', async () => {
 vi.mocked(read).mockImplementation(async (_sid,tool,args)=>tool==='get_spending_summary'
  ? {month:args?.month,total_cents:644800,categories:[]}
  : tool==='detect_anomalies' ? {items:[{transaction:{id:'tx_sep',occurred_at:'2026-09-12T02:00:00+08:00',amount_cents:9900},codes:['ANOMALY_NIGHT_LARGE'],evidence:{baseline_count:3}}]} : {accounts:[]});
 const user=userEvent.setup();render(<Assistant {...props}/>);
 await user.type(screen.getByRole('textbox'),'跨场景联动：分析上月账单，冻结可疑交易对应的卡');await user.click(screen.getByRole('button',{name:'发送消息'}));
 expect(await screen.findByText('6,448.00')).toBeInTheDocument();expect(await screen.findByText('99.00')).toBeInTheDocument();expect(screen.getByText(/深夜大额/)).toBeInTheDocument();
 expect(read).toHaveBeenCalledWith('ses_topics','get_spending_summary',{month:'2026-09',account_id:null});expect(props.onPropose).not.toHaveBeenCalled();
});

it('does not silently turn an unsupported monthly period into the current month', async () => {
 const user=userEvent.setup();render(<Assistant {...props}/>);
 await user.type(screen.getByRole('textbox'),'生成十三月月报');await user.click(screen.getByRole('button',{name:'发送消息'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('月份');expect(read).not.toHaveBeenCalled();
});

it('does not display a structured anomaly from outside the requested report month', async () => {
 vi.mocked(stream).mockImplementation(async(_sid,_body,handle)=>handle({event:'custom',data:{type:'cards',cards:[{id:'wrong_anomaly',type:'ANOMALIES',source_ref:'src_wrong_anomaly',data:{items:[{transaction:{id:'tx_wrong_month',occurred_at:'2026-10-12T02:00:00+08:00',amount_cents:9900},codes:['ANOMALY_NIGHT_LARGE']}]},actions:[]}]}}));
 const user=userEvent.setup();render(<Assistant {...props}/>);
 await user.type(screen.getByRole('textbox'),'生成9月月报');await user.click(screen.getByRole('button',{name:'发送消息'}));
 const card=screen.getByText('来源 src_wrong_anomaly').closest('.safe-ui-card')!;
 expect(card).toHaveTextContent('所选月份');expect(card).not.toHaveTextContent('99.00');
});
