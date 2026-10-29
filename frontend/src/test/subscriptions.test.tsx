import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { SubscriptionsPage } from '../pages/Subscriptions';
import { SubscriptionsServiceProvider } from '../services/subscriptions/SubscriptionsService';
import {
  MockSubscriptionsService,
  clearMockSubscriptionsRecords,
} from '../services/subscriptions/MockSubscriptionsService';
import type { SubscriptionsService } from '../services/subscriptions/SubscriptionsService';
import type { Subscription, SubscriptionPlanView } from '../services/subscriptions/types';
import { GatewayOfflineError } from '../services/approval/shared';

let path = '/subscriptions';
function Probe() {
  path = useLocation().pathname;
  return null;
}

function renderSubs(service: SubscriptionsService) {
  return render(
    <MemoryRouter initialEntries={['/subscriptions']}>
      <SubscriptionsServiceProvider service={service}>
        <SubscriptionsPage />
        <Probe />
      </SubscriptionsServiceProvider>
    </MemoryRouter>,
  );
}

function mockSvc() {
  return new MockSubscriptionsService({}, 0);
}

function defer<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const SUB_FIXTURE: Subscription[] = [
  {
    id: 's1',
    name: '视频会员',
    iconLabel: '视',
    cycleText: '每月',
    nextChargeDate: '11-05',
    nextChargeDateISO: '2026-11-05',
    amountCents: 2500,
    amountUnit: '/ 月',
    status: 'active',
    idle: false,
    reminderTime: '09:00',
  },
];

/** 造一个可注入 deferred 预演 / 可注入过期 / 可注入查询失败的订阅服务。 */
function makeFakeSubs(opts?: { expired?: () => boolean; statusFail?: () => boolean }) {
  const prepareCalls: string[] = [];
  const prepareDefers: Array<ReturnType<typeof defer<{ kind: 'ok'; approvalId: string }>>> = [];
  const idToTime = new Map<string, string>();
  const svc: SubscriptionsService = {
    listSubscriptions: async () => SUB_FIXTURE.map((s) => ({ ...s })),
    getUpcomingCharges: async () => ({ upcomingTotalCents: 2500, upcomingCount: 1 }),
    getSummary: async () => ({ monthExpenseCents: 2500, annualizedCents: 30000, activeCount: 1 }),
    prepareCancellation: async () => ({ kind: 'ok', approvalId: 'cancel-1' }),
    prepareReminderChange: (_id, time) => {
      prepareCalls.push(time);
      const d = defer<{ kind: 'ok'; approvalId: string }>();
      prepareDefers.push(d);
      return d.promise;
    },
    getPlanView: async (id): Promise<SubscriptionPlanView> => {
      const time = idToTime.get(id) ?? '??';
      return {
        approvalId: id,
        riskLevel: 'yellow',
        title: '修改提醒时间',
        subscriptionName: '视频会员',
        operation: 'reminder',
        billingText: '每月 11-05 扣款',
        beforeReminder: '09:00',
        afterReminder: time,
        effectText: `扣费提醒时间将调整为 ${time}。`,
        riskNotes: [],
        expiresAtMillis: opts?.expired?.() ? Date.now() - 1000 : Date.now() + 60_000,
      };
    },
    confirmApproval: async () => ({ status: 'success' }),
    getApprovalStatus: async () => {
      if (opts?.statusFail?.()) throw new GatewayOfflineError();
      return 'success';
    },
    rejectApproval: async () => undefined,
  };
  return { svc, prepareCalls, prepareDefers, idToTime };
}

describe('MockSubscriptionsService', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('lists subscriptions with summary and upcoming charges', async () => {
    const s = mockSvc();
    const subs = await s.listSubscriptions();
    expect(subs).toHaveLength(4);
    const summary = await s.getSummary();
    expect(summary.activeCount).toBe(4);
    expect(summary.annualizedCents).toBe(summary.monthExpenseCents * 12);
    const up = await s.getUpcomingCharges();
    expect(up.upcomingCount).toBeGreaterThanOrEqual(0);
  });

  it('upcoming charges respect the 7-day window (today / day boundary / past / cancelled)', async () => {
    // now = 2026-11-01：7 天窗口＝11-01..11-07（不含当天+7 整点）。11-02/11-05 在窗内，11-08/11-12 窗外。
    const s = new MockSubscriptionsService({}, 0, () => Date.parse('2026-11-01T12:00:00Z'));
    const up = await s.getUpcomingCharges();
    expect(up.upcomingCount).toBe(2); // 视频会员(11-05) + 云音乐(11-02)
    expect(up.upcomingTotalCents).toBe(4000);

    // now = 2026-11-02：窗口 11-02..11-08。云存储(11-08) 在窗内，健身私教(11-12) 窗外的第8天之外。
    const s2 = new MockSubscriptionsService({}, 0, () => Date.parse('2026-11-02T12:00:00Z'));
    const up2 = await s2.getUpcomingCharges();
    expect(up2.upcomingCount).toBe(3); // 云音乐/视频/云存储

    // now 早于所有扣款：无未来 7 天待扣
    const s3 = new MockSubscriptionsService({}, 0, () => Date.parse('2026-10-01T12:00:00Z'));
    const up3 = await s3.getUpcomingCharges();
    expect(up3.upcomingCount).toBe(0);
    expect(up3.upcomingTotalCents).toBe(0);
  });

  it('cancelled subscription is excluded from upcoming', async () => {
    const s = new MockSubscriptionsService({ confirm: 'success' }, 0, () => Date.parse('2026-11-01T12:00:00Z'));
    const r = await s.prepareCancellation('s2');
    const id = r.kind === 'ok' ? r.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    const up = await s.getUpcomingCharges();
    // s2(云音乐) 已取消，未来 7 天只余 s1(11-05)
    expect(up.upcomingCount).toBe(1);
    expect(up.upcomingTotalCents).toBe(2500);
  });

  it('cancel success marks subscription cancelled after confirm', async () => {
    const s = new MockSubscriptionsService({ confirm: 'success' }, 0);
    const r = await s.prepareCancellation('s1');
    const id = r.kind === 'ok' ? r.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    const subs = await s.listSubscriptions();
    expect(subs.find((x) => x.id === 's1')?.status).toBe('cancelled');
  });

  it('failed cancel leaves subscription active', async () => {
    const s = new MockSubscriptionsService({ confirm: 'failedUncertain' }, 0);
    const r = await s.prepareCancellation('s1');
    const id = r.kind === 'ok' ? r.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    expect((await s.listSubscriptions()).find((x) => x.id === 's1')?.status).toBe('active');
  });

  it('reminder change stores before/after and applies on success', async () => {
    const s = new MockSubscriptionsService({ confirm: 'success' }, 0);
    const r = await s.prepareReminderChange('s1', '18:30');
    const id = r.kind === 'ok' ? r.approvalId : '';
    const plan = await s.getPlanView(id);
    expect(plan.beforeReminder).toBe('09:00');
    expect(plan.afterReminder).toBe('18:30');
    await s.confirmApproval(id, { kind: 'yellow' });
    expect((await s.listSubscriptions()).find((x) => x.id === 's1')?.reminderTime).toBe('18:30');
  });

  it('invalid reminder time rejected at prepare', async () => {
    const s = mockSvc();
    await expect(s.prepareReminderChange('s1', '25:00')).resolves.toMatchObject({ kind: 'error' });
  });

  it('expired confirm rejects', async () => {
    let now = 0;
    const s = new MockSubscriptionsService({}, 0, () => now);
    const r = await s.prepareCancellation('s1');
    const id = r.kind === 'ok' ? r.approvalId : '';
    now = 5 * 60_000 + 1;
    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_EXPIRED' });
  });

  it('clearMockSubscriptionsRecords resets state', async () => {
    const s = new MockSubscriptionsService({ confirm: 'success' }, 0);
    const r = await s.prepareCancellation('s1');
    const id = r.kind === 'ok' ? r.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    clearMockSubscriptionsRecords();
    expect((await new MockSubscriptionsService({}, 0).listSubscriptions()).find((x) => x.id === 's1')?.status).toBe('active');
  });
});

