import type {
  Envelope,
  StreamEvent,
  Fields,
  Decision,
  PlanView,
  PlanStatus,
  CardNumberDetail,
} from "./types";
import { uid } from "./types";
import { previewRequest, previewStream } from "./preview";
export const isPreview = import.meta.env.VITE_BANK_MODE !== "live";
export class BankError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
    public details: Fields = {},
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  if (isPreview) return previewRequest(path, body) as Promise<T>;
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-SafeBank-Client": "workbench",
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new BankError(
      data.error?.code ?? "HTTP_ERROR",
      data.error?.message || "服务暂时不可用，请稍后重试",
      response.status,
      data.error?.details ?? {},
    );
  return data as T;
}
export async function read<T>(
  sid: string,
  tool: string,
  args: Fields = {},
): Promise<T> {
  return (
    await request<Envelope<T>>(`/tools/read/${tool}`, { session_id: sid, args })
  ).data;
}
export async function propose(
  sid: string,
  action_kind: string,
  fields: Fields,
): Promise<Decision> {
  const action = await request<{ action_id: string }>(
    `/sessions/${sid}/actions`,
    {
      request_id: uid(),
      kind: "FORM",
      action_kind,
      fields,
      parent_utterance_id: null,
    },
  );
  return request("/plans/evaluate", {
    request_id: uid(),
    session_id: sid,
    form_action_ids: [action.action_id],
  });
}
export const getView = async (id: string): Promise<PlanView> => {
  const view = await request<PlanView>(`/approvals/${encodeURIComponent(id)}`);
  // G9 exposes the current ApprovalStatus; the UI shares its pending state
  // with preview plans.
  return view.status === "PENDING"
    ? { ...view, status: "PENDING_APPROVAL" }
    : view;
};
export const getPlan = (id: string) =>
  request<PlanStatus>(`/plans/${encodeURIComponent(id)}`);
export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<StreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  function decode(block: string): StreamEvent | null {
    let event = "message";
    const lines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
    }
    if (!lines.length) return null;
    return { event, data: JSON.parse(lines.join("\n")) };
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let at;
      while ((at = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, at);
        buffer = buffer.slice(at + 2);
        const item = decode(block);
        if (item) yield item;
      }
      if (done) {
        if (buffer.trim()) {
          const item = decode(buffer);
          if (item) yield item;
        }
        break;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export async function stream(
  sid: string,
  body: Fields,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
  resume = false,
) {
  if (isPreview) {
    for await (const e of previewStream(sid, body, resume)) {
      if (signal?.aborted) return;
      onEvent(e);
    }
    return;
  }
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sid)}/${resume ? "resume" : "messages"}`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-SafeBank-Client": "workbench",
      },
      body: JSON.stringify(body),
      signal,
    },
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new BankError(
      data.error?.code ?? "STREAM_ERROR",
      data.error?.message || "连接中断，请检查任务状态",
      response.status,
    );
  }
  if (!response.body) throw new BankError("STREAM_ERROR", "未收到响应");
  for await (const event of parseSSE(response.body)) onEvent(event);
}

/** Full PAN is deliberately separate from get_cards and never sent to L1. */
export async function getCardNumber(
  sessionId: string, cardId: string, pin: string,
): Promise<CardNumberDetail> {
  if (!isPreview) throw new BankError(
    "CARD_NUMBER_UNAVAILABLE", "当前银行接口暂不支持验密查看完整卡号。",
  );
  return request<CardNumberDetail>(`/cards/${encodeURIComponent(cardId)}/number`, {
    session_id: sessionId, pin,
  });
}

/** F13 demo console: the operator signs in once; the secret stays in the BFF. */
export async function consoleRequest<T>(
  path: string,
  body?: unknown,
): Promise<T> {
  if (isPreview) throw new BankError("CONSOLE_UNAVAILABLE", "合成预览无演示控制台");
  return request<T>(`/console${path}`, body);
}
export const consoleStatus = () =>
  consoleRequest<{ signed_in: boolean }>("/status");
export const consoleLogin = (secret: string) =>
  consoleRequest<{ signed_in: boolean }>("/login", { secret });
export const demoSms = (approvalId: string) =>
  consoleRequest<{ approval_id: string; code: string; expires_at_wall: string }>(
    `/sms?approval_id=${encodeURIComponent(approvalId)}`,
  );
