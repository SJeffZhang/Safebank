import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { TransferPage } from '../pages/Transfer';
import { TransferServiceProvider } from '../services/transfer/TransferService';
import type { TransferService } from '../services/transfer/TransferService';
import { OfflineError } from '../services/transfer/TransferService';
import { useTransferFlow } from '../services/transfer/useTransferFlow';
import { MockTransferService } from '../services/transfer/MockTransferService';
import type { MockScenario } from '../services/transfer/MockTransferService';
import { DEMO_ACCOUNTS, DEMO_RECIPIENTS } from '../services/transfer/types';
import type { PlanView, PreviewResult } from '../services/transfer/types';
import { ToastProvider } from '../components/Toast';

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function buildPlan(id: string, expiresAtMillis: number): PlanView {
  return {
    approvalId: id,
    riskLevel: 'yellow',
    title: '转给王五',
    recipientName: '王五',
    recipientAccount: '本行 · 8891',
    fromAccount: '活期 · 尾号 1234',
    amountCents: 300000,
    memo: '无备注',
    availableBeforeCents: 600000,
    availableAfterCents: 300000,
    dailyTotalCents: 300000,
    reserved7dCents: 255000,
    feeCents: 0,
    riskFindings: [],
    expiresAtMillis,
  };
}

function renderWithService(service: TransferService, initialEntry = '/transfer') {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <TransferServiceProvider service={service}>
        <ToastProvider>
          <TransferPage />
        </ToastProvider>
      </TransferServiceProvider>
    </MemoryRouter>,
  );
}

function renderTransfer(scenario?: Partial<MockScenario>) {
  return new MockTransferService(scenario, 0);
}

async function fillValidForm(user: ReturnType<typeof userEvent.setup>, amount = '3000') {
  await user.type(screen.getByLabelText('转账金额（元）'), amount);
  await user.selectOptions(screen.getByLabelText('付款账户'), 'acc-current');
  await user.selectOptions(screen.getByLabelText('收款人'), 'rcp-wang');
}

