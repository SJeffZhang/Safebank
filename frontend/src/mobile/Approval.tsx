import { explainCode } from "./copy";
import { Fragment, useEffect, useRef, useState } from "react";
import { getView, getPlan, request, isPreview, demoSms, BankError } from "./api";
import { uid, type PlanView, type PlanStatus, type Decision } from "./types";
import {
  Modal,
  Button,
  Icon,
  Alert,
  Loading,
  Money,
  money,
  dateTime,
  Badge,
} from "./ui";
import { isPlanTerminal, planPresentation } from "./plan-status";
export function Approval({
  id,
  onClose,
  onComplete,
  onDecision,
  onResume,
}: {
  id: string;
  onClose: () => void;
  onComplete: () => void;
  onDecision: (d: Decision) => void;
  onResume: (v: PlanView) => Promise<void>;
}) {
  const completed = useRef(false);
  const [view, setView] = useState<PlanView>();
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [otp, setOtp] = useState("");
  const [sent, setSent] = useState(false);
  const [sms, setSms] = useState("");
  const [retry, setRetry] = useState(0);
  const [ack, setAck] = useState(false);
  const [status, setStatus] = useState<PlanStatus>();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    let active = true;
    completed.current = false;
    setView(undefined);
    setStatus(undefined);
    setSent(false);
    setSms("");
    setRetry(0);
    setError("");
    setReady(false);
    setAck(false);
    setOtp("");
    getView(id)
      .then(async (v) => {
        if (active) {
          setView(v);
          setTimeout(() => {
            if (active) setReady(true);
          }, 1000);
          if (["USED", "CONFIRMED"].includes(v.status)) {
            const current = await getPlan(v.plan_id);
            if (!active) return;
            if (current.plan_id !== v.plan_id) throw new Error("计划身份不一致，请重新查询。");
            setStatus(current);
          }
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [id]);
  useEffect(() => {
    const t = setInterval(() => {
      setNow(Date.now());
      setRetry((v) => Math.max(0, v - 1));
    }, 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (
      !status ||
      isPlanTerminal(status.status)
    )
      return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const p = await getPlan(status.plan_id);
        if (controller.signal.aborted) return;
        if (p.plan_id !== status.plan_id) throw new Error("计划身份不一致，请重新查询。");
        setStatus(p);
        if (!isPlanTerminal(p.status)) timer = setTimeout(poll, 1800);
      } catch (e) {
        if (!controller.signal.aborted)
          setError(
            e instanceof Error ? e.message : "结果查询中断，请在安全中心重查",
          );
      }
    };
    timer = setTimeout(poll, 1800);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [status?.plan_id, status?.status]);
  useEffect(() => {
    if (status?.status === "EXECUTED" && !completed.current) {
      completed.current = true;
      onComplete();
    }
  }, [status?.status]);
  const expired = !!view && now >= Date.parse(view.expires_at_wall);
  const currentStatus = status?.status ?? (expired && view?.status === "PENDING_APPROVAL" ? "EXPIRED" : view?.status ?? "CHECKING");
  const presentation = planPresentation(currentStatus);
  const awaiting = currentStatus === "PENDING_APPROVAL";
  async function submit(reject = false) {
    if (!view) return;
    setBusy(true);
    setError("");
    try {
      await request(`/approvals/${id}/${reject ? "reject" : "confirm"}`, {
        request_id: uid(),
        view_hash: view.view_hash,
        ...(reject ? {} : { otp: otp || null, risk_ack: ack }),
      });
      if (reject) {
        if (view.origin === "agent") await onResume(view);
        onComplete();
        onClose();
        return;
      }
      if (view.origin === "agent") {
        await onResume(view);
      } else {
        await request(`/plans/${view.plan_id}/execute`, { request_id: uid() });
      }
      const p = await getPlan(view.plan_id);
      if (p.plan_id !== view.plan_id) throw new Error("计划身份不一致，请重新查询。");
      setStatus(p);
      if (p.decision?.approval_id && p.decision.approval_id !== id)
        onDecision(p.decision);
    } catch (e) {
      if (e && typeof e === "object" && "details" in e) {
        const d = (e as { details: { decision?: Decision } }).details.decision;
        if (d) {
          onDecision(d);
          return;
        }
      }
      setError(e instanceof Error ? e.message : "操作未完成，请查询计划状态");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="核对并确认"
      onClose={() => {
        if (!busy) onClose();
      }}
      wide
    >
      {!view && !error ? <Loading /> : null}
      {error && <Alert tone="error">{error}</Alert>}
      {view && (
        <>
          <div className="approval-intro">
            <span className={`approval-emblem ${view.risk.toLowerCase()}`}>
              <Icon name="shield" size={27} />
            </span>
            <div>
              <Badge tone={awaiting ? (view.risk === "RED" ? "danger" : "amber") : presentation.tone}>
                {awaiting ? (view.risk === "RED" ? "高风险 · 需短信验证" : "待您确认") : presentation.label}
              </Badge>
              <h3>{awaiting ? "请确认这份操作计划" : presentation.label}</h3>
              <p>
                {awaiting ? (isPreview ? "合成数据演示 · 不涉及真实资金" : "预演已完成，确认后才会提交执行") : presentation.message}
              </p>
            </div>
          </div>
          <div className="approval-content">
            {view.steps.map((s) => (
              <section key={s.step_id} className="approval-step">
                <h3>{s.title}</h3>
                {s.irreversible && (
                  <Alert tone="error">此操作不可逆，请仔细核对。</Alert>
                )}
                <dl>
                  {s.fields.map((f) => (
                    <div key={f.key}>
                      <dt>{f.label}</dt>
                      <dd>
                        {f.unit === "datetime"
                          ? typeof f.value === "string" && Number.isFinite(Date.parse(f.value))
                            ? dateTime(f.value)
                            : "时间待核实"
                          : f.unit === "cents" || f.unit === "分"
                          ? `¥ ${money(Number(f.value))}`
                          : typeof f.value === "boolean"
                            ? f.value
                              ? "是 / 开启"
                              : "否 / 关闭"
                            : Array.isArray(f.value)
                              ? f.value.join("、")
                              : `${String(f.value ?? "—")}${f.unit ? ` ${f.unit}` : ""}`}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
            {view.preflight && (
              <section className="preflight">
                <div className="section-label">
                  <Icon name="chart" size={17} />
                  资金变化预演
                </div>
                {view.preflight.balances.map((b) => (
                  <Fragment key={b.account_id}>
                    <div className="balance-diff">
                      <span>可用余额</span>
                      <Money value={b.available_before_cents} animate={false} />
                      <Icon name="arrow" size={18} />
                      <Money value={b.available_after_cents} animate={false} />
                    </div>
                    <div className="balance-diff">
                      <span>当日限额占用</span>
                      <Money value={b.daily_used_before_cents} animate={false} />
                      <Icon name="arrow" size={18} />
                      <Money value={b.daily_used_after_cents} animate={false} />
                    </div>
                  </Fragment>
                ))}
                <div className="reserve-note">
                  <Icon name="clock" size={16} />
                  {view.preflight.reserve_projection_available === true ? <>
                    计划前未来 7 天预留{" "}
                    <strong>¥ {money(view.preflight.scheduled_reserve_cents)}</strong>
                  </> : "未来 7 天预留未计算"}
                </div>
              </section>
            )}
            {view.findings.map((f, i) => (
              <Alert key={i}>{f.message || explainCode(f.code)}</Alert>
            ))}
            {view.disclosures.map((d) => (
              <div className="disclosure" key={d.product_id}>
                <h4>风险揭示 · {d.version}</h4>
                <p>{d.text}</p>
              </div>
            ))}
          </div>
          {status ? (
            <div className="execution-result">
              <Icon
                name={status.status === "EXECUTED" ? "check" : "clock"}
                size={30}
              />
              <h3>
                {presentation.label}
              </h3>
              <p>{presentation.message}</p>
              <code>{status.plan_id}</code>
              <Button onClick={onClose}>完成</Button>
            </div>
          ) : (
            <>
              <div className="approval-auth">
                {view.risk_ack_required && (
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={ack}
                      onChange={(e) => setAck(e.target.checked)}
                    />
                    我已阅读完整风险揭示，理解本次操作的风险
                  </label>
                )}
                {view.otp_required && (
                  <div>
                    <label className="field-label" htmlFor="approval-otp">
                      短信验证码
                    </label>
                    <div className="otp-row">
                      <input
                        id="approval-otp"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        maxLength={6}
                        value={otp}
                        placeholder="输入 6 位验证码"
                        onChange={(e) =>
                          setOtp(e.target.value.replace(/\D/g, ""))
                        }
                      />
                      <Button
                        variant="secondary"
                        disabled={retry > 0 || busy || expired}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            const r = await request<{
                              retry_after_seconds: number;
                            }>(`/approvals/${id}/otp`, {
                              request_id: uid(),
                              view_hash: view.view_hash,
                            });
                            setSent(true);
                            setRetry(r.retry_after_seconds);
                            if (!isPreview) {
                              // Demo stack only: the console reads the simulated SMS (D5).
                              demoSms(id)
                                .then((m) => setSms(`【BankSense 演示短信】验证码 ${m.code}，2 分钟内有效，请勿泄露。`))
                                .catch((e) =>
                                  setSms(
                                    e instanceof BankError && e.code === "CONSOLE_LOGIN_REQUIRED"
                                      ? "演示短信需要先登录演示控制台。"
                                      : "",
                                  ),
                                );
                            }
                          } catch (e) {
                            setError((e as Error).message);
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        {retry > 0
                          ? `${retry}s 后重发`
                          : sent
                            ? "重新发送"
                            : "获取验证码"}
                      </Button>
                    </div>
                    {!isPreview && sent && sms && (
                      <p className="helper demo-sms" role="status">{sms}</p>
                    )}
                    {isPreview && sent && (
                      <p className="helper">
                        演示验证码：123456，仅用于本地预览。
                      </p>
                    )}
                  </div>
                )}
                <p className="helper">
                  {expired
                    ? "授权已过期，请重新发起操作。"
                    : `有效至 ${dateTime(view.expires_at_wall)}`}
                </p>
              </div>
              <div className="modal-actions">
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => submit(true)}
                >
                  拒绝此计划
                </Button>
                <button
                  className="button"
                  disabled={
                    busy ||
                    !ready ||
                    expired ||
                    view.status !== "PENDING_APPROVAL" ||
                    (view.otp_required && otp.length !== 6) ||
                    (view.risk_ack_required && !ack)
                  }
                  onClick={(e) => {
                    if (e.isTrusted) void submit();
                  }}
                >
                  {busy ? "正在提交…" : "确认操作"}
                  <Icon name="arrow" size={17} />
                </button>
              </div>
            </>
          )}
        </>
      )}
    </Modal>
  );
}
