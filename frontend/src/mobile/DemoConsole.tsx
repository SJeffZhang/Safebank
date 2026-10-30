import { useEffect, useState } from "react";
import { consoleLogin, consoleRequest, consoleStatus } from "./api";
import { uid } from "./types";
import { Alert, Button, Modal } from "./ui";

type Advance = {
  status: string;
  sim_now?: string;
  processed?: number;
  blocked?: number;
  remaining?: number;
};

type ClockJob = { id?: string; request_id: string; target_at: string };
const JOB_KEY = "safebank-demo-clock";
function savedJob(): ClockJob | undefined {
  try {
    const j = JSON.parse(sessionStorage.getItem(JOB_KEY) ?? "null");
    return j && typeof j.request_id === "string" && typeof j.target_at === "string" &&
      (j.id === undefined || typeof j.id === "string") ? j : undefined;
  } catch { return undefined; }
}

/** Operator panel for the demo stack (F13). Live mode only. */
export function DemoConsole({ onClose }: { onClose: () => void }) {
  const [signedIn, setSignedIn] = useState<boolean>();
  const [secret, setSecret] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [advance, setAdvance] = useState<Advance>();
  const [job, setJob] = useState<ClockJob | undefined>(savedJob);
  const [timedOut, setTimedOut] = useState(false);
  const pending = Boolean(job && (!advance || !["DONE", "FAILED"].includes(advance.status)));
  function keepJob(value: ClockJob) {
    setJob(value);
    try { sessionStorage.setItem(JOB_KEY, JSON.stringify(value)); } catch { /* memory state remains */ }
  }
  useEffect(() => {
    consoleStatus()
      .then((s) => setSignedIn(s.signed_in))
      .catch((e) => setError((e as Error).message));
  }, []);
  async function login() {
    setBusy(true);
    setError("");
    try {
      await consoleLogin(secret);
      setSecret("");
      setSignedIn(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function poll(current: ClockJob) {
    if (!current.id) return;
    for (let i = 0; i < 30; i++) {
      const a = await consoleRequest<Advance>(`/clock/advances/${current.id}`);
      setAdvance(a);
      if (!["RUNNING", "PENDING"].includes(a.status)) return;
      await new Promise((r) => setTimeout(r, 1000));
    }
    setTimedOut(true);
  }
  async function queryOrSubmit(current: ClockJob, submit: boolean) {
    setBusy(true);
    setError("");
    setTimedOut(false);
    try {
      if (submit || !current.id) {
        // Retry only the original request identity; never invent another job.
        const { advance_job_id } = await consoleRequest<{ advance_job_id: string }>(
          "/clock/advance", { request_id: current.request_id, target_at: current.target_at },
        );
        current = { ...current, id: advance_job_id };
        keepJob(current);
      }
      await poll(current);
    } catch (e) {
      setError((e as Error).message);
      if ((e as { status?: number }).status === 404) {
        setJob(undefined);
        try { sessionStorage.removeItem(JOB_KEY); } catch { /* no persisted record */ }
      }
    } finally {
      setBusy(false);
    }
  }
  async function runAdvance() {
    if (pending) return;
    const current = { request_id: uid(), target_at: `${target}:00+08:00` };
    keepJob(current);
    setAdvance(undefined);
    await queryOrSubmit(current, true);
  }
  return (
    <Modal title="演示控制台" onClose={onClose}>
      <div className="modal-body">
      <p className="helper">仅用于演示环境的运营操作，不属于客户 App 功能。</p>
      {error && <Alert>{error}</Alert>}
      {signedIn === false && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (secret) void login();
          }}
        >
          <label className="field-label" htmlFor="console-secret">控制台口令</label>
          <input
            id="console-secret"
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
          <div className="modal-actions">
            <Button type="submit" disabled={busy || !secret}>登录控制台</Button>
          </div>
        </form>
      )}
      {signedIn && (
        <div>
          <p className="helper">已登录。获取验证码后，确认面板会显示演示短信。</p>
          <label className="field-label" htmlFor="console-clock">快进模拟时钟到</label>
          <input
            id="console-clock"
            type="datetime-local"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
          <div className="modal-actions">
            <Button disabled={busy || pending || !target} onClick={() => void runAdvance()}>
              {busy ? "处理中…" : "快进"}
            </Button>
          </div>
          {job && (
            <div>
              <p className="helper">作业 <code>{job.id ?? job.request_id}</code></p>
              {timedOut && <p role="status">仍在处理，可查询原作业进度。</p>}
              <Button disabled={busy} onClick={() => void queryOrSubmit(job, false)}>查询进度</Button>
              {advance?.status === "RETRYING" && (
                <Button disabled={busy} onClick={() => void queryOrSubmit(job, true)}>继续原作业</Button>
              )}
            </div>
          )}
          {advance && (
            <p className="helper" role="status">
              状态 {advance.status}
              {advance.sim_now ? ` · 当前 ${advance.sim_now}` : ""}
              {advance.processed !== undefined ? ` · 已处理 ${advance.processed}` : ""}
              {advance.blocked ? ` · 拦截 ${advance.blocked}` : ""}
            </p>
          )}
        </div>
      )}
      </div>
    </Modal>
  );
}
