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

/** Operator panel for the demo stack (F13). Live mode only. */
export function DemoConsole({ onClose }: { onClose: () => void }) {
  const [signedIn, setSignedIn] = useState<boolean>();
  const [secret, setSecret] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [advance, setAdvance] = useState<Advance>();
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
  async function runAdvance() {
    setBusy(true);
    setError("");
    setAdvance(undefined);
    try {
      const { advance_job_id } = await consoleRequest<{ advance_job_id: string }>(
        "/clock/advance",
        { request_id: uid(), target_at: `${target}:00+08:00` },
      );
      for (let i = 0; i < 30; i++) {
        const a = await consoleRequest<Advance>(`/clock/advances/${advance_job_id}`);
        setAdvance(a);
        if (a.status !== "RUNNING" && a.status !== "PENDING") break;
        await new Promise((r) => setTimeout(r, 1000));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="演示控制台" onClose={onClose}>
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
            <Button disabled={busy || !target} onClick={() => void runAdvance()}>
              {busy ? "处理中…" : "快进"}
            </Button>
          </div>
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
    </Modal>
  );
}
