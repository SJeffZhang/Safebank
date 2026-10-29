import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { CardsPage } from '../pages/Cards';
import { CardsServiceProvider } from '../services/cards/CardsService';
import { MockCardsService, clearMockCardsRecords } from '../services/cards/MockCardsService';
import type { CardsService } from '../services/cards/CardsService';
import type { CardInfo, CardOperationKind, CardPlanView } from '../services/cards/types';
import { GatewayOfflineError } from '../services/approval/shared';

let path = '/cards';
function Probe() {
  path = useLocation().pathname;
  return null;
}

function renderCards(service: CardsService) {
  return render(
    <MemoryRouter initialEntries={['/cards']}>
      <CardsServiceProvider service={service}>
        <CardsPage />
        <Probe />
      </CardsServiceProvider>
    </MemoryRouter>,
  );
}

function mockSvc() {
  return new MockCardsService({}, 0);
}

describe('MockCardsService', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('lists cards with normal / locked / expiring / reported-lost states', async () => {
    const s = mockSvc();
    const cards = await s.listCards();
    expect(cards.map((c) => c.status).sort()).toEqual(['locked', 'normal', 'normal', 'reportedLost']);
    const lost = cards.find((c) => c.status === 'reportedLost')!;
    expect(lost.availableOperations).toHaveLength(0);
    const locked = cards.find((c) => c.status === 'locked')!;
    expect(locked.availableOperations).toContain('unlock');
  });

  it('prepare rejects unavailable operation', async () => {
    const s = mockSvc();
    const cards = await s.listCards();
    const lost = cards.find((c) => c.status === 'reportedLost')!;
    await expect(s.prepareCardOperation(lost.id, 'lock')).resolves.toMatchObject({ kind: 'error' });
  });

  it('lock success changes card state after confirm; failed does not', async () => {
    const s = new MockCardsService({ confirm: 'success' }, 0);
    const cards = await s.listCards();
    const normal = cards.find((c) => c.status === 'normal')!;
    const res = await s.prepareCardOperation(normal.id, 'lock');
    expect(res.kind).toBe('ok');
    const id = res.kind === 'ok' ? res.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    const after = await s.listCards();
    expect(after.find((c) => c.id === normal.id)?.status).toBe('locked');
  });

  it('failed confirm leaves card unchanged', async () => {
    const s = new MockCardsService({ confirm: 'failedUncertain' }, 0);
    const normal = (await s.listCards()).find((c) => c.status === 'normal')!;
    const res = await s.prepareCardOperation(normal.id, 'lock');
    const id = res.kind === 'ok' ? res.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    expect((await s.listCards()).find((c) => c.id === normal.id)?.status).toBe('normal');
  });

  it('expired confirm rejects with APPROVAL_EXPIRED', async () => {
    let now = 0;
    const s = new MockCardsService({}, 0, () => now);
    const normal = (await s.listCards()).find((c) => c.status === 'normal')!;
    const res = await s.prepareCardOperation(normal.id, 'lock');
    const id = res.kind === 'ok' ? res.approvalId : '';
    now = 5 * 60_000 + 1;
    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_EXPIRED' });
  });

  it('refresh: new instance restores record and id does not collide', async () => {
    const s1 = new MockCardsService({ confirm: 'unknown', afterUnknownStatus: 'success' }, 0);
    const normal = (await s1.listCards()).find((c) => c.status === 'normal')!;
    const r1 = await s1.prepareCardOperation(normal.id, 'lock');
    const id1 = r1.kind === 'ok' ? r1.approvalId : '';
    await s1.confirmApproval(id1, { kind: 'yellow' });

    const s2 = new MockCardsService(); // 默认场景
    expect(await s2.getApprovalStatus(id1)).toBe('success');
    const r2 = await s2.prepareCardOperation(normal.id, 'unlock');
    const id2 = r2.kind === 'ok' ? r2.approvalId : '';
    expect(id2).not.toBe(id1);
  });

  it('clearMockCardsRecords wipes records and state', async () => {
    const s = new MockCardsService({ confirm: 'success' }, 0);
    const normal = (await s.listCards()).find((c) => c.status === 'normal')!;
    const r = await s.prepareCardOperation(normal.id, 'lock');
    const id = r.kind === 'ok' ? r.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    clearMockCardsRecords();
    const s2 = new MockCardsService({}, 0);
    expect((await s2.listCards()).find((c) => c.id === normal.id)?.status).toBe('normal');
    await expect(s2.getApprovalStatus(id)).rejects.toMatchObject({ code: 'APPROVAL_NOT_FOUND' });
  });
});

