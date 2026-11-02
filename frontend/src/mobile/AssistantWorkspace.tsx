import { useState } from "react";
import { Bills, Transfers, type PageProps } from "./Banking";
import { Subscriptions } from "./Services";
import { read, isPreview } from "./api";
import { useLoad } from "./hooks";
import { Alert, Badge, Button, Icon, Loading, Money, MonthPicker } from "./ui";
import type { Account, Summary, Upcoming } from "./types";
import { monthRange } from "./bill-month";
import { explainCode } from "./copy";

export type AssistantTopic = "账单分析" | "资金安排" | "跨场景联动";
export function detectTopic(text: string): AssistantTopic | undefined {
  if (/账单|月报/.test(text)) return "账单分析";
  if (/跨场景|联动|预留|留够|剩余资金/.test(text)) return "跨场景联动";
  if (/账单|月报|消费|花了|花费/.test(text)) return "账单分析";
  if (/资金安排|转账|收款|AA|余额/.test(text)) return "资金安排";
}

/** Read-only context card. Any expanded write form retains the existing approval path. */
export function AssistantWorkspace({ topic, requestedMonth, ...p }: PageProps & { topic: AssistantTopic; requestedMonth?: string | null }) {
  const [month, setMonth] = useState(requestedMonth === undefined ? (p.profile.sim_now ?? "").slice(0,7) : requestedMonth ?? "");
  const [expanded, setExpanded] = useState<"bills" | "transfer" | "subscriptions">();
  const q = useLoad(async () => {
    if (topic === "账单分析") {
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) throw new Error("请明确选择月报月份后重新读取。");
      const [summary, anomalies] = await Promise.all([
        read<Summary>(p.sid,"get_spending_summary",{month,account_id:null}),
        read<{items: {transaction:{id:string;occurred_at:string;amount_cents:number};codes:string[]}[]}>(p.sid,"detect_anomalies",monthRange(month)),
      ]);
      if (summary.month !== month || !Number.isSafeInteger(summary.total_cents) || !Array.isArray(summary.categories))
        throw new Error("月报月份或数据与本次请求不一致，请重新读取。");
      const range = monthRange(month);
      if (!Array.isArray(anomalies.items) || anomalies.items.some(item => !item.transaction ||
        !Number.isSafeInteger(item.transaction.amount_cents) ||
        !(Date.parse(item.transaction.occurred_at) >= Date.parse(range.from_at) && Date.parse(item.transaction.occurred_at) < Date.parse(range.to_at))))
        throw new Error("异常记录不在所选月份范围内，请重新读取。");
      return {summary, anomalies};
    }
    const accounts = await read<{accounts:Account[]}>(p.sid,"get_accounts",{history_days:0});
    const upcoming = topic === "跨场景联动" ? await read<Upcoming>(p.sid,"upcoming_charges",{days:7,account_id:null}) : undefined;
    return {accounts:accounts.accounts,upcoming};
  },[p.sid,p.revision,topic,month]);
  const categories = q.value?.summary?.categories ?? [];
  const displayCategories = categories.some(c=>c.category === "其他") ? categories : [...categories,{category:"其他",amount_cents:0,share_bp:0}];
  return <section className="assistant-business-card" aria-label={`${topic}内容`}>
    <header><span className="soft-icon"><Icon name={topic === "账单分析" ? "chart" : topic === "资金安排" ? "transfer" : "repeat"}/></span><div><strong>{topic}</strong><small>{isPreview ? "合成数据 · 交互预览" : "银行数据 · 只读概览"}</small></div><Badge>对话内办理</Badge></header>
    {topic === "账单分析" && <MonthPicker value={month || p.profile.sim_now.slice(0,7)} onChange={setMonth}/>}
    {q.loading ? <Loading/> : q.error ? <><Alert tone="error">{q.error}</Alert><Button variant="secondary" onClick={q.reload}>重新读取</Button></> : <>
      {q.value?.summary && <><div className="assistant-business-total"><small>所选月份消费</small><Money value={q.value.summary.total_cents}/></div><div className="assistant-category-list">{displayCategories.map(c=><div key={c.category}><span>{c.category}</span><Money value={c.amount_cents}/><span className="assistant-category-track"><i style={{width:`${Math.max(0,Math.min(100,c.share_bp/100))}%`}}/></span></div>)}</div></>}
      {q.value?.anomalies && <div><p>所选月份异常记录：{q.value.anomalies.items.length} 条</p>{q.value.anomalies.items.map(item=><div key={item.transaction.id}><div className="card-row"><span>{item.transaction.occurred_at}</span><Money value={item.transaction.amount_cents}/></div><small>{item.codes.map(explainCode).join("、")}</small></div>)}</div>}
      {q.value?.accounts && <div className="assistant-account-list">{q.value.accounts.map(a=><div key={a.id}><span><strong>{a.display_name}</strong><small>{a.account_no_masked}</small></span><Money value={a.available_cents}/></div>)}{!q.value.accounts.length && <p>暂无可用账户</p>}</div>}
      {q.value?.upcoming && <div className="assistant-reserve"><small>未来 7 天待扣预留</small><Money value={q.value.upcoming.reserve_total_cents}/><p>先核对待扣项目，再决定如何安排资金。预留展示不代表已扣款或已转账。</p></div>}
      <div className="assistant-business-actions">
        {topic === "账单分析" ? <Button variant="secondary" onClick={()=>setExpanded(expanded === "bills" ? undefined : "bills")}>{expanded === "bills" ? "收起账单明细" : "展开账单明细"}</Button> : <Button variant="secondary" onClick={()=>setExpanded(expanded === "transfer" ? undefined : "transfer")}>{expanded === "transfer" ? "收起资金安排" : "填写资金安排"}</Button>}
        {topic === "跨场景联动" && <Button variant="ghost" onClick={()=>setExpanded(expanded === "subscriptions" ? undefined : "subscriptions")}>核对订阅扣款</Button>}
      </div>
      {expanded && <div className="assistant-embedded" key={expanded}>{expanded === "bills" ? <Bills {...p} initialMonth={month}/> : expanded === "transfer" ? <Transfers {...p}/> : <Subscriptions {...p} embedded/>}</div>}
    </>}
  </section>;
}
