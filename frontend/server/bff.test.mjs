import { test } from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import { createBff } from "./bff.mjs";
async function setup(t, upstream) {
  const calls = [];
  const server = createBff({
    serviceToken: "synthetic-server-service-token",
    publicOrigin: "http://localhost:5173",
    fetcher: async (url, options) => {
      calls.push({ url, options });
      return upstream(url, options);
    },
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  let cookie = "";
  return {
    calls,
    async req(path, data, headers = {}) {
      const response = await fetch(`${base}/api${path}`, {
        method: data === undefined ? "GET" : "POST",
        headers: {
          Origin: "http://localhost:5173",
          "X-SafeBank-Client": "workbench",
          "Content-Type": "application/json",
          Cookie: cookie,
          ...headers,
        },
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      });
      if (response.headers.get("set-cookie"))
        cookie = response.headers.get("set-cookie").split(";")[0];
      return response;
    },
  };
}
const json = (v, status = 200) =>
  new Response(JSON.stringify(v), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const login = () =>
  json({
    app_token: "synthetic-app-jwt",
    expires_at_wall: new Date(Date.now() + 600000).toISOString(),
    customer: { customer_id: "cus_test", display_name: "测试客户" },
  });
test("cookie stores an opaque session; tokens never enter login JSON; client auth is ignored", async (t) => {
  const s = await setup(t, (url) =>
    url.endsWith("/auth/login") ? login() : json({ items: [] }),
  );
  const r = await s.req("/auth/login", {
    customer_id: "cus_test",
    pin: "123456",
  });
  assert.match(r.headers.get("set-cookie"), /HttpOnly; SameSite=Strict/);
  assert.doesNotMatch(r.headers.get("set-cookie"), /synthetic-app/);
  const data = await r.text();
  assert.doesNotMatch(data, /app_token|synthetic-app-jwt|pin/);
  await s.req("/approvals", undefined, {
    Authorization: "Bearer attacker-value",
  });
  assert.equal(
    s.calls.at(-1).options.headers.Authorization,
    "Bearer synthetic-app-jwt",
  );
  assert.equal(
    s.calls.at(-1).options.headers["X-Service-Token"],
    "synthetic-server-service-token",
  );
});
test("cross-origin writes rejected before any upstream call", async (t) => {
  const s = await setup(t, () => login());
  const r = await s.req(
    "/auth/login",
    { pin: "123456" },
    { Origin: "https://external.invalid" },
  );
  assert.equal(r.status, 403);
  assert.equal(s.calls.length, 0);
});
test("same-origin missing custom CSRF header rejected", async (t) => {
  const s = await setup(t, () => login());
  const r = await s.req(
    "/auth/login",
    { pin: "123456" },
    { "X-SafeBank-Client": "" },
  );
  assert.equal(r.status, 403);
  assert.equal(s.calls.length, 0);
});
test("arbitrary proxy and internal endpoints fail closed", async (t) => {
  const s = await setup(t, () => login());
  await s.req("/auth/login", {});
  for (const path of [
    "/internal/snapshot",
    "/tools/write/transfer",
    "/console/otp",
    "/auth/demo-customers",
    "/sessions/other/actions",
  ]) {
    const r = await s.req(path, {});
    assert.equal(r.status, 404, path);
  }
  assert.equal(s.calls.length, 1);
});
test("read tools require a session created in this browser login", async (t) => {
  const s = await setup(t, (url) =>
    url.endsWith("/auth/login")
      ? login()
      : url.endsWith("/sessions")
        ? json({ session_id: "ses_owned" }, 201)
        : json({ data: { accounts: [] } }),
  );
  await s.req("/auth/login", {});
  let r = await s.req("/tools/read/get_accounts", {
    session_id: "ses_foreign",
    args: { history_days: 0 },
  });
  assert.equal(r.status, 404);
  await s.req("/sessions", { request_id: "req_one" });
  r = await s.req("/tools/read/get_accounts", {
    session_id: "ses_owned",
    args: { history_days: 0 },
  });
  assert.equal(r.status, 200);
  assert.match(s.calls.at(-1).url, /\/tools\/read\/get_accounts$/);
});
test("gateway errors and status pass through while service tokens are redacted", async (t) => {
  const s = await setup(t, (url) =>
    url.endsWith("/auth/login")
      ? login()
      : json(
          {
            error: {
              code: "STATE_CHANGED",
              message: "synthetic-server-service-token",
              details: { decision: { decision: "REPLAN" } },
            },
          },
          409,
        ),
  );
  await s.req("/auth/login", {});
  const r = await s.req("/plans/pln_test/execute", { request_id: "req_x" });
  assert.equal(r.status, 409);
  const data = await r.json();
  assert.equal(data.error.code, "STATE_CHANGED");
  assert.equal(data.error.details.decision.decision, "REPLAN");
  assert.doesNotMatch(JSON.stringify(data), /synthetic-server-service-token/);
});
test("resume cannot use a later or unbound assistant token", async (t) => {
  const s = await setup(t, (url) =>
    url.endsWith("/auth/login")
      ? login()
      : url.endsWith("/sessions")
        ? json({ session_id: "ses_owned" }, 201)
        : json({ session_id: "ses_owned", plan_id: "pln_test" }),
  );
  await s.req("/auth/login", {});
  await s.req("/sessions", {});
  const r = await s.req("/sessions/ses_owned/resume", {
    approval_id: "apv_test",
  });
  assert.equal(r.status, 401);
  assert.equal(s.calls.filter((c) => c.url.includes("/runs/stream")).length, 0);
});
test("SSE interrupt binds original token; confirm and OTP stay on gateway; resume uses bound token", async (t) => {
  let interrupted = false;
  const s = await setup(t, (url, options) => {
    if (url.endsWith("/auth/login")) return login();
    if (url.endsWith("/sessions"))
      return json({ session_id: "ses_owned" }, 201);
    if (url.endsWith("/utterances"))
      return json(
        {
          utterance: { id: "utt_one", kind: "typed", text: "已登记原话" },
          assistant_token: "original-assistant",
          expires_at_wall: new Date(Date.now() + 600000).toISOString(),
        },
        201,
      );
    if (url.endsWith("/runs/stream")) {
      assert.equal(options.headers.Authorization, "Bearer original-assistant");
      const b = JSON.parse(options.body);
      if (!interrupted) {
        assert.deepEqual(b.input, {
          utterances: [{ id: "utt_one", kind: "typed", text: "已登记原话" }],
        });
        interrupted = true;
        return new Response(
          'event: updates\ndata: {"__interrupt__":[{"value":{"approval_id":"apv_test"}}]}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        );
      }
      assert.deepEqual(b.command, { resume: "apv_test" });
      return new Response(
        'event: updates\ndata: {"reply":{"content":"done"}}\n\n',
      );
    }
    if (url.endsWith("/state"))
      return json({
        tasks: [{ interrupts: [{ value: { approval_id: "apv_test" } }] }],
      });
    if (url.endsWith("/approvals/apv_test"))
      return json({ session_id: "ses_owned", plan_id: "pln_test" });
    return json({ status: "CONFIRMED" });
  });
  await s.req("/auth/login", {});
  await s.req("/sessions", {});
  await (
    await s.req("/sessions/ses_owned/messages", {
      request_id: "req_1",
      kind: "typed",
      text: "测试",
    })
  ).text();
  await s.req("/approvals/apv_test/otp", {
    view_hash: "hash",
    request_id: "req_2",
  });
  await s.req("/approvals/apv_test/confirm", {
    view_hash: "hash",
    request_id: "req_3",
    otp: "123456",
    risk_ack: false,
  });
  const r = await s.req("/sessions/ses_owned/resume", {
    approval_id: "apv_test",
  });
  assert.equal(r.status, 200);
  assert.match(await r.text(), /done/);
  const agentCalls = s.calls.filter((c) =>
    c.url.startsWith("http://127.0.0.1:8100"),
  );
  assert.equal(
    agentCalls.some((c) => String(c.options.body).includes("123456")),
    false,
  );
  assert.equal(
    agentCalls.some((c) => "X-Service-Token" in c.options.headers),
    false,
  );
});

test("current L1 messages normalize to reply, raw values are filtered and interrupt is last", async (t) => {
  const s = await setup(t, (url) => {
    if (url.endsWith("/auth/login")) return login();
    if (url.endsWith("/sessions")) return json({ session_id: "ses_wire" }, 201);
    if (url.endsWith("/utterances"))
      return json({
        utterance: { id: "utt_one", kind: "typed", text: "正式原话" },
        assistant_token: "synthetic-assistant",
        expires_at_wall: new Date(Date.now() + 600000).toISOString(),
      });
    return new Response(
      'event: updates\ndata: {"__interrupt__":[{"value":{"approval_id":"apv_wire"}}]}\n\nevent: values\ndata: {"internal_state":"do-not-forward"}\n\nevent: messages\ndata: [{"type":"ai","content":"已完成预演"}]\n\n',
    );
  });
  await s.req("/auth/login", {});
  await s.req("/sessions", {});
  const text = await (
    await s.req("/sessions/ses_wire/messages", {
      request_id: "req_wire",
      kind: "typed",
      text: "原话",
    })
  ).text();
  assert.doesNotMatch(
    text,
    /internal_state|do-not-forward|event: values|event: messages/,
  );
  assert.match(text, /"reply"/);
  assert.match(text, /已完成预演/);
  assert.ok(text.lastIndexOf("__interrupt__") > text.indexOf('"reply"'));
});

test("one thread cannot register overlapping utterances while its original request is running", async (t) => {
  let entered;
  const started = new Promise((r) => {
    entered = r;
  });
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  const s = await setup(t, async (url) => {
    if (url.endsWith("/auth/login")) return login();
    if (url.endsWith("/sessions"))
      return json({ session_id: "ses_serial" }, 201);
    if (url.endsWith("/utterances")) {
      entered();
      await gate;
      return json({
        utterance: { id: "utt_serial", kind: "typed", text: "第一条" },
        assistant_token: "serial-token",
        expires_at_wall: new Date(Date.now() + 600000).toISOString(),
      });
    }
    return new Response(
      'event: messages\ndata: [{"type":"ai","content":"done"}]\n\n',
    );
  });
  t.after(() => release());
  await s.req("/auth/login", {});
  await s.req("/sessions", {});
  const first = s.req("/sessions/ses_serial/messages", {
    kind: "typed",
    text: "第一条",
  });
  await started;
  const second = await s.req("/sessions/ses_serial/messages", {
    kind: "typed",
    text: "第二条",
  });
  assert.equal(second.status, 409);
  assert.equal(s.calls.filter((c) => c.url.endsWith("/utterances")).length, 1);
  release();
  assert.match(await (await first).text(), /done/);
});
