import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { ProfilePage } from '../pages/Profile';
import { ProfileServiceProvider } from '../services/profile/ProfileService';
import type { ProfileService } from '../services/profile/ProfileService';
import { MockProfileService } from '../services/profile/MockProfileService';
import { TransferServiceProvider } from '../services/transfer/TransferService';
import { MockTransferService } from '../services/transfer/MockTransferService';
import { clearMockTransferRecords } from '../services/transfer/MockTransferService';
import { CardsServiceProvider } from '../services/cards/CardsService';
import { MockCardsService, clearMockCardsRecords } from '../services/cards/MockCardsService';
import { SubscriptionsServiceProvider } from '../services/subscriptions/SubscriptionsService';
import {
  MockSubscriptionsService,
  clearMockSubscriptionsRecords,
} from '../services/subscriptions/MockSubscriptionsService';
import { WealthServiceProvider } from '../services/wealth/WealthService';
import { MockWealthService, clearMockWealthRecords } from '../services/wealth/MockWealthService';
import { SessionProvider } from '../app/SessionProvider';
import { ThemeProvider } from '../app/ThemeProvider';
import { AuthServiceProvider } from '../services/auth/AuthService';
import { writeViewState } from '../app/viewState';

let currentPath = '/profile';
function Probe() {
  currentPath = useLocation().pathname;
  return null;
}

interface RenderOpts {
  profile?: ProfileService;
  transfer?: MockTransferService;
  cards?: MockCardsService;
  subs?: MockSubscriptionsService;
  wealth?: MockWealthService;
}

function renderProfile(opts: RenderOpts = {}) {
  const transfer = opts.transfer ?? new MockTransferService({}, 0);
  const cards = opts.cards ?? new MockCardsService({}, 0);
  const subs = opts.subs ?? new MockSubscriptionsService({}, 0);
  const wealth = opts.wealth ?? new MockWealthService({}, 0);
  const profile = opts.profile ?? new MockProfileService(wealth, {}, 0);
  return render(
    <ThemeProvider>
      <SessionProvider initialStatus="authenticated">
        <AuthServiceProvider>
          <MemoryRouter initialEntries={['/profile']}>
            <TransferServiceProvider service={transfer}>
              <CardsServiceProvider service={cards}>
                <SubscriptionsServiceProvider service={subs}>
                  <WealthServiceProvider service={wealth}>
                    <ProfileServiceProvider service={profile}>
                      <ProfilePage />
                      <Probe />
                    </ProfileServiceProvider>
                  </WealthServiceProvider>
                </SubscriptionsServiceProvider>
              </CardsServiceProvider>
            </TransferServiceProvider>
          </MemoryRouter>
        </AuthServiceProvider>
      </SessionProvider>
    </ThemeProvider>,
  );
}

async function seedFourPendings(transfer: MockTransferService, cards: MockCardsService, subs: MockSubscriptionsService, wealth: MockWealthService) {
  const t = await transfer.previewTransfer({
    amountCents: 3000_00,
    fromAccountId: 'acc-current',
    recipientId: 'rcp-wang',
    memo: '',
  });
  if (t.kind === 'ok') writeViewState('sb-pending-approval', t.approvalId);
  const c = await cards.prepareCardOperation('c1', 'lock');
  if (c.kind === 'ok') writeViewState('sb-card-pending-approval', c.approvalId);
  const s = await subs.prepareCancellation('s1');
  if (s.kind === 'ok') writeViewState('sb-subscription-pending-approval', s.approvalId);
  const w = await wealth.preparePurchase('p1', 1000_00);
  if (w.kind === 'ok') writeViewState('sb-wealth-pending-approval', w.approvalId);
}

describe('个人中心 · 账户摘要', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    currentPath = '/profile';
  });

  it('显示用户摘要、L2、风险等级与统计，金额隐私覆盖资产', async () => {
    const user = userEvent.setup();
    const wealth = new MockWealthService({}, 0);
    renderProfile({ wealth, profile: new MockProfileService(wealth, {}, 0) });

    expect(await screen.findByText('李四')).toBeInTheDocument();
    expect(screen.getByText('L2')).toBeInTheDocument();
    expect(screen.getByText('R3 稳健型')).toBeInTheDocument();
    expect(screen.getByText('138****6602')).toBeInTheDocument();
    expect(screen.getByText('已实名')).toBeInTheDocument();
    expect(screen.getByText('¥137,160.30')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument(); // 持仓产品数

    await user.click(screen.getByRole('button', { name: '隐藏金额' }));
    expect(screen.queryByText('¥137,160.30')).not.toBeInTheDocument();
    expect(screen.getByText('••••')).toBeInTheDocument();
  });

  it('资料缺失时降级展示，不整页崩溃', async () => {
    const wealth = new MockWealthService({}, 0);
    renderProfile({
      wealth,
      profile: new MockProfileService(wealth, { missingUser: true }, 0),
    });
    expect(await screen.findByText('未命名用户')).toBeInTheDocument();
    expect(screen.getByText('手机号未提供')).toBeInTheDocument();
  });

  it('摘要加载失败显示错误与重试', async () => {
    const user = userEvent.setup();
    const wealth = new MockWealthService({}, 0);
    const profile = new MockProfileService(wealth, { overviewFailOnce: true }, 0);
    renderProfile({ wealth, profile });
    expect(await screen.findByText(/账户摘要加载失败/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('李四')).toBeInTheDocument();
  });
});

