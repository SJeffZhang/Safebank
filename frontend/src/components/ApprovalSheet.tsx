import { useEffect, useId, useState } from 'react';
import { Button } from './Button';
import { Icon } from './Icon';
import { Sheet } from './Sheet';
import styles from './ApprovalSheet.module.css';

export type ApprovalMode = 'yellow' | 'red';
export type ApprovalState = 'ready' | 'confirming' | 'success' | 'failed' | 'unknown';

export interface ApprovalRow {
  label: string;
  value: string;
  emphasis?: boolean;
}

export interface ApprovalSummary {
  title: string;
  subtitle?: string;
  /** Pre-formatted amount, e.g. "¥3,000.00". Keep formatting outside the sheet. */
  amountText?: string;
  rows?: ApprovalRow[];
  riskNotes?: string[];
}

export interface ApprovalSheetProps {
  open: boolean;
  /** Mock value in this stage; real ids come from the gateway later. */
  approvalId: string;
  mode: ApprovalMode;
  state: ApprovalState;
  summary: ApprovalSummary;
  riskAckRequired?: boolean;
  otpValue?: string;
  onOtpChange?: (value: string) => void;
  otpError?: string;
  otpExpiresInSeconds?: number;
  onConfirm: () => void;
  onReject?: () => void;
  onClose: () => void;
  onViewStatus?: () => void;
  /** failed 时主按钮动作；默认复用 onConfirm，业务可改为「返回修改」避免盲目重试同一授权。 */
  onFailedAction?: () => void;
  failedActionLabel?: string;
  /** failed 时的说明；仅当业务明确知道「未记账」时才由调用方传入该结论。 */
  failedDescription?: string;
  /** 红级风险揭示文案；业务方可传入与场景相关的中文说明，默认保持通用理财文案。 */
  riskDisclosure?: string;
}

export function ApprovalSheet({
  open,
  approvalId,
  mode,
  state,
  summary,
  riskAckRequired = false,
  otpValue = '',
  onOtpChange,
  otpError,
  otpExpiresInSeconds,
  onConfirm,
  onReject,
  onClose,
  onViewStatus,
  onFailedAction,
  failedActionLabel = '重新尝试',
  failedDescription,
  riskDisclosure,
}: ApprovalSheetProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  const otpId = useId();
  const otpErrorId = useId();
  const ackId = useId();

  useEffect(() => {
    if (open) setAcknowledged(false);
  }, [open, approvalId]);

  const isRed = mode === 'red';
  const busy = state === 'confirming';
  const requiresAck = isRed && riskAckRequired;
  const otpComplete = !isRed || otpValue.length === 6;
  const canConfirm = state === 'ready' && (!requiresAck || acknowledged) && otpComplete;
  const dismissible = !busy;

  const footer = (() => {
    if (state === 'success') {
      return <Button onClick={onClose}>完成</Button>;
    }
    if (state === 'unknown') {
      return (
        <>
          <Button variant="secondary" onClick={onClose}>
            关闭
          </Button>
          <Button onClick={onViewStatus ?? onClose}>查看状态</Button>
        </>
      );
    }
    if (state === 'failed') {
      return (
        <>
          <Button variant="secondary" onClick={onClose}>
            关闭
          </Button>
          <Button onClick={onFailedAction ?? onConfirm}>{failedActionLabel}</Button>
        </>
      );
    }
    return (
      <>
        <Button
          variant="secondary"
          onClick={() => (onReject ? onReject() : onClose())}
          disabled={busy}
        >
          拒绝
        </Button>
        <Button
          variant={isRed ? 'danger' : 'primary'}
          onClick={() => {
            if (canConfirm) onConfirm();
          }}
          loading={busy}
          disabled={!canConfirm}
        >
          {busy ? '确认中…' : isRed ? '验证并确认' : '确认操作'}
        </Button>
      </>
    );
  })();

  return (
    <Sheet
      open={open}
      title="确认操作"
      description="请核对以下信息，确认后将进入安全网关处理。"
      onClose={onClose}
      dismissible={dismissible}
      footer={footer}
    >
      <span className={`${styles.mode} ${isRed ? styles.redMode : styles.yellowMode}`}>
        {isRed ? '红级 · 强验证' : '黄级 · 需确认'}
      </span>

      <div className={styles.summary}>
        <h3 className={styles.summaryTitle}>{summary.title}</h3>
        {summary.subtitle ? <p className={styles.summarySubtitle}>{summary.subtitle}</p> : null}
        {summary.amountText ? <div className={styles.amount}>{summary.amountText}</div> : null}

        {summary.rows && summary.rows.length > 0 ? (
          <dl className={styles.rows}>
            {summary.rows.map((row) => (
              <div key={row.label}>
                <dt>{row.label}</dt>
                <dd className={row.emphasis ? styles.emphasis : undefined}>{row.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {summary.riskNotes && summary.riskNotes.length > 0 ? (
          <ul className={styles.notes}>
            {summary.riskNotes.map((note) => (
              <li className={styles.note} key={note}>
                <Icon name="shield" size={13} />
                <span>{note}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {requiresAck ? (
        <div className={styles.disclosure}>
          <div className={styles.disclosureTitle}>
            <Icon name="alert" size={14} />
            风险揭示
          </div>
          <p>
            {riskDisclosure ??
              '此操作需要你的明确授权确认。请确认你已核对本次操作的目标、影响与结果，授权仅对本次操作生效。'}
          </p>
          <label className={styles.ack} htmlFor={ackId}>
            <input
              id={ackId}
              type="checkbox"
              checked={acknowledged}
              disabled={state !== 'ready'}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            <span>我已阅读并理解上述风险揭示与费用说明</span>
          </label>
        </div>
      ) : null}

      {isRed ? (
        <div className={styles.otp}>
          <label className={styles.otpLabel} htmlFor={otpId}>
            短信验证码（6 位）
          </label>
          <input
            id={otpId}
            className={styles.otpInput}
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={otpValue}
            disabled={state !== 'ready'}
            aria-invalid={otpError ? true : undefined}
            aria-describedby={otpError ? otpErrorId : undefined}
            onChange={(event) => onOtpChange?.(event.target.value.replace(/\D/g, '').slice(0, 6))}
          />
          <p className={styles.otpHint}>
            验证码已发送至绑定手机
            {typeof otpExpiresInSeconds === 'number'
              ? ` · ${Math.max(0, Math.floor(otpExpiresInSeconds))} 秒内有效`
              : ''}
            。
          </p>
          {otpError ? (
            <p className={styles.otpError} id={otpErrorId} role="alert">
              <Icon name="alert" size={13} />
              <span>{otpError}</span>
            </p>
          ) : null}
        </div>
      ) : null}

      {busy ? (
        <p className={styles.busy} role="status">
          <Icon name="clock" size={14} />
          正在提交，请勿重复操作。
        </p>
      ) : null}

      {state === 'success' ? (
        <div className={styles.status} role="status">
          <span className={`${styles.statusIcon} ${styles.successIcon}`}>
            <Icon name="check" size={22} />
          </span>
          <h3 className={styles.statusTitle}>操作已完成</h3>
          <p className={styles.statusText}>演示状态：真实结果以服务端返回为准。</p>
        </div>
      ) : null}

      {state === 'failed' ? (
        <div className={styles.status} role="alert">
          <span className={`${styles.statusIcon} ${styles.failedIcon}`}>
            <Icon name="alert" size={22} />
          </span>
          <h3 className={styles.statusTitle}>操作未完成</h3>
          <p className={styles.statusText}>
            {failedDescription ?? '操作未完成，请查看详情或根据提示决定下一步。'}
          </p>
        </div>
      ) : null}

      {state === 'unknown' ? (
        <div className={styles.status} role="status">
          <span className={`${styles.statusIcon} ${styles.unknownIcon}`}>
            <Icon name="clock" size={22} />
          </span>
          <h3 className={styles.statusTitle}>结果待确认</h3>
          <p className={styles.statusText}>
            请求已提交，但结果尚未返回。请勿重复提交，可稍后查看状态。
          </p>
        </div>
      ) : null}

      <p className={styles.approvalId}>审批号 {approvalId} · 本页为演示数据</p>
    </Sheet>
  );
}
