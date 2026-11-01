import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeEach, expect, it, vi } from "vitest";
import { Assistant } from "./Assistant";
import type { PageProps } from "./Banking";

// Exercise the real request/stream/SSE code; only the external HTTP boundary is fake.
vi.hoisted(() => vi.stubEnv("VITE_BANK_MODE", "live"));
afterAll(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
const original = "把下周要扣的会员费留够，剩下的钱买稳健型理财";
const props = {
  sid: "ses_x3", profile: { sim_now: "2026-10-15T09:00:00+08:00" } as PageProps["profile"],
  accounts: [], revision: 0, busy: false, notify: vi.fn(), onPropose: vi.fn(),
  openApproval: vi.fn(), onNewSession: vi.fn(), onResumeRegister: vi.fn(),
};
const slots = () => ({
  id: "clr_product", kind: "SLOTS", parent_utterance_ids: ["utt_parent"],
  required_fields: ["product_id"], prompt_template_id: "select_missing_slot",
  candidates: [
    { field: "product_id", entity_id: "prd_mixed", label: "均衡混合", source_ref: "src_candidates" },
    { field: "product_id", entity_id: "prd_sbond", label: "稳盈短债", source_ref: "src_candidates" },
    { field: "product_id", entity_id: "prd_stable", label: "稳健增利", source_ref: "src_candidates" },
  ],
  partial_scope: { action_kinds: ["FUND_SUBSCRIBE"], fields: { account_id: "acc_ls_chk", derived_template: "available_less_7d_charges" } },
  expires_at_wall: "2099-01-01T00:00:00+00:00",
});
type Wire = ReturnType<typeof slots>;
type Body = Record<string, unknown>;
let current: Wire | null;
let actions: Body[];
let messages: { sid: string; body: Body }[];
let rejected: string[];
let actionReply: (() => Promise<Response>) | undefined;
let streamCards: Record<string, unknown>[];
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const failure = (code: string, status = 422) => json({ error: { code, message: code, details: {} } }, status);
const sse = () => new Response(`${streamCards.length ? `event: custom\ndata: ${JSON.stringify({ type: "cards", cards: streamCards })}\n\n` : ""}event: updates\ndata: {"reply":{"id":"reply_${messages.length}","content":"已读取本次请求。"}}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  current = slots(); actions = []; messages = []; rejected = []; actionReply = undefined; streamCards = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const b: Body = init?.body ? JSON.parse(String(init.body)) : {};
    if (path.endsWith("/state")) return json({ values: { messages: [] }, tasks: [] });
    if (path.endsWith("/clarifications/current")) return json({ clarification: current });
    if (path.endsWith("/actions")) {
      actions.push(b);
      // G19 ActionRequest.shape + current clarification membership, no permissive mocks.
      if (b.kind !== "CLARIFICATION" || b.action_kind !== null || !current ||
          !current.parent_utterance_ids.includes(String(b.parent_utterance_id)) ||
          JSON.stringify(b.fields) !== JSON.stringify([{ field: "product_id", entity_id: "prd_sbond" }])) {
        rejected.push("G19"); return failure("CONTRACT_INVALID");
      }
      return actionReply ? actionReply() : json({ action_id: "act_selected", normalized_fields: b.fields, expires_at_wall: current.expires_at_wall });
    }
    if (path.endsWith("/messages")) {
      messages.push({ sid: path.split("/")[3], body: b });
      // G3 Registration.exclusive: clarification_id belongs only to explicit typed answers.
      if (b.kind === "action" && (b.action_id !== "act_selected" || b.clarification_id != null || b.text != null || b.hint_id != null)) {
        rejected.push("G3"); return failure("CONTRACT_INVALID");
      }
      if (b.kind === "action" || (b.kind === "typed" && b.clarification_id === "clr_product")) current = null;
      return sse();
    }
    if (path.includes("/tools/read/")) return json({ data: path.endsWith("get_accounts") ? { accounts: [] } : { items: [], reserve_total_cents: 4500 }, source_ref: "src_context", untrusted_paths: [], versions: { cus_ls: 1 }, observed_at: props.profile.sim_now });
    throw new Error(`Unexpected test route: ${path}`);
  }));
});
async function begin(text = original) {
  const user = userEvent.setup();
  const view = render(<Assistant {...props} />);
  await user.type(screen.getByRole("textbox", { name: "向小安描述您的需求" }), text);
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await screen.findByText("需要补充信息");
  return { user, ...view };
}
it("continues an actual G21 candidate through valid G19 then action-only G3", async () => {
  const { user } = await begin();
  await user.click(screen.getByRole("button", { name: "稳盈短债" }));
  await waitFor(() => expect(messages).toHaveLength(2));
  expect(actions).toHaveLength(1);
  expect(actions[0]).toMatchObject({ kind: "CLARIFICATION", action_kind: null, parent_utterance_id: "utt_parent", fields: [{ field: "product_id", entity_id: "prd_sbond" }] });
  expect(messages[1]).toMatchObject({ sid: "ses_x3", body: { kind: "action", action_id: "act_selected" } });
  expect(messages[1].body.clarification_id == null).toBe(true);
  expect(rejected).toEqual([]);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(props.onPropose).not.toHaveBeenCalled(); expect(props.openApproval).not.toHaveBeenCalled();
});
it.each([{ parents: [] }, { parents: ["utt_one", "utt_two"] }])("does not guess a missing or non-unique parent $parents", async ({ parents }) => {
  current!.parent_utterance_ids = parents;
  const { user } = await begin();
  await user.click(screen.getByRole("button", { name: "稳盈短债" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/重新|失效/);
  expect(actions).toEqual([]); expect(messages).toHaveLength(1);
});
it("sends a new request independently of an old FORM_REQUIRED", async () => {
  current = { ...slots(), kind: "FORM_REQUIRED", required_fields: [], candidates: [], prompt_template_id: "open_structured_form" };
  const { user } = await begin("我有哪些定时转账");
  await user.type(screen.getByRole("textbox", { name: "向小安描述您的需求" }), "我的风险等级是多少");
  await user.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(messages).toHaveLength(2));
  expect(messages[1].body.clarification_id == null).toBe(true);
  expect(actions).toEqual([]);
});
it("reserves typed clarification_id for an explicit SLOTS answer", async () => {
  const { user } = await begin();
  await user.type(screen.getByRole("textbox", { name: "补充本次目标" }), "稳盈短债");
  await user.click(screen.getByRole("button", { name: "提交补充" }));
  await waitFor(() => expect(messages).toHaveLength(2));
  expect(messages[1].body).toMatchObject({ kind: "typed", text: "稳盈短债", clarification_id: "clr_product", action_id: null, hint_id: null });
  expect(actions).toEqual([]);
});
it("does not submit a stale candidate after switching sessions while G19 is pending", async () => {
  let complete!: (value: Response) => void;
  actionReply = () => new Promise(resolve => { complete = resolve; });
  const { user, rerender } = await begin();
  await user.click(screen.getByRole("button", { name: "稳盈短债" }));
  await waitFor(() => expect(typeof complete).toBe("function"));
  rerender(<Assistant {...props} sid="ses_other" />);
  await act(async () => complete(json({ action_id: "act_selected", normalized_fields: [{ field: "product_id", entity_id: "prd_sbond" }], expires_at_wall: "2099-01-01T00:00:00Z" })));
  expect(messages).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "稳盈短债" })).toBeNull();
});
it("admits only one candidate submission while G19 is pending", async () => {
  let complete!: (value: Response) => void;
  actionReply = () => new Promise(resolve => { complete = resolve; });
  await begin();
  const button = screen.getByRole("button", { name: "稳盈短债" });
  fireEvent.click(button); fireEvent.click(button);
  expect(actions).toHaveLength(1);
  await act(async () => complete(json({ action_id: "act_selected", normalized_fields: [{ field: "product_id", entity_id: "prd_sbond" }], expires_at_wall: "2099-01-01T00:00:00Z" })));
  await waitFor(() => expect(messages).toHaveLength(2));
});
it.each(["network", "expired"])("does not continue or execute after a G19 %s failure", async (kind) => {
  actionReply = async () => { if (kind === "network") throw new TypeError("network unavailable"); return failure("UTTERANCE_EXPIRED", 409); };
  const { user } = await begin();
  await user.click(screen.getByRole("button", { name: "稳盈短债" }));
  await screen.findByRole("alert");
  expect(actions).toHaveLength(1); expect(messages).toHaveLength(1);
  expect(props.onPropose).not.toHaveBeenCalled(); expect(props.openApproval).not.toHaveBeenCalled();
});
it("does not register an action utterance after the choosing Assistant has unmounted", async () => {
  let complete!: (value: Response) => void;
  actionReply = () => new Promise(resolve => { complete = resolve; });
  const { user, unmount } = await begin();
  await user.click(screen.getByRole("button", { name: "稳盈短债" }));
  await waitFor(() => expect(typeof complete).toBe("function"));
  unmount();
  await act(async () => complete(json({ action_id: "act_selected", normalized_fields: [{ field: "product_id", entity_id: "prd_sbond" }], expires_at_wall: "2099-01-01T00:00:00Z" })));
  expect(messages).toHaveLength(1);
  expect(actions).toHaveLength(1);
  expect(props.onPropose).not.toHaveBeenCalled();
});
it.each([
  { template: "risk_assessment_form", text: "帮我把风险测评改成R5", detail: /亲自.*六题|六题.*亲自/, link: "前往理财页", href: "#wealth" },
  { template: "register_payee_form", text: "给钱七6222020000009999转5000", detail: /手工.*收款人|收款人.*手工/, link: "前往转账页", href: "#transfer" },
  { template: "transfer_consultation", text: "我能不能给王五转100？", detail: /本次没有发起转账/, link: "查看账户余额", href: "#overview" },
])("renders server-selected $template guidance without submitting a form", async ({ template, text, detail, link, href }) => {
  current = { ...slots(), kind: "FORM_REQUIRED", required_fields: [], candidates: [], prompt_template_id: template };
  await begin(text);
  expect(screen.getByText(detail)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: link })).toHaveAttribute("href", href);
  expect(screen.queryByText(/您可以在下方补充/)).toBeNull();
  expect(screen.queryByRole("textbox", { name: "补充本次目标" })).toBeNull();
  expect(actions).toEqual([]); expect(messages).toHaveLength(1);
  expect(props.onPropose).not.toHaveBeenCalled(); expect(props.openApproval).not.toHaveBeenCalled();
});
it.each(["unknown_template", "__proto__", "constructor"])("uses safe generic guidance for unknown FORM_REQUIRED template %s without an empty slot prompt", async (template) => {
  current = { ...slots(), kind: "FORM_REQUIRED", required_fields: [], candidates: [], prompt_template_id: template };
  await begin("办理未知事项");
  expect(screen.getByText(/对应业务页面补充/)).toBeInTheDocument();
  expect(screen.queryByText(/您可以在下方补充/)).toBeNull();
  expect(screen.queryByRole("textbox", { name: "补充本次目标" })).toBeNull();
  expect(actions).toEqual([]); expect(messages).toHaveLength(1);
});
it("deduplicates the same clarification id across stream and current G21 while retaining other history", async () => {
  streamCards = [
    { id: "stream_current", type: "CLARIFICATION", source_ref: "src_current", data: slots(), actions: [] },
    { id: "stream_old", type: "CLARIFICATION", source_ref: "src_history", data: { ...slots(), id: "clr_history" }, actions: [] },
    { id: "stream_old_duplicate", type: "CLARIFICATION", source_ref: "src_duplicate", data: { ...slots(), id: "clr_history" }, actions: [] },
    { id: "account", type: "ACCOUNT", source_ref: "src_account", data: { accounts: [{ id: "acc_ls_chk", display_name: "活期", available_cents: 1000000 }] }, actions: [] },
  ];
  await begin();
  expect(screen.queryByText("来源 src_current")).toBeNull();
  expect(screen.getByText("来源 src_history")).toBeInTheDocument();
  expect(screen.queryByText("来源 src_duplicate")).toBeNull();
  expect(screen.getByText("来源 src_account")).toBeInTheDocument();
  expect(screen.getByText("已读取本次请求。")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "稳盈短债" })).toHaveLength(1);
});
