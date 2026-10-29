import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '../components/Button';
import { Icon } from '../components/Icon';
import { Skeleton, SkeletonText } from '../components/Skeleton';
import { StateView } from '../components/StateView';
import { ApprovalSheet } from '../components/ApprovalSheet';
import type { ApprovalSummary } from '../components/ApprovalSheet';
import { useAssistantService } from '../services/assistant/AssistantService';
import { AssistantOfflineError, AssistantError } from '../services/assistant/AssistantService';
import { MockAssistantService } from '../services/assistant/MockAssistantService';
import type { MockAssistantScenario } from '../services/assistant/MockAssistantService';
import type {
  AssistantHint,
  AssistantPlan,
  AssistantStep,
  Clarification,
} from '../services/assistant/types';
import { useTransferService } from '../services/transfer/TransferService';
import { MockTransferService } from '../services/transfer/MockTransferService';
import type { PlanView } from '../services/transfer/types';
import { useApprovalFlow } from '../services/approval/useApprovalFlow';
import { makePendingAccessors, PENDING_APPROVAL_KEY, readViewState, writeViewState, ASSISTANT_VIEW_KEY } from '../app/viewState';
import { useDocumentTitle } from '../app/hooks';
import { PATHS } from '../app/paths';
import styles from '../layout/layout.module.css';
import a from './assistant.module.css';

type Phase = 'IDLE' | 'PLANNING' | 'READY' | 'CLARIFICATION' | 'UNPLANNABLE' | 'ERROR';
type ClarPhase = 'idle' | 'selecting' | 'answering' | 'resolving' | 'ready' | 'expired' | 'error';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

interface AssistantView {
  phase: Phase;
  messages: ChatMessage[];
  plan: AssistantPlan | null;
  clarification: Clarification | null;
  clarPhase: ClarPhase;
  input: string;
  answer: string;
}

const EMPTY_VIEW: AssistantView = {
  phase: 'IDLE',
  messages: [],
  plan: null,
  clarification: null,
  clarPhase: 'idle',
  input: '',
  answer: '',
};

const pending = makePendingAccessors(PENDING_APPROVAL_KEY);

const STEP_STATE_CLASS: Record<AssistantStep['state'], string> = {
  done: a.stateDone,
  active: a.stateActive,
  passed: a.statePassed,
  insufficient: a.stateWarn,
  needsClarification: a.stateWarn,
  awaitingConfirm: a.stateAwait,
  pending: '',
};

const PHASES: Phase[] = ['IDLE', 'PLANNING', 'READY', 'CLARIFICATION', 'UNPLANNABLE', 'ERROR'];
const CLAR_PHASES: ClarPhase[] = ['idle', 'selecting', 'answering', 'resolving', 'ready', 'expired', 'error'];

/**
 * 归一化持久化的会话视图：
 * - 生成中 / 澄清提交中属于瞬时状态，刷新后原请求已不存在，回到可操作状态并标记「已中断」；
 * - 结构损坏或字段缺失时安全回退到空会话，不整页崩溃；
 * - 不读写任何审批凭证。
 */
function normalizeRestored(raw: unknown): { view: AssistantView; interrupted: boolean } {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { view: EMPTY_VIEW, interrupted: false };
  }
  const source = raw as Partial<AssistantView>;

  const messages = Array.isArray(source.messages)
    ? source.messages
        .filter(
          (m): m is ChatMessage =>
            Boolean(m) && typeof m === 'object' && typeof (m as ChatMessage).text === 'string',
        )
        .map((m) => ({ role: m.role === 'user' ? ('user' as const) : ('assistant' as const), text: m.text }))
    : [];

  const plan =
    source.plan && typeof source.plan === 'object' && typeof (source.plan as AssistantPlan).planId === 'string'
      ? source.plan
      : null;
  const clarification =
    source.clarification &&
    typeof source.clarification === 'object' &&
    typeof (source.clarification as Clarification).clarificationId === 'string'
      ? source.clarification
      : null;

  let phase: Phase = PHASES.includes(source.phase as Phase) ? (source.phase as Phase) : 'IDLE';
  let clarPhase: ClarPhase = CLAR_PHASES.includes(source.clarPhase as ClarPhase)
    ? (source.clarPhase as ClarPhase)
    : 'idle';

  let interrupted = false;
  if (phase === 'PLANNING') {
    phase = 'IDLE';
    interrupted = true;
  }
  if (clarPhase === 'selecting' || clarPhase === 'answering' || clarPhase === 'resolving') {
    clarPhase = 'idle';
  }
  if (!clarification) {
    clarPhase = 'idle';
    if (phase === 'CLARIFICATION') phase = 'IDLE';
  }
  if (phase === 'READY' && !plan) phase = 'IDLE';

  return {
    view: {
      phase,
      messages,
      plan,
      clarification,
      clarPhase,
      input: typeof source.input === 'string' ? source.input : '',
      answer: typeof source.answer === 'string' ? source.answer : '',
    },
    interrupted,
  };
}

