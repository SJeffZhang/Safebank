import { describe, it, expect, vi } from "vitest";
import { parseSSE } from "./api";
import { parseAmountToCents } from "../services/transfer/money";
describe("new App wire contracts", () => {
  it("parses SSE across UTF-8 chunks, CRLF and EOF without invented end event", async () => {
    const text =
      ': keepalive\r\nevent: updates\r\ndata: {"reply":{"content":"你好"}}\r\n\r\nevent: updates\r\ndata: {"__interrupt__":[{"value":{"approval_id":"apv_one"}}]}';
    const bytes = new TextEncoder().encode(text);
    const result = [];
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (let i = 0; i < bytes.length; i += 3)
          c.enqueue(bytes.slice(i, i + 3));
        c.close();
      },
    });
    for await (const e of parseSSE(body)) result.push(e);
    expect(result).toEqual([
      { event: "updates", data: { reply: { content: "你好" } } },
      {
        event: "updates",
        data: { __interrupt__: [{ value: { approval_id: "apv_one" } }] },
      },
    ]);
  });
  it.each(["1e3", "-1", "0", "1.001", "9007199254740991", "<script>"])(
    "rejects unsafe currency input %s",
    (value) => expect(parseAmountToCents(value)).toHaveProperty("error"),
  );
  it("converts currency without float rounding", () => {
    expect(parseAmountToCents("0.29")).toEqual({ cents: 29 });
    expect(parseAmountToCents("3000.01")).toEqual({ cents: 300001 });
  });
  it("preview cannot execute an unconfirmed plan, and repeated execution does not debit twice", async () => {
    vi.resetModules();
    const { previewRequest: r } = await import("./preview");
    const account = (await r("/tools/read/get_accounts", {
      args: { history_days: 0 },
    })) as { data: { accounts: { id: string; available_cents: number }[] } };
    const source = account.data.accounts[0];
    const action = (await r("/sessions/ses_test/actions", {
      action_kind: "TRANSFER_EXTERNAL",
      fields: {
        from_account_id: source.id,
        payee_id: "pye_ww",
        to_account_id: null,
        amount_cents: 10000,
        memo: "<img src=x onerror=alert(1)>",
      },
    })) as { action_id: string };
    const d = (await r("/plans/evaluate", {
      form_action_ids: [action.action_id],
    })) as { plan_id: string; approval_id: string };
    await expect(r(`/plans/${d.plan_id}/execute`, {})).rejects.toThrow(
      "先确认",
    );
    const view = (await r(`/approvals/${d.approval_id}`)) as {
      view_hash: string;
    };
    await expect(
      r(`/approvals/${d.approval_id}/confirm`, { view_hash: "wrong" }),
    ).rejects.toThrow("面板已变化");
    await r(`/approvals/${d.approval_id}/confirm`, {
      view_hash: view.view_hash,
      risk_ack: false,
      otp: null,
    });
    await r(`/plans/${d.plan_id}/execute`, {});
    await r(`/plans/${d.plan_id}/execute`, {});
    const after = (await r("/tools/read/get_accounts", {
      args: { history_days: 0 },
    })) as typeof account;
    expect(after.data.accounts[0].available_cents).toBe(
      source.available_cents - 10000,
    );
  });
});
