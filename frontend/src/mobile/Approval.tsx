import { explainCode } from "./copy";
import { useEffect, useState } from "react";
import { getView, getPlan, request, isPreview } from "./api";
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
  const [view, setView] = useState<PlanView>();
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [otp, setOtp] = useState("");
  const [sent, setSent] = useState(false);
  const [retry, setRetry] = useState(0);
  const [ack, setAck] = useState(false);
  const [status, setStatus] = useState<PlanStatus>();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    let active = true;
    setView(undefined);
    setStatus(undefined);
    setSent(false);
    setRetry(0);
    setError("");
    setReady(false);
    setAck(false);
    setOtp("");
    getView(id)
      .then((v) => {
        if (active) {
          setView(v);
          setTimeout(() => {
            if (active) setReady(true);
          }, 1000);
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
      ["EXECUTED", "FAILED", "DENIED", "REJECTED"].includes(status.status)
    )
      return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const p = await getPlan(status.plan_id);
        if (controller.signal.aborted) return;
        setStatus(p);
        if (p.status === "EXECUTED") {
          onComplete();
          return;
        }
        if (["UNKNOWN", "EXECUTING", "RECONCILING"].includes(p.status))
          timer = setTimeout(poll, 1800);
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
  const expired = !!view && now >= Date.parse(view.expires_at_wall);
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
      setStatus(p);
      if (p.status === "EXECUTED") onComplete();
      if (p.decision.approval_id && p.decision.approval_id !== id)
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
              <Badge tone={view.risk === "RED" ? "danger" : "amber"}>
                {view.risk === "RED" ? "高风险 · 需短信验证" : "待您确认"}
              </Badge>
              <h3>请确认这份操作计划</h3>
              <p>
                {isPreview
                  ? "合成数据演示 · 不涉及真实资金"
                  : "预演已完成，确认后才会提交执行"}
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
                        {f.unit === "cents" || f.unit === "分"
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
                  <div key={b.account_id} className="balance-diff">
                    <span>可用余额</span>
                    <Money value={b.available_before_cents} />
                    <Icon name="arrow" size={18} />
                    <Money value={b.available_after_cents} />
                  </div>
                ))}
                <div className="reserve-note">
                  <Icon name="clock" size={16} />
                  未来 7 天预留{" "}
                  <strong>
                    ¥ {money(view.preflight.scheduled_reserve_cents)}
                  </strong>
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
                {status.status === "EXECUTED"
                  ? isPreview
                    ? "演示操作已完成"
                    : "操作已完成"
                  : status.status === "FAILED"
                    ? "执行失败"
                    : "结果确认中"}
              </h3>
              <p>
                {status.status === "EXECUTED"
                  ? "账户与业务状态已刷新。"
                  : status.status === "FAILED"
                    ? "请查看计划详情，确认实际执行结果。"
                    : "请勿重复发起。系统正在查询原计划的实际状态。"}
              </p>
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
