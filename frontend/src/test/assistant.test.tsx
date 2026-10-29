import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { AssistantPage } from '../pages/Assistant';
import { AssistantServiceProvider } from '../services/assistant/AssistantService';
import type { AssistantService } from '../services/assistant/AssistantService';
import { MockAssistantService } from '../services/assistant/MockAssistantService';
import type { AssistantPlan, AssistantReply, InstructionInput } from '../services/assistant/types';
import { TransferServiceProvider } from '../services/transfer/TransferService';
import { MockTransferService } from '../services/transfer/MockTransferService';
import { clearMockTransferRecords } from '../services/transfer/MockTransferService';

function defer<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const STEPS: AssistantPlan['steps'] = [
  { key: 'understand', label: '理解', state: 'done', stateText: '完成', evidence: [{ label: '结论', value: '识别为转账意图' }, { label: '来源', value: '你的输入' }] },
  { key: 'lookup', label: '查询', state: 'passed', stateText: '通过', evidence: [{ label: '结论', value: '匹配到王五' }] },
  { key: 'plan', label: '计划', state: 'done', stateText: '完成', evidence: [{ label: '结论', value: '编排 4 步' }] },
  { key: 'assess', label: '评估', state: 'passed', stateText: '通过', evidence: [{ label: '结论', value: '余额与限额通过' }] },
  { key: 'execute', label: '执行', state: 'awaitingConfirm', stateText: '待确认', evidence: [{ label: '结论', value: '等待你签字' }] },
  { key: 'reply', label: '回复', state: 'pending', stateText: '待执行', evidence: [{ label: '结论', value: '尚未执行' }] },
];

function planReply(opts?: { amountCents?: number; executable?: boolean; recipientId?: string }): AssistantReply {
  const amountCents = opts?.amountCents ?? 3000_00;
  const plan: AssistantPlan = {
    planId: 'plan_test01',
    intent: 'transfer',
    title: `转账给王五 ¥${(amountCents / 100).toFixed(2)}`,
    steps: STEPS,
    summary: '从活期转出并等待你确认',
    fundingPath: '活期 → 王五',
    feeNote: '0.00 元',
    riskNote: '黄级',
    target: {
      kind: 'transfer',
      amountCents,
      fromAccountId: 'acc-current',
      fromAccountName: '活期 · 尾号 1234',
      recipientId: opts?.recipientId ?? 'rcp-wang',
      recipientName: '王五',
      recipientAccount: '本行 · 尾号 8891',
      memo: '小安代为规划',
    },
    executable: opts?.executable ?? true,
  };
  return { kind: 'plan', plan, message: '明白。这是计划，不会直接转账。' };
}

function clarificationReply(): AssistantReply {
  return {
    kind: 'clarification',
    message: '收款人里有两个读音相同的名字。请确认是哪一位，我不会替你猜。',
    clarification: {
      clarificationId: 'clr_test01',
      question: '请选择收款人',
      reason: '两个名字同音、尾号不同',
      candidates: [
        { candidateId: 'cand-2210', name: '王武', bankLabel: '本行', mask: '2210', hint: '最近转账 2025-08' },
        { candidateId: 'cand-8873', name: '王伍', bankLabel: '本行', mask: '8873', hint: '新收款人' },
      ],
      allowManual: true,
    },
  };
}

function renderAssistant(assistant: AssistantService, transfer?: MockTransferService) {
  return render(
    <MemoryRouter initialEntries={['/assistant']}>
      <TransferServiceProvider service={transfer ?? new MockTransferService({}, 0)}>
        <AssistantServiceProvider service={assistant}>
          <AssistantPage />
        </AssistantServiceProvider>
      </TransferServiceProvider>
    </MemoryRouter>,
  );
}

let routedPath = '/assistant';
function RouteProbe() {
  routedPath = useLocation().pathname;
  return null;
}

/** 带 /wealth 路由的渲染，用于验证「前往理财页」经过 React Router。 */
function renderAssistantWithRoutes(assistant: AssistantService) {
  return render(
    <MemoryRouter initialEntries={['/assistant']}>
      <TransferServiceProvider service={new MockTransferService({}, 0)}>
        <AssistantServiceProvider service={assistant}>
          <RouteProbe />
          <Routes>
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/wealth" element={<div>WEALTH_PAGE_CONTENT</div>} />
          </Routes>
        </AssistantServiceProvider>
      </TransferServiceProvider>
    </MemoryRouter>,
  );
}

function mockAssistant(scenario = {}) {
  return new MockAssistantService(scenario, 0);
}

