import { explainCode } from "./copy";
import { useEffect, useRef, useState } from "react";
import { getPlan, request, stream, isPreview } from "./api";
import {
  uid,
  productTypeLabel,
  type UICard,
  type StreamEvent,
  type Clarification,
  type Fields,
  type PlanStatus,
} from "./types";
import type { PageProps } from "./Banking";
import { Icon, Button, Alert, Badge } from "./ui";
import { AssistantWorkspace, detectTopic, type AssistantTopic } from "./AssistantWorkspace";
import { billMonth, monthRange } from "./bill-month";
import { isPlanTerminal, planPresentation } from "./plan-status";
interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  cards?: UICard[];
  business?: {topic: AssistantTopic; month: string | null};
  requestedMonth?: string | null;
}
const topics = [
  { icon: "chart", title: "账单分析", description: "消费分类与异常依据" },
  { icon: "transfer", title: "资金安排", description: "转账、定时与 AA 收款" },
  { icon: "repeat", title: "跨场景联动", description: "先分析，再预演整份计划" },
];
export function Assistant(
  p: PageProps & {
    openApproval: (id: string) => void;
    onNewSession: () => void;
    onResumeRegister: (handler: (id: string) => Promise<void>) => void;
    onExecuted?: () => void;
  },
) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [steps, setSteps] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string>();
  const [clar, setClar] = useState<Clarification | null>(null);
  const controller = useRef<AbortController>();
  const refreshedPlans = useRef(new Set<string>());
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  function openTopic(topic: (typeof topics)[number]) {
    if (busy || pending) return;
    void send(topic.title);
  }
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, stage]);
  useEffect(() => {
    refreshedPlans.current.clear();
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
  function handle(event: StreamEvent, requestedMonth?: string | null) {
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
      if (progress?.label) {
        setStage(progress.label);
        setSteps(s => s.includes(progress.label!) ? s : [...s,progress.label!]);
      }
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
          requestedMonth,
        },
      ]);
  }
  async function run(body: Fields, resume = false) {
    if (busy) return;
    setBusy(true);
    setSteps([]);
    setError("");
    const current = new AbortController();
    controller.current = current;
    const active = () => controller.current === current && !current.signal.aborted;
    try {
      await stream(p.sid, body, event => { if (active()) handle(event, typeof body.text === "string" && detectTopic(body.text) === "账单分析" ? billMonth(body.text, p.profile.sim_now) : undefined); }, current.signal, resume);
      if (!active()) return false;
      const c = await request<{ clarification: Clarification | null }>(
        `/sessions/${p.sid}/clarifications/current`,
      );
      if (!active()) return false;
      setClar(c.clarification);
      return true;
    } catch (e) {
      if (active())
        setError(e instanceof Error ? e.message : "连接中断，请检查任务状态");
      return false;
    } finally {
      if (controller.current === current) {
        setBusy(false);
        setStage("");
      }
    }
  }
  useEffect(() => {
    p.onResumeRegister(async (id) => {
      setPending(undefined);
      await run({ approval_id: id }, true);
    });
  });
  async function send(selected?: string) {
    const value = (selected ?? text).trim();
    if (!value || busy || pending) return;
    setText("");
    setMessages((m) => [...m, { id: uid(), role: "user", text: value }]);
    const completed = await run({
      request_id: uid(),
      kind: "typed",
      text: value,
      hint_id: null,
      action_id: null,
      clarification_id: clar?.id ?? null,
    });
    const topic = detectTopic(value);
    if (completed && topic) setMessages(m => [...m,{id:uid(),role:"assistant",text:"",business:{topic,month:billMonth(value,p.profile.sim_now)}}]);
    input.current?.focus();
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
                  {topics.map((topic) => (
                    <button type="button" key={topic.title} aria-label={topic.title}
                      disabled={busy || !!pending} onClick={() => openTopic(topic)}>
                      <Icon name={topic.icon} />
                      <strong>{topic.title}</strong>
                      <small>{topic.description}</small>
                    </button>
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
                    {m.business && <AssistantWorkspace {...p} topic={m.business.topic} requestedMonth={m.business.month}/>}
                    {m.cards?.map((card) => (
                      <SafeCard
                        card={card}
                        requestedMonth={m.requestedMonth}
                        key={card.id}
                        sid={p.sid}
                        busy={busy}
                        revision={p.revision}
                        onStatus={(status) => {
                          if (status !== "PENDING_APPROVAL" && card.actions?.some(a => a.kind === "OPEN_APPROVAL" && a.ref === pending)) setPending(undefined);
                          const planId = String((card.data as Fields)?.plan_id ?? "");
                          if (status === "EXECUTED" && !refreshedPlans.current.has(planId)) {
                            refreshedPlans.current.add(planId);
                            p.onExecuted?.();
                          }
                        }}
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
              <div className="chat-progress assistant-thinking" role="status" aria-label="小安处理进度">
                <span className="thinking-orb"><Icon name="assistant" size={18}/></span>
                <div><strong>{stage || "正在理解您的需求"}</strong><small>{isPreview ? "合成预览处理中" : "正在处理，请稍候"}</small>
                {steps.length > 0 && <ol>{steps.map((s,i)=><li key={s} className={i===steps.length-1 ? "active" : "done"}>{i===steps.length-1 ? "◦" : "✓"} {s}</li>)}</ol>}</div>
                <span className="typing-dots">
                  <i />
                  <i />
                  <i />
                </span>
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
              ref={input}
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
  requestedMonth,
  sid,
  busy,
  revision,
  onStatus,
  onApproval,
  onHint,
}: {
  card: UICard;
  requestedMonth?: string | null;
  sid: string;
  busy: boolean;
  revision: number;
  onStatus: (status: string) => void;
  onApproval: (id: string) => void;
  onHint: (id: string) => void;
}) {
  const statusHandler = useRef(onStatus);
  statusHandler.current = onStatus;
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<PlanStatus>();
  const [planError, setPlanError] = useState("");
  const planId = card.type === "PLAN_STATUS" && card.data && typeof card.data === "object" ? String((card.data as Fields).plan_id ?? "") : "";
  useEffect(() => {
    if (!planId || !/^pln_[\w-]+$/.test(planId)) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    setPlan(undefined); setPlanError("");
    const load = async () => {
      try {
        const current = await getPlan(planId);
        if (!live) return;
        if (current.plan_id !== planId) throw new Error("计划身份不一致，请重新查询。");
        setPlan(current); statusHandler.current(current.status);
        if (!isPlanTerminal(current.status)) timer = setTimeout(load,1800);
      } catch (e) {
        if (live) setPlanError(e instanceof Error ? e.message : "计划状态读取失败");
      }
    };
    void load();
    return () => {live=false;clearTimeout(timer);};
  }, [sid,planId,revision]);
  const shownStatus = planError ? "UNAVAILABLE" : plan?.status ?? "CHECKING";
  const shownCard = planId ? {...card,data:{...(card.data as Fields),status:shownStatus}} : card;
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
      <CardData card={shownCard} requestedMonth={requestedMonth} />
      {planError && <Alert tone="error">{planError}</Alert>}
      <div className="row">
        {(card.actions ?? []).map((a, i) =>
          a.kind === "OPEN_APPROVAL" && /^apv_/.test(a.ref) && (!planId || !isPlanTerminal(shownStatus)) ? (
            <Button
              variant="secondary"
              key={i}
              disabled={busy || (!!planId && ["CHECKING","UNAVAILABLE"].includes(shownStatus))}
              onClick={() => onApproval(plan?.decision?.approval_id ?? a.ref)}
            >
              {!planId || shownStatus === "PENDING_APPROVAL" ? "查看确认面板" : "查看计划状态"}
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

function CardData({ card, requestedMonth }: { card: UICard; requestedMonth?: string | null }) {
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
    const presentation = planPresentation(String(data.status));
    return (
      <>
        <div className="card-status">
          <Badge tone={presentation.tone}>
            {presentation.label}
          </Badge>
        </div>
        {data.status === "UNKNOWN" && (
          <p>请勿重复发起。您可以在安全中心查询原计划。</p>
        )}
        <code>{String(data.plan_id ?? "")}</code>
      </>
    );
  }
  if (card.type === "TRANSACTIONS" && typeof data.month === "string") {
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(data.month) || !Number.isSafeInteger(data.total_cents) ||
      (requestedMonth !== undefined && data.month !== requestedMonth))
      return <Alert tone="error">月报月份或数据与本次请求不一致，请重新读取。</Alert>;
    return <><p>{data.month} 月度消费</p><div className="card-row"><span>消费合计</span><strong>{cash(data.total_cents)}</strong></div>
      {objects("categories").map((c,i) => <div className="card-row" key={i}><span>{String(c.category ?? "未分类")}</span><strong>{cash(c.amount_cents)}</strong></div>)}</>;
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
  if (card.type === "ANOMALIES") {
    const anomalies = objects("items");
    if (requestedMonth !== undefined) {
      if (!requestedMonth) return <Alert tone="error">请明确所选月份后重新读取异常记录。</Alert>;
      const range = monthRange(requestedMonth);
      if (anomalies.some(item => {
        const tx = item.transaction as Fields | undefined;
        const at = Date.parse(String(tx?.occurred_at ?? ""));
        return !(at >= Date.parse(range.from_at) && at < Date.parse(range.to_at));
      })) return <Alert tone="error">异常记录不在所选月份范围内，请重新读取。</Alert>;
    }
    if (!anomalies.length) return <p>所选范围内没有异常记录。</p>;
    return <>{anomalies.slice(0,5).map((item,i) => {
      const tx = item.transaction && typeof item.transaction === "object" ? item.transaction as Fields : {};
      return <div key={i}><div className="card-row"><span>{String(tx.counterparty ?? tx.id ?? "异常交易")}</span><strong>{cash(tx.amount_cents)}</strong></div>
        <small>{String(tx.occurred_at ?? "")} · {Array.isArray(item.codes) ? item.codes.map(code=>explainCode(String(code))).join("、") : "异常依据暂不可用"}</small></div>;
    })}{anomalies.length > 5 && <p>另有 {anomalies.length-5} 条，请到业务页面查看。</p>}</>;
  }
  if (card.type === "HOLDINGS") {
    const holdings = objects("holdings"), orders = objects("orders");
    const orderLabels: Record<string,string> = {PENDING_CONFIRM:"待确认",PENDING_SETTLEMENT:"待结算",SETTLED:"已结算",FAILED:"失败",EXPIRED:"已过期"};
    return <>{holdings.length ? holdings.slice(0,5).map((h,i)=><div className="card-row" key={i}><span>持仓 · {String(h.product_id ?? "")}</span><strong>{String(h.available_units ?? "—")} 份</strong></div>) : <p>暂无持仓</p>}
      {orders.length > 0 && <><p>在途与近期订单 · 待结算款不计入可用余额</p>{orders.slice(0,5).map((o,i)=><div className="card-row" key={i}><span>{String(o.product_id ?? "")} · {orderLabels[String(o.status)] ?? "未知订单状态"}</span><strong>{cash(o.amount_cents)}</strong></div>)}</>}
      {holdings.length > 5 || orders.length > 5 ? <p>请到业务页面查看完整持仓与订单。</p> : null}</>;
  }
  const entries =
    card.type === "ACCOUNT"
      ? objects("accounts")
      : card.type === "PRODUCTS" && typeof data.id === "string"
        ? [data]
        : card.type === "SUBSCRIPTIONS" ? [...objects("items"),...objects("scheduled_items")] : objects("items");
  if (!entries.length) return <p>当前没有可展示的记录。</p>;
  return (
    <>
      {entries.slice(0, 5).map((item, i) => {
        const name = String(
          item.display_name ??
            item.counterparty ??
            item.merchant_name ??
            item.target_display ??
            item.name ??
            (card.type === "CARD"
              ? `银行卡 · ${item.last4 ?? "—"}`
              : card.type === "PRODUCTS"
                ? `产品 · ${item.id ?? ""}`
                : card.type === "SUBSCRIPTIONS"
                  ? `订阅 · ${item.instruction_id ?? item.merchant_id ?? ""}`
                  : "业务记录"),
        );
        const value =
          card.type === "ACCOUNT"
            ? cash(item.available_cents)
            : card.type === "CARD"
              ? String(item.status ?? "")
              : card.type === "PRODUCTS"
                ? `R${item.risk_level ?? "—"} · ${productTypeLabel(item.product_type)}`
                : item.typical_amount_cents !== undefined ? `典型扣费 ${cash(item.typical_amount_cents)}` : cash(item.amount_cents);
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