describe('security: stale preview invalidation', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('editing amount during preview: old response does not open panel; new preview accepted', async () => {
    const user = userEvent.setup();
    const defers = [] as { promise: Promise<PreviewResult>; resolve: (v: PreviewResult) => void }[];
    let planCalls = 0;
    const service: TransferService = {
      listAccounts: async () => DEMO_ACCOUNTS.map((a) => ({ ...a })),
      listRecipients: async () => DEMO_RECIPIENTS.map((r) => ({ ...r })),
      getDailyRemainingCents: async () => 2400000,
      previewTransfer: () => {
        const d = deferred<PreviewResult>();
        defers.push(d);
        return d.promise;
      },
      getPlanView: async (id) => {
        planCalls += 1;
        return buildPlan(id, Date.now() + 300000);
      },
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'unknown',
      rejectApproval: async () => undefined,
    };
    renderWithService(service);
    await screen.findByRole('option', { name: /活期/ });

    const amount = screen.getByLabelText('转账金额（元）');
    await fillValidForm(user, '3000');
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    expect(defers).toHaveLength(1);

    // 请求未返回时修改金额
    await user.clear(amount);
    await user.type(amount, '4000');

    // 旧响应返回：不得打开面板、不得加载 PlanView
    defers[0].resolve({ kind: 'ok', approvalId: 'mock-apv-old' });
    await waitFor(() => {});
    expect(planCalls).toBe(0);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // 重新预演：只接受最新请求
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    expect(defers).toHaveLength(2);
    defers[1].resolve({ kind: 'ok', approvalId: 'mock-apv-new' });
    expect(await screen.findByRole('dialog', { name: '确认操作' })).toBeInTheDocument();
    expect(planCalls).toBe(1);
  });

  it('editing account during preview: old response cannot override', async () => {
    const user = userEvent.setup();
    const defers = [] as { promise: Promise<PreviewResult>; resolve: (v: PreviewResult) => void }[];
    let lastInput = '';
    const service: TransferService = {
      listAccounts: async () => DEMO_ACCOUNTS.map((a) => ({ ...a })),
      listRecipients: async () => DEMO_RECIPIENTS.map((r) => ({ ...r })),
      getDailyRemainingCents: async () => 2400000,
      previewTransfer: (input) => {
        lastInput = input.fromAccountId;
        const d = deferred<PreviewResult>();
        defers.push(d);
        return d.promise;
      },
      getPlanView: async (id) => buildPlan(id, Date.now() + 300000),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'unknown',
      rejectApproval: async () => undefined,
    };
    renderWithService(service);
    await screen.findByRole('option', { name: /活期/ });

    await fillValidForm(user, '3000');
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    expect(lastInput).toBe('acc-current');

    // 修改账户使旧请求失效
    await user.selectOptions(screen.getByLabelText('付款账户'), 'acc-savings');
    defers[0].resolve({ kind: 'ok', approvalId: 'mock-apv-old' });
    await waitFor(() => {});
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('security: offline reference loading', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('initial offline shows reload entry, then recovers lists without page refresh', async () => {
    const user = userEvent.setup();
    let offline = true;
    const service: TransferService = {
      listAccounts: async () => {
        if (offline) throw new OfflineError();
        return DEMO_ACCOUNTS.map((a) => ({ ...a }));
      },
      listRecipients: async () => {
        if (offline) throw new OfflineError();
        return DEMO_RECIPIENTS.map((r) => ({ ...r }));
      },
      getDailyRemainingCents: async () => {
        if (offline) throw new OfflineError();
        return 2400000;
      },
      previewTransfer: async () => ({ kind: 'ok', approvalId: 'a1' }),
      getPlanView: async (id) => buildPlan(id, Date.now() + 300000),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'unknown',
      rejectApproval: async () => undefined,
    };
    renderWithService(service);

    expect(await screen.findByRole('button', { name: '重新加载' })).toBeInTheDocument();
    // 不能把失败误报为“没有收款人/0 个账户”
    expect(screen.queryByRole('option', { name: /活期/ })).not.toBeInTheDocument();

    offline = false;
    await user.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByRole('option', { name: /活期/ })).toBeInTheDocument();
  });

  it('reload failure with existing data keeps old content', async () => {
    let fail = false;
    const service: TransferService = {
      listAccounts: async () => {
        if (fail) throw new OfflineError();
        return DEMO_ACCOUNTS.map((a) => ({ ...a }));
      },
      listRecipients: async () => DEMO_RECIPIENTS.map((r) => ({ ...r })),
      getDailyRemainingCents: async () => 2400000,
      previewTransfer: async () => ({ kind: 'ok', approvalId: 'a1' }),
      getPlanView: async (id) => buildPlan(id, Date.now() + 300000),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'unknown',
      rejectApproval: async () => undefined,
    };
    const { result } = renderHook(() => useTransferFlow(service));

    await act(async () => {
      await result.current.actions.loadReferences();
    });
    expect(result.current.state.accounts.length).toBeGreaterThan(0);

    // 二次加载失败：已有数据保留，不误报为空
    fail = true;
    await act(async () => {
      await result.current.actions.loadReferences();
    });
    expect(result.current.state.accounts.length).toBeGreaterThan(0);
    expect(result.current.state.offline).toBe(true);
  });
});

describe('security: expiry at confirm + risk disclosure', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('front-end: expired at confirm does not call confirmApproval', async () => {
    let now = 0;
    let confirmCalls = 0;
    const service: TransferService = {
      listAccounts: async () => DEMO_ACCOUNTS.map((a) => ({ ...a })),
      listRecipients: async () => DEMO_RECIPIENTS.map((r) => ({ ...r })),
      getDailyRemainingCents: async () => 2400000,
      previewTransfer: async () => ({ kind: 'ok', approvalId: 'a1' }),
      getPlanView: async (id) => buildPlan(id, 300000),
      confirmApproval: async () => {
        confirmCalls += 1;
        return { status: 'success' };
      },
      getApprovalStatus: async () => 'unknown',
      rejectApproval: async () => undefined,
    };
    const { result } = renderHook(() => useTransferFlow(service, { now: () => now }));

    await act(async () => {
      result.current.actions.updateAmount('3000');
      result.current.actions.selectAccount('acc-current');
      result.current.actions.selectRecipient('rcp-wang');
    });
    await act(async () => {
      await result.current.actions.preview();
    });
    expect(result.current.state.planStatus).toBe('ready');

    now = 300001; // 打开后过期
    await act(async () => {
      await result.current.actions.confirm();
    });

    expect(confirmCalls).toBe(0);
    expect(result.current.state.planStatus).toBe('expired');
    expect(result.current.state.sheetOpen).toBe(false);
  });

  it('red transfer shows authorization disclosure, not investment advice', async () => {
    const user = userEvent.setup();
    const service = renderTransfer({ confirm: 'success' });
    renderWithService(service);

    await fillValidForm(user, '6000');
    await user.click(screen.getByRole('button', { name: /预演这笔转账/ }));
    const dialog = await screen.findByRole('dialog', { name: '确认操作' });

    expect(within(dialog).getByText(/OTP 仅用于本次授权/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/投资建议/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/市场有风险/)).not.toBeInTheDocument();
  });
});