async function sendText(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText('告诉小安你的需求'), text);
  await user.click(screen.getByRole('button', { name: '发送' }));
}

describe('小安 · 对话与计划', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('空输入禁止发送并给出反馈', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant());
    await user.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('请输入你的需求后再发送')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('快捷指令只发送 hint_id，不把按钮文字当作用户输入', async () => {
    const calls: InstructionInput[] = [];
    const d = defer<AssistantReply>();
    const fake: AssistantService = {
      getHints: async () => [{ hintId: 'hint-transfer', title: '给王五转 3,000 元', intent: 'transfer' }],
      submitInstruction: (input) => {
        calls.push(input);
        return d.promise;
      },
      selectCandidate: async () => clarificationReply(),
      submitClarificationAnswer: async () => clarificationReply(),
      restartClarification: async () => clarificationReply(),
      cancelPlan: async () => undefined,
    };
    const user = userEvent.setup();
    renderAssistant(fake);
    await user.click(await screen.findByRole('button', { name: '给王五转 3,000 元' }));

    expect(calls).toEqual([{ hintId: 'hint-transfer' }]);
    // 不产生伪造的用户气泡
    expect(screen.queryByText('给王五转 3,000 元', { selector: '.x' })).not.toBeInTheDocument();
    await act(async () => {
      d.resolve(planReply());
    });
    expect(await screen.findByText('明白。这是计划，不会直接转账。')).toBeInTheDocument();
  });

  it('生成后显示六个节点、整条回复与计划卡', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant());
    await sendText(user, '给王五转 3000');

    expect(await screen.findByText('明白。我先说明计划，再把完整内容交给你确认，不会直接转账。')).toBeInTheDocument();
    const planCard = screen.getByText(/执行计划 · 6 步/).closest('div')!;
    for (const label of ['理解', '查询', '计划', '评估', '执行', '回复']) {
      expect(within(planCard).getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText('规划摘要')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认执行/ })).toBeInTheDocument();
  });

  it('点击步骤展开依据（结论/来源），不展示虚构推理', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant());
    await sendText(user, '给王五转 3000');
    await screen.findByText(/执行计划 · 6 步/);

    const stepBtn = screen.getAllByRole('button', { name: /理解/ })[0];
    expect(stepBtn).toHaveAttribute('aria-expanded', 'false');
    await user.click(stepBtn);
    expect(stepBtn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('来源')).toBeInTheDocument();
    expect(screen.queryByText(/思维链|内部推理过程/)).not.toBeInTheDocument();
  });

  it('无法规划时给出解释，不生成计划', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ mode: 'unplannable' }));
    await sendText(user, '帮我写一首诗');
    expect(await screen.findByText('无法规划这个请求')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认执行/ })).not.toBeInTheDocument();
  });

  it('不支持的长期自动申购：可规划但无可点击的确认执行', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ mode: 'autoInvest' }));
    await sendText(user, '把每月工资的 30% 自动申购稳健产品');
    expect(await screen.findByText(/执行计划 · 6 步/)).toBeInTheDocument();
    expect(screen.getByText(/当前原型尚未开放「自动申购」执行/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认执行/ })).not.toBeInTheDocument();
  });
});

