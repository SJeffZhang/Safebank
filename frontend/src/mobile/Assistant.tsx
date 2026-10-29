import { explainCode } from "./copy";
import { useEffect, useRef, useState } from "react";
import { request, stream, isPreview } from "./api";
import {
  uid,
  type UICard,
  type StreamEvent,
  type Clarification,
  type Fields,
} from "./types";
import type { PageProps } from "./Banking";
import { Icon, Button, Alert, Badge } from "./ui";
interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  cards?: UICard[];
}
export function Assistant(
  p: PageProps & {
    openApproval: (id: string) => void;
    onNewSession: () => void;
    onResumeRegister: (handler: (id: string) => Promise<void>) => void;
  },
) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string>();
  const [clar, setClar] = useState<Clarification | null>(null);
  const controller = useRef<AbortController>();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, stage]);
  useEffect(() => {
    setMessages([]);
    setPending(undefined);
    setClar(null);
    if (!isPreview)
      request<{
        values: { messages?: { id: string; type: string; content: string }[] };
        tasks?: { interrupts: { value: { approval_id?: string } }[] }[];
      }>(`/sessions/${p.sid}/state`)
        .then((s) => {
          setMessages(
            (s.values.messages ?? [])
              .filter((m) => typeof m.content === "string")
              .map((m) => ({
                id: m.id,
                role: m.type === "human" ? "user" : "assistant",
                text: m.content,
              })),
          );
          const id = s.tasks
            ?.flatMap((t) => t.interrupts)
            .find((i) => i.value.approval_id)?.value.approval_id;
          if (id) setPending(id);
        })
        .catch(() => setError("历史会话暂不可用，新的原话仍需安全登记。"));
  }, [p.sid]);
  function handle(event: StreamEvent) {
    const data = event.data;
    if (event.event === "error") {
      throw new Error(
        typeof data.message === "string"
          ? data.message
          : "助手连接中断，请查询任务状态",
      );
    }
    if (event.event === "updates") {
      const progress = data.progress as { label?: string } | undefined;
      if (progress?.label) setStage(progress.label);
      const reply = data.reply as { id: string; content: string } | undefined;
      if (reply && typeof reply.content === "string")
        setMessages((m) => [
          ...m,
          { id: reply.id, role: "assistant", text: reply.content },
        ]);
      const interrupts = data.__interrupt__ as
        | { value: { approval_id?: string } }[]
        | undefined;
      const approvalId = interrupts?.find(
        (i) => typeof i.value?.approval_id === "string",
      )?.value.approval_id;
      if (approvalId) {
        setPending(approvalId);
        setStage("等待您核对操作计划");
      }
    }
    if (
      event.event === "custom" &&
      data.type === "cards" &&
      Array.isArray(data.cards)
    )
      setMessages((m) => [
        ...m,
        {
          id: uid(),
          role: "assistant",
          text: "",
          cards: data.cards as UICard[],
        },
      ]);
  }
  async function run(body: Fields, resume = false) {
    if (busy) return;
    setBusy(true);
    setError("");
    controller.current = new AbortController();
    try {
      await stream(p.sid, body, handle, controller.current.signal, resume);
      const c = await request<{ clarification: Clarification | null }>(
        `/sessions/${p.sid}/clarifications/current`,
      );
      setClar(c.clarification);
    } catch (e) {
      if (!controller.current.signal.aborted)
        setError(e instanceof Error ? e.message : "连接中断，请检查任务状态");
    } finally {
      setBusy(false);
      setStage("");
    }
  }
  useEffect(() => {
    p.onResumeRegister(async (id) => {
      setPending(undefined);
      await run({ approval_id: id }, true);
    });
  });
  async function send() {
    if (!text.trim() || busy || pending) return;
    const value = text.trim();
    setText("");
    setMessages((m) => [...m, { id: uid(), role: "user", text: value }]);
    await run({
      request_id: uid(),
      kind: "typed",
      text: value,
      hint_id: null,
      action_id: null,
      clarification_id: clar?.id ?? null,
    });
  }
  async function choose(c: Clarification["candidates"][number]) {
    try {
      const a = await request<{ action_id: string }>(
        `/sessions/${p.sid}/actions`,
        {
          request_id: uid(),
          kind: "CLARIFICATION",
          action_kind: null,
          fields: [{ field: c.field, entity_id: c.entity_id }],
          parent_utterance_id: null,
        },
      );
      await run({
        request_id: uid(),
        kind: "action",
        text: null,
        hint_id: null,
        action_id: a.action_id,
        clarification_id: clar?.id,
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="assistant-page">
      <div className="chat-layout">
        <section className="chat-panel" aria-label="小安对话">
          <div className="chat-top">
            <span className="assistant-orb">
              <Icon name="assistant" />
            </span>
            <div>
              <strong>小安</strong>
              <small>{isPreview ? "交互预览" : "SafeBank 银行助手"}</small>
            </div>
            <Badge>
              <Icon name="shield" size={12} />
              安全网关保护
            </Badge>
            <Button className="chat-new-session" variant="ghost" disabled={busy || !!pending} onClick={p.onNewSession}>
              <Icon name="plus" size={14}/>新会话
            </Button>
          </div>
          <div className="chat-history" aria-live="polite">
            {!messages.length ? (
              <div className="assistant-welcome">
                <span className="welcome-orb">
                  <Icon name="assistant" size={35} />
                </span>
                <h2>有什么可以帮您？</h2>
                <p>
                  查一笔账，管一张卡，或安排一份
                  <br />
                  跨场景的资金计划。
                </p>
                <div className="capability-list">
                  {[
                    ["chart", "账单分析", "消费分类与异常依据"],
                    ["transfer", "资金安排", "转账、定时与 AA 收款"],
                    ["repeat", "跨场景联动", "先分析，再预演整份计划"],
                  ].map(([i, t, d]) => (
                    <div key={t}>
                      <Icon name={i} />
                      <strong>{t}</strong>
                      <small>{d}</small>
                    </div>
                  ))}
                </div>
                <small className="helper">
                  例如：先留够下周会员费，再安排剩余资金。
                </small>
              </div>
            ) : (
              messages.map((m) => (
                <div className={`chat-message ${m.role}`} key={m.id}>
                  {m.role === "assistant" && (
                    <span className="chat-avatar">
                      <Icon name="assistant" size={17} />
                    </span>
                  )}
                  <div>
                    {m.text && <p>{m.text}</p>}
                    {m.cards?.map((card) => (
                      <SafeCard
                        card={card}
                        key={card.id}
                        sid={p.sid}
                        busy={busy}
                        onApproval={p.openApproval}
                        onHint={(id) =>
                          run({
                            request_id: uid(),
                            kind: "hint",
                            hint_id: id,
                            text: null,
                            action_id: null,
                            clarification_id: null,
                          })
                        }
                      />
                    ))}
                  </div>
                </div>
              ))
            )}
            {busy && (
              <div className="chat-progress">
                <span className="typing-dots">
                  <i />
                  <i />
                  <i />
                </span>
                {stage || "小安正在处理"}
              </div>
            )}
            {clar && (
              <div className="clarification">
                <Badge>需要补充信息</Badge>
                <h3>
                  {clar.kind === "FORM_REQUIRED"
                    ? "请在对应业务页面补充操作细节"
                    : "请选择您指的目标"}
                </h3>
                {clar.candidates.map((c) => (
                  <Button
                    key={`${c.field}-${c.entity_id}`}
                    variant="secondary"
                    disabled={busy}
                    onClick={() => choose(c)}
                  >
                    {c.label}
                  </Button>
                ))}
                {!clar.candidates.length && (
                  <p>您可以在下方补充：{clar.required_fields.join("、")}</p>
                )}
              </div>
            )}
            {pending && (
              <div className="pending-approval">
                <span className="soft-icon">
                  <Icon name="shield" />
                </span>
                <div>
                  <strong>一份操作计划等待您核对</strong>
                  <small>面板内容由安全网关独立提供</small>
                </div>
                <Button onClick={() => p.openApproval(pending)}>
                  查看确认面板
                </Button>
              </div>
            )}
            {error && <Alert tone="error">{error}</Alert>}
            <div ref={end} />
          </div>
          <form
            className="chat-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <label className="sr-only" htmlFor="chat-input">
              向小安描述您的需求
            </label>
            <textarea
              id="chat-input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={
                pending ? "请先处理待确认计划" : "向小安描述您的需求…"
              }
              disabled={busy || !!pending}
              maxLength={2000}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            {busy ? (
              <button
                type="button"
                className="send-button"
                aria-label="停止接收回复"
                onClick={() => {
                  controller.current?.abort();
                  setBusy(false);
                  setStage("");
                  setError(
                    "已停止接收回复。操作可能仍在处理，请在我的页面的安全记录中查看实际状态。",
                  );
                }}
              >
                <Icon name="close" />
              </button>
            ) : (
              <button
                type="submit"
                className="send-button"
                disabled={!text.trim() || !!pending}
                aria-label="发送消息"
              >
                <Icon name="arrow" />
              </button>
            )}
          </form>
          <div className="chat-footnote">
            操作需要您的确认时，将在独立面板中完成。请勿在聊天中发送验证码。
          </div>
        </section>

      </div>
    </div>
  );
}
import { money } from "./ui";
const allowedRoutes: Record<string, string> = {
  "/": "overview",
  "/home": "overview",
  "/transfer": "transfer",
  "/bills": "bills",
  "/cards": "cards",
  "/subscriptions": "security",
  "/wealth": "wealth",
  "/wealth/holdings": "wealth",
  "/wealth/assessment": "wealth",
  "/profile": "security",
};
function SafeCard({
  card,
  sid,
  busy,
  onApproval,
  onHint,
}: {
  card: UICard;
  sid: string;
  busy: boolean;
  onApproval: (id: string) => void;
  onHint: (id: string) => void;
}) {
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    let live = true;
    for (const a of card.actions ?? [])
      if (a.kind === "HINT" && /^hnt_/.test(a.ref))
        request<{ label: string }>(`/sessions/${sid}/hints/${a.ref}`)
          .then((h) => {
            if (live) setLabels((v) => ({ ...v, [a.ref]: h.label }));
          })
          .catch(() => {});
    return () => {
      live = false;
    };
  }, [card.id, sid]);
  const title: Record<string, string> = {
    ACCOUNT: "账户信息",
    TRANSACTIONS: "交易记录",
    ANOMALIES: "异常提醒",
    CARD: "银行卡",
    SUBSCRIPTIONS: "订阅安排",
    PRODUCTS: "产品信息",
    HOLDINGS: "持仓信息",
    PLAN_STATUS: "计划状态",
    BLOCKED: "操作已被拦截",
    REPLAN_HINTS: "需要调整计划",
    CLARIFICATION: "请补充信息",
    NAVIGATE: "继续办理",
  };
  return (
    <div className="safe-ui-card">
      <strong>{title[card.type] ?? "业务信息"}</strong>
      {card.source_ref && <small>来源 {card.source_ref}</small>}
      <CardData card={card} />
      <div className="row">
        {(card.actions ?? []).map((a, i) =>
          a.kind === "OPEN_APPROVAL" && /^apv_/.test(a.ref) ? (
            <Button
              variant="secondary"
              key={i}
              disabled={busy}
              onClick={() => onApproval(a.ref)}
            >
              查看确认面板
            </Button>
          ) : a.kind === "HINT" && labels[a.ref] ? (
            <Button
              variant="secondary"
              key={i}
              disabled={busy}
              onClick={() => onHint(a.ref)}
            >
              {labels[a.ref]}
            </Button>
          ) : a.kind === "NAVIGATE" && allowedRoutes[a.ref] ? (
            <a
              className="button secondary"
              href={`#${allowedRoutes[a.ref]}`}
              key={i}
            >
              打开业务页面
            </a>
          ) : null,
        )}
      </div>
    </div>
  );
}

