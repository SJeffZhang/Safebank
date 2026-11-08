import { explainCode } from "./copy";
import { useEffect, useRef, useState } from "react";
import { getPlan, request, stream, isPreview } from "./api";
import {
  uid,
  type UICard,
  type StreamEvent,
  type Clarification,
  type Fields,
  type PlanStatus,
} from "./types";
import type { PageProps } from "./Banking";
import { Icon, Button, Alert, Badge } from "./ui";
import { AssistantWorkspace, detectTopic, type AssistantTopic } from "./AssistantWorkspace";
import { billMonth, billMonths } from "./bill-month";
import { AssistantReadData } from "./AssistantReadData";
import { AssistantReply } from "./AssistantReply";
import { isPlanTerminal, planPresentation } from "./plan-status";
interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  cards?: UICard[];
  business?: {topic: AssistantTopic; month: string | null};
  requestedMonths?: string[] | null;
}
const topics = [
  { icon: "chart", title: "账单分析", description: "消费分类与异常依据" },
  { icon: "transfer", title: "资金安排", description: "转账、定时与 AA 收款" },
  { icon: "repeat", title: "跨场景联动", description: "先分析，再预演整份计划" },
];
const genericFormGuidance = {
  title: "请在对应业务页面补充操作细节",
  body: "请在对应业务页面核对并补充操作信息，或发送一条新的明确需求。",
  links: [] as { href: string; label: string }[],
};
// Template IDs come from current G21; destinations and copy never come from L1 text.
const formGuidance: Record<string, typeof genericFormGuidance> = {
  risk_assessment_form: {
    title: "请亲自完成风险测评",
    body: "风险等级由您亲自完成六题测评后计算，小安不能直接将等级设为 R5。请前往理财页的风险测评，按实际情况作答。",
    links: [{ href: "#wealth", label: "前往理财页" }],
  },
  register_payee_form: {
    title: "请先核对并登记收款人",
    body: "该收款目标尚未登记，无法自动新增收款人或转账。请在转账页手工填写并核对收款人信息。",
    links: [{ href: "#transfer", label: "前往转账页" }],
  },
  transfer_consultation: {
    title: "转账咨询不代表操作授权",
    body: "这是转账咨询，本次没有发起转账。您可以查看账户余额和卡限额，或重新发送明确的转账指令；是否可办理仍需安全预演。",
    links: [{ href: "#overview", label: "查看账户余额" }, { href: "#cards", label: "查看卡片限额" }],
  },
  amount_invalid: {
    title: "金额无法办理",
    body: "金额需要大于 0、最多两位小数且不超过系统上限，本次没有发起转账。请重新发送正确的金额，或在转账页填写。",
    links: [{ href: "#transfer", label: "前往转账页" }],
  },
  schedule_date_invalid: {
    title: "定时日期无法识别",
    body: "每月扣款日需在 1 到 31 号之间，每周需在周一到周日之间，本次没有创建定时转账。请重新发送正确的日期，或在转账页设置。",
    links: [{ href: "#transfer", label: "前往转账页" }],
  },
  subscription_select: {
    title: "请说明要取消哪个订阅",
    body: "本次没有取消任何订阅。请说出订阅名称（如“取消乐听音乐订阅”）或“取消所有订阅”，也可以在订阅页逐个核对。",
    links: [{ href: "#subscriptions", label: "查看我的订阅" }],
  },
};
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
  const [clarification, setClar] = useState<{ sid: string; value: Clarification } | null>(null);
  const clar = clarification?.sid === p.sid ? clarification.value : null;
  const [answer, setAnswer] = useState("");
  const [selecting, setSelecting] = useState(false);
  const selection = useRef<object | null>(null);
  const currentSession = useRef(p.sid);
  currentSession.current = p.sid;
  const controller = useRef<AbortController>();
  const refreshedPlans = useRef(new Set<string>());
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  function openTopic(topic: (typeof topics)[number]) {
    if (busy || selecting || pending) return;
    void send(topic.title);
  }
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, stage]);
  useEffect(() => {
    let live = true;
    controller.current?.abort();
    selection.current = null;
    setBusy(false);
    setSelecting(false);
    setAnswer("");
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
          if (!live) return;
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
        .catch(() => { if (live) setError("历史会话暂不可用，新的原话仍需安全登记。"); });
    return () => { live = false; selection.current = null; controller.current?.abort(); };
  }, [p.sid]);
  function handle(event: StreamEvent, requestedMonths?: string[] | null) {
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
          requestedMonths,
        },
      ]);
  }
  async function run(body: Fields, resume = false) {
    if (busy) return;
    setBusy(true);
    setSteps([]);
    setError("");
    const sid = p.sid;
    const current = new AbortController();
    controller.current = current;
    const active = () => currentSession.current === sid && controller.current === current && !current.signal.aborted;
    try {
      await stream(p.sid, body, event => { if (active()) handle(event, typeof body.text === "string" && detectTopic(body.text) === "账单分析" ? billMonths(body.text, p.profile.sim_now) : undefined); }, current.signal, resume);
      if (!active()) return false;
      const c = await request<{ clarification: Clarification | null }>(
        `/sessions/${p.sid}/clarifications/current`,
      );
      if (!active()) return false;
      setClar(c.clarification ? { sid, value: c.clarification } : null);
      setAnswer("");
      return true;
    } catch (e) {
      if (active())
        setError(e instanceof Error ? e.message : "连接中断，请检查任务状态");
      return false;
    } finally {
      if (currentSession.current === sid && controller.current === current) {
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
    if (!value || busy || selecting || pending) return;
    setText("");
    setMessages((m) => [...m, { id: uid(), role: "user", text: value }]);
    const completed = await run({
      request_id: uid(),
      kind: "typed",
      text: value,
      hint_id: null,
      action_id: null,
      clarification_id: null,
    });
    const topic = detectTopic(value);
    if (completed && topic) setMessages(m => [...m,{id:uid(),role:"assistant",text:"",business:{topic,month:billMonth(value,p.profile.sim_now)}}]);
    input.current?.focus();
  }
  function slotParent() {
    if (clar?.kind !== "SLOTS" || !Array.isArray(clar.parent_utterance_ids) ||
        clar.parent_utterance_ids.length !== 1 || !/^utt_[\w-]+$/.test(clar.parent_utterance_ids[0]) ||
        clar.required_fields.length !== 1 || !clar.candidates.length) return null;
    return clar.parent_utterance_ids[0];
  }
  async function submitAnswer() {
    if (busy || selection.current || pending || !answer.trim()) return;
    if (!slotParent()) { setError("补充信息已失效，请重新发起需求。"); return; }
    const submission = {};
    selection.current = submission;
    setSelecting(true);
    const value = answer.trim();
    setMessages(m => [...m, { id: uid(), role: "user", text: value }]);
    try {
      await run({ request_id: uid(), kind: "typed", text: value, hint_id: null,
        action_id: null, clarification_id: clar!.id });
    } finally {
      if (selection.current === submission) {
        selection.current = null;
        setSelecting(false);
      }
    }
  }
  async function choose(c: Clarification["candidates"][number]) {
    if (busy || pending || selection.current) return;
    const parent = slotParent();
    if (!parent || !clar?.candidates.some(candidate => candidate.field === c.field && candidate.entity_id === c.entity_id) ||
        clar.required_fields[0] !== c.field) {
      setError("补充信息已失效，请重新发起需求。");
      return;
    }
    const sid = p.sid;
    const choice = {};
    selection.current = choice;
    setSelecting(true);
    setError("");
    try {
      const a = await request<{ action_id: string }>(
        `/sessions/${sid}/actions`,
        {
          request_id: uid(),
          kind: "CLARIFICATION",
          action_kind: null,
          fields: [{ field: c.field, entity_id: c.entity_id }],
          parent_utterance_id: parent,
        },
      );
      if (currentSession.current !== sid || selection.current !== choice) return;
      if (!/^act_[\w-]+$/.test(a.action_id)) throw new Error("补充信息登记失败，请重新发起需求。");
      await run({
        request_id: uid(),
        kind: "action",
        text: null,
        hint_id: null,
        action_id: a.action_id,
        clarification_id: null,
      });
    } catch (e) {
      if (currentSession.current === sid && selection.current === choice)
        setError(e instanceof Error ? e.message : "补充信息登记失败，请重试。");
    } finally {
      if (selection.current === choice) {
        selection.current = null;
        setSelecting(false);
      }
    }
  }
  const guidance = clar && Object.prototype.hasOwnProperty.call(formGuidance, clar.prompt_template_id)
    ? formGuidance[clar.prompt_template_id] : genericFormGuidance;
  const seenClarifications = new Set(clar ? [clar.id] : []);
  const displayedMessages = messages.map(message => ({
    ...message,
    cards: message.cards?.filter(card => {
      if (card.type !== "CLARIFICATION" || !card.data || typeof card.data !== "object") return true;
      const id = (card.data as Fields).id;
      if (typeof id !== "string") return true;
      if (seenClarifications.has(id)) return false;
      seenClarifications.add(id);
      return true;
    }),
  }));
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
              <small>{isPreview ? "交互预览" : "BankSense 银行助手"}</small>
            </div>
            <Badge>
              <Icon name="shield" size={12} />
              安全网关保护
            </Badge>
            <Button className="chat-new-session" variant="ghost" disabled={busy || selecting || !!pending} onClick={p.onNewSession}>
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
                      disabled={busy || selecting || !!pending} onClick={() => openTopic(topic)}>
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
              displayedMessages.map((m) => (
                <div className={`chat-message ${m.role}`} key={m.id}>
                  {m.role === "assistant" && (
                    <span className="chat-avatar">
                      <Icon name="assistant" size={17} />
                    </span>
                  )}
                  <div>
                    {m.text && (m.role === "assistant" ? <AssistantReply text={m.text} /> : <p>{m.text}</p>)}
                    {m.business && <AssistantWorkspace {...p} topic={m.business.topic} requestedMonth={m.business.month}/>}
                    {m.cards?.map((card) => (
                      <SafeCard
                        card={card}
                        requestedMonths={m.requestedMonths}
                        key={card.id}
                        sid={p.sid}
                        busy={busy || selecting}
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
                    ? guidance.title
                    : "请选择您指的目标"}
                </h3>
                {clar.kind === "FORM_REQUIRED" && <>
                  <p>{guidance.body}</p>
                  <div className="row">{guidance.links.map(link => <a className="button secondary" href={link.href} key={link.href}>{link.label}</a>)}</div>
                </>}
                {clar.kind === "SLOTS" && clar.candidates.map((c) => (
                  <Button
                    key={`${c.field}-${c.entity_id}`}
                    variant="secondary"
                    disabled={busy || selecting}
                    onClick={() => choose(c)}
                  >
                    {c.label}
                  </Button>
                ))}
                {clar.kind === "SLOTS" && <form onSubmit={e => { e.preventDefault(); void submitAnswer(); }}>
                  <label>补充本次目标<input value={answer} onChange={e => setAnswer(e.target.value)} disabled={busy || selecting || !!pending} maxLength={2000}/></label>
                  <Button disabled={busy || selecting || !!pending || !answer.trim()} onClick={() => void submitAnswer()}>提交补充</Button>
                </form>}
                {!clar.candidates.length && clar.required_fields.length > 0 && (
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
              disabled={busy || selecting || !!pending}
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
                disabled={selecting || !text.trim() || !!pending}
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
  requestedMonths,
  sid,
  busy,
  revision,
  onStatus,
  onApproval,
  onHint,
}: {
  card: UICard;
  requestedMonths?: string[] | null;
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
    <div className="safe-ui-card" role="group" aria-label={title[card.type] ?? "业务信息"}>
      <strong>{title[card.type] ?? "业务信息"}</strong>
      <CardData card={shownCard} requestedMonths={requestedMonths} />
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

function CardData({ card, requestedMonths }: { card: UICard; requestedMonths?: string[] | null }) {
  const data =
    card.data && typeof card.data === "object" ? (card.data as Fields) : {};
  const objects = (key: string) =>
    Array.isArray(data[key])
      ? (data[key] as unknown[]).filter(
          (v): v is Fields => !!v && typeof v === "object",
        )
      : [];
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
  return <AssistantReadData card={card} requestedMonths={requestedMonths} />;
}