describe('个人中心 · 跨业务待确认', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    clearMockTransferRecords();
    clearMockCardsRecords();
    clearMockSubscriptionsRecords();
    clearMockWealthRecords();
    currentPath = '/profile';
  });

  it('四类 pending 分别按 approval_id 路由到对应 service', async () => {
    const transfer = new MockTransferService({}, 0);
    const cards = new MockCardsService({}, 0);
    const subs = new MockSubscriptionsService({}, 0);
    const wealth = new MockWealthService({}, 0);
    await seedFourPendings(transfer, cards, subs, wealth);

    renderProfile({ transfer, cards, subs, wealth });
    expect(await screen.findByText('待确认审批')).toBeInTheDocument();
    expect(await screen.findByText(/转账 · 王五/)).toBeInTheDocument();
    expect(screen.getByText(/卡片 · Everyday 借记卡/)).toBeInTheDocument();
    expect(screen.getByText(/订阅 · 视频会员/)).toBeInTheDocument();
    expect(screen.getByText(/理财 · 稳健增利/)).toBeInTheDocument();
    // 计数徽标
    expect(screen.getAllByText('4').length).toBeGreaterThan(0);
  });

  it('点击待确认重开对应业务的 ApprovalSheet', async () => {
    const user = userEvent.setup();
    const transfer = new MockTransferService({}, 0);
    const cards = new MockCardsService({}, 0);
    const subs = new MockSubscriptionsService({}, 0);
    const wealth = new MockWealthService({}, 0);
    await seedFourPendings(transfer, cards, subs, wealth);

    renderProfile({ transfer, cards, subs, wealth });
    await screen.findByText(/卡片 · Everyday 借记卡/);
    await user.click(screen.getByText(/卡片 · Everyday 借记卡/));

    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getAllByText(/锁定卡片/).length).toBeGreaterThan(0);
  });

  it('空待确认不显示计数徽标', async () => {
    renderProfile();
    expect(await screen.findByText('李四')).toBeInTheDocument();
    expect(await screen.findByText('当前没有待确认审批。')).toBeInTheDocument();
    expect(screen.queryByText('待确认审批')).not.toBeInTheDocument();
  });

  it('摘要查询失败保留入口并可重试', async () => {
    const user = userEvent.setup();
    let calls = 0;
    let fail = true;
    const transfer = new MockTransferService({}, 0);
    const r = await transfer.previewTransfer({
      amountCents: 1000_00,
      fromAccountId: 'acc-current',
      recipientId: 'rcp-wang',
      memo: '',
    });
    if (r.kind === 'ok') writeViewState('sb-pending-approval', r.approvalId);

    const flakyTransfer = Object.create(transfer) as MockTransferService;
    flakyTransfer.getPlanView = async (id: string) => {
      calls += 1;
      if (fail) throw new Error('摘要获取失败');
      return transfer.getPlanView(id);
    };

    renderProfile({ transfer: flakyTransfer });
    expect(await screen.findByText(/摘要加载失败/)).toBeInTheDocument();
    expect(screen.getByText(/部分摘要无法加载/)).toBeInTheDocument();

    fail = false;
    await user.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(calls).toBeGreaterThan(1));
    expect(await screen.findByText(/转账 · 王五/)).toBeInTheDocument();
  });
});

describe('个人中心 · 审计与通知', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    currentPath = '/profile';
  });

  it('审计只读：无编辑、删除、清空入口', async () => {
    renderProfile();
    await screen.findByText('李四');
    expect(await screen.findByText(/plan_8f3a/)).toBeInTheDocument();
    for (const name of [/^编辑$/, /^删除$/, /^清空$/]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
  });

  it('审计为空显示空态', async () => {
    const wealth = new MockWealthService({}, 0);
    renderProfile({ wealth, profile: new MockProfileService(wealth, { emptyAudit: true }, 0) });
    expect(await screen.findByText('暂无审计记录。')).toBeInTheDocument();
  });

  it('审计加载失败可重试', async () => {
    const user = userEvent.setup();
    const wealth = new MockWealthService({}, 0);
    const profile = new MockProfileService(wealth, { auditFailOnce: true }, 0);
    renderProfile({ wealth, profile });
    expect(await screen.findByText(/审计链加载失败/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText(/plan_8f3a/)).toBeInTheDocument();
  });

  it('通知进入对应业务页；无目标的通知禁用并说明', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('李四');
    expect(await screen.findByText('云音乐即将扣款 ¥15.00')).toBeInTheDocument();

    const noTarget = screen.getByRole('button', { name: /健身私教连续 90 天未使用/ });
    expect(noTarget).toBeDisabled();
    expect(screen.getByText(/该提醒暂无对应业务页/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /云音乐即将扣款/ }));
    expect(currentPath).toBe('/subscriptions');
  });

  it('通知为空显示空态', async () => {
    const wealth = new MockWealthService({}, 0);
    renderProfile({ wealth, profile: new MockProfileService(wealth, { emptyNotifications: true }, 0) });
    expect(await screen.findByText('暂无通知。')).toBeInTheDocument();
  });

  it('离线保留已加载记录并说明不是最新数据', async () => {
    const wealth = new MockWealthService({}, 0);
    renderProfile({ wealth, profile: new MockProfileService(wealth, { offline: true }, 0) });
    expect(await screen.findByText(/当前离线，已加载的只读记录会保留/)).toBeInTheDocument();
  });

  it('风险测评状态来自 service，且重测给出明确反馈', async () => {
    const user = userEvent.setup();
    const wealth = new MockWealthService({}, 0);
    renderProfile({ wealth, profile: new MockProfileService(wealth, { riskAssessment: 'expired' }, 0) });
    await screen.findByText('李四');
    await user.click(screen.getByRole('button', { name: /风险测评/ }));
    expect(await screen.findByText('风险测评问卷：原型尚未开放重测。')).toBeInTheDocument();
  });
});