function CardData({ card }: { card: UICard }) {
  const data =
    card.data && typeof card.data === "object" ? (card.data as Fields) : {};
  const objects = (key: string) =>
    Array.isArray(data[key])
      ? (data[key] as unknown[]).filter(
          (v): v is Fields => !!v && typeof v === "object",
        )
      : [];
  const cash = (v: unknown) =>
    typeof v === "number" && Number.isSafeInteger(v) ? `¥ ${money(v)}` : "—";
  if (card.type === "PLAN_STATUS") {
    const states: Record<string, string> = {
      EXECUTED: "操作已完成",
      UNKNOWN: "结果确认中",
      PENDING_APPROVAL: "等待您确认",
      FAILED: "执行失败",
    };
    return (
      <>
        <div className="card-status">
          <Badge tone={data.status === "EXECUTED" ? "success" : "amber"}>
            {states[String(data.status)] ?? "请查询计划状态"}
          </Badge>
        </div>
        {data.status === "UNKNOWN" && (
          <p>请勿重复发起。您可以在安全中心查询原计划。</p>
        )}
        <code>{String(data.plan_id ?? "")}</code>
      </>
    );
  }
  if (card.type === "BLOCKED")
    return (
      <>
        {objects("findings").map((f, i) => (
          <div className="card-row" key={i}>
            <span>拦截原因</span>
            <strong>{explainCode(String(f.code ?? "安全规则未通过"))}</strong>
          </div>
        ))}
        <p>操作尚不能继续，请先核对您的需求。</p>
      </>
    );
  if (card.type === "REPLAN_HINTS")
    return <p>当前计划需要调整。可用建议会在安全网关核实后显示为按钮。</p>;
  if (card.type === "CLARIFICATION")
    return <p>请通过下方的补充信息区选择目标或补充需求。</p>;
  if (card.type === "NAVIGATE") return <p>您可以打开对应业务页面继续办理。</p>;
  const entries =
    card.type === "ACCOUNT"
      ? objects("accounts")
      : card.type === "HOLDINGS"
        ? objects("holdings")
        : objects("items");
  if (!entries.length) return <p>当前没有可展示的记录。</p>;
  return (
    <>
      {entries.slice(0, 5).map((item, i) => {
        const name = String(
          item.display_name ??
            item.counterparty ??
            item.merchant_name ??
            item.name ??
            (card.type === "CARD"
              ? `银行卡 · ${item.last4 ?? "—"}`
              : card.type === "PRODUCTS"
                ? `产品 · ${item.id ?? ""}`
                : card.type === "HOLDINGS"
                  ? `持仓 · ${item.product_id ?? ""}`
                  : "业务记录"),
        );
        const value =
          card.type === "ACCOUNT"
            ? cash(item.available_cents)
            : card.type === "CARD"
              ? String(item.status ?? "")
              : card.type === "PRODUCTS"
                ? `R${item.risk_level ?? "—"}`
                : card.type === "HOLDINGS"
                  ? String(item.available_units ?? "—") + " 份"
                  : cash(item.amount_cents);
        return (
          <div className="card-row" key={i}>
            <span>{name}</span>
            <strong>{value}</strong>
          </div>
        );
      })}
      {entries.length > 5 && (
        <p>另有 {entries.length - 5} 条，可到业务页面查看完整记录。</p>
      )}
    </>
  );
}