describe('SubscriptionsPage', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    path = '/subscriptions';
  });

  it('renders summary and subscription list', async () => {
    renderSubs(mockSvc());
    expect(await screen.findByText('视频会员')).toBeInTheDocument();
    expect(screen.getByText(/4 个有效订阅/)).toBeInTheDocument();
  });

  it('cancel → success removes subscription after refetch', async () => {
    const user = userEvent.setup();
    renderSubs(new MockSubscriptionsService({ confirm: 'success' }, 0));
    await screen.findByText('视频会员');

    await user.click(screen.getAllByRole('button', { name: '取消订阅' })[0]);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
    expect(await within(dialog).findByText('操作已完成')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '完成' }));

    // 列表重取后该订阅显示已取消
    expect(await screen.findByText(/已取消/)).toBeInTheDocument();
    expect(path).toBe('/subscriptions');
  });

  it('failed cancel keeps subscription', async () => {
    const user = userEvent.setup();
    renderSubs(new MockSubscriptionsService({ confirm: 'failedUncertain' }, 0));
    await screen.findByText('视频会员');

    await user.click(screen.getAllByRole('button', { name: '取消订阅' })[0]);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
    expect(await within(dialog).findByText('操作未完成')).toBeInTheDocument();
    // 视频会员仍存在（列表或面板中）
    expect(screen.getAllByText('视频会员').length).toBeGreaterThan(0);
  });

  it('unchanged reminder cannot be prepared (button disabled)', async () => {
    renderSubs(mockSvc());
    await screen.findByText('视频会员');
    const buttons = screen.getAllByRole('button', { name: '修改提醒' });
    expect(buttons[0]).toBeDisabled();
  });

  it('reminder change success refetches and updates time', async () => {
    const user = userEvent.setup();
    renderSubs(new MockSubscriptionsService({ confirm: 'success' }, 0));
    await screen.findByText('视频会员');

    // 修改第一个订阅的提醒时间
    const timeInput = screen.getAllByLabelText(/提醒时间/)[0];
    await user.clear(timeInput);
    await user.type(timeInput, '18:30');
    await user.click(screen.getAllByRole('button', { name: '修改提醒' })[0]);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('18:30')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
    expect(await within(dialog).findByText('操作已完成')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '完成' }));

    expect(await screen.findByText(/提醒 18:30/)).toBeInTheDocument();
  });

  it('cancel PlanView shows retentionNote when present (idle subscription)', async () => {
    const user = userEvent.setup();
    renderSubs(new MockSubscriptionsService({ confirm: 'success' }, 0));
    await screen.findByText('视频会员');

    // 健身私教 is idle → retentionNote
    const cancelButtons = screen.getAllByRole('button', { name: '取消订阅' });
    await user.click(cancelButtons[3]); // 健身私教 is the 4th subscription
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/90 天未使用/)).toBeInTheDocument();
  });

  it('cancel PlanView does not show irreversible/refund policy claims', async () => {
    const user = userEvent.setup();
    renderSubs(new MockSubscriptionsService({ confirm: 'success' }, 0));
    await screen.findByText('视频会员');

    await user.click(screen.getAllByRole('button', { name: '取消订阅' })[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByText(/不可逆/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/不退回/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/不退款/)).not.toBeInTheDocument();
  });

  it('red cancel does not show investment advice', async () => {
    const user = userEvent.setup();
    renderSubs(new MockSubscriptionsService({ cancelRisk: 'red' }, 0));
    await screen.findByText('视频会员');

    await user.click(screen.getAllByRole('button', { name: '取消订阅' })[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/红级/)).toBeInTheDocument();
    expect(screen.queryByText(/投资建议/)).not.toBeInTheDocument();
    expect(screen.queryByText(/市场有风险/)).not.toBeInTheDocument();
  });

  it('cancel PlanView failure shows visible error and retry', async () => {
    const user = userEvent.setup();
    renderSubs(new MockSubscriptionsService({ planViewFailOnce: true }, 0));
    await screen.findByText('视频会员');

    await user.click(screen.getAllByRole('button', { name: '取消订阅' })[0]);
    expect(await screen.findByText('预演详情加载失败，请重试')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试预演详情' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('empty subscription list shows empty state', async () => {
    renderSubs(new MockSubscriptionsService({ emptyList: true }, 0));
    expect(await screen.findByText('暂无订阅')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeInTheDocument();
  });

  it('changing reminder draft invalidates the in-flight preview; only the latest time is shown', async () => {
    const user = userEvent.setup();
    const fake = makeFakeSubs();
    renderSubs(fake.svc);
    await screen.findByText('视频会员');

    const input = screen.getByLabelText(/提醒时间/);
    await user.clear(input);
    await user.type(input, '09:20');
    await user.click(screen.getByRole('button', { name: '修改提醒' }));
    expect(fake.prepareCalls).toEqual(['09:20']);

    // 预演未返回时修改草稿 → 旧预演失效
    await user.clear(input);
    await user.type(input, '09:30');

    // 释放旧（09:20）响应：不得打开面板
    fake.idToTime.set('old-1', '09:20');
    await act(async () => {
      fake.prepareDefers[0].resolve({ kind: 'ok', approvalId: 'old-1' });
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // 用最新草稿重新预演
    await user.click(screen.getByRole('button', { name: '修改提醒' }));
    expect(fake.prepareCalls).toEqual(['09:20', '09:30']);
    fake.idToTime.set('new-1', '09:30');
    await act(async () => {
      fake.prepareDefers[1].resolve({ kind: 'ok', approvalId: 'new-1' });
    });

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('09:30')).toBeInTheDocument();
    expect(within(dialog).queryByText('09:20')).not.toBeInTheDocument();
    expect(path).toBe('/subscriptions');
  });

  it('stale preview failure does not override the new request', async () => {
    const user = userEvent.setup();
    const fake = makeFakeSubs();
    renderSubs(fake.svc);
    await screen.findByText('视频会员');

    const input = screen.getByLabelText(/提醒时间/);
    await user.clear(input);
    await user.type(input, '09:20');
    await user.click(screen.getByRole('button', { name: '修改提醒' }));

    await user.clear(input);
    await user.type(input, '09:30');

    // 旧请求失败：不得展示错误、不得影响状态
    await act(async () => {
      fake.prepareDefers[0].reject(new Error('old failure'));
    });
    await waitFor(() => expect(screen.queryByText(/预演失败/)).not.toBeInTheDocument());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('expired 重新预演 re-runs prepare with the latest reminder time and a new approval id', async () => {
    const user = userEvent.setup();
    let expired = true;
    const fake = makeFakeSubs({ expired: () => expired });
    // 让 prepare 立即返回可区分的 id
    const ids = ['apv-exp-1', 'apv-exp-2'];
    let seq = 0;
    fake.svc.prepareReminderChange = (_id, time) => {
      fake.prepareCalls.push(time);
      const id = ids[seq++];
      fake.idToTime.set(id, time);
      return Promise.resolve({ kind: 'ok', approvalId: id });
    };
    renderSubs(fake.svc);
    await screen.findByText('视频会员');

    const input = screen.getByLabelText(/提醒时间/);
    await user.clear(input);
    await user.type(input, '09:20');
    await user.click(screen.getByRole('button', { name: '修改提醒' }));
    expect(await screen.findByText('预演已过期，请重新预演')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // 过期后草稿变化，应以最新时间重新预演
    await user.clear(input);
    await user.type(input, '09:30');
    expired = false;
    await user.click(screen.getByRole('button', { name: '重新预演' }));

    const dialog = await screen.findByRole('dialog');
    expect(fake.prepareCalls).toEqual(['09:20', '09:30']);
    expect(within(dialog).getByText('09:30')).toBeInTheDocument();
    expect(within(dialog).getByText(/apv-exp-2/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/apv-exp-1/)).not.toBeInTheDocument();
    expect(path).toBe('/subscriptions');
  });

  it('pending entry survives a failed status query and recovers after coming back online', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('sb-subscription-pending-approval', JSON.stringify('apv-9'));
    let offline = true;
    const fake = makeFakeSubs({ statusFail: () => offline });
    renderSubs(fake.svc);
    await screen.findByText('视频会员');

    expect(screen.getByText(/有一项待确认的订阅操作/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText(/查询未完成|网络不可用/)).toBeInTheDocument();

    // 入口仍在，且 sessionStorage pending 未被清除
    expect(screen.getByRole('button', { name: '查看状态' })).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-subscription-pending-approval')).toBe(JSON.stringify('apv-9'));

    offline = false;
    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    // SUCCESS 后入口消失且 pending 清除
    expect(screen.queryByText(/有一项待确认的订阅操作/)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-subscription-pending-approval')).toBeNull();
  });
});