import { useEffect, useRef, useState, type CSSProperties } from "react";
import { read, getCardNumber, isPreview } from "./api";
import { useLoad } from "./hooks";
import type {
  BankCard,
  CardNumberDetail,
  Subscription,
  Upcoming,
  Product,
  Question,
} from "./types";
import type { PageProps } from "./Banking";
import { Panel, Field, Segments } from "./Banking";
import {
  ChoiceSelect,
  Icon,
  Button,
  Money,
  money,
  Empty,
  Alert,
  Loading,
  Badge,
  date,
  Modal,
} from "./ui";
import { parseAmountToCents } from "../services/transfer/money";
import { loadPurposes, previewPurposes, purposeOptions, purposeStorageKey } from "./card-purpose";
export function Cards(p: PageProps) {
  const q = useLoad(
    () => read<{ items: BankCard[] }>(p.sid, "get_cards", { account_id: null }),
    [p.sid, p.revision],
  );
  const [cardId, setCardId] = useState("");
  const [deckSettled, setDeckSettled] = useState(false);
  const materials = ["graphite", "silver", "champagne"] as const;
  const materialOf = (id: string) => materials[Math.max(0, q.value?.items.findIndex(item=>item.id === id) ?? 0) % materials.length];
  const [applicationOpen, setApplicationOpen] = useState(false);
  const [applicationPurpose, setApplicationPurpose] = useState("日常消费");
  const [pendingApplication, setPendingApplication] = useState<{ids:string[];purpose:string}>();
  const [restrictionsOpen, setRestrictionsOpen] = useState(false);
  const [purposes, setPurposes] = useState(() => loadPurposes(p.profile.customer_id));
  const [purposeEditing, setPurposeEditing] = useState(false);
  const [purposeDraft, setPurposeDraft] = useState("");
  const [purposeError, setPurposeError] = useState("");
  const purposeOf = (id: string) => purposes[id] ?? (isPreview ? previewPurposes[id] : undefined) ?? "未设置用途";
  useEffect(() => { setPurposes(loadPurposes(p.profile.customer_id)); setPurposeEditing(false); }, [p.profile.customer_id]);
  function savePurpose() {
    if (!card) return;
    const label = purposeDraft.trim();
    if (!label || label.length > 12) { setPurposeError("请输入 1–12 个字的用途名称。"); return; }
    const next = { ...purposes, [card.id]: label };
    try {
      localStorage.setItem(purposeStorageKey(p.profile.customer_id), JSON.stringify(next));
      setPurposes(next); setPurposeEditing(false);
      p.notify(`尾号 ${card.last4} 的用途已更新为${label}`);
    } catch { setPurposeError("暂时无法保存，请检查浏览器存储设置后重试。"); }
  }
  const [limit, setLimit] = useState<"single" | "daily" | null>(null);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [lost, setLost] = useState(false);
  const [replace, setReplace] = useState(false);
  const numberRequest = useRef(0);
  const [numberPin, setNumberPin] = useState("");
  const [numberOpen, setNumberOpen] = useState(false);
  const [numberDetail, setNumberDetail] = useState<CardNumberDetail>();
  const [numberLoading, setNumberLoading] = useState(false);
  const [numberError, setNumberError] = useState("");
  const [numberCopied, setNumberCopied] = useState(false);
  const card =
    q.value?.items.find((c) => c.id === cardId) ??
    q.value?.items.find((c) => c.current) ??
    q.value?.items[0];
  useEffect(() => {
    if (!pendingApplication || !q.value) return;
    const added=q.value.items.find(item=>!pendingApplication.ids.includes(item.id));
    if (!added) return;
    const next={...loadPurposes(p.profile.customer_id),[added.id]:pendingApplication.purpose};
    setPurposes(next);
    try { localStorage.setItem(purposeStorageKey(p.profile.customer_id),JSON.stringify(next)); }
    catch { p.notify("演示卡已生成，用途暂未保存到浏览器。"); }
    setCardId(added.id);setPendingApplication(undefined);setApplicationOpen(false);
  },[q.value,pendingApplication,p.profile.customer_id]);
  useEffect(() => {
    closeNumber();
    setPurposeEditing(false);
    setRestrictionsOpen(false);
  }, [card?.id, p.sid]);
  useEffect(() => () => { numberRequest.current += 1; }, []);
  function closeNumber() {
    numberRequest.current += 1;
    setNumberOpen(false);
    setNumberDetail(undefined);
    setNumberPin("");
    setNumberError("");
    setNumberLoading(false);
    setNumberCopied(false);
  }
  async function verifyNumber() {
    if (!card || numberLoading || !/^\d{6}$/.test(numberPin)) return;
    const attempt = ++numberRequest.current;
    const pin = numberPin;
    setNumberPin("");
    setNumberError("");
    setNumberLoading(true);
    try {
      const detail = await getCardNumber(p.sid, card.id, pin);
      if (attempt !== numberRequest.current) return;
      if (
        detail.card_id !== card.id ||
        !/^\d{12,32}$/.test(detail.card_number) ||
        !detail.card_number.endsWith(card.last4)
      ) throw new Error("卡号信息与当前卡片不一致，请重新查询");
      setNumberDetail(detail);
    } catch (error) {
      if (attempt === numberRequest.current)
        setNumberError(error instanceof Error ? error.message : "卡号查询失败");
    } finally {
      if (attempt === numberRequest.current) setNumberLoading(false);
    }
  }
  async function setLimits() {
    if (!card) return;
    const result = parseAmountToCents(amount);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    const previous =
      limit === "single" ? card.single_limit_cents : card.daily_limit_cents;
    await p.onPropose(
      result.cents > previous ? "CARD_LIMIT_LOOSEN" : "CARD_LIMIT_TIGHTEN",
      {
        card_id: card.id,
        single_limit_cents: limit === "single" ? result.cents : null,
        daily_limit_cents: limit === "daily" ? result.cents : null,
      },
    );
    setLimit(null);
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">YOUR CARDS</div>
          <h1>我的银行卡</h1>
          <p>查看使用情况，随时管理支付权限。</p>
        </div>
        {isPreview && <Button variant="secondary" onClick={() => setApplicationOpen(true)}><Icon name="plus" size={15}/>申请新卡</Button>}
      </div>
      {q.loading ? (
        <Loading />
      ) : q.error ? (
        <Alert tone="error">
          {q.error}
          <button className="text-button" onClick={q.reload}>
            重试
          </button>
        </Alert>
      ) : !card ? (
        <Empty title="暂无银行卡" detail="账户关联的卡片会显示在这里。" />
      ) : (
        <>
          <div className="cards-layout">
            <div className="card-display">
              <div className="card-deck" role="group" aria-label="我的银行卡叠卡" data-settled={deckSettled}
                style={{ "--deck-count": (q.value?.items.length ?? 1) - 1 } as CSSProperties}>
                {(q.value?.items ?? []).filter((item) => item.id !== card.id).map((item, index) => (
                  <button key={item.id} type="button"
                    className="card-deck-peek"
                    data-card-material={materialOf(item.id)}
                    style={{ "--deck-index": index, zIndex: index + 1 } as CSSProperties}
                    onClick={() => { setDeckSettled(true); setCardId(item.id); setLimit(null); setLost(false); }}
                    aria-label={`选择${purposeOf(item.id) !== "未设置用途" ? purposeOf(item.id) : p.accounts.find((a) => a.id === item.account_id)?.display_name ?? "银行卡"}，尾号${item.last4}`}>
                    <span className="deck-bank"><span className="brand-glyph">S</span> {purposeOf(item.id)}</span>
                    <span className="deck-tail">{item.last4}<Icon name="down" size={12} /></span>
                  </button>
                ))}
              <div key={card.id}
                data-card-material={materialOf(card.id)}
                onAnimationEnd={event=>{if(event.target === event.currentTarget) setDeckSettled(true);}}
                className={`bank-card large card-deck-front ${card.status !== "ACTIVE" ? "inactive" : ""}`}
              >
                <div className="bank-card-brand">
                  <span className="brand-glyph">S</span>SafeBank
                  <span>{purposeOf(card.id)}</span>
                </div>
                <div className="chip">
                  <i />
                  <i />
                </div>
                <div className="bank-number">
                  ••••　••••　••••　{card.last4}
                </div>
                {isPreview && <button type="button" className="card-number-reveal" onClick={()=>{setNumberCopied(false);setNumberOpen(true)}}><Icon name="eye" size={14}/>查看卡号</button>}
                <div className="bank-card-bottom">
                  <span>
                    {p.profile.display_name}
                    <small>
                      {card.status === "ACTIVE"
                        ? "安心相伴，随行所需"
                        : card.status === "FROZEN"
                          ? "此卡已临时冻结"
                          : "此卡已挂失"}
                    </small>
                  </span>
                  <span className="card-circles">
                    <i />
                    <i />
                  </span>
                </div>
              </div>
              </div>
              <div className="card-deck-caption" aria-live="polite">
                <div className="card-caption-info">
                  <div className="card-caption-title"><strong>{purposeOf(card.id)}</strong>
                    <button type="button" className="card-purpose-edit" aria-label="修改用途" onClick={() => {
                      setPurposeDraft(purposeOf(card.id) === "未设置用途" ? "" : purposeOf(card.id));
                      setPurposeError(""); setPurposeEditing(true);
                    }}>修改<Icon name="chevron" size={12} /></button>
                  </div>
                  <small>{q.value?.items.length} 张卡 · 点击叠卡切换</small>
                </div>
                <Badge tone={card.status === "ACTIVE" ? "success" : "amber"}>
                  {card.status === "ACTIVE"
                    ? "使用正常"
                    : card.status === "FROZEN"
                      ? "已冻结"
                      : "已挂失"}
                </Badge>
              </div>
              <div className="card-actions">
                <Button
                  variant="secondary"
                  disabled={p.busy || card.status === "LOST"}
                  onClick={() =>
                    p.onPropose(
                      card.status === "FROZEN"
                        ? "CARD_UNFREEZE"
                        : "CARD_FREEZE",
                      { card_id: card.id },
                    )
                  }
                >
                  <Icon name="lock" size={17} />
                  {card.status === "FROZEN" ? "解冻卡片" : "临时冻结"}
                </Button>
                <Button
                  variant="ghost danger-text"
                  disabled={p.busy || card.status === "LOST"}
                  onClick={() => setLost(true)}
                >
                  挂失 / 补卡
                </Button>
              </div>
              <p className="helper">
                冻结后，该账户的刷卡与对外转账将暂停；本人互转和订阅代扣不受影响。
              </p>
            </div>
            <Panel
              title="支付限额"
              meta={<span className="helper">卡片 {card.last4}</span>}
            >
              <div className="limit-summary">
                <span>今日已用</span>
                <Money value={card.daily_used_cents} />
                <div className="usage-track">
                  <span
                    style={{
                      width: `${Math.min(100, (card.daily_used_cents / Math.max(card.daily_limit_cents, 1)) * 100)}%`,
                    }}
                  />
                </div>
                <p>当日剩余 ¥ {money(card.remaining_daily_cents)}</p>
              </div>
              {[
                {
                  key: "single" as const,
                  label: "单笔限额",
                  value: card.single_limit_cents,
                },
                {
                  key: "daily" as const,
                  label: "日累计限额",
                  value: card.daily_limit_cents,
                },
              ].map((l) => (
                <div className="data-row card-limit-row" key={l.key}>
                  <div>
                    <strong>{l.label}</strong>
                    <small>刷卡与对外转账共用</small>
                  </div>
                  <Money value={l.value} />
                  <Button
                    variant="ghost"
                    disabled={card.status === "LOST"}
                    onClick={() => {
                      setLimit(l.key);
                      setAmount("");
                      setError("");
                    }}
                  >
                    调整
                  </Button>
                </div>
              ))}
              <Button variant="ghost" className="limit-unlock-entry" onClick={() => setRestrictionsOpen(true)}>
                <Icon name="shield" size={18}/>交易限制 / 解锁<Icon name="chevron" size={14}/>
              </Button>
              <Alert>调高限额需短信验证；调低限额仍需核对确认。</Alert>
            </Panel>
          </div>
          <Panel
            title="支付渠道"
            meta={<span className="helper">开启任一渠道需要短信验证</span>}
          >
            <div className="channel-grid">
              {[
                {
                  key: "online" as const,
                  title: "线上支付",
                  desc: "网络购物与线上交易",
                  icon: "globe",
                },
                {
                  key: "overseas" as const,
                  title: "境外支付",
                  desc: "境外商户交易",
                  icon: "globe",
                },
                {
                  key: "atm" as const,
                  title: "ATM 取现",
                  desc: "自助设备现金交易",
                  icon: "card",
                },
                {
                  key: "quickpass" as const,
                  title: "小额免密",
                  desc: "无需密码的小额支付",
                  icon: "card",
                },
              ].map((c) => (
                <div className="channel" key={c.key}>
                  <span className="soft-icon">
                    <Icon name={c.icon} />
                  </span>
                  <div>
                    <strong>{c.title}</strong>
                    <p>{c.desc}</p>
                  </div>
                  <button
                    className="switch"
                    role="switch"
                    aria-label={c.title}
                    aria-checked={card.controls[c.key]}
                    disabled={
                      p.busy ||
                      card.status === "LOST" ||
                      (card.status === "FROZEN" && !card.controls[c.key])
                    }
                    onClick={() =>
                      p.onPropose(
                        card.controls[c.key]
                          ? "CARD_CONTROL_TIGHTEN"
                          : "CARD_CONTROL_LOOSEN",
                        {
                          card_id: card.id,
                          online: null,
                          overseas: null,
                          atm: null,
                          quickpass: null,
                          [c.key]: !card.controls[c.key],
                        },
                      )
                    }
                  >
                    <span />
                  </button>
                </div>
              ))}
            </div>
          </Panel>
        </>
      )}
      {applicationOpen && <Modal title="申请新银行卡" onClose={() => setApplicationOpen(false)}>
        <div className="modal-body card-application">
          {!isPreview ? <Alert>当前环境暂未接入新卡申请服务，请联系银行办理。</Alert> : <>
            <Badge>合成演示申请</Badge><h3>SafeBank 标准借记卡</h3>
            <p className="muted">申请人：{p.profile.display_name} · {p.profile.phone_masked}</p>
            <p className="helper">确认后在演示环境生成一张新卡和余额为零的活期账户，不涉及真实开户。</p>
            <Field label="新卡用途"><input value={applicationPurpose} maxLength={12} onChange={event=>setApplicationPurpose(event.target.value)} placeholder="如：工资收款" /></Field>
            <Button disabled={p.busy || !applicationPurpose.trim()} onClick={async()=>{
              if (!isPreview || p.busy || !applicationPurpose.trim()) return;
              setPendingApplication({ids:(q.value?.items ?? []).map(item=>item.id),purpose:applicationPurpose.trim()});
              await p.onPropose("CARD_APPLY",{purpose:applicationPurpose.trim()});
            }}>预演并确认申请</Button>
          </>}
          <Button variant="ghost" onClick={() => setApplicationOpen(false)}>返回我的银行卡</Button>
        </div>
      </Modal>}
      {restrictionsOpen && card && <Modal title="交易限制与解锁" onClose={() => setRestrictionsOpen(false)}>
        <div className="modal-body card-restrictions"><p className="muted">尾号 {card.last4} · {purposeOf(card.id)}</p>
          {card.status === "LOST" && <Alert>此卡已挂失，不能解除交易限制，请通过挂失 / 补卡办理。</Alert>}
          <div className="restriction-row"><div><strong>卡片冻结</strong><small>{card.status === "FROZEN" ? "刷卡和对外转账已暂停" : card.status === "LOST" ? "已挂失" : "当前未冻结"}</small></div>
            <Button variant="secondary" disabled={p.busy || card.status !== "FROZEN"} onClick={()=>p.onPropose("CARD_UNFREEZE",{card_id:card.id})}>{card.status === "FROZEN" ? "申请解冻" : "无需解冻"}</Button></div>
          <div className="restriction-row"><div><strong>单笔 / 日累计限额</strong><small>¥{money(card.single_limit_cents)} / ¥{money(card.daily_limit_cents)}</small></div>
            <Button variant="ghost" disabled={card.status === "LOST"} onClick={()=>{setRestrictionsOpen(false);setAmount("");setError("");setLimit("daily");}}>调整限额</Button></div>
          <p className="helper">今日剩余 ¥{money(card.remaining_daily_cents)}。日累计用量按交易记录统计，不能通过解锁清零。</p>
          {([ ["online","线上支付"], ["overseas","境外支付"], ["atm","ATM 取现"], ["quickpass","小额免密"] ] as const).map(([key,label])=><div className="restriction-row" key={key}>
            <div><strong>{label}</strong><small>{card.controls[key] ? "已开启" : "已关闭"}</small></div>
            <Button variant="secondary" disabled={p.busy || card.status !== "ACTIVE" || card.controls[key]} onClick={()=>p.onPropose("CARD_CONTROL_LOOSEN",{card_id:card.id,online:null,overseas:null,atm:null,quickpass:null,[key]:true})}>{card.controls[key] ? "已开启" : "申请开启"}</Button>
          </div>)}
          <p className="helper">解冻、开启渠道和提高限额需预演并完成短信验证；冻结中的卡请先解冻。</p>
        </div>
      </Modal>}
      {purposeEditing && card && (
        <Modal title="修改卡片用途" onClose={() => setPurposeEditing(false)}>
          <form className="modal-body card-purpose-form" onSubmit={(event) => { event.preventDefault(); savePurpose(); }}>
            <p className="muted">尾号 {card.last4} · 为这张卡设置一个好记的用途</p>
            <div className="purpose-presets" role="group" aria-label="常用用途">
              {purposeOptions.map((label) => <button type="button" key={label}
                aria-pressed={purposeDraft === label}
                onClick={() => { setPurposeDraft(label); setPurposeError(""); }}>{label}</button>)}
            </div>
            <Field label="用途名称"><input autoFocus value={purposeDraft} maxLength={12}
              onChange={(event) => { setPurposeDraft(event.target.value); setPurposeError(""); }}
              placeholder="选择常用用途，或自定义名称" /></Field>
            <small className="muted">最多 12 个字，仅保存在当前浏览器。</small>
            {purposeError && <Alert tone="error">{purposeError}</Alert>}
            <Button type="submit" disabled={!purposeDraft.trim()}>保存用途</Button>
            <Button variant="ghost" onClick={() => setPurposeEditing(false)}>取消</Button>
          </form>
        </Modal>
      )}
      {numberOpen && card && (
        <Modal title="银行卡号" onClose={closeNumber}>
          <div className="modal-body card-number-details">
            <div className="card-number-bank">
              <span className="brand-mark">S</span>
              <div>
                <strong>SafeBank 借记卡</strong>
                <small>尾号 {card.last4} · {p.profile.display_name}</small>
              </div>
            </div>
            {!numberDetail && (
              <form className="card-pin-form" onSubmit={(event) => {
                event.preventDefault();
                void verifyNumber();
              }}>
                <div className="card-pin-intro">
                  <Icon name="lock" size={22} />
                  <strong>验证身份后查看卡号</strong>
                  <p>请输入 6 位 PIN，验证通过后显示完整卡号。</p>
                </div>
                <Field label="验证 PIN">
                  <input autoFocus type="password" inputMode="numeric"
                    autoComplete="off" maxLength={6} pattern="[0-9]{6}"
                    value={numberPin} disabled={numberLoading}
                    onChange={(event) => setNumberPin(event.target.value.replace(/\D/g, ""))}
                    placeholder="输入 6 位 PIN" />
                </Field>
                {isPreview && <p className="muted card-pin-hint">合成演示验证 PIN：123456</p>}
                {numberError && <Alert tone="error">{numberError}</Alert>}
                <Button type="submit" disabled={numberLoading || numberPin.length !== 6}>
                  {numberLoading ? "正在验证…" : "验证并查看卡号"}
                </Button>
              </form>
            )}
            {numberDetail && (
              <>
                <div className="full-card-number">
                  <span>完整卡号</span>
                  <strong aria-label="完整卡号">
                    {numberDetail.card_number.replace(/(\d{4})(?=\d)/g, "$1 ")}
                  </strong>
                </div>
                {numberDetail.synthetic && <Badge>合成演示卡号</Badge>}
                <Button variant="secondary" onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(numberDetail.card_number);
                    setNumberCopied(true);
                  } catch {
                    p.notify("复制未成功，可长按卡号选择复制。");
                  }
                }}>
                  <Icon name={numberCopied ? "check" : "card"} size={16} />
                  {numberCopied ? "已复制卡号" : "复制卡号"}
                </Button>
              </>
            )}
            <Button variant="ghost" onClick={closeNumber}>关闭并隐藏卡号</Button>
          </div>
        </Modal>
      )}
      {limit && card && (
        <Modal
          title={limit === "single" ? "调整单笔限额" : "调整日累计限额"}
          onClose={() => setLimit(null)}
        >
          <div className="modal-body">
            <p className="muted">
              当前限额 ¥{" "}
              {money(
                limit === "single"
                  ? card.single_limit_cents
                  : card.daily_limit_cents,
              )}
            </p>
            <Field label="新的限额（元）">
              <input
                autoFocus
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="请输入金额"
              />
            </Field>
            {error && <Alert tone="error">{error}</Alert>}
            <Button disabled={p.busy} onClick={setLimits}>
              查看预演
            </Button>
          </div>
        </Modal>
      )}
      {lost && card && (
        <Modal title="挂失银行卡" onClose={() => setLost(false)}>
          <div className="modal-body">
            <Alert tone="error">
              挂失不可逆，尾号 {card.last4} 的旧卡将永久停用。
            </Alert>
            <label className="check-row">
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => setReplace(e.target.checked)}
              />
              同时申请补发新卡
            </label>
            <p className="helper">补卡将继承原限额，渠道恢复默认设置。</p>
            <Button
              disabled={p.busy}
              onClick={async () => {
                await p.onPropose("CARD_LOST", { card_id: card.id, replace });
                setLost(false);
              }}
            >
              查看挂失计划
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
export function Subscriptions(p: PageProps & { embedded?: boolean }) {
  const [tab, setTab] = useState("ACTIVE");
  const q = useLoad(async () => {
    const [list, recurring, upcoming] = await Promise.allSettled([
      read<{
        items: Subscription[];
        total_monthly_cents: number;
        total_annual_cents: number;
      }>(p.sid, "list_subscriptions", {
        status: "ALL",
        cursor: null,
        limit: 100,
      }),
      read<{
        items: {
          instruction_id: string;
          merchant_name: string;
          is_idle: boolean;
          occurrences: number;
          usage_evidence: {
            recent_usage_count: number;
            previous_usage_count: number;
          };
        }[];
      }>(p.sid, "detect_subscriptions", { lookback_days: 120 }),
      read<Upcoming>(p.sid, "upcoming_charges", { days: 7, account_id: null }),
    ]);
    return { list, recurring, upcoming };
  }, [p.sid, p.revision]);
  const list = q.value?.list.status === "fulfilled" ? q.value.list.value : null;
  const recurring =
    q.value?.recurring.status === "fulfilled"
      ? q.value.recurring.value.items
      : [];
  const upcoming =
    q.value?.upcoming.status === "fulfilled" ? q.value.upcoming.value : null;
  const items = list?.items.filter((s) => s.status === tab) ?? [];
  return (
    <>
      {!p.embedded && <div className="page-heading">
        <div>
          <div className="eyebrow">RECURRING PAYMENTS</div>
          <h1>订阅管理</h1>
          <p>看清每项订阅，留住真正需要的。</p>
        </div>
        <a href="#assistant" className="button secondary">
          <Icon name="assistant" size={17} />
          与小安一起整理
        </a>
      </div>}
      {q.loading ? (
        <Loading />
      ) : (
        <>
          <div className="subscription-summary">
            <div>
              <span>每月订阅支出</span>
              <Money
                value={list?.total_monthly_cents ?? 0}
                className="bill-total"
              />
              <p>按当前有效订阅折算</p>
            </div>
            <div>
              <span>年化支出</span>
              <Money value={list?.total_annual_cents ?? 0} />
              <p>按周 / 按月逐项折算后合计</p>
            </div>
            <div>
              <span>未来 7 天预留</span>
              <Money value={upcoming?.reserve_total_cents ?? 0} />
              <p>{upcoming?.items.length ?? 0} 笔即将到期扣款</p>
            </div>
          </div>
          <div className="subscription-layout">
            <Panel
              title="我的订阅"
              meta={
                <Badge>
                  {list?.items.filter((s) => s.status === "ACTIVE").length ?? 0}{" "}
                  项有效
                </Badge>
              }
            >
              <Segments
                items={[
                  { id: "ACTIVE", label: "进行中" },
                  { id: "DELETED", label: "已取消" },
                ]}
                value={tab}
                onChange={setTab}
              />
              {!list ? (
                <Alert tone="error">
                  订阅列表暂不可用。
                  <button className="text-button" onClick={q.reload}>
                    重试
                  </button>
                </Alert>
              ) : !items.length ? (
                <Empty
                  title={tab === "ACTIVE" ? "暂无有效订阅" : "暂无已取消订阅"}
                  detail="订阅状态变更后会显示在这里。"
                />
              ) : (
                items.map((s, i) => {
                  const idle = recurring.find(
                    (r) => r.instruction_id === s.instruction_id,
                  )?.is_idle;
                  return (
                    <div className="subscription-row" key={s.instruction_id}>
                      <span className="subscription-logo" data-color={i % 3}>
                        {s.merchant_name.slice(0, 1)}
                      </span>
                      <div>
                        <h3>
                          {s.merchant_name}{" "}
                          {idle && <Badge tone="amber">可能闲置</Badge>}
                        </h3>
                        <p>
                          {s.period === "MONTHLY" ? "每月" : "每周"}扣费 · 下次{" "}
                          {date(s.next_due_at)}
                        </p>
                        <small>年化 ¥ {money(s.annual_cost_cents)}</small>
                      </div>
                      <Money value={s.amount_cents} />
                      {s.status === "ACTIVE" ? (
                        <Button
                          variant="ghost"
                          disabled={p.busy}
                          onClick={() =>
                            p.onPropose("MANDATE_CANCEL", {
                              instruction_id: s.instruction_id,
                            })
                          }
                        >
                          管理授权
                        </Button>
                      ) : (
                        <Badge>已取消</Badge>
                      )}
                    </div>
                  );
                })
              )}
            </Panel>
            <aside>
              <Panel title="即将扣费">
                {upcoming?.items.length ? (
                  upcoming.items.map((u) => (
                    <div className="upcoming-row" key={u.billing_id}>
                      <span className="date-tile">
                        {date(u.due_at).replace("/", "月")}
                        <small>待扣费</small>
                      </span>
                      <div>
                        <strong>{u.merchant_name}</strong>
                        <small>¥ {money(u.amount_cents)}</small>
                      </div>
                    </div>
                  ))
                ) : (
                  <Empty
                    title="近期没有扣款"
                    detail="未来 7 天暂未查询到到期安排。"
                  />
                )}
              </Panel>
              <div className="soft-note">
                <Icon name="shield" />
                <h3>取消后，授权即撤销</h3>
                <p>
                  商户再次发起扣款时将由网关检查并拦截无效授权。取消操作需要您明确确认。
                </p>
              </div>
            </aside>
          </div>
          {recurring.some((r) => r.is_idle) && (
            <Alert>
              闲置判断依据：近 60 天没有非扣费使用记录，60–120
              天前曾有使用。结果仅供您决定是否保留。
            </Alert>
          )}
        </>
      )}
    </>
  );
}
interface Holding {
  account_id: string;
  product_id: string;
  units: string;
  available_units: string;
  market_value_cents: number;
  unrealized_change_cents: number;
  reserved_receivable_cents: number;
}
interface FundOrder {
  id: string;
  product_id: string;
  type: string;
  status: string;
  amount_cents: number;
  units: string;
  confirm_at: string | null;
  settle_at: string | null;
}
export function Wealth(p: PageProps) {
  const [tab, setTab] = useState("products");
  const [productFilter, setProductFilter] = useState("all");
  const [riskDetail, setRiskDetail] = useState<Product>();
  const [chosen, setChosen] = useState<string[]>([]);
  const [detail, setDetail] = useState<Product>();
  const [trade, setTrade] = useState<{
    product: Product;
    kind: "buy" | "sell";
  }>();
  const [account, setAccount] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [assessmentEditing, setAssessmentEditing] = useState(false);
  const riskProfile = p.profile.risk_profile;
  const hasAssessment = riskProfile.level !== null;
  useEffect(() => {
    setAssessmentEditing(false);
    setAnswers({});
  }, [p.revision]);
  const [comparison, setComparison] = useState<Product[]>();
  const q = useLoad(async () => {
    const results = await Promise.allSettled([
      read<{ items: Product[] }>(p.sid, "list_products", {
        max_risk_level: null,
        product_type: null,
        cursor: null,
        limit: 100,
      }),
      read<{ holdings: Holding[]; orders: FundOrder[] }>(
        p.sid,
        "get_holdings",
        { account_id: null, product_id: null },
      ),
      read<{ questionnaire_id: string; questions: Question[] }>(
        p.sid,
        "get_risk_questionnaire",
        {},
      ),
    ]);
    return {
      products: results[0],
      holdings: results[1],
      questionnaire: results[2],
    };
  }, [p.sid, p.revision]);
  const products =
    q.value?.products.status === "fulfilled"
      ? q.value.products.value.items
      : [];
  const holdings =
    q.value?.holdings.status === "fulfilled" ? q.value.holdings.value : null;
  const questionnaire =
    q.value?.questionnaire.status === "fulfilled"
      ? q.value.questionnaire.value
      : null;
  const assessed = riskProfile.is_valid && riskProfile.level !== null;
  const matchesRisk = (product: Product) => assessed && product.risk_level <= riskProfile.level!;
  const visibleProducts = products.filter(product => productFilter === "matched" ? matchesRisk(product) : productFilter === "low" ? product.risk_level <= 2 : true);
  const availableHolding = (productId: string, accountId?: string) => holdings?.holdings.find(h => h.product_id === productId && (!accountId || h.account_id === accountId) && /[1-9]/.test(h.available_units));
  function openTrade(product: Product, kind: "buy" | "sell", accountId?: string) {
    const holding = availableHolding(product.id, accountId);
    setTrade({product,kind});
    setAccount(kind === "sell" ? holding?.account_id ?? "" : p.accounts[0]?.id ?? "");
    setAmount(kind === "sell" ? holding?.available_units ?? "" : "");
    setError("");
  }
  async function submitTrade() {
    if (!trade) return;
    setError("");
    if (!account) {
      setError("请选择交易账户。");
      return;
    }
    if (trade.kind === "sell") {
      if (!/^\d+(\.\d{1,4})?$/.test(amount) || !/[1-9]/.test(amount)) {
        setError("请输入大于 0、最多 4 位小数的份额。");
        return;
      }
      const holding = availableHolding(trade.product.id, account);
      const rawUnits = (value: string) => { const [whole, fraction = ""] = value.split("."); return BigInt(whole) * 10000n + BigInt(fraction.padEnd(4,"0")); };
      if (!holding || rawUnits(amount) > rawUnits(holding.available_units)) {
        setError("赎回份额超过该账户的可用持仓。"); return;
      }
      await p.onPropose("FUND_REDEEM", {
        account_id: account,
        product_id: trade.product.id,
        units: amount.includes(".")
          ? `${amount.split(".")[0]}.${amount.split(".")[1].padEnd(4, "0")}`
          : `${amount}.0000`,
      });
    } else {
      if (!matchesRisk(trade.product)) { setError(assessed ? "产品风险等级高于您的测评等级。" : "请先完成有效风险测评。"); return; }
      const v = parseAmountToCents(amount);
      if ("error" in v) {
        setError(v.error);
        return;
      }
      if (v.cents < trade.product.min_amount_cents) {
        setError(`起购金额为 ¥ ${money(trade.product.min_amount_cents)}`);
        return;
      }
      if (v.cents > (p.accounts.find(item => item.id === account)?.available_cents ?? 0)) { setError("申购金额超过当前账户可用余额。"); return; }
      await p.onPropose("FUND_SUBSCRIBE", {
        account_id: account,
        product_id: trade.product.id,
        amount_cents: v.cents,
      });
    }
    setTrade(undefined);
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">WEALTH & INVESTMENTS</div>
          <h1>理财投资</h1>
          <p>了解产品与风险，再安排您的资金。</p>
        </div>
        <button className="risk-profile" onClick={() => setTab("assessment")}>
          <span className="soft-icon">
            <Icon name="shield" />
          </span>
          <span>
            我的风险等级
            <strong>
              {p.profile.risk_profile.is_valid
                ? `R${p.profile.risk_profile.level} · 测评有效`
                : "尚未完成有效测评"}
            </strong>
          </span>
          <Icon name="chevron" size={16} />
        </button>
      </div>
      <Segments
        items={[
          { id: "products", label: "产品精选" },
          { id: "holdings", label: "我的持仓与订单" },
          { id: "assessment", label: "风险测评" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {q.loading ? (
        <Loading />
      ) : tab === "products" ? (
        <>
          <div className="wealth-risk-banner">
            <div><Icon name="shield" size={21}/><span><strong>{assessed ? `我的风险等级 R${riskProfile.level}` : "先了解您的风险承受能力"}</strong><small>{assessed ? `${products.filter(matchesRisk).length} 款产品在当前风险等级内 · 非收益推荐` : "完成六题测评后，再选择匹配的产品"}</small></span></div>
            <button type="button" className="text-button" onClick={() => setTab("assessment")}>{assessed ? "查看/更新测评" : "开始风险测评"}<Icon name="chevron" size={14}/></button>
          </div>
          <div className="wealth-shortcuts"><Button variant="secondary" onClick={() => setTab("holdings")}><Icon name="wallet" size={16}/>一键赎回 · 我的持仓</Button><small>申购和赎回都需核对并确认</small></div>
          <div className="product-filters" role="group" aria-label="产品筛选">
            {[ ["all","全部产品"], ["matched","风险匹配"], ["low","低风险 R1–R2"] ].map(([value,label]) => <button type="button" key={value} aria-pressed={productFilter === value} onClick={() => setProductFilter(value)}>{label}</button>)}
          </div>
          <div className="section-head">
            <h2>产品推荐 · {visibleProducts.length} 款</h2>
            <span className="helper">近 30 个交易日涨跌幅 · 非收益承诺</span>
          </div>
          {q.value?.products.status === "rejected" ? (
            <Alert tone="error">
              产品服务暂不可用。
              <button className="text-button" onClick={q.reload}>
                重试
              </button>
            </Alert>
          ) : visibleProducts.length ? (
            <div className="product-list">
              {visibleProducts.map((product) => (
                <article className="product-row" key={product.id}>
                  <div className="product-main">
                    <span className="product-type">
                      {product.product_type === "MONEY_MARKET"
                        ? "货币基金"
                        : product.product_type === "BOND"
                          ? "债券产品"
                          : "混合产品"}
                    </span>
                    <button
                      className="product-title"
                      onClick={async () => {
                        try {
                          setDetail(
                            await read<Product>(p.sid, "get_product", {
                              product_id: product.id,
                            }),
                          );
                        } catch (e) {
                          p.notify((e as Error).message);
                        }
                      }}
                    >
                      {product.name}
                      <Icon name="chevron" size={16} />
                    </button>
                    <div className="product-tags">
                      <Badge>
                        R{product.risk_level} · {product.risk_label}
                      </Badge>
                      <span>¥{money(product.min_amount_cents)} 起购</span>
                    </div>
                  </div>
                  <div className="product-return">
                    <strong
                      className={
                        product.recent_return_bp >= 0 ? "gain" : "loss"
                      }
                    >
                      {product.recent_return_bp >= 0 ? "+" : ""}
                      {(product.recent_return_bp / 100).toFixed(2)}
                      <small>%</small>
                    </strong>
                    <span>近 30 交易日</span>
                  </div>
                  <div className="product-settle">
                    <strong>T+{product.confirm_days}</strong>
                    <span>确认 · 赎回 T+{product.settle_days}</span>
                  </div>
                  <div className="product-actions">
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={chosen.includes(product.id)}
                        disabled={
                          !chosen.includes(product.id) && chosen.length >= 4
                        }
                        onChange={(e) =>
                          setChosen(
                            e.target.checked
                              ? [...chosen, product.id]
                              : chosen.filter((id) => id !== product.id),
                          )
                        }
                      />
                      对比
                    </label>
                    <button type="button" className="text-button" onClick={() => setRiskDetail(product)}>风险评估</button>
                    {availableHolding(product.id) && <Button variant="ghost" onClick={() => openTrade(product,"sell")}>赎回</Button>}
                    <Button variant="secondary" disabled={!matchesRisk(product)} onClick={() => openTrade(product,"buy")}>
                      {!assessed ? "需先测评" : matchesRisk(product) ? "一键申购" : "超出风险等级"}
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <Empty title="暂无在售产品" />
          )}
          {chosen.length > 0 && (
            <div className="compare-bar">
              <span>已选择 {chosen.length} / 4 项产品</span>
              <Button
                disabled={chosen.length < 2}
                onClick={async () => {
                  try {
                    const r = await read<{ items: Product[] }>(
                      p.sid,
                      "compare_products",
                      { product_ids: chosen },
                    );
                    setComparison(r.items);
                  } catch (e) {
                    p.notify((e as Error).message);
                  }
                }}
              >
                比较产品 <Icon name="arrow" size={16} />
              </Button>
            </div>
          )}
          <Alert>
            投资有风险，产品不保证本金及收益。申购前会检查风险适当性、可用余额与未来扣费预留。
          </Alert>
        </>
      ) : tab === "holdings" ? (
        <>
          <Panel title="我的持仓">
            {holdings ? (
              holdings.holdings.length ? (
                holdings.holdings.map((h) => (
                  <div
                    className="data-row"
                    key={`${h.account_id}-${h.product_id}`}
                  >
                    <div>
                      <strong>
                        {products.find((p) => p.id === h.product_id)?.name ??
                          h.product_id}
                      </strong>
                      <small>
                        可用份额 {h.available_units} · 市值 ¥
                        {money(h.market_value_cents)}
                      </small>
                    </div>
                    <span>待到账 ¥{money(h.reserved_receivable_cents)}</span>
                    <Button
                      variant="secondary"
                      onClick={() => {
                        const product = products.find(
                          (p) => p.id === h.product_id,
                        );
                        if (product) {
                          openTrade(product, "sell", h.account_id);
                        }
                      }}
                    >
                      一键赎回
                    </Button>
                  </div>
                ))
              ) : (
                <Empty
                  title="您的第一份持仓，从了解开始"
                  detail="申购确认后，产品份额会显示在这里。"
                />
              )
            ) : (
              <Alert>
                持仓服务暂不可用。
                <button className="text-button" onClick={q.reload}>
                  重试
                </button>
              </Alert>
            )}
          </Panel>
          <Panel title="在途与近期订单">
            {holdings?.orders.length ? (
              holdings.orders.map((o) => (
                <div className="data-row" key={o.id}>
                  <div>
                    <strong>
                      {products.find((p) => p.id === o.product_id)?.name ??
                        o.product_id}
                    </strong>
                    <small>
                      {o.type} · {o.id}
                    </small>
                  </div>
                  <Money value={o.amount_cents} />
                  <Badge>{o.status}</Badge>
                </div>
              ))
            ) : (
              <Empty
                title="暂无订单"
                detail="下单、确认与结算状态将在这里更新。"
              />
            )}
          </Panel>
        </>
      ) : hasAssessment && !assessmentEditing ? (
        <Panel
          title="我的风险测评"
          meta={
            <Button
              variant="ghost"
              onClick={() => {
                setAnswers({});
                setAssessmentEditing(true);
              }}
            >
              <Icon name="repeat" size={15} />
              更新风险测评
            </Button>
          }
        >
          <div className="assessment-result">
            <span className="assessment-done">
              <Icon name="check" size={27} />
            </span>
            <h2>
              {riskProfile.is_valid ? "风险测评已完成" : "风险测评已过期"}
            </h2>
            <p>
              {riskProfile.is_valid
                ? "您的测评结果已生效，可用于投资适当性检查。"
                : "请更新测评后再办理需要有效测评的理财业务。"}
            </p>
            <div className="assessment-level">
              <span>当前风险等级</span>
              <strong>R{riskProfile.level}</strong>
            </div>
            <dl>
              <div>
                <dt>测评状态</dt>
                <dd>
                  {riskProfile.is_valid ? "已完成 · 有效" : "已完成 · 已过期"}
                </dd>
              </div>
              <div>
                <dt>有效期至</dt>
                <dd>
                  {riskProfile.valid_until
                    ? new Date(riskProfile.valid_until).toLocaleDateString(
                        "zh-CN",
                        {
                          timeZone: "Asia/Shanghai",
                          year: "numeric",
                          month: "long",
                          day: "numeric",
                        },
                      )
                    : "—"}
                </dd>
              </div>
            </dl>
          </div>
        </Panel>
      ) : (
        <Panel
          title="了解您的风险承受能力"
          meta={
            hasAssessment ? (
              <Button
                variant="ghost"
                onClick={() => setAssessmentEditing(false)}
              >
                返回测评结果
              </Button>
            ) : (
              <Badge>6 题 · 有效期 12 个月</Badge>
            )
          }
        >
          <p className="muted">
            请按实际情况逐题作答。测评等级由银行根据您的答案计算，小安不会代您回答。
          </p>
          {questionnaire ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (questionnaire.questions.some((q) => !answers[q.id])) {
                  p.notify("请完成全部六题。");
                  return;
                }
                void p.onPropose("ASSESSMENT_SUBMIT", {
                  questionnaire_id: questionnaire.questionnaire_id,
                  answers: questionnaire.questions.map((q) => ({
                    question_id: q.id,
                    option_id: answers[q.id],
                  })),
                });
              }}
            >
              <div className="questionnaire">
                {questionnaire.questions.map((q, i) => (
                  <fieldset key={q.id}>
                    <legend>
                      <span>0{i + 1}</span>
                      {q.prompt}
                    </legend>
                    <div>
                      {q.options.map((o) => (
                        <label
                          className={answers[q.id] === o.id ? "selected" : ""}
                          key={o.id}
                        >
                          <input
                            type="radio"
                            name={q.id}
                            value={o.id}
                            checked={answers[q.id] === o.id}
                            onChange={() =>
                              setAnswers({ ...answers, [q.id]: o.id })
                            }
                            required
                          />
                          {o.label}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ))}
              </div>
              <div className="form-footer">
                <span>{Object.keys(answers).length}/6 已完成</span>
                <Button
                  type="submit"
                  disabled={p.busy || Object.keys(answers).length !== 6}
                >
                  提交测评，查看预演
                </Button>
              </div>
            </form>
          ) : (
            <Alert>
              问卷暂不可用。
              <button className="text-button" onClick={q.reload}>
                重试
              </button>
            </Alert>
          )}
        </Panel>
      )}
      {detail && (
        <Modal title="产品详情" onClose={() => setDetail(undefined)} wide>
          <div className="modal-body">
            <div className="product-detail-heading">
              <div>
                <Badge>
                  R{detail.risk_level} · {detail.risk_label}
                </Badge>
                <h2>{detail.name}</h2>
              </div>
              <div>
                <strong>{detail.nav}</strong>
                <small>单位净值 · {detail.nav_date}</small>
              </div>
            </div>
            <dl>
              <div>
                <dt>起购金额</dt>
                <dd>¥ {money(detail.min_amount_cents)}</dd>
              </div>
              <div>
                <dt>申购确认</dt>
                <dd>T+{detail.confirm_days} 个交易日</dd>
              </div>
              <div>
                <dt>赎回到账</dt>
                <dd>T+{detail.settle_days} 个交易日</dd>
              </div>
            </dl>
            <div className="disclosure">
              <h3>风险揭示 · {detail.disclosure_version}</h3>
              <p>{detail.risk_disclosure}</p>
            </div>
            <Button disabled={!matchesRisk(detail)}
              onClick={() => {
                openTrade(detail,"buy");
                setDetail(undefined);
              }}
            >
              申购此产品
            </Button>
          </div>
        </Modal>
      )}
      {riskDetail && <Modal title="产品风险评估" onClose={() => setRiskDetail(undefined)}>
        <div className="modal-body product-risk-detail"><h3>{riskDetail.name}</h3>
          <Badge tone={matchesRisk(riskDetail) ? "success" : "amber"}>{!assessed ? "需先完成有效测评" : matchesRisk(riskDetail) ? "风险等级匹配" : "超出当前承受等级"}</Badge>
          <dl><div><dt>产品风险</dt><dd>R{riskDetail.risk_level} · {riskDetail.risk_label}</dd></div><div><dt>我的等级</dt><dd>{assessed ? `R${riskProfile.level}` : "未完成有效测评"}</dd></div><div><dt>流动性</dt><dd>确认 T+{riskDetail.confirm_days} · 赎回到账 T+{riskDetail.settle_days}</dd></div></dl>
          <Alert>{riskDetail.risk_disclosure}</Alert><p className="helper">等级匹配不等于保本或保证收益，实际适当性由安全网关与银行执行时检查。</p>
          <Button variant="secondary" onClick={() => {setRiskDetail(undefined);setTab("assessment");}}>查看我的风险测评</Button>
        </div></Modal>}
      {comparison && (
        <Modal title="产品比较" onClose={() => setComparison(undefined)} wide>
          <div className="comparison-scroll">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>比较项目</th>
                  {comparison.map((p) => (
                    <th key={p.id}>{p.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  [
                    "风险等级",
                    (p: Product) => `R${p.risk_level} · ${p.risk_label}`,
                  ],
                  ["单位净值", (p: Product) => p.nav],
                  [
                    "近 30 交易日",
                    (p: Product) => `${(p.recent_return_bp / 100).toFixed(2)}%`,
                  ],
                  [
                    "起购金额",
                    (p: Product) => `¥ ${money(p.min_amount_cents)}`,
                  ],
                  ["确认日", (p: Product) => `T+${p.confirm_days}`],
                  ["赎回到账", (p: Product) => `T+${p.settle_days}`],
                ].map(([label, fn]) => (
                  <tr key={String(label)}>
                    <th>{String(label)}</th>
                    {comparison.map((p) => (
                      <td key={p.id}>{(fn as (p: Product) => string)(p)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="modal-body">
            <Alert>历史表现仅供参考，不代表未来收益。</Alert>
          </div>
        </Modal>
      )}
      {trade && (
        <Modal
          title={trade.kind === "buy" ? "申购理财产品" : "赎回产品份额"}
          onClose={() => setTrade(undefined)}
        >
          <div className="modal-body">
            <Badge>
              R{trade.product.risk_level} · {trade.product.risk_label}
            </Badge>
            <h3>{trade.product.name}</h3>
            <Field label="交易账户">
              <ChoiceSelect
                label="交易账户"
                value={account}
                onChange={setAccount}
                placeholder="请选择账户"
                options={p.accounts.filter(a => trade.kind === "buy" || !!availableHolding(trade.product.id,a.id)).map((a) => ({
                  value: a.id,
                  label: a.display_name,
                  description: a.account_no_masked,
                }))}
              />
            </Field>
            <Field
              label={
                trade.kind === "buy"
                  ? "申购金额（元）"
                  : "赎回份额（最多四位小数）"
              }
            >
              <input
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                inputMode="decimal"
                placeholder={
                  trade.kind === "buy"
                    ? `¥ ${money(trade.product.min_amount_cents)} 起购`
                    : "0.0000"
                }
              />
            </Field>
            {trade.kind === "sell" && <div className="redeem-available"><span>可用份额 {availableHolding(trade.product.id,account)?.available_units ?? "0.0000"}</span><button type="button" className="text-button" onClick={() => setAmount(availableHolding(trade.product.id,account)?.available_units ?? "")}>全部赎回</button></div>}
            {error && <Alert tone="error">{error}</Alert>}
            <p className="helper">
              {trade.kind === "buy"
                ? `预计 T+${trade.product.confirm_days} 确认，资金变化以预演与实际执行结果为准。`
                : `预计 T+${trade.product.settle_days} 到账，在途金额不能提前使用。`}
            </p>
            <Button disabled={p.busy} onClick={submitTrade}>
              查看预演
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