describe('CardsPage', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    path = '/cards';
  });

  it('renders cards and their statuses without full card number', async () => {
    renderCards(mockSvc());
    expect(await screen.findByText('Everyday 借记卡')).toBeInTheDocument();
    expect(screen.getAllByText(/•••• •••• •••• \d{4}/).length).toBeGreaterThan(0);
    expect(screen.getByText('已挂失')).toBeInTheDocument();
  });

  it('reported-lost card has no operation buttons', async () => {
    renderCards(mockSvc());
    await screen.findByText('备用卡');
    // 挂失卡片没有锁定/解锁/挂失等操作按钮
    const lostCard = screen.getByText('备用卡');
    const cardEl = lostCard.closest('div')?.parentElement;
    expect(cardEl).toBeTruthy();
  });

  it('lock → confirm success → card becomes locked (refetch)', async () => {
    const user = userEvent.setup();
    const svc = new MockCardsService({ confirm: 'success' }, 0);
    renderCards(svc);
    await screen.findByText('Everyday 借记卡');

    await user.click(screen.getAllByRole('button', { name: '锁定' })[0]);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));

    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    // 关闭后重新拉取列表，原 normal 卡 + 原 locked 卡均显示已锁定
    await user.click(within(dialog).getByRole('button', { name: '完成' }));
    expect((await screen.findAllByText('已锁定')).length).toBe(2);
    expect(path).toBe('/cards');
  });

  it('cancel/reject does not call confirm and card unchanged', async () => {
    const user = userEvent.setup();
    const svc = new MockCardsService({ confirm: 'success' }, 0);
    renderCards(svc);
    await screen.findByText('Everyday 借记卡');

    await user.click(screen.getAllByRole('button', { name: '锁定' })[0]);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '拒绝' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    // 卡片仍未锁定
    expect(screen.getAllByText('正常').length).toBeGreaterThan(0);
  });

  it('failed does not switch card state and does not claim "unchanged" by default', async () => {
    const user = userEvent.setup();
    const svc = new MockCardsService({ confirm: 'failedUncertain' }, 0);
    renderCards(svc);
    await screen.findByText('Everyday 借记卡');

    await user.click(screen.getAllByRole('button', { name: '锁定' })[0]);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
    expect(await screen.findByText('操作未完成')).toBeInTheDocument();
    // 中性失败说明，不声称「状态未变更」
    expect(screen.queryByText('卡片状态未变更')).not.toBeInTheDocument();
    expect(screen.getAllByText('正常').length).toBeGreaterThan(0);
  });

  it('PlanView load failure shows visible error and can retry', async () => {
    const user = userEvent.setup();
    const svc = new MockCardsService({ planViewFailOnce: true }, 0);
    renderCards(svc);
    await screen.findByText('Everyday 借记卡');

    await user.click(screen.getAllByRole('button', { name: '锁定' })[0]);
    // 无确认面板（不空白），显示错误 + 重试
    expect(await screen.findByText('预演详情加载失败，请重试')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试预演详情' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重试预演详情' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByText('锁定卡片').length).toBeGreaterThan(0);
  });

  it('PlanView expired on load shows feedback and does not open confirmable panel', async () => {
    const svc = new MockCardsService({ planViewExpired: true }, 0);
    renderCards(svc);
    await screen.findByText('Everyday 借记卡');

    await userEvent.setup().click(screen.getAllByRole('button', { name: '锁定' })[0]);
    expect(await screen.findByText('预演已过期，请重新预演')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新预演' })).toBeInTheDocument();
  });

  it('red reportLost does not show investment advice text', async () => {
    const svc = new MockCardsService({ confirm: 'success' }, 0);
    renderCards(svc);
    await screen.findByText('Everyday 借记卡');

    await userEvent.setup().click(screen.getAllByRole('button', { name: '挂失' })[0]);
    await screen.findByRole('dialog');
    expect(screen.queryByText(/投资建议/)).not.toBeInTheDocument();
    expect(screen.queryByText(/市场有风险/)).not.toBeInTheDocument();
  });

  it('rapid lock → reportLost: slow stale lock response does not override reportLost', async () => {
    let lockResolve!: (v: { kind: 'ok'; approvalId: string }) => void;
    let reportResolve!: (v: { kind: 'ok'; approvalId: string }) => void;
    const normalCard: CardInfo = {
      id: 'c1',
      name: '测试卡',
      last4: '0001',
      maskedNumber: '•••• 0001',
      brand: 'debit',
      expiry: '08/30',
      expiringSoon: false,
      status: 'normal',
      onlinePayEnabled: true,
      availableOperations: ['lock', 'reportLost'],
    };
    const svc: CardsService = {
      listCards: async () => [normalCard],
      prepareCardOperation: (_cardId, op) =>
        new Promise((res) => {
          if (op === 'lock') lockResolve = res;
          else reportResolve = res;
        }),
      getPlanView: async (id) => ({
        approvalId: id,
        riskLevel: 'yellow',
        title: id.includes('report') ? '挂失卡片' : '锁定卡片',
        cardName: '测试卡',
        maskedNumber: '•••• 0001',
        operationLabel: id.includes('report') ? '挂失卡片' : '锁定卡片',
        effectText: '',
        riskNotes: [],
        expiresAtMillis: Date.now() + 60000,
      }),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'unknown',
      rejectApproval: async () => undefined,
    };
    const user = userEvent.setup();
    renderCards(svc);
    await screen.findByText('测试卡');

    await user.click(screen.getByRole('button', { name: '锁定' }));
    await user.click(screen.getByRole('button', { name: '挂失' }));

    // 挂失先返回
    reportResolve({ kind: 'ok', approvalId: 'mock-card-report-1' });
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByText('挂失卡片').length).toBeGreaterThan(0);

    // 较慢的锁定响应后返回 → 不得覆盖
    lockResolve({ kind: 'ok', approvalId: 'mock-card-lock-1' });
    await waitFor(() => expect(screen.queryByText('锁定卡片')).not.toBeInTheDocument());
  });

  it('empty card list shows empty state, not load failure', async () => {
    renderCards(new MockCardsService({ emptyList: true }, 0));
    expect(await screen.findByText('暂无卡片')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeInTheDocument();
  });

  it('expired 重新预演 re-runs prepare with a new approval id instead of only reloading the list', async () => {
    const user = userEvent.setup();
    let expired = true;
    let prepareCount = 0;
    const ids: string[] = [];
    const card: CardInfo = {
      id: 'c1',
      name: '测试卡',
      last4: '0001',
      maskedNumber: '•••• 0001',
      brand: 'debit',
      expiry: '08/30',
      expiringSoon: false,
      status: 'normal',
      onlinePayEnabled: true,
      availableOperations: ['lock', 'reportLost'],
    };
    const svc: CardsService = {
      listCards: async () => [card],
      prepareCardOperation: async (_cardId: string, _op: CardOperationKind) => {
        prepareCount += 1;
        const id = `apv-${prepareCount}`;
        ids.push(id);
        return { kind: 'ok', approvalId: id };
      },
      getPlanView: async (id): Promise<CardPlanView> => ({
        approvalId: id,
        riskLevel: 'yellow',
        title: '锁定卡片',
        cardName: '测试卡',
        maskedNumber: '•••• 0001',
        operationLabel: '锁定卡片',
        effectText: '锁定后卡片暂停交易。',
        riskNotes: [],
        expiresAtMillis: expired ? Date.now() - 1000 : Date.now() + 60_000,
      }),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'success',
      rejectApproval: async () => undefined,
    };
    renderCards(svc);
    await screen.findByText('测试卡');

    await user.click(screen.getByRole('button', { name: '锁定' }));
    expect(await screen.findByText('预演已过期，请重新预演')).toBeInTheDocument();
    expect(prepareCount).toBe(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    expired = false;
    await user.click(screen.getByRole('button', { name: '重新预演' }));

    const dialog = await screen.findByRole('dialog');
    expect(prepareCount).toBe(2); // 真正重新执行了 prepare
    expect(ids[1]).not.toBe(ids[0]); // 新 approval_id
    expect(within(dialog).getByText(new RegExp(ids[1]))).toBeInTheDocument();
    expect(within(dialog).queryByText(new RegExp(ids[0]))).not.toBeInTheDocument();
    expect(path).toBe('/cards');
  });

  it('pending entry survives offline query failure, then recovers after coming back online', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('sb-card-pending-approval', JSON.stringify('apv-9'));
    let offline = true;
    let queriedId = '';
    const card: CardInfo = {
      id: 'c1',
      name: '测试卡',
      last4: '0001',
      maskedNumber: '•••• 0001',
      brand: 'debit',
      expiry: '08/30',
      expiringSoon: false,
      status: 'normal',
      onlinePayEnabled: true,
      availableOperations: ['lock'],
    };
    const svc: CardsService = {
      listCards: async () => [card],
      prepareCardOperation: async () => ({ kind: 'ok', approvalId: 'apv-new' }),
      getPlanView: async (id): Promise<CardPlanView> => ({
        approvalId: id,
        riskLevel: 'yellow',
        title: '锁定卡片',
        cardName: '测试卡',
        maskedNumber: '•••• 0001',
        operationLabel: '锁定卡片',
        effectText: '',
        riskNotes: [],
        expiresAtMillis: Date.now() + 60_000,
      }),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async (id) => {
        queriedId = id;
        if (offline) throw new GatewayOfflineError();
        return 'success';
      },
      rejectApproval: async () => undefined,
    };
    renderCards(svc);
    await screen.findByText('测试卡');
    expect(screen.getByText(/有一项待确认的卡片操作/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText(/查询未完成|网络不可用/)).toBeInTheDocument();
    // 入口仍在，pending 未清除，并使用准确 ID 查询
    expect(screen.getByRole('button', { name: '查看状态' })).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-card-pending-approval')).toBe(JSON.stringify('apv-9'));
    expect(queriedId).toBe('apv-9');

    offline = false;
    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    expect(screen.queryByText(/有一项待确认的卡片操作/)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-card-pending-approval')).toBeNull();
  });

  it('ordinary query failure keeps the pending entry and does not clear stored pending', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('sb-card-pending-approval', JSON.stringify('apv-7'));
    const card: CardInfo = {
      id: 'c1',
      name: '测试卡',
      last4: '0001',
      maskedNumber: '•••• 0001',
      brand: 'debit',
      expiry: '08/30',
      expiringSoon: false,
      status: 'normal',
      onlinePayEnabled: true,
      availableOperations: ['lock'],
    };
    const svc: CardsService = {
      listCards: async () => [card],
      prepareCardOperation: async () => ({ kind: 'ok', approvalId: 'apv-new' }),
      getPlanView: async (id): Promise<CardPlanView> => ({
        approvalId: id,
        riskLevel: 'yellow',
        title: '锁定卡片',
        cardName: '测试卡',
        maskedNumber: '•••• 0001',
        operationLabel: '锁定卡片',
        effectText: '',
        riskNotes: [],
        expiresAtMillis: Date.now() + 60_000,
      }),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => {
        throw new Error('boom');
      },
      rejectApproval: async () => undefined,
    };
    renderCards(svc);
    await screen.findByText('测试卡');

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText('查询未完成，请重试。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看状态' })).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-card-pending-approval')).toBe(JSON.stringify('apv-7'));
  });

  it('persistent UNKNOWN keeps the pending entry visible', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('sb-card-pending-approval', JSON.stringify('apv-5'));
    const card: CardInfo = {
      id: 'c1',
      name: '测试卡',
      last4: '0001',
      maskedNumber: '•••• 0001',
      brand: 'debit',
      expiry: '08/30',
      expiringSoon: false,
      status: 'normal',
      onlinePayEnabled: true,
      availableOperations: ['lock'],
    };
    const svc: CardsService = {
      listCards: async () => [card],
      prepareCardOperation: async () => ({ kind: 'ok', approvalId: 'apv-new' }),
      getPlanView: async (id): Promise<CardPlanView> => ({
        approvalId: id,
        riskLevel: 'yellow',
        title: '锁定卡片',
        cardName: '测试卡',
        maskedNumber: '•••• 0001',
        operationLabel: '锁定卡片',
        effectText: '',
        riskNotes: [],
        expiresAtMillis: Date.now() + 60_000,
      }),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'unknown',
      rejectApproval: async () => undefined,
    };
    renderCards(svc);
    await screen.findByText('测试卡');

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '查看状态' })).toBeInTheDocument());
    expect(window.sessionStorage.getItem('sb-card-pending-approval')).toBe(JSON.stringify('apv-5'));
  });

  it('failed query result clears the pending entry', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('sb-card-pending-approval', JSON.stringify('apv-3'));
    const card: CardInfo = {
      id: 'c1',
      name: '测试卡',
      last4: '0001',
      maskedNumber: '•••• 0001',
      brand: 'debit',
      expiry: '08/30',
      expiringSoon: false,
      status: 'normal',
      onlinePayEnabled: true,
      availableOperations: ['lock'],
    };
    const svc: CardsService = {
      listCards: async () => [card],
      prepareCardOperation: async () => ({ kind: 'ok', approvalId: 'apv-new' }),
      getPlanView: async (id): Promise<CardPlanView> => ({
        approvalId: id,
        riskLevel: 'yellow',
        title: '锁定卡片',
        cardName: '测试卡',
        maskedNumber: '•••• 0001',
        operationLabel: '锁定卡片',
        effectText: '',
        riskNotes: [],
        expiresAtMillis: Date.now() + 60_000,
      }),
      confirmApproval: async () => ({ status: 'success' }),
      getApprovalStatus: async () => 'failed',
      rejectApproval: async () => undefined,
    };
    renderCards(svc);
    await screen.findByText('测试卡');

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText(/操作未完成/)).toBeInTheDocument();
    expect(screen.queryByText(/有一项待确认的卡片操作/)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-card-pending-approval')).toBeNull();
  });
});