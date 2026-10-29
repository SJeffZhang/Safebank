import { useMemo } from 'react';
import { ApprovalSheet } from '../components/ApprovalSheet';
import type { ApprovalState, ApprovalSummary } from '../components/ApprovalSheet';
import { Button } from '../components/Button';
import { Icon } from '../components/Icon';
import { Skeleton, SkeletonText } from '../components/Skeleton';
import { StateView } from '../components/StateView';
import { TextField } from '../components/TextField';
import { useTransferService } from '../services/transfer/TransferService';
import { useTransferFlow } from '../services/transfer/useTransferFlow';
import { MockTransferService } from '../services/transfer/MockTransferService';
import type { MockScenario } from '../services/transfer/MockTransferService';
import { formatYuan } from '../services/transfer/money';
import type { TransferRecipient, TransferAccount } from '../services/transfer/types';
import { useDocumentTitle } from '../app/hooks';
import styles from '../layout/layout.module.css';
import tstyles from './Transfer.module.css';

function accountOptionLabel(account: TransferAccount): string {
  const paused = account.status !== 'normal';
  return `${account.name} · 尾号 ${account.mask}${paused ? '（暂停外转）' : ''}`;
}

export function TransferPage() {
  const service = useTransferService();
  const flow = useTransferFlow(service);
  const { state, selectedAccount, accountPaused, actions } = flow;
  useDocumentTitle('转账 · SafeBank');

  const isMock = service instanceof MockTransferService;

  const summary: ApprovalSummary | null = useMemo(() => {
    if (!state.planView) return null;
    const pv = state.planView;
    return {
      title: pv.title,
      amountText: formatYuan(pv.amountCents),
      rows: [
        { label: '收款人', value: pv.recipientName },
        { label: '收款账户', value: pv.recipientAccount },
        { label: '付款账户', value: pv.fromAccount },
        { label: '备注', value: pv.memo },
        { label: '转前可用', value: formatYuan(pv.availableBeforeCents) },
        { label: '转后可用', value: formatYuan(pv.availableAfterCents) },
        { label: '当日累计', value: formatYuan(pv.dailyTotalCents) },
        { label: '7 天预留', value: formatYuan(pv.reserved7dCents) },
      ],
      riskNotes: pv.riskFindings,
    };
  }, [state.planView]);

  const sheetState: ApprovalState =
    state.flow === 'CONFIRMING'
      ? 'confirming'
      : state.flow === 'SUCCESS'
        ? 'success'
        : state.flow === 'FAILED'
          ? 'failed'
          : state.flow === 'UNKNOWN'
            ? 'unknown'
            : 'ready';

  const mode = state.planView?.riskLevel ?? 'yellow';
  const noFundsMoved = state.result?.noFundsMoved === true;

  const previewDisabledReason = (() => {
    if (state.flow === 'PREVIEWING') return '正在预演…';
    if (state.amountError) return state.amountError;
    if (!state.fromAccountId) return '请先选择付款账户';
    if (!state.recipientId) return '请先选择收款人';
    if (accountPaused) return '付款账户已暂停外转';
    return null;
  })();

  const applyScenario = (patch: Partial<MockScenario>) => {
    if (isMock) (service as MockTransferService).setScenario(patch);
  };

  return (
    <div className={styles.content}>
      <div className={styles.intro}>
        <p className={styles.sectionLabel}>转账</p>
        <p className={styles.introText}>
          填写转账信息并预演；确认面板展示安全网关返回的 PlanView。所有金额以整数分计算，本页不直接扣款。
        </p>
      </div>

      {state.offline ? (
        <div className={`${tstyles.banner} ${tstyles.offline}`} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={tstyles.bannerText}>当前处于离线状态，已展示的内容不会丢失；恢复网络后重试。</span>
          <Button variant="secondary" onClick={actions.loadReferences}>
            重新加载
          </Button>
        </div>
      ) : null}

      {state.listError && state.accounts.length === 0 ? (
        <div className={tstyles.planBox} role="alert">
          <StateView
            state="error"
            compact
            title="账户信息加载失败"
            description={state.listError}
            actionLabel="重新加载"
            onAction={actions.loadReferences}
          />
        </div>
      ) : null}

      {state.refsLoading && state.accounts.length === 0 ? (
        <div className={tstyles.planBox} role="status" aria-live="polite">
          <Skeleton height={16} />
          <SkeletonText lines={2} />
          <p className={tstyles.hint}>正在加载账户与收款人…</p>
        </div>
      ) : null}

      {state.pendingApprovalId && !state.sheetOpen && state.flow !== 'CONFIRMING' && state.flow !== 'SUCCESS' ? (
        <div className={tstyles.banner} role="status">
          <Icon name="clock" size={16} />
          <span className={tstyles.bannerText}>
            有待确认的转账操作（{state.pendingApprovalId}）。请查询最新状态，系统不会默认将其视为成功。
          </span>
          <Button variant="secondary" onClick={actions.viewStatus}>
            查看状态
          </Button>
        </div>
      ) : null}

      {state.planView == null && !state.sheetOpen && (state.flow === 'SUCCESS' || state.flow === 'FAILED') ? (
        <div className={tstyles.planBox}>
          <StateView
            state={state.flow === 'SUCCESS' ? 'success' : 'failed'}
            description={
              state.result?.noFundsMoved ? '本次转账未产生资金变动。' : undefined
            }
          />
        </div>
      ) : null}

      <div className={tstyles.form}>
        <div className={tstyles.fieldBlock}>
          <TextField
            label="转账金额（元）"
            inputMode="decimal"
            placeholder="0.00"
            value={state.amountText}
            error={state.amountError ?? state.fieldErrors.amount}
            hint="金额必须手输，最多两位小数，不接受负数。"
            onChange={(event) => actions.updateAmount(event.target.value)}
          />
        </div>

        <div className={tstyles.fieldBlock}>
          <label className={tstyles.label} htmlFor="transfer-account">
            付款账户
          </label>
          <select
            id="transfer-account"
            className={tstyles.select}
            value={state.fromAccountId ?? ''}
            onChange={(event) => actions.selectAccount(event.target.value)}
          >
            <option value="" disabled>
              请选择付款账户
            </option>
            {state.accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {accountOptionLabel(account)}
              </option>
            ))}
          </select>
          {state.fieldErrors.account ? (
            <p className={tstyles.error} role="alert">
              <Icon name="alert" size={13} />
              <span>{state.fieldErrors.account}</span>
            </p>
          ) : selectedAccount ? (
            <div className={tstyles.accountMeta}>
              <span>可用 {formatYuan(selectedAccount.availableCents)}</span>
              <span>本行账户</span>
              {accountPaused ? <span className={tstyles.pausedBadge}>暂停外转</span> : null}
            </div>
          ) : null}
        </div>

        <div className={tstyles.fieldBlock}>
          <label className={tstyles.label} htmlFor="transfer-recipient">
            收款人
          </label>
          <select
            id="transfer-recipient"
            className={tstyles.select}
            value={state.recipientId ?? ''}
            onChange={(event) => actions.selectRecipient(event.target.value)}
          >
            <option value="" disabled>
              请选择收款人
            </option>
            {state.recipients.map((recipient) => (
              <option key={recipient.id} value={recipient.id}>
                {recipientLabel(recipient)}
              </option>
            ))}
          </select>
          {state.fieldErrors.recipient ? (
            <p className={tstyles.error} role="alert">
              <Icon name="alert" size={13} />
              <span>{state.fieldErrors.recipient}</span>
            </p>
          ) : null}
        </div>

        <TextField
          label="备注（选填）"
          placeholder="最多 30 字，纯文本"
          value={state.memo}
          hint="备注仅按纯文本展示，不被解析或执行。"
          onChange={(event) => actions.updateMemo(event.target.value)}
        />

        {state.dailyRemainingCents != null ? (
          <div className={tstyles.fieldBlock}>
            <span className={tstyles.label}>今日剩余额度</span>
            <p className={tstyles.hint}>
              {formatYuan(state.dailyRemainingCents)} · 进网关后校验设备、收款人与限额
            </p>
          </div>
        ) : null}

        {state.previewError ? (
          <p className={tstyles.error} role="alert">
            <Icon name="alert" size={13} />
            <span>{state.previewError}</span>
          </p>
        ) : null}

        <div>
          <Button
            fullWidth
            loading={state.flow === 'PREVIEWING'}
            disabled={previewDisabledReason != null}
            onClick={actions.preview}
          >
            {state.flow === 'PREVIEWING' ? '预演中…' : '预演这笔转账'}
          </Button>
          {previewDisabledReason ? (
            <p className={tstyles.disabledReason}>{previewDisabledReason}</p>
          ) : (
            <p className={tstyles.hint} style={{ textAlign: 'center', marginTop: 6 }}>
              下一步：安全网关预演，不会立即扣款
            </p>
          )}
        </div>

        {state.planStatus === 'loading' ? (
          <div className={tstyles.planBox} role="status" aria-live="polite">
            <Skeleton height={16} />
            <SkeletonText lines={3} />
            <p className={tstyles.hint}>正在获取网关预演详情…</p>
          </div>
        ) : null}

        {state.planStatus === 'error' ? (
          <div className={tstyles.planBox}>
            <StateView
              state="error"
              compact
              title="预演详情加载失败"
              description={state.planError ?? '请重试，或重新预演。'}
              actionLabel="重试"
              onAction={actions.retryPlan}
            />
            <Button variant="ghost" fullWidth onClick={() => actions.onClose()}>
              取消并返回
            </Button>
          </div>
        ) : null}

        {state.planStatus === 'expired' ? (
          <div className={tstyles.planBox}>
            <StateView
              state="unknown"
              compact
              title="预演已过期"
              description="预演详情已失效，请重新预演以获取新的审批信息。"
            />
            <Button fullWidth onClick={actions.preview}>
              重新预演
            </Button>
          </div>
        ) : null}
      </div>

      {isMock ? (
        <div className={tstyles.demoPanel} aria-label="演示场景（仅演示）">
          <div className={tstyles.demoHead}>
            <Icon name="info" size={14} />
            <span>演示场景（仅演示，不影响真实逻辑）</span>
          </div>
          <div className={tstyles.scenarioGrid}>
            <Button variant="secondary" onClick={() => applyScenario({ preview: 'insufficient', confirm: 'success' })}>
              余额不足
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ preview: 'frozen', confirm: 'success' })}>
              账户暂停外转
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ preview: 'overLimit', confirm: 'success' })}>
              超单笔/日限额
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ preview: 'fail', confirm: 'success' })}>
              预演失败
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewFailOnce: true })}>
              PlanView 加载失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ planViewExpired: true })}>
              PlanView 过期
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'success', afterUnknownStatus: 'unknown' })}>
              黄级成功
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'success', afterUnknownStatus: 'unknown' })}>
              红级 OTP 成功（大于 5,000）
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'failedNoFunds' })}>
              失败·明确未记账
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'failedUncertain' })}>
              失败·结果不明
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'success' })}>
              UNKNOWN → 查询成功
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'failed' })}>
              UNKNOWN → 查询失败
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ confirm: 'unknown', afterUnknownStatus: 'unknown' })}>
              持续 UNKNOWN
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: true })}>
              离线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: false })}>
              恢复在线
            </Button>
          </div>
          <p className={tstyles.demoNote}>
            演示场景只改变 mock 返回，不预填金额/账号/收款人；接入真实服务时整块移除。
          </p>
        </div>
      ) : null}

      {summary && state.planView ? (
        <ApprovalSheet
          open={state.sheetOpen}
          approvalId={state.planView.approvalId}
          mode={mode}
          state={sheetState}
          summary={summary}
          riskAckRequired={mode === 'red'}
          riskDisclosure="请核对收款人、账户尾号和金额；OTP 仅用于本次授权。"
          otpValue={state.otp}
          onOtpChange={actions.setOtp}
          otpError={state.otpError ?? undefined}
          otpExpiresInSeconds={
            state.planView.expiresAtMillis > Date.now()
              ? Math.max(0, Math.floor((state.planView.expiresAtMillis - Date.now()) / 1000))
              : undefined
          }
          onConfirm={actions.confirm}
          onReject={actions.reject}
          onClose={actions.onClose}
          onViewStatus={actions.viewStatus}
          onFailedAction={actions.onClose}
          failedActionLabel="返回修改"
          failedDescription={
            noFundsMoved ? '本次转账未产生资金变动，收款账户状态异常。' : (state.result?.reason ?? undefined)
          }
        />
      ) : null}
    </div>
  );
}

function recipientLabel(recipient: TransferRecipient): string {
  return `${recipient.name} · ${recipient.bankLabel} 尾号 ${recipient.mask}`;
}