describe('小安 · 澄清', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('同音候选默认不选中，选择后生成计划但不直接执行', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ mode: 'homophone' }));
    await sendText(user, '给王武转 3000');

    expect(await screen.findByText('需要澄清')).toBeInTheDocument();
    const cands = screen.getAllByRole('button', { pressed: false });
    const wangwu = screen.getByRole('button', { name: /王武/ });
    const wangwu5 = screen.getByRole('button', { name: /王伍/ });
    expect(wangwu).toHaveAttribute('aria-pressed', 'false');
    expect(wangwu5).toHaveAttribute('aria-pressed', 'false');
    expect(cands.length).toBeGreaterThan(0);

    await user.click(wangwu5);
    expect(await screen.findByText(/执行计划 · 6 步/)).toBeInTheDocument();
    // 没有直接执行：没有审批面板
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-pending-approval')).toBeNull();
  });

  it('无匹配候选时提示手动填写', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ mode: 'homophone' }));
    await sendText(user, '给王武转 3000');
    await screen.findByText('需要澄清');
    // 手动补充失败一次
    await user.type(screen.getByLabelText('补充线索'), '随便写点');
    await user.click(screen.getByRole('button', { name: '提交线索' }));
    expect(await screen.findByText('未能确认收款人，请补充更明确的线索')).toBeInTheDocument();
    // 保留输入
    expect(screen.getByLabelText('补充线索')).toHaveValue('随便写点');
  });

  it('普通新指令取消本次澄清并作为新请求处理', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant());
    await sendText(user, '给王武转 3000');
    await screen.findByText('需要澄清');

    await sendText(user, '给王五转 3000');
    expect(await screen.findByText(/执行计划 · 6 步/)).toBeInTheDocument();
    expect(screen.queryByText('需要澄清')).not.toBeInTheDocument();
  });

  it('取消澄清回到小安且不产生审批', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant());
    await sendText(user, '给王武转 3000');
    await screen.findByText('需要澄清');
    await user.click(screen.getByRole('button', { name: '取消澄清' }));
    expect(await screen.findByText('已取消本次澄清。你可以重新描述需求。')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-pending-approval')).toBeNull();
  });

  it('澄清过期显示重新发起入口，旧 clarification 不可继续提交', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ mode: 'homophone', clarificationExpired: true }));
    await sendText(user, '给王武转 3000');
    await screen.findByText('需要澄清');
    await user.click(screen.getByRole('button', { name: /王武/ }));
    expect(await screen.findByText('澄清已过期，请重新发起')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新发起' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交线索' })).not.toBeInTheDocument();
  });

  it('旧澄清响应不能覆盖新指令（请求序号）', async () => {
    const user = userEvent.setup();
    const selectDefer = defer<AssistantReply>();
    const planDefer = defer<AssistantReply>();
    const fake: AssistantService = {
      getHints: async () => [],
      submitInstruction: () => planDefer.promise,
      selectCandidate: () => selectDefer.promise,
      submitClarificationAnswer: async () => clarificationReply(),
      restartClarification: async () => clarificationReply(),
      cancelPlan: async () => undefined,
    };
    renderAssistant(fake);

    // 先进入澄清
    await sendText(user, '给王武转 3000');
    await act(async () => {
      planDefer.resolve(clarificationReply());
    });
    await screen.findByText('需要澄清');

    // 选择候选（在途）
    await user.click(screen.getByRole('button', { name: /王武/ }));
    // 普通新指令取消澄清
    await sendText(user, '给王五转 3000');
    // 旧的候选响应此时返回 → 必须被丢弃
    await act(async () => {
      selectDefer.resolve(planReply({ amountCents: 9999_00 }));
    });
    expect(screen.queryByText(/¥9,999\.00/)).not.toBeInTheDocument();
  });
});

describe('小安 · 安全交接', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    clearMockTransferRecords();
  });

  it('确认执行进入既有 ApprovalSheet（黄级）', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant(), new MockTransferService({ confirm: 'success' }, 0));
    await sendText(user, '给王五转 3000');
    await screen.findByRole('button', { name: /确认执行/ });
    await user.click(screen.getByRole('button', { name: /确认执行/ }));

    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByText(/黄级/)).toBeInTheDocument();
    await user.click(within(sheet).getByRole('button', { name: '确认操作' }));
    expect(await within(sheet).findByText('操作已完成')).toBeInTheDocument();
  });

  it('红级金额需要 OTP，未完成不可确认且无重发入口', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ amountCents: 6000_00 }), new MockTransferService({}, 0));
    await sendText(user, '给王五转 6000');
    await screen.findByRole('button', { name: /确认执行/ });
    await user.click(screen.getByRole('button', { name: /确认执行/ }));

    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByText(/红级/)).toBeInTheDocument();
    const confirm = within(sheet).getByRole('button', { name: '验证并确认' });
    expect(confirm).toBeDisabled();
    expect(within(sheet).queryByText(/重新发送/)).not.toBeInTheDocument();

    await user.click(within(sheet).getByRole('checkbox'));
    await user.type(within(sheet).getByLabelText('短信验证码（6 位）'), '123456');
    expect(confirm).toBeEnabled();
  });

  it('UNKNOWN 保存转账 pending，并可在小安页恢复', async () => {
    const user = userEvent.setup();
    const transfer = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'success' }, 0);
    const view = renderAssistant(mockAssistant(), transfer);
    await sendText(user, '给王五转 3000');
    await screen.findByRole('button', { name: /确认执行/ });
    await user.click(screen.getByRole('button', { name: /确认执行/ }));
    const sheet = await screen.findByRole('dialog');
    await user.click(within(sheet).getByRole('button', { name: '确认操作' }));
    expect(await within(sheet).findByText('结果待确认')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-pending-approval')).not.toBeNull();

    view.unmount();
    renderAssistant(mockAssistant(), new MockTransferService());
    expect(await screen.findByText('有一项待确认的转账操作。')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-pending-approval')).toBeNull();
  });

  it('取消计划作废旧预演且不调用确认', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant(), new MockTransferService({}, 0));
    await sendText(user, '给王五转 3000');
    await screen.findByRole('button', { name: /确认执行/ });
    await user.click(screen.getByRole('button', { name: '取消计划' }));
    expect(await screen.findByText('已取消当前计划，没有产生任何资金变动。')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认执行/ })).not.toBeInTheDocument();
  });

  it('返回后保留会话，重新挂载仍显示计划', async () => {
    const user = userEvent.setup();
    const view = renderAssistant(mockAssistant());
    await sendText(user, '给王五转 3000');
    await screen.findByText(/执行计划 · 6 步/);
    view.unmount();

    renderAssistant(mockAssistant());
    expect(await screen.findByText(/执行计划 · 6 步/)).toBeInTheDocument();
    expect(screen.getByText('规划摘要')).toBeInTheDocument();
  });

  it('离线禁用发送并显示恢复入口', async () => {
    renderAssistant(mockAssistant({ offline: true }));
    await waitFor(() => expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument());
    expect(screen.getByLabelText('告诉小安你的需求')).toBeDisabled();
  });

  it('生成失败可重试并保留上下文', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ generateFailOnce: true }));
    await sendText(user, '给王五转 3000');
    expect(await screen.findByText('计划生成失败，请重试')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText(/执行计划 · 6 步/)).toBeInTheDocument();
  });
});

