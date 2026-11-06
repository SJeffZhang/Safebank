import http from "node:http";
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve, extname } from "node:path";
import { pathToFileURL } from "node:url";
const readTools = new Set([
  "get_accounts",
  "list_payees",
  "get_transactions",
  "get_spending_summary",
  "detect_anomalies",
  "get_cards",
  "list_scheduled",
  "get_split_status",
  "detect_subscriptions",
  "list_subscriptions",
  "upcoming_charges",
  "get_risk_questionnaire",
  "get_risk_profile",
  "list_products",
  "compare_products",
  "get_holdings",
  "get_product",
  "get_profile",
]);
const idPattern = "[A-Za-z0-9_-]+";
const routes = [
  ["POST", new RegExp(`^/sessions/${idPattern}/actions$`)],
  ["GET", new RegExp(`^/sessions/${idPattern}/clarifications/current$`)],
  ["GET", new RegExp(`^/sessions/${idPattern}/hints/${idPattern}$`)],
  ["POST", /^\/plans\/evaluate$/],
  ["GET", new RegExp(`^/plans/${idPattern}$`)],
  ["POST", new RegExp(`^/plans/${idPattern}/execute$`)],
  ["GET", /^\/approvals$/],
  ["GET", new RegExp(`^/approvals/${idPattern}$`)],
  ["POST", new RegExp(`^/approvals/${idPattern}/(confirm|reject|otp)$`)],
  ["GET", /^\/audit$/],
  ["GET", new RegExp(`^/plans/${idPattern}/audit$`)],
  ["GET", /^\/notifications$/],
  ["POST", new RegExp(`^/notifications/${idPattern}/read$`)],
];
// Gateway error bodies carry a stable code and, at most, a developer-facing
// English message. The customer sees this readable text instead; the code and
// details are passed through unchanged for the UI and for diagnosis.
const ERROR_TEXT = {
  CONTRACT_INVALID: "请求内容不完整或格式不正确，请检查后重试",
  UNAUTHENTICATED: "登录已失效，请重新登录",
  TOKEN_EXPIRED: "登录已过期，请重新登录",
  FORBIDDEN: "当前账户无权执行此操作",
  ORIGIN_NOT_ALLOWED: "此操作需要在 App 内由您本人完成",
  NOT_FOUND: "未找到相关记录，可能已失效",
  OWNERSHIP_MISMATCH: "未找到相关记录，可能已失效",
  TOOL_NOT_ALLOWED: "此操作当前不可用",
  INTENT_UNCLEAR: "没能确认您的具体意图，请补充说明",
  INTENT_SCOPE_EXCEEDED: "计划超出了您本次说明的范围，请重新说明",
  UTTERANCE_EXPIRED: "这条指令已过期，请重新说一遍",
  AUTHORIZATION_CONSUMED: "这条指令已被使用，请重新发起",
  AUTHORIZATION_CANCELLED: "这条指令已被取消或替换，请重新发起",
  STATE_CHANGED: "账户状态刚刚发生变化，请查看最新计划后再确认",
  POLICY_CHANGED: "安全策略已更新，请重新发起",
  APPROVAL_EXPIRED: "确认已超时，请重新发起",
  APPROVAL_REJECTED: "该计划已被拒绝",
  APPROVAL_SUPERSEDED: "该确认已被新的计划替代，请查看最新计划",
  APPROVAL_USED: "该计划已确认过，无需重复操作",
  APPROVAL_BINDING_MISMATCH: "确认信息与计划不一致，请刷新后重试",
  VIEW_HASH_MISMATCH: "计划内容已更新，请刷新确认面板后再确认",
  RISK_ACK_REQUIRED: "请先阅读并确认风险提示",
  OTP_INVALID: "验证码不正确，请重新输入",
  OTP_EXPIRED: "验证码已过期，请重新获取",
  OTP_LOCKED: "验证码错误次数过多，请稍后重新发起",
  RATE_LIMITED: "操作过于频繁，请稍后再试",
  IDEMPOTENCY_CONFLICT: "重复的请求内容不一致，请刷新后重试",
  EXECUTION_IN_PROGRESS: "该计划正在处理中，请稍后查看结果",
  EXECUTION_CANCELLED: "该计划已取消",
  EXECUTION_DEADLINE_EXPIRED: "执行已超时，请查询计划状态",
  ASSESSMENT_MISSING: "请先完成风险测评",
  SUITABILITY_MISMATCH: "该产品风险等级高于您的测评结果",
  DEPENDENCY_UNAVAILABLE: "银行服务暂不可用，请稍后重试",
};
const CJK = /[\u4e00-\u9fff]/;
function readableMessage(code, message) {
  if (typeof message === "string" && CJK.test(message)) return message;
  return ERROR_TEXT[code] ?? "请求未能完成，请稍后重试";
}
export function createBff({
  gatewayUrl = process.env.GATEWAY_URL ?? "http://127.0.0.1:8200",
  agentUrl = process.env.AGENT_URL ?? "http://127.0.0.1:8100",
  serviceToken = process.env.FRONTEND_SERVICE_TOKEN,
  publicOrigin = process.env.PUBLIC_ORIGIN ?? "http://127.0.0.1:5173",
  secure = process.env.COOKIE_SECURE === "true",
  dist = resolve("dist"),
  fetcher = fetch,
} = {}) {
  const sessions = new Map();
  const clean = (value) => {
    if (typeof value === "string")
      return serviceToken && value.includes(serviceToken)
        ? "服务暂不可用"
        : value;
    if (Array.isArray(value)) return value.map(clean);
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.entries(value)
          .filter(
            ([k]) =>
              !["app_token", "assistant_token", "pin", "otp"].includes(k),
          )
          .map(([k, v]) => [k, clean(v)]),
      );
    return value;
  };
  function fail(status, code, message, details = {}) {
    return Object.assign(new Error(message), { status, code, details });
  }
  function json(res, status, data) {
    if (res.destroyed) return;
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(JSON.stringify(clean(data)));
  }
  async function body(req) {
    let bytes = 0;
    const chunks = [];
    for await (const chunk of req) {
      bytes += chunk.length;
      if (bytes > 65536) throw fail(413, "BODY_TOO_LARGE", "请求内容过大");
      chunks.push(chunk);
    }
    try {
      return JSON.parse(Buffer.concat(chunks).toString() || "{}");
    } catch {
      throw fail(400, "INVALID_JSON", "请求格式错误");
    }
  }
  async function upstream(
    path,
    { method = "GET", data, session, assistant = false, signal } = {},
  ) {
    if (!serviceToken)
      throw fail(
        503,
        "BFF_NOT_CONFIGURED",
        "银行连接尚未配置，请联系演示管理员",
      );
    const headers = { "Content-Type": "application/json" };
    if (!assistant) headers["X-Service-Token"] = serviceToken;
    const token = assistant ? session?.token : session?.appToken;
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetcher(
      `${assistant ? agentUrl : gatewayUrl}${path}`,
      {
        method,
        headers,
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
        redirect: "error",
        signal: signal ?? AbortSignal.timeout(120000),
      },
    );
    return response;
  }
  async function call(path, opts) {
    const r = await upstream(path, opts);
    const data = await r.json().catch(() => ({
      error: { code: "UPSTREAM_ERROR", message: "服务返回了无法读取的响应" },
    }));
    if (!r.ok)
      throw fail(
        r.status,
        data.error?.code ?? "UPSTREAM_ERROR",
        readableMessage(data.error?.code, data.error?.message),
        data.error?.details ?? {},
      );
    return { data, status: r.status };
  }
  // F13 demo console (PRD §9.6): the operator signs in once with the console
  // secret; it lives only in this BFF process memory behind a separate
  // sb_console cookie, never in env files or browser storage.
  const consoleSessions = new Map();
  async function consoleCall(cs, path, { method = "GET", data } = {}) {
    const r = await fetcher(`${gatewayUrl}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Service-Token": cs.secret,
        Authorization: `Bearer ${cs.token}`,
      },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      redirect: "error",
      signal: AbortSignal.timeout(120000),
    });
    const out = await r.json().catch(() => ({
      error: { code: "UPSTREAM_ERROR", message: "服务返回了无法读取的响应" },
    }));
    if (!r.ok)
      throw fail(
        r.status,
        out.error?.code ?? "UPSTREAM_ERROR",
        readableMessage(out.error?.code, out.error?.message),
        out.error?.details ?? {},
      );
    return { data: out, status: r.status };
  }
  const consoleRoutes = [
    ["POST", /^\/console\/clock\/advance$/],
    ["GET", new RegExp(`^/console/clock/advances/${idPattern}$`)],
    ["POST", new RegExp(`^/console/attack/cases/${idPattern}/start$`)],
    ["POST", /^\/console\/attack\/submit_plan$/],
    ["GET", new RegExp(`^/console/attack/plans/${idPattern}$`)],
    ["POST", /^\/console\/eval\/assert$/],
    ["GET", new RegExp(`^/console/plans/${idPattern}/comparison$`)],
  ];
  function owns(session, sid) {
    const s = session.threads.get(sid);
    if (!s) throw fail(404, "NOT_FOUND", "会话不存在");
    return s;
  }
  function assistant(thread, token) {
    if (
      !token ||
      !Number.isFinite(Date.parse(token.expires)) ||
      Date.parse(token.expires) <= Date.now()
    )
      throw fail(401, "AUTH_EXPIRED", "授权已过期，请重新发起");
    return { token: token.token };
  }
  async function pipe(res, req, session, sid, token, data) {
    const thread = owns(session, sid);

    const abort = new AbortController();
    const disconnected = () => {
      if (!res.writableEnded) abort.abort();
    };
    res.on("close", disconnected);
    try {
      const r = await upstream(
        `/threads/${encodeURIComponent(sid)}/runs/stream`,
        {
          method: "POST",
          data,
          session: assistant(thread, token),
          assistant: true,
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(180000)]),
        },
      );
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw fail(
          r.status,
          e.error?.code ?? "AGENT_ERROR",
          e.error?.message ?? "助手服务暂不可用",
        );
      }
      if (!r.body) throw fail(502, "EMPTY_STREAM", "助手未返回响应");
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-store",
        "X-Accel-Buffering": "no",
      });
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let interrupts = null;
      const emit = (event, payload) =>
        res.write(
          `event: ${event}\ndata: ${JSON.stringify(clean(payload))}\n\n`,
        );
      const consume = (block) => {
        const lines = block.split("\n");
        const event =
          lines
            .find((l) => l.startsWith("event:"))
            ?.slice(6)
            .trim() ?? "message";
        const text = lines
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (!text) return;
        const parsed = JSON.parse(text);
        if (event === "updates") {
          if (Array.isArray(parsed.__interrupt__)) {
            const valid = parsed.__interrupt__.filter((item) =>
              /^apv_[\w-]+$/.test(item.value?.approval_id ?? ""),
            );
            for (const item of valid)
              thread.pending.set(item.value.approval_id, token);
            interrupts = { __interrupt__: valid };
          }
          if (parsed.progress) emit("updates", { progress: parsed.progress });
          if (parsed.reply && typeof parsed.reply.content === "string")
            emit("updates", { reply: parsed.reply });
        } else if (event === "messages" && Array.isArray(parsed)) {
          // Compatibility with current services/agent/app/main.py. Never forward raw values.
          for (const message of parsed)
            if (
              message.type === "ai" &&
              typeof message.content === "string" &&
              message.content
            )
              emit("updates", {
                reply: {
                  id: message.id ?? `msg_${randomBytes(10).toString("hex")}`,
                  type: "ai",
                  content: message.content,
                },
              });
        } else if (
          event === "custom" &&
          parsed.type === "cards" &&
          Array.isArray(parsed.cards)
        )
          emit("custom", parsed);
        else if (event === "metadata") emit("metadata", parsed);
        else if (event === "error")
          emit("error", {
            error: parsed.error ?? parsed.type ?? "AGENT_ERROR",
            message: parsed.message ?? "助手处理失败",
          });
      };
      try {
        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          buffer = buffer.replace(/\r\n/g, "\n");
          let split;
          while ((split = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);
            consume(block);
          }
          if (buffer.length > 1000000)
            throw fail(502, "STREAM_TOO_LARGE", "助手响应过大");
          if (done) {
            if (buffer.trim()) consume(buffer);
            break;
          }
        }
        if (interrupts) emit("updates", interrupts);
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      res.end();
    } finally {
      res.off("close", disconnected);
    }
  }
  const server = http.createServer(async (req, res) => {
    try {
      res.setHeader("X-Frame-Options", "DENY");
      res.setHeader("Referrer-Policy", "same-origin");
      res.setHeader("X-Content-Type-Options", "nosniff");
      res.setHeader(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
      );
      const url = new URL(req.url, "http://bff.local");
      if (url.pathname === "/health" && req.method === "GET") {
        json(res, 200, { service: "frontend", status: "ok" });
        return;
      }
      if (!url.pathname.startsWith("/api/")) {
        if (req.method !== "GET")
          throw fail(405, "METHOD_NOT_ALLOWED", "不支持此请求");
        const pathname = decodeURIComponent(url.pathname);
        const file = resolve(dist, `.${pathname}`);
        if (file !== dist && !file.startsWith(`${dist}/`))
          throw fail(404, "NOT_FOUND", "页面不存在");
        let content;
        let actual = file;
        try {
          content = await readFile(file);
        } catch {
          if (extname(pathname)) throw fail(404, "NOT_FOUND", "文件不存在");
          actual = resolve(dist, "index.html");
          content = await readFile(actual);
        }
        const mime = {
          ".html": "text/html; charset=utf-8",
          ".js": "text/javascript",
          ".css": "text/css",
          ".svg": "image/svg+xml",
          ".png": "image/png",
          ".webp": "image/webp",
          ".woff2": "font/woff2",
        };
        res.writeHead(200, {
          "Content-Type": mime[extname(actual)] ?? "application/octet-stream",
          "Cache-Control":
            extname(actual) === ".html" ? "no-cache" : "public, max-age=3600",
        });
        res.end(content);
        return;
      }
      const path = url.pathname.slice(4);
      const method = req.method ?? "GET";
      if (method !== "GET" && method !== "POST")
        throw fail(405, "METHOD_NOT_ALLOWED", "不支持此请求");
      // Same-origin browser writes require a non-simple custom header. No CORS allowance.
      if (
        method === "POST" &&
        (req.headers["x-safebank-client"] !== "workbench" ||
          req.headers.origin !== publicOrigin)
      )
        throw fail(403, "CSRF_REJECTED", "请求来源校验失败");
      for (const [key, s] of sessions)
        if (s.expires <= Date.now()) sessions.delete(key);
      const sessionId = req.headers.cookie
        ?.split(";")
        .map((c) => c.trim())
        .find((c) => c.startsWith("sb_app="))
        ?.slice(7);
      const session = sessionId ? sessions.get(sessionId) : undefined;
      if (path === "/demo/customers" && method === "GET") {
        const r = await call("/auth/demo-customers");
        json(res, r.status, r.data);
        return;
      }
      if (path === "/auth/login" && method === "POST") {
        const b = await body(req);
        const r = await call("/auth/login", {
          method,
          data: { customer_id: b.customer_id, pin: b.pin },
        });
        if (session) {
          sessions.delete(sessionId);
        }
        const key = randomBytes(32).toString("hex");
        const expires = Date.parse(r.data.expires_at_wall);
        if (
          !r.data.app_token ||
          !Number.isFinite(expires) ||
          expires <= Date.now()
        )
          throw fail(502, "LOGIN_FAILED", "登录响应无效");
        sessions.set(key, {
          appToken: r.data.app_token,
          expires,
          customer: r.data.customer,
          threads: new Map(),
        });
        res.setHeader(
          "Set-Cookie",
          `sb_app=${key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${Math.floor((expires - Date.now()) / 1000)}${secure ? "; Secure" : ""}`,
        );
        json(res, 200, {
          customer: r.data.customer,
          expires_at_wall: r.data.expires_at_wall,
        });
        return;
      }
      for (const [key, cs] of consoleSessions)
        if (cs.expires <= Date.now()) consoleSessions.delete(key);
      const consoleId = req.headers.cookie
        ?.split(";")
        .map((c) => c.trim())
        .find((c) => c.startsWith("sb_console="))
        ?.slice(11);
      const consoleSession = consoleId ? consoleSessions.get(consoleId) : undefined;
      if (path === "/console/status" && method === "GET") {
        json(res, 200, { signed_in: Boolean(consoleSession) });
        return;
      }
      if (path === "/console/login" && method === "POST") {
        const b = await body(req);
        const secret = typeof b.secret === "string" ? b.secret : "";
        if (!secret) throw fail(400, "BAD_REQUEST", "请输入控制台口令");
        const r = await fetcher(`${gatewayUrl}/demo/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Service-Token": secret },
          body: JSON.stringify({ secret }),
          redirect: "error",
          signal: AbortSignal.timeout(30000),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.token) throw fail(401, "UNAUTHENTICATED", "控制台口令错误");
        const expires = Date.parse(d.expires_at_wall);
        const key = randomBytes(32).toString("hex");
        consoleSessions.set(key, { secret, token: d.token, expires });
        res.setHeader(
          "Set-Cookie",
          `sb_console=${key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${Math.max(0, Math.floor((expires - Date.now()) / 1000))}${secure ? "; Secure" : ""}`,
        );
        json(res, 200, { signed_in: true, expires_at_wall: d.expires_at_wall });
        return;
      }
      if (path.startsWith("/console/")) {
        const known =
          (path === "/console/sms" && method === "GET") ||
          consoleRoutes.some(([m, re]) => m === method && re.test(path));
        if (!known) throw fail(404, "NOT_FOUND", "接口不存在");
        if (!consoleSession) throw fail(401, "CONSOLE_LOGIN_REQUIRED", "请先登录演示控制台");
        if (path === "/console/sms" && method === "GET") {
          if (!session) throw fail(401, "LOGIN_REQUIRED", "登录已失效，请重新登录");
          const approvalId = url.searchParams.get("approval_id") ?? "";
          if (!new RegExp(`^${idPattern}$`).test(approvalId))
            throw fail(400, "BAD_REQUEST", "缺少审批编号");
          // Only reveal the demo SMS for an approval this signed-in customer can see.
          await call(`/approvals/${approvalId}`, { session });
          const r = await consoleCall(
            consoleSession,
            `/demo/sms?approval_id=${encodeURIComponent(approvalId)}`,
          );
          json(res, r.status, r.data);
          return;
        }
        if (consoleRoutes.some(([m, re]) => m === method && re.test(path))) {
          const data = method === "POST" ? await body(req) : undefined;
          const r = await consoleCall(
            consoleSession,
            `/demo${path.slice("/console".length)}`,
            { method, data },
          );
          json(res, r.status, r.data);
          return;
        }
        throw fail(404, "NOT_FOUND", "接口不存在");
      }
      if (!session) throw fail(401, "LOGIN_REQUIRED", "登录已失效，请重新登录");
      if (path === "/auth/logout" && method === "POST") {
        const r = await call("/auth/logout", {
          method,
          data: await body(req),
          session,
        });
        sessions.delete(sessionId);
        res.setHeader(
          "Set-Cookie",
          `sb_app=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0${secure ? "; Secure" : ""}`,
        );
        json(res, r.status, r.data);
        return;
      }
      if (path === "/sessions" && method === "POST") {
        const r = await call("/sessions", {
          method,
          data: await body(req),
          session,
        });
        session.threads.set(r.data.session_id, {
          pending: new Map(),
          latest: null,
          running: false,
        });
        json(res, r.status, r.data);
        return;
      }
      const threadMatch = path.match(
        /^\/sessions\/([\w-]+)\/(messages|resume|state)$/,
      );
      if (threadMatch) {
        const [, sid, operation] = threadMatch;
        const thread = owns(session, sid);
        if (operation === "state" && method === "GET") {
          const r = await call(`/sessions/${sid}/assistant-token`, {
            method: "POST",
            data: { request_id: `req_${randomBytes(12).toString("hex")}` },
            session,
          });
          let state;
          try {
            state = await call(`/threads/${sid}/state`, {
              assistant: true,
              session: { token: r.data.assistant_token },
            });
          } catch (e) {
            // A thread that has never run has no checkpoint yet: show an empty chat.
            if (e.status !== 404) throw e;
            json(res, 200, { values: { messages: [] }, tasks: [] });
            return;
          }
          const v = state.data.values ?? {};
          const messages = Array.isArray(v.messages)
            ? v.messages
            : (v.utterances ?? []).map((u) => ({
                id: u.id,
                type: "human",
                content: u.text,
              }));
          if (!Array.isArray(v.messages) && v.reply_draft)
            messages.push({
              id: `msg_restored_${sid}`,
              type: "ai",
              content: v.reply_draft,
            });
          json(res, state.status, {
            values: { messages },
            tasks: state.data.tasks ?? [],
          });
          return;
        }
        if (method !== "POST")
          throw fail(405, "METHOD_NOT_ALLOWED", "不支持此请求");
        const b = await body(req);
        if (thread.running)
          throw fail(409, "THREAD_BUSY", "小安正在处理该会话，请稍候");
        thread.running = true;
        try {
          if (operation === "messages") {
            const r = await call(`/sessions/${sid}/utterances`, {
              method,
              data: b,
              session,
            });
            const token = {
              token: r.data.assistant_token,
              expires: r.data.expires_at_wall,
            };
            thread.latest = token;
            await pipe(res, req, session, sid, token, {
              input: {
                utterances: [
                  {
                    id: r.data.utterance.id,
                    kind: r.data.utterance.kind,
                    text: r.data.utterance.text,
                  },
                ],
              },
              command: null,
            });
            return;
          }
          if (operation === "resume") {
            if (!/^apv_[\w-]+$/.test(b.approval_id ?? ""))
              throw fail(422, "INVALID_APPROVAL", "审批编号无效");
            const view = await call(`/approvals/${b.approval_id}`, { session });
            if (view.data.session_id !== sid)
              throw fail(403, "APPROVAL_MISMATCH", "审批不属于此会话");
            const token = thread.pending.get(b.approval_id);
            assistant(thread, token);
            const state = await call(`/threads/${sid}/state`, {
              assistant: true,
              session: { token: token.token },
            });
            const pending = (state.data.tasks ?? [])
              .flatMap((t) => t.interrupts ?? [])
              .some((i) => i.value?.approval_id === b.approval_id);
            if (!pending)
              throw fail(409, "NOT_WAITING", "此会话已不再等待该审批");
            await pipe(res, req, session, sid, token, {
              input: null,
              command: { resume: b.approval_id },
            });
            thread.pending.delete(b.approval_id);
            return;
          }
          throw fail(404, "NOT_FOUND", "接口不存在");
        } finally {
          thread.running = false;
        }
      }
      const tool = path.match(/^\/tools\/read\/([a-z_]+)$/);
      if (tool && method === "POST" && readTools.has(tool[1])) {
        const b = await body(req);
        owns(session, b.session_id);
        const r = await call(path, { method, data: b, session });
        json(res, r.status, r.data);
        return;
      }
      if (!routes.some(([m, re]) => m === method && re.test(path)))
        throw fail(404, "NOT_FOUND", "接口不存在");
      if (path.startsWith("/sessions/")) owns(session, path.split("/")[2]);
      const data = method === "POST" ? await body(req) : undefined;
      if (path === "/plans/evaluate") owns(session, data.session_id);
      const r = await call(`${path}${url.search}`, { method, data, session });
      json(res, r.status, r.data);
    } catch (e) {
      if (res.headersSent) {
        if (!res.destroyed)
          res.end(
            `event: error\ndata: ${JSON.stringify({ error: "CONNECTION_INTERRUPTED", message: "连接中断，请在安全中心查询原计划状态" })}\n\n`,
          );
      } else
        json(res, e.status ?? 503, {
          error: {
            code: e.code ?? "DEPENDENCY_UNAVAILABLE",
            message: e.status ? e.message : "银行服务暂不可用，请稍后重试",
            details: e.details ?? {},
          },
        });
    }
  });
  return server;
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  const port = Number(process.env.PORT ?? 3001);
  const host = process.env.HOST ?? "127.0.0.1";
  createBff().listen(port, host, () =>
    console.log(`BankSense BFF listening on http://${host}:${port}`),
  );
}