function formatCents(cents: number): string {
  return `¥${(cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function AssistantPage() {
  const assistant = useAssistantService();
  const transfer = useTransferService();
  const navigate = useNavigate();
  useDocumentTitle('小安 · SafeBank');

  // 恢复会话：瞬时状态（生成中 / 澄清提交中）在刷新后已无对应请求，必须归一化。
  const restoredState = useMemo(() => normalizeRestored(readViewState<unknown>(ASSISTANT_VIEW_KEY, null)), []);
  const [phase, setPhase] = useState<Phase>(restoredState.view.phase);
  const [messages, setMessages] = useState<ChatMessage[]>(restoredState.view.messages);
  const [plan, setPlan] = useState<AssistantPlan | null>(restoredState.view.plan);
  const [clarification, setClarification] = useState<Clarification | null>(restoredState.view.clarification);
  const [clarPhase, setClarPhase] = useState<ClarPhase>(restoredState.view.clarPhase);
  const [input, setInput] = useState(restoredState.view.input);
  const [answer, setAnswer] = useState(restoredState.view.answer);
  const [interrupted, setInterrupted] = useState(restoredState.interrupted);

  const [hints, setHints] = useState<AssistantHint[]>([]);
  const [offline, setOffline] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [clarError, setClarError] = useState<string | null>(null);
  const [openStep, setOpenStep] = useState<string | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustText, setAdjustText] = useState('');
  const [adjustError, setAdjustError] = useState<string | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null);

  const seqRef = useRef(0);
  const mountedRef = useRef(true);
  const lastInstructionRef = useRef<{ hintId?: string; text?: string } | null>(null);

  const approval = useApprovalFlow<PlanView>(transfer, pending, {
    onSuccess: () => {
      if (!mountedRef.current) return;
      setMessages((m) => [...m, { role: 'assistant', text: '安全网关已返回结果，请查看确认面板中的状态。' }]);
    },
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 会话持久化（仅视图状态，不含任何凭证）
  useEffect(() => {
    writeViewState<AssistantView>(ASSISTANT_VIEW_KEY, {
      phase,
      messages,
      plan,
      clarification,
      clarPhase,
      input,
      answer,
    });
  }, [phase, messages, plan, clarification, clarPhase, input, answer]);

  useEffect(() => {
    let cancelled = false;
    void assistant
      .getHints()
      .then((list) => {
        if (!cancelled) setHints(list);
      })
      .catch((error) => {
        if (cancelled) return;
        if (error instanceof AssistantOfflineError) setOffline(true);
      });
    return () => {
      cancelled = true;
    };
  }, [assistant]);

  const applyReply = useCallback(
    (reply: { kind: string; message: string; plan?: AssistantPlan; clarification?: Clarification }) => {
      setMessages((m) => [...m, { role: 'assistant', text: reply.message }]);
      if (reply.kind === 'plan' && reply.plan) {
        setPlan(reply.plan);
        setClarification(null);
        setClarPhase('idle');
        setPhase('READY');
        setOpenStep(null);
        setAdjusting(false);
        return;
      }
      if (reply.kind === 'clarification' && reply.clarification) {
        setPlan(null);
        setClarification(reply.clarification);
        setClarPhase('idle');
        setSelectedCandidate(null);
        setAnswer('');
        setClarError(null);
        setPhase('CLARIFICATION');
        return;
      }
      // unplannable
      setPlan(null);
      setClarification(null);
      setClarPhase('idle');
      setPhase('UNPLANNABLE');
    },
    [],
  );

  /** 发送指令（文本或快捷指令 hint_id）。失败后可用同一指令重试。 */
  const runInstruction = useCallback(
    async (instruction: { hintId?: string; text?: string }, pushUser: boolean) => {
      if (offline) return;
      const text = (instruction.text ?? '').trim();
      if (!instruction.hintId && !text) {
        setFormError('请输入你的需求后再发送');
        return;
      }
      setFormError(null);
      if (clarification) {
        // 新指令明确取消本次澄清
        setClarification(null);
        setClarPhase('idle');
        setClarError(null);
      }
      lastInstructionRef.current = instruction.hintId ? { hintId: instruction.hintId } : { text };
      const seq = ++seqRef.current;
      setInterrupted(false);
      setPhase('PLANNING');
      setPlan(null);
      if (pushUser && !instruction.hintId) {
        setMessages((m) => [...m, { role: 'user', text }]);
        setInput('');
      }
      try {
        const reply = await assistant.submitInstruction(
          instruction.hintId ? { hintId: instruction.hintId } : { text },
        );
        if (seq !== seqRef.current || !mountedRef.current) return;
        applyReply(reply);
      } catch (error) {
        if (seq !== seqRef.current || !mountedRef.current) return;
        if (error instanceof AssistantOfflineError) {
          setOffline(true);
          setPhase('IDLE');
          setFormError('当前离线，无法生成计划');
          return;
        }
        setPhase('ERROR');
        setFormError(error instanceof AssistantError ? error.message : '计划生成失败，请重试');
      }
    },
    [offline, clarification, assistant, applyReply],
  );

  const send = useCallback(
    (hintId?: string) => {
      void runInstruction(hintId ? { hintId } : { text: input }, true);
    },
    [runInstruction, input],
  );

  const retrySend = useCallback(() => {
    const last = lastInstructionRef.current;
    if (last) void runInstruction(last, false);
  }, [runInstruction]);

  const selectCandidate = useCallback(
    async (candidateId: string) => {
      if (!clarification || clarPhase === 'resolving') return;
      setSelectedCandidate(candidateId);
      setClarPhase('selecting');
      const seq = ++seqRef.current;
      setClarPhase('resolving');
      setClarError(null);
      try {
        const reply = await assistant.selectCandidate(clarification.clarificationId, candidateId);
        if (seq !== seqRef.current || !mountedRef.current) return;
        applyReply(reply);
      } catch (error) {
        if (seq !== seqRef.current || !mountedRef.current) return;
        if (error instanceof AssistantOfflineError) {
          setOffline(true);
          setClarPhase('idle');
          setClarError('当前离线，无法提交澄清');
          return;
        }
        const code = error instanceof AssistantError ? error.code : 'ERROR';
        setClarPhase(code === 'CLARIFICATION_EXPIRED' ? 'expired' : 'error');
        setClarError(error instanceof Error ? error.message : '澄清提交失败');
      }
    },
    [clarification, clarPhase, assistant, applyReply],
  );

  const submitAnswer = useCallback(async () => {
    if (!clarification || clarPhase === 'resolving') return;
    const text = answer.trim();
    if (!text) {
      setClarError('请输入补充线索');
      return;
    }
    const seq = ++seqRef.current;
    setClarPhase('answering');
    setClarPhase('resolving');
    setClarError(null);
    try {
      const reply = await assistant.submitClarificationAnswer(clarification.clarificationId, text);
      if (seq !== seqRef.current || !mountedRef.current) return;
      applyReply(reply);
    } catch (error) {
      if (seq !== seqRef.current || !mountedRef.current) return;
      if (error instanceof AssistantOfflineError) {
        setOffline(true);
        setClarPhase('idle');
        setClarError('当前离线，无法提交澄清');
        return;
      }
      const code = error instanceof AssistantError ? error.code : 'ERROR';
      setClarPhase(code === 'CLARIFICATION_EXPIRED' ? 'expired' : 'error');
      setClarError(error instanceof Error ? error.message : '补充线索校验失败');
      // 保留输入，便于修改后重试
    }
  }, [clarification, clarPhase, answer, assistant, applyReply]);

  const restartClarification = useCallback(async () => {
    if (!clarification) return;
    const seq = ++seqRef.current;
    setClarPhase('resolving');
    setClarError(null);
    try {
      const reply = await assistant.restartClarification(clarification.clarificationId);
      if (seq !== seqRef.current || !mountedRef.current) return;
      applyReply(reply);
    } catch (error) {
      if (seq !== seqRef.current || !mountedRef.current) return;
      setClarPhase('error');
      setClarError(error instanceof Error ? error.message : '重新发起失败');
    }
  }, [clarification, assistant, applyReply]);

  const cancelClarification = useCallback(() => {
    seqRef.current += 1;
    setClarification(null);
    setClarPhase('idle');
    setClarError(null);
    setAnswer('');
    setSelectedCandidate(null);
    setPhase('IDLE');
    setMessages((m) => [...m, { role: 'assistant', text: '已取消本次澄清。你可以重新描述需求。' }]);
  }, []);

  /** 取消计划并作废旧预演/审批。 */
  const cancelPlan = useCallback(() => {
    seqRef.current += 1;
    approval.actions.invalidate();
    if (plan) void assistant.cancelPlan(plan.planId).catch(() => undefined);
    setPlan(null);
    setPhase('IDLE');
    setAdjusting(false);
    setAdjustText('');
    setAdjustError(null);
    setMessages((m) => [...m, { role: 'assistant', text: '已取消当前计划，没有产生任何资金变动。' }]);
  }, [approval, assistant, plan]);

  /** 换业务对象：作废旧计划与旧审批，回到输入态。 */
  const changeTarget = useCallback(() => {
    seqRef.current += 1;
    approval.actions.invalidate();
    setPlan(null);
    setPhase('IDLE');
    setAdjusting(false);
    setMessages((m) => [
      ...m,
      { role: 'assistant', text: '已作废当前计划。请直接输入新的业务对象（例如另一位收款人）。' },
    ]);
  }, [approval]);

  const submitAdjust = useCallback(async () => {
    if (!plan?.target) return;
    const raw = adjustText.trim();
    if (!/^\d+(\.\d{1,2})?$/.test(raw)) {
      setAdjustError('金额格式不正确，最多两位小数');
      return;
    }
    const cents = Math.round(Number(raw) * 100);
    if (cents <= 0) {
      setAdjustError('金额必须大于 0');
      return;
    }
    setAdjustError(null);
    setAdjusting(false);
    seqRef.current += 1;
    approval.actions.invalidate();
    const seq = ++seqRef.current;
    setPhase('PLANNING');
    setPlan(null);
    setMessages((m) => [...m, { role: 'user', text: `把金额调整为 ${raw} 元` }]);
    try {
      const reply = await assistant.submitInstruction({ text: `给${plan.target.recipientName}转 ${raw}` });
      if (seq !== seqRef.current || !mountedRef.current) return;
      applyReply(reply);
    } catch (error) {
      if (seq !== seqRef.current || !mountedRef.current) return;
      setPhase('ERROR');
      setFormError(error instanceof Error ? error.message : '计划生成失败，请重试');
    }
  }, [plan, adjustText, approval, assistant, applyReply]);

  /** 确认执行：必须经由 TransferService 生成 approval_id，再由公共审批闭环确认。 */
  const confirmExecute = useCallback(async () => {
    if (!plan?.target || !plan.executable) return;
    const seq = approval.actions.startPreview();
    try {
      const res = await transfer.previewTransfer({
        amountCents: plan.target.amountCents,
        fromAccountId: plan.target.fromAccountId,
        recipientId: plan.target.recipientId,
        memo: plan.target.memo,
      });
      if (!approval.actions.isCurrent(seq)) return;
      if (res.kind === 'error') {
        approval.actions.previewFailed(res.message, false);
        return;
      }
      approval.actions.begin(res.approvalId);
    } catch {
      if (!approval.actions.isCurrent(seq)) return;
      approval.actions.previewFailed('预演失败，请稍后重试', false);
    }
  }, [plan, transfer, approval]);

  const planView = approval.state.planView;
  const planState = approval.state;

  const sheetSummary: ApprovalSummary = planView
    ? {
        title: planView.title,
        subtitle: `${planView.recipientName} · ${planView.recipientAccount}`,
        amountText: formatCents(planView.amountCents),
        rows: [
          { label: '转出账户', value: planView.fromAccount },
          { label: '备注', value: planView.memo },
          { label: '资金路径', value: `${planView.fromAccount} → ${planView.recipientAccount}` },
          { label: '余额变化', value: `${formatCents(planView.availableBeforeCents)} → ${formatCents(planView.availableAfterCents)}` },
        ],
        riskNotes: planView.riskFindings,
      }
    : { title: '' };

  const isMock = assistant instanceof MockAssistantService;
  const applyScenario = (patch: Partial<MockAssistantScenario>) => {
    if (isMock) (assistant as MockAssistantService).setScenario(patch);
  };

  // 六节点：有真实步骤用真实状态；否则按当前阶段推导（不伪造进度）
  const steps: Array<{ key: string; label: string; state: AssistantStep['state']; stateText: string; evidence?: AssistantStep['evidence'] }> =
    plan?.steps ??
    [
      { key: 'understand', label: '理解', state: phase === 'IDLE' ? 'pending' : 'done', stateText: phase === 'IDLE' ? '待开始' : '完成' },
      { key: 'lookup', label: '查询', state: phase === 'PLANNING' ? 'done' : 'pending', stateText: phase === 'PLANNING' ? '完成' : '待开始' },
      { key: 'plan', label: '计划', state: phase === 'PLANNING' ? 'active' : 'pending', stateText: phase === 'PLANNING' ? '进行中' : '待开始' },
      { key: 'assess', label: '评估', state: 'pending', stateText: '待开始' },
      { key: 'execute', label: '执行', state: 'pending', stateText: '待确认' },
      { key: 'reply', label: '回复', state: 'pending', stateText: '待执行' },
    ];

  const barClass = (state: AssistantStep['state']) =>
    state === 'done' || state === 'passed'
      ? a.stepBarDone
      : state === 'active'
        ? a.stepBarActive
        : state === 'insufficient' || state === 'needsClarification'
          ? a.stepBarWarn
          : '';

  return (
    <div className={styles.content}>
      <div className={a.aiHead}>
        <span className={a.orb} aria-hidden="true">
          <Icon name="assistant" size={20} />
        </span>
        <span className={a.headMain}>
          <span className={a.headTitle}>小安 · 银行智能体</span>
          <span className={a.headSub}>
            {phase === 'PLANNING'
              ? '正在规划任务…'
              : phase === 'READY'
                ? '计划已就绪 · 等你确认'
                : phase === 'CLARIFICATION'
                  ? '需要你澄清'
                  : '不会直接执行任何写操作'}
          </span>
        </span>
        <span className={a.l2}>L2</span>
      </div>

      <div className={a.steps} aria-hidden="true">
        {steps.map((s) => (
          <span key={s.key} className={[a.stepBar, barClass(s.state)].filter(Boolean).join(' ')} />
        ))}
      </div>
      <div className={a.stepLabels} aria-hidden="true">
        {steps.map((s) => (
          <span key={s.key}>{s.label}</span>
        ))}
      </div>

      {interrupted ? (
        <div className={a.banner} role="status">
          <Icon name="clock" size={16} />
          <span className={a.bannerText}>上次生成已中断，请重新发送（不会自动重发）。</span>
          <Button variant="secondary" onClick={() => setInterrupted(false)}>
            知道了
          </Button>
        </div>
      ) : null}

      {offline ? (
        <div className={a.banner} role="status">
          <Icon name="wifi-off" size={16} />
          <span className={a.bannerText}>当前离线，无法发送或生成计划；恢复后可重试。</span>
          <Button
            variant="secondary"
            onClick={() => {
              setOffline(false);
              setFormError(null);
              void assistant.getHints().then(setHints).catch(() => setOffline(true));
            }}
          >
            重试
          </Button>
        </div>
      ) : null}

      {planState.pendingApprovalId && !planState.sheetOpen ? (
        <div className={a.banner} role="status">
          <Icon name="clock" size={16} />
          <span className={a.bannerText}>{planState.pendingQueryError ?? '有一项待确认的转账操作。'}</span>
          <Button
            variant="secondary"
            disabled={planState.querying}
            onClick={() => void approval.actions.viewStatus()}
          >
            {planState.querying ? '查询中…' : '查看状态'}
          </Button>
        </div>
      ) : null}

      {planState.recoveredNotice ? (
        <div
          className={`${a.banner} ${planState.recoveredNotice.kind === 'failed' ? a.alertBanner : ''}`}
          role={planState.recoveredNotice.kind === 'failed' ? 'alert' : 'status'}
        >
          <Icon name={planState.recoveredNotice.kind === 'failed' ? 'alert' : 'check'} size={16} />
          <span className={a.bannerText}>{planState.recoveredNotice.message}</span>
          <Button variant="secondary" onClick={approval.actions.dismissNotice}>
            关闭
          </Button>
        </div>
      ) : null}

      {(planState.planStatus === 'error' || planState.planStatus === 'expired') && planState.planError ? (
        <div className={`${a.banner} ${a.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={a.bannerText}>{planState.planError}</span>
          {planState.planStatus === 'error' ? (
            <Button variant="secondary" onClick={approval.actions.retryPlan}>
              重试预演详情
            </Button>
          ) : (
            <Button variant="secondary" onClick={() => void confirmExecute()}>
              重新预演
            </Button>
          )}
        </div>
      ) : null}

      {messages.length > 0 ? (
        <div className={a.chat}>
          {messages.map((m, i) => (
            <div key={`${m.role}-${i}`} className={[a.bubble, m.role === 'user' ? a.bubbleUser : a.bubbleAi].join(' ')}>
              {m.text}
            </div>
          ))}
        </div>
      ) : null}

      {phase === 'PLANNING' ? (
        <div className={styles.stack} role="status" aria-live="polite">
          <Skeleton height={16} />
          <SkeletonText lines={3} />
          <p className={a.count}>正在理解需求、查询账户并编排计划…</p>
        </div>
      ) : null}

      {phase === 'UNPLANNABLE' ? (
        <StateView
          state="empty"
          compact
          title="无法规划这个请求"
          description="小安只能处理账户、转账、卡片、账单、订阅与理财相关的请求。"
          actionLabel="重新描述"
          onAction={() => {
            setPhase('IDLE');
            setFormError(null);
          }}
        />
      ) : null}

      {phase === 'ERROR' && formError ? (
        <div className={`${a.banner} ${a.alertBanner}`} role="alert">
          <Icon name="alert" size={16} />
          <span className={a.bannerText}>{formError}</span>
          <Button variant="secondary" onClick={retrySend}>
            重试
          </Button>
        </div>
      ) : null}

      {phase === 'CLARIFICATION' && clarification ? (
        <div className={styles.stack}>
          <div className={a.planCard}>
            <span className={a.planLabel}>需要澄清</span>
            <p className={a.pstepSub}>{clarification.question}</p>
            {clarification.candidates.length === 0 ? (
              <p className={a.pstepSub}>没有匹配的候选，请手动补充线索。</p>
            ) : (
              clarification.candidates.map((c) => {
                const selected = selectedCandidate === c.candidateId;
                return (
                  <button
                    key={c.candidateId}
                    type="button"
                    className={[a.cand, selected ? a.candSelected : ''].filter(Boolean).join(' ')}
                    aria-pressed={selected}
                    disabled={clarPhase === 'resolving'}
                    onClick={() => void selectCandidate(c.candidateId)}
                  >
                    <span className={[a.radio, selected ? a.radioOn : ''].filter(Boolean).join(' ')} aria-hidden="true" />
                    <span className={a.candMain}>
                      <span className={a.candName}>{c.name}</span>
                      <span className={a.candMeta}>
                        {c.bankLabel} · 尾号 {c.mask}
                        {c.hint ? ` · ${c.hint}` : ''}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
            {clarification.allowManual ? (
              <p className={a.manual}>都不是？可在下方输入补充线索，例如「上次转过的那位」。</p>
            ) : null}
            <div className={a.summaryCard}>
              <div className={a.summaryRow}>
                <span className={a.summaryKey}>为什么需要澄清</span>
                <span className={a.summaryVal}>{clarification.reason}</span>
              </div>
            </div>
          </div>

          {clarError ? (
            <div className={`${a.banner} ${a.alertBanner}`} role="alert">
              <Icon name="alert" size={16} />
              <span className={a.bannerText}>{clarError}</span>
            </div>
          ) : null}

          {clarPhase === 'expired' ? (
            <div className={a.banner} role="alert">
              <Icon name="clock" size={16} />
              <span className={a.bannerText}>澄清已过期，请重新发起。</span>
              <Button variant="secondary" onClick={() => void restartClarification()}>
                重新发起
              </Button>
            </div>
          ) : null}

          {clarPhase !== 'expired' ? (
            <div className={a.inputBar}>
              <label className="sb-sr-only" htmlFor="assistant-answer">
                补充线索
              </label>
              <input
                id="assistant-answer"
                className={a.input}
                placeholder="补充线索，如「上次转过的那位」"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
              />
              <Button
                className={a.sendBtn}
                onClick={() => void submitAnswer()}
                disabled={clarPhase === 'resolving'}
              >
                提交线索
              </Button>
            </div>
          ) : null}

          <div className={a.chips}>
            <button type="button" className={a.chip} onClick={cancelClarification}>
              取消澄清
            </button>
          </div>
          <p className={a.demoNote}>普通输入框发送新指令会取消本次澄清，并作为新请求处理。</p>
        </div>
      ) : null}

      {phase === 'READY' && plan ? (
        <div className={styles.stack}>
          <div className={a.planCard}>
            <span className={a.planLabel}>执行计划 · {plan.steps.length} 步</span>
            {plan.steps.map((s, i) => {
              const open = openStep === s.key;
              return (
                <div key={s.key}>
                  <button
                    type="button"
                    className={a.pstep}
                    aria-expanded={open}
                    aria-controls={`step-evidence-${s.key}`}
                    onClick={() => setOpenStep(open ? null : s.key)}
                  >
                    <span className={[a.pd, s.state === 'done' || s.state === 'passed' ? a.pdDone : ''].filter(Boolean).join(' ')}>
                      {i + 1}
                    </span>
                    <span className={a.pstepMain}>
                      <span className={a.pstepTitle}>{s.label}</span>
                      <span className={a.pstepSub}>{s.evidence[0]?.value ?? s.stateText}</span>
                    </span>
                    <span className={[a.pstate, STEP_STATE_CLASS[s.state]].filter(Boolean).join(' ')}>{s.stateText}</span>
                  </button>
                  {open ? (
                    <div className={a.evidence} id={`step-evidence-${s.key}`}>
                      {s.evidence.map((e) => (
                        <div className={a.evidenceRow} key={e.label}>
                          <span className={a.evidenceLabel}>{e.label}</span>
                          <span>{e.value}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>

          <div className={a.summaryCard}>
            <div className={a.summaryRow}>
              <span className={a.summaryKey}>规划摘要</span>
              <span className={a.summaryVal}>{plan.planId}</span>
            </div>
            <div className={a.summaryRow}>
              <span className={a.summaryKey}>资金路径</span>
              <span className={a.summaryVal}>{plan.fundingPath}</span>
            </div>
            <div className={a.summaryRow}>
              <span className={a.summaryKey}>费用</span>
              <span className={a.summaryVal}>{plan.feeNote}</span>
            </div>
            <div className={a.summaryRow}>
              <span className={a.summaryKey}>风险</span>
              <span className={a.summaryVal}>{plan.riskNote}</span>
            </div>
          </div>

          {!plan.executable ? (
            <div className={a.banner} role="status">
              <Icon name="info" size={16} />
              <span className={a.bannerText}>{plan.unsupportedReason ?? '当前原型尚未开放该计划的执行。'}</span>
              {plan.intent === 'autoInvest' ? (
                <Button variant="secondary" onClick={() => navigate(PATHS.wealth)}>
                  前往理财页
                </Button>
              ) : null}
            </div>
          ) : null}

          {adjusting && plan.target ? (
            <div className={a.summaryCard}>
              <label className="sb-sr-only" htmlFor="assistant-adjust">
                调整金额（元）
              </label>
              <input
                id="assistant-adjust"
                className={a.input}
                inputMode="decimal"
                placeholder="新的金额（元）"
                value={adjustText}
                onChange={(e) => {
                  setAdjustText(e.target.value);
                  setAdjustError(null);
                }}
              />
              {adjustError ? (
                <p className={a.alertBanner} role="alert">
                  {adjustError}
                </p>
              ) : null}
              <div className={a.chips}>
                <Button onClick={() => void submitAdjust()}>重新规划</Button>
                <button type="button" className={a.chip} onClick={() => setAdjusting(false)}>
                  取消调整
                </button>
              </div>
            </div>
          ) : (
            <div className={a.chips}>
              <button
                type="button"
                className={a.chip}
                onClick={() => {
                  setAdjusting(true);
                  setAdjustText(plan.target ? String(plan.target.amountCents / 100) : '');
                  setAdjustError(null);
                }}
              >
                调整金额
              </button>
              <button type="button" className={a.chip} onClick={changeTarget}>
                换业务对象
              </button>
              <button type="button" className={a.chip} onClick={cancelPlan}>
                取消计划
              </button>
            </div>
          )}

          {plan.executable ? (
            <button type="button" className={a.cta} onClick={() => void confirmExecute()}>
              确认执行 · {plan.target ? formatCents(plan.target.amountCents) : ''}
            </button>
          ) : null}
          <p className={a.demoNote}>点击任意步骤可查看依据（结论 / 来源 / 校验），不会展示虚构的内部推理。</p>
        </div>
      ) : null}

      <div className={a.inputBar}>
        <label className="sb-sr-only" htmlFor="assistant-input">
          告诉小安你的需求
        </label>
        <input
          id="assistant-input"
          className={a.input}
          placeholder={offline ? '当前离线' : phase === 'CLARIFICATION' ? '输入新指令会取消本次澄清' : '例如：给王五转 3000 元'}
          value={input}
          disabled={offline}
          onChange={(e) => {
            setInput(e.target.value);
            setFormError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void send();
            }
          }}
        />
        <Button className={a.sendBtn} disabled={offline} onClick={() => void send()}>
          发送
        </Button>
      </div>

      {formError && phase !== 'ERROR' ? (
        <p className={a.alertBanner} role="alert">
          <Icon name="alert" size={13} /> {formError}
        </p>
      ) : null}

      {phase === 'IDLE' || phase === 'UNPLANNABLE' ? (
        <div className={a.hints}>
          {hints.map((h) => (
            <button
              key={h.hintId}
              type="button"
              className={a.hintChip}
              disabled={offline}
              onClick={() => void send(h.hintId)}
            >
              {h.title}
            </button>
          ))}
        </div>
      ) : null}

      {isMock ? (
        <div className={a.demoPanel} aria-label="演示场景（仅演示）">
          <span className={styles.sectionLabel}>演示场景（仅演示）</span>
          <div className={a.demoGrid}>
            <Button variant="secondary" onClick={() => applyScenario({ mode: 'normal' })}>
              普通转账
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ mode: 'homophone' })}>
              同音澄清
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ mode: 'noMatch' })}>
              无匹配候选
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ mode: 'unplannable' })}>
              无法规划
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ mode: 'autoInvest' })}>
              长期自动申购
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ manualFailOnce: true })}>
              手动补充失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ generateFailOnce: true })}>
              生成失败一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ clarificationExpired: true })}>
              澄清过期
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ amountCents: 6000_00 })}>
              红级金额（6,000）
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ amountCents: 3000_00 })}>
              黄级金额（3,000）
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ slowFirstMs: 1500 })}>
              慢响应一次
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: true })}>
              离线
            </Button>
            <Button variant="secondary" onClick={() => applyScenario({ offline: false })}>
              恢复在线
            </Button>
            {transfer instanceof MockTransferService ? (
              <Button
                variant="secondary"
                onClick={() =>
                  (transfer as MockTransferService).setScenario({ confirm: 'unknown', afterUnknownStatus: 'success' })
                }
              >
                转账 UNKNOWN → 成功
              </Button>
            ) : null}
          </div>
          <p className={a.demoNote}>演示场景只改变 mock 返回；接入真实服务时整块移除。</p>
        </div>
      ) : null}

      <ApprovalSheet
        open={planState.sheetOpen}
        approvalId={planState.approvalId ?? ''}
        mode={planView?.riskLevel ?? 'yellow'}
        state={
          planState.flow === 'CONFIRMING'
            ? 'confirming'
            : planState.flow === 'SUCCESS'
              ? 'success'
              : planState.flow === 'FAILED'
                ? 'failed'
                : planState.flow === 'UNKNOWN'
                  ? 'unknown'
                  : 'ready'
        }
        summary={sheetSummary}
        riskAckRequired={planView?.riskLevel === 'red'}
        otpValue={planState.otp}
        onOtpChange={approval.actions.setOtp}
        otpError={planState.otpError ?? undefined}
        onConfirm={() => void approval.actions.confirm()}
        onReject={approval.actions.reject}
        onClose={approval.actions.onClose}
        onViewStatus={() => void approval.actions.viewStatus()}
        failedActionLabel="关闭"
        failedDescription={planState.result?.reason ?? undefined}
        riskDisclosure="小安只负责规划与预演；本次转账的最终授权由你在本确认面板完成。请核对收款人、金额与资金来源。"
      />
    </div>
  );
}