describe('小安 · 澄清候选与转账权威目录一致', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearMockTransferRecords();
  });

  async function pickAndPreview(name: string) {
    const assistant = new MockAssistantService({ mode: 'homophone' }, 0);
    const transfer = new MockTransferService({ confirm: 'success' }, 0);
    const reply = await assistant.submitInstruction({ text: '给王武转 3000' });
    if (reply.kind !== 'clarification') throw new Error('expected clarification');
    const candidate = reply.clarification.candidates.find((c) => c.name === name);
    if (!candidate) throw new Error(`missing candidate ${name}`);
    const chosen = await assistant.selectCandidate(reply.clarification.clarificationId, candidate.candidateId);
    if (chosen.kind !== 'plan' || !chosen.plan.target) throw new Error('expected plan');
    const preview = await transfer.previewTransfer({
      amountCents: chosen.plan.target.amountCents,
      fromAccountId: chosen.plan.target.fromAccountId,
      recipientId: chosen.plan.target.recipientId,
      memo: chosen.plan.target.memo,
    });
    if (preview.kind !== 'ok') throw new Error(`preview failed: ${JSON.stringify(preview)}`);
    const planView = await transfer.getPlanView(preview.approvalId);
    return { chosen, transfer, preview, planView };
  }

  it('王武候选 → PlanView → 黄级确认成功，姓名与尾号不串换', async () => {
    const { chosen, transfer, preview, planView } = await pickAndPreview('王武');
    expect(chosen.plan.target!.recipientId).toBe('rcp-2210');
    expect(planView.recipientName).toBe('王武');
    expect(planView.recipientAccount).toContain('2210');
    expect(planView.riskLevel).toBe('yellow');
    await expect(transfer.confirmApproval(preview.approvalId, { kind: 'yellow' })).resolves.toMatchObject({
      status: 'success',
    });
  });

  it('王伍候选 → PlanView → 黄级确认成功，姓名与尾号不串换', async () => {
    const { chosen, transfer, preview, planView } = await pickAndPreview('王伍');
    expect(chosen.plan.target!.recipientId).toBe('rcp-8873');
    expect(planView.recipientName).toBe('王伍');
    expect(planView.recipientAccount).toContain('8873');
    await expect(transfer.confirmApproval(preview.approvalId, { kind: 'yellow' })).resolves.toMatchObject({
      status: 'success',
    });
  });

  it('不存在的收款人仍被拒绝，且不回退到其他收款人', async () => {
    const transfer = new MockTransferService({}, 0);
    await expect(
      transfer.previewTransfer({
        amountCents: 1000_00,
        fromAccountId: 'acc-current',
        recipientId: 'rcp-9999',
        memo: '',
      }),
    ).resolves.toMatchObject({ kind: 'error', code: 'RECIPIENT_NOT_FOUND' });

    const list = await transfer.listRecipients();
    expect(list.find((r) => r.id === 'rcp-wang')).toMatchObject({ name: '王五', mask: '8891' });
    expect(list.find((r) => r.id === 'rcp-2210')).toMatchObject({ name: '王武', mask: '2210' });
    expect(list.find((r) => r.id === 'rcp-8873')).toMatchObject({ name: '王伍', mask: '8873' });
  });

  it('页面内选择王伍后确认执行，面板显示王伍 8873 并成功', async () => {
    const user = userEvent.setup();
    renderAssistant(mockAssistant({ mode: 'homophone' }), new MockTransferService({ confirm: 'success' }, 0));
    await sendText(user, '给王武转 3000');
    await screen.findByText('需要澄清');
    await user.click(screen.getByRole('button', { name: /王伍/ }));
    await screen.findByRole('button', { name: /确认执行/ });
    await user.click(screen.getByRole('button', { name: /确认执行/ }));

    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getAllByText(/王伍/).length).toBeGreaterThan(0);
    expect(within(sheet).getAllByText(/8873/).length).toBeGreaterThan(0);
    await user.click(within(sheet).getByRole('button', { name: '确认操作' }));
    expect(await within(sheet).findByText('操作已完成')).toBeInTheDocument();
  });
});

describe('小安 · 业务跳转与瞬时状态恢复', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearMockTransferRecords();
    routedPath = '/assistant';
  });

  it('「前往理财页」经过 React Router 并真实渲染目标页', async () => {
    const user = userEvent.setup();
    renderAssistantWithRoutes(mockAssistant({ mode: 'autoInvest' }));
    await sendText(user, '把每月工资的 30% 自动申购稳健产品');
    await screen.findByText(/执行计划 · 6 步/);
    await user.click(screen.getByRole('button', { name: '前往理财页' }));
    expect(await screen.findByText('WEALTH_PAGE_CONTENT')).toBeInTheDocument();
    expect(routedPath).toBe('/wealth');
  });

  it('PLANNING 刷新后不永久加载，可重新发送并提示已中断', async () => {
    window.sessionStorage.setItem(
      'sb-assistant-view',
      JSON.stringify({
        phase: 'PLANNING',
        messages: [{ role: 'user', text: '给王五转 3000' }],
        plan: null,
        clarification: null,
        clarPhase: 'idle',
        input: '给王五转 3000',
        answer: '',
      }),
    );
    const user = userEvent.setup();
    renderAssistant(mockAssistant());

    expect(screen.queryByText(/正在理解需求、查询账户并编排计划/)).not.toBeInTheDocument();
    expect(await screen.findByText(/上次生成已中断/)).toBeInTheDocument();
    expect(screen.getByLabelText('告诉小安你的需求')).toHaveValue('给王五转 3000');

    await user.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText(/执行计划 · 6 步/)).toBeInTheDocument();
  });

  it('resolving 刷新后回到可操作澄清，候选无默认选中且草稿保留', async () => {
    window.sessionStorage.setItem(
      'sb-assistant-view',
      JSON.stringify({
        phase: 'CLARIFICATION',
        messages: [],
        plan: null,
        clarification: {
          clarificationId: 'clr_saved',
          question: '请选择收款人',
          reason: '两个名字同音',
          candidates: [
            { candidateId: 'cand-2210', name: '王武', bankLabel: '本行', mask: '2210' },
            { candidateId: 'cand-8873', name: '王伍', bankLabel: '本行', mask: '8873' },
          ],
          allowManual: true,
        },
        clarPhase: 'resolving',
        input: '',
        answer: '上次转过的那位',
      }),
    );
    renderAssistant(mockAssistant());

    const wangwu = await screen.findByRole('button', { name: /王武/ });
    expect(wangwu).toBeEnabled();
    expect(wangwu).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /王伍/ })).toBeEnabled();
    expect(screen.getByLabelText('补充线索')).toHaveValue('上次转过的那位');
    expect(screen.getByRole('button', { name: '提交线索' })).toBeEnabled();
  });

  it('READY 仍能恢复原计划', async () => {
    const user = userEvent.setup();
    const view = renderAssistant(mockAssistant());
    await sendText(user, '给王五转 3000');
    await screen.findByText(/执行计划 · 6 步/);
    view.unmount();

    renderAssistant(mockAssistant());
    expect(await screen.findByText(/执行计划 · 6 步/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认执行/ })).toBeInTheDocument();
  });

  it('损坏的会话视图安全回退到空会话', async () => {
    window.sessionStorage.setItem(
      'sb-assistant-view',
      JSON.stringify({ phase: 123, messages: 'oops', plan: 5, clarification: 'x', clarPhase: 9 }),
    );
    renderAssistant(mockAssistant());
    expect(await screen.findByText('小安 · 银行智能体')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument();
    expect(screen.getByLabelText('告诉小安你的需求')).toHaveValue('');
  });
});