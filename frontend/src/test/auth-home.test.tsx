import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { WelcomePage } from '../pages/Welcome';
import { LoginPage } from '../pages/Login';
import { HomePage } from '../pages/Home';
import { SessionProvider, useSession } from '../app/SessionProvider';
import { AuthServiceProvider } from '../services/auth/AuthService';
import { MockAuthService, clearMockAuthState } from '../services/auth/MockAuthService';
import type { AuthService } from '../services/auth/AuthService';
import { HomeServiceProvider } from '../services/home/HomeService';
import type { HomeService } from '../services/home/HomeService';
import type { HomeBill, HomeSummary, HomeTransaction, HomeUpcoming } from '../services/home/types';
import { MockProfileService } from '../services/profile/MockProfileService';
import { MockHomeService } from '../services/home/MockHomeService';
import { MockSubscriptionsService } from '../services/subscriptions/MockSubscriptionsService';
import { MockBillsService } from '../services/bills/MockBillsService';
import { MockTransferService } from '../services/transfer/MockTransferService';
import { MockWealthService } from '../services/wealth/MockWealthService';

let currentPath = '/';
function Probe() {
  currentPath = useLocation().pathname;
  return null;
}

function renderWelcome(status: 'anonymous' | 'authenticated' = 'anonymous') {
  currentPath = '/welcome';
  return render(
    <SessionProvider initialStatus={status}>
      <MemoryRouter initialEntries={['/welcome']}>
        <Probe />
        <Routes>
          <Route path="/welcome" element={<WelcomePage />} />
          <Route path="/login" element={<div>LOGIN_STUB</div>} />
          <Route path="/" element={<div>HOME_STUB</div>} />
        </Routes>
      </MemoryRouter>
    </SessionProvider>,
  );
}

function renderLogin(auth: AuthService) {
  currentPath = '/login';
  return render(
    <SessionProvider initialStatus="anonymous">
      <AuthServiceProvider service={auth}>
        <MemoryRouter initialEntries={['/login']}>
          <Probe />
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<div>HOME_STUB</div>} />
          </Routes>
        </MemoryRouter>
      </AuthServiceProvider>
    </SessionProvider>,
  );
}

async function typePin(user: ReturnType<typeof userEvent.setup>, digits: string) {
  for (const d of digits.split('')) {
    await user.click(screen.getByRole('button', { name: d }));
  }
}

function homeService(
  overrides?: { emptyRecent?: boolean; emptyUpcoming?: boolean; now?: number },
  // 首页聚合测试校验「UI 与 Service 一致」，因此使用原始 mock 数据；
  // 目标简报默认场景另有专门用例。
  targetDefaults = false,
) {
  const now = overrides?.now ?? Date.parse('2026-11-01T12:00:00Z');
  return new MockHomeService(
    {
      auth: new MockAuthService({}, 0),
      transfer: new MockTransferService({}, 0),
      wealth: new MockWealthService({}, 0),
      subscriptions: new MockSubscriptionsService({}, 0, () => now),
      bills: new MockBillsService({}, 0),
    },
    {
      emptyRecent: overrides?.emptyRecent ?? false,
      emptyUpcoming: overrides?.emptyUpcoming ?? false,
    },
    0,
    targetDefaults,
  );
}

function renderHome(
  service: ReturnType<typeof homeService>,
  customer = { customerId: 'cust-zhang', customerName: '张三' },
) {
  // 会话必须在 SessionProvider 初始化之前写入，首页才能读到当前客户。
  window.localStorage.setItem(
    'sb-demo-session',
    JSON.stringify({ authenticated: true, customerId: customer.customerId, customerName: customer.customerName }),
  );
  return render(
    <SessionProvider initialStatus="authenticated">
      <MemoryRouter initialEntries={['/']}>
        <Probe />
        <HomeServiceProvider service={service}>
          <HomePage />
        </HomeServiceProvider>
      </MemoryRouter>
    </SessionProvider>,
  );
}

describe('欢迎页', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    currentPath = '/welcome';
  });

  it('两个入口都只标记已访问并进入登录，不建立会话', async () => {
    for (const label of ['开始使用', '已有账户？登录']) {
      window.localStorage.clear();
      const user = userEvent.setup();
      const view = renderWelcome();
      await user.click(screen.getByRole('button', { name: label }));
      await waitFor(() => expect(currentPath).toBe('/login'));
      expect(window.localStorage.getItem('sb-has-visited')).toBe('1');
      expect(window.localStorage.getItem('sb-demo-session')).toBeNull();
      view.unmount();
    }
  });

  it('已登录用户仍看到欢迎页，点击继续使用直接回首页', async () => {
    const user = userEvent.setup();
    renderWelcome('authenticated');

    expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '已有账户？登录' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '继续使用' }));

    await waitFor(() => expect(currentPath).toBe('/'));
  });

  it('渲染 SafeBank 品牌、英文标题与中文说明', () => {
    renderWelcome();
    expect(screen.getByText('SafeBank')).toBeInTheDocument();
    expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument();
    expect(screen.getByText(/Take control of/)).toBeInTheDocument();
    expect(screen.getByText(/账户、转账与理财/)).toBeInTheDocument();
  });

  it('不再出现旧投资落地页文案', () => {
    renderWelcome();
    for (const legacy of [
      'Clear Insights For Smarter Decisions',
      'Your Investment Space',
      '100+ Markets',
      '+2.4M Investors',
      'Clearvest',
      '跳过引导',
    ]) {
      expect(screen.queryByText(legacy)).not.toBeInTheDocument();
    }
  });
});

describe('演示认证 service', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearMockAuthState();
  });

  it('三位客户与正确 PIN 返回会话', async () => {
    const auth = new MockAuthService({}, 0);
    const customers = await auth.getCustomers();
    expect(customers.map((c) => c.name)).toEqual(['张三', '李四', '孙八']);
    await expect(auth.submitPin('cust-li', '222222')).resolves.toMatchObject({
      kind: 'ok',
      session: { customerId: 'cust-li', customerName: '李四' },
    });
  });

  it('错误 PIN 返回剩余次数，5 次后锁定并可通过时间解锁', async () => {
    let now = 1_000_000;
    const auth = new MockAuthService({}, 0, () => now);
    const results = [];
    for (let i = 0; i < 4; i += 1) results.push(await auth.submitPin('cust-zhang', '000000'));
    expect(results[0]).toMatchObject({ kind: 'error', code: 'PIN_INVALID', remainingAttempts: 4 });
    expect(results[3]).toMatchObject({ kind: 'error', code: 'PIN_INVALID', remainingAttempts: 1 });

    const locked = await auth.submitPin('cust-zhang', '000000');
    expect(locked).toMatchObject({ kind: 'error', code: 'LOCKED' });
    expect(await auth.getLockState('cust-zhang')).toMatchObject({ locked: true });

    // 锁定期间正确 PIN 也不能登录
    await expect(auth.submitPin('cust-zhang', '111111')).resolves.toMatchObject({ code: 'LOCKED' });

    now += 61_000;
    expect(await auth.getLockState('cust-zhang')).toMatchObject({ locked: false });
    await expect(auth.submitPin('cust-zhang', '111111')).resolves.toMatchObject({ kind: 'ok' });
  });

  it('锁定状态跨实例保留（刷新不能绕过），且存储中不含 PIN', async () => {
    const auth = new MockAuthService({}, 0, () => 5_000);
    for (let i = 0; i < 5; i += 1) await auth.submitPin('cust-sun', '999999');
    const refreshed = new MockAuthService({}, 0, () => 5_000);
    expect(await refreshed.getLockState('cust-sun')).toMatchObject({ locked: true });
    const raw = window.sessionStorage.getItem('mock-auth-state') ?? '';
    expect(raw).not.toContain('333333');
    expect(raw).not.toContain('999999');
  });

  it('离线返回 OFFLINE', async () => {
    const auth = new MockAuthService({ offline: true }, 0);
    await expect(auth.submitPin('cust-zhang', '111111')).resolves.toMatchObject({ code: 'OFFLINE' });
  });
});

describe('登录页', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearMockAuthState();
    currentPath = '/login';
  });

  it('三客户平铺，默认选中张三，PIN 圆点与计数', async () => {
    const user = userEvent.setup();
    renderLogin(new MockAuthService({}, 0));
    await screen.findByText('选择演示客户');

    const zhang = screen.getByRole('button', { name: /张三/ });
    expect(zhang).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /李四/ })).toHaveAttribute('aria-pressed', 'false');

    await typePin(user, '123');
    expect(screen.getByText('已输入 3 / 6 位')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '退格' }));
    expect(screen.getByText('已输入 2 / 6 位')).toBeInTheDocument();
  });

  it('未满 6 位不提交', async () => {
    const user = userEvent.setup();
    const auth = new MockAuthService({}, 0);
    let calls = 0;
    const spy: AuthService = {
      getCustomers: () => auth.getCustomers(),
      getLockState: (id) => auth.getLockState(id),
      submitPin: (id, pin) => {
        calls += 1;
        return auth.submitPin(id, pin);
      },
    };
    renderLogin(spy);
    await screen.findByText('选择演示客户');
    await typePin(user, '12345');
    await waitFor(() => expect(screen.getByText('已输入 5 / 6 位')).toBeInTheDocument());
    expect(calls).toBe(0);
  });

  it('错误 PIN 清空输入并显示剩余次数；切换客户保留权威计数', async () => {
    const user = userEvent.setup();
    const auth = new MockAuthService({}, 0);
    renderLogin(auth);
    await screen.findByText('选择演示客户');

    await typePin(user, '000000');
    expect(await screen.findByText(/PIN 不正确 · 剩余 4 次/)).toBeInTheDocument();
    expect(screen.getByText('已输入 0 / 6 位')).toBeInTheDocument();

    // 切换客户：本地输入清空，但张三的服务端计数不重置
    await user.click(screen.getByRole('button', { name: /李四/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /李四/ })).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.queryByText(/PIN 不正确/)).not.toBeInTheDocument();
    expect(await auth.getLockState('cust-zhang')).toMatchObject({ remainingAttempts: 4 });
  });

  it('连续 5 次错误锁定并显示倒计时，键盘不可用', async () => {
    const user = userEvent.setup();
    const auth = new MockAuthService({}, 0);
    renderLogin(auth);
    await screen.findByText('选择演示客户');

    for (let i = 0; i < 5; i += 1) {
      await typePin(user, '000000');
      await waitFor(() => expect(screen.getByText('已输入 0 / 6 位')).toBeInTheDocument());
    }
    expect((await screen.findAllByText(/账户已临时锁定/)).length).toBeGreaterThan(0);
    expect(screen.getByText('已输入 0 / 6 位')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1' })).toBeDisabled();
  });

  it('离线禁止登录并提示', async () => {
    renderLogin(new MockAuthService({ offline: true }, 0));
    expect(await screen.findByText(/当前离线，无法登录/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1' })).toBeDisabled();
  });

  it('成功登录写入正确 customerId 并 replace 到首页', async () => {
    const user = userEvent.setup();
    renderLogin(new MockAuthService({}, 0));
    await screen.findByText('选择演示客户');
    await user.click(screen.getByRole('button', { name: /李四/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /李四/ })).toHaveAttribute('aria-pressed', 'true'));

    await typePin(user, '222222');
    expect(await screen.findByText('HOME_STUB')).toBeInTheDocument();
    expect(currentPath).toBe('/');
    const session = JSON.parse(window.localStorage.getItem('sb-demo-session') ?? '{}');
    expect(session).toMatchObject({ authenticated: true, customerId: 'cust-li', customerName: '李四' });
  });
});

describe('首页聚合', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearMockAuthState();
    currentPath = '/';
  });

  it('按当前客户显示身份与总资产（张三 26,000 / 李四含理财持仓）', async () => {
    const view = renderHome(homeService());
    expect(await screen.findByText(/张三 · Everyday/)).toBeInTheDocument();
    expect(await screen.findByTestId('home-total')).toHaveTextContent('26,000.00');
    expect(screen.getByTestId('home-current')).toHaveTextContent('6,000.00');
    expect(screen.getByTestId('home-savings')).toHaveTextContent('20,000.00');
    view.unmount();

    renderHome(homeService(), { customerId: 'cust-li', customerName: '李四' });
    expect(await screen.findByText(/李四 · Everyday/)).toBeInTheDocument();
    expect(await screen.findByTestId('home-total')).toHaveTextContent('154,560.30');
  });

  it('金额隐私覆盖总资产、活期储蓄、待扣、交易与账单', async () => {
    // 首页不再放隐私开关（与目标一致），开关在理财页；这里直接写入偏好
    window.localStorage.setItem('sb-amount-hidden', '1');
    renderHome(homeService());
    await screen.findByText(/未来 7 天有/);

    expect(screen.getByTestId('home-total')).toHaveTextContent('••••');
    expect(screen.getByTestId('home-current')).toHaveTextContent('••••');
    expect(screen.queryAllByText(/¥\d/).length).toBe(0);
    expect(screen.getAllByText(/••••/).length).toBeGreaterThan(0);
  });

  it('待扣与订阅 service 一致，最近交易与账单一致', async () => {
    renderHome(homeService());
    // 订阅 mock 在 2026-11-01 的 7 天窗口内是 2 笔（视频会员 11-05、云音乐 11-02）
    expect(await screen.findByText('未来 7 天有 2 笔待扣')).toBeInTheDocument();
    // 账单 2026-09 总支出
    expect(await screen.findByText(/¥5,620\.40/)).toBeInTheDocument();
    // 最近交易来自账单 mock 的流水
    expect(await screen.findByText('全家便利店')).toBeInTheDocument();
  });

  it('空交易显示空态与去转账，无待扣显示提示', async () => {
    renderHome(homeService({ emptyRecent: true, emptyUpcoming: true }));
    expect(await screen.findByText('最近没有交易')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去转账' })).toBeInTheDocument();
    expect(await screen.findByText('未来 7 天无待扣。')).toBeInTheDocument();
  });

  it('单模块失败只影响该模块，并可重试', async () => {
    const user = userEvent.setup();
    const service = homeService();
    service.setScenario({ failSummaryOnce: true });
    renderHome(service);

    expect(await screen.findByText('资产摘要加载失败')).toBeInTheDocument();
    // 其他模块仍正常
    expect(await screen.findByText('全家便利店')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByTestId('home-total')).toHaveTextContent('26,000.00');
  });

  it('离线保留已加载数据并提示不是最新', async () => {
    const user = userEvent.setup();
    const service = homeService();
    renderHome(service);
    await screen.findByTestId('home-total');

    await user.click(screen.getByRole('button', { name: '离线' }));
    expect(await screen.findByText(/当前离线，已加载内容会保留/)).toBeInTheDocument();
    expect(screen.getByTestId('home-total')).toHaveTextContent('26,000.00');
  });

  it('各入口进入正确路由', async () => {
    const user = userEvent.setup();
    renderHome(homeService());
    await screen.findByTestId('home-total');

    await user.click(screen.getByRole('button', { name: /理财收益/ }));
    await waitFor(() => expect(currentPath).toBe('/wealth'));
  });

  it('待扣与账单入口指向对应业务页', async () => {
    const user = userEvent.setup();
    renderHome(homeService());
    const notice = await screen.findByRole('button', { name: /查看未来 7 天待扣款/ });
    await user.click(notice);
    await waitFor(() => expect(currentPath).toBe('/subscriptions'));
  });
});

/* ------------------------------------------------------------------ *
 * 首页与个人中心资产口径一致性
 * ------------------------------------------------------------------ */

function assetStack() {
  const auth = new MockAuthService({}, 0);
  const transfer = new MockTransferService({}, 0);
  const wealth = new MockWealthService({}, 0);
  const subscriptions = new MockSubscriptionsService({}, 0, () => Date.parse('2026-11-01T12:00:00Z'));
  const bills = new MockBillsService({}, 0);
  const home = new MockHomeService({ auth, transfer, wealth, subscriptions, bills }, {}, 0);
  const profile = new MockProfileService(wealth, {}, 0, auth, home);
  return { home, profile, wealth };
}

describe('资产口径一致性', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearMockAuthState();
  });

  it('张三：首页与个人中心总资产一致，不计理财持仓', async () => {
    const { home, profile } = assetStack();
    const summary = await home.getSummary('cust-zhang');
    const overview = await profile.getOverview('cust-zhang');
    expect(summary.totalCents).toBe(2_600_000);
    expect(summary.holdingProductCount).toBe(0);
    expect(overview.stats.totalAssetCents).toBe(summary.totalCents);
    expect(overview.stats.holdingProductCount).toBe(0);
    expect(overview.user?.name).toBe('张三');
    expect(overview.riskAssessment).toBe('missing');
  });

  it('李四：首页与个人中心总资产及持仓数一致', async () => {
    const { home, profile } = assetStack();
    const summary = await home.getSummary('cust-li');
    const overview = await profile.getOverview('cust-li');
    expect(summary.totalCents).toBe(15_456_030);
    expect(summary.holdingProductCount).toBe(5);
    expect(overview.stats.totalAssetCents).toBe(summary.totalCents);
    expect(overview.stats.holdingProductCount).toBe(5);
    expect(overview.user?.riskBand).toBe('R3');
  });

  it('孙八：只计算其账户目录允许的资产，不含理财持仓', async () => {
    const { home, profile } = assetStack();
    const summary = await home.getSummary('cust-sun');
    const overview = await profile.getOverview('cust-sun');
    expect(summary.savingsCents).toBe(2_000_000);
    expect(summary.currentCents).toBe(0);
    expect(summary.totalCents).toBe(2_000_000);
    expect(overview.stats.holdingProductCount).toBe(0);
    expect(overview.stats.totalAssetCents).toBe(2_000_000);
  });

  it('未知 customerId 不回退到其他客户', async () => {
    const { home, profile } = assetStack();
    await expect(home.getSummary('cust-unknown')).rejects.toThrow(/客户不存在/);
    await expect(profile.getOverview('cust-unknown')).rejects.toThrow(/客户不存在/);
  });

  it('仅注入 wealth 的 ProfileService 仍独立可用（不依赖 home）', async () => {
    const wealth = new MockWealthService({}, 0);
    const profile = new MockProfileService(wealth, {}, 0);
    const overview = await profile.getOverview();
    expect(overview.stats.totalAssetCents).toBe(13_716_030);
    expect(overview.stats.holdingProductCount).toBe(5);
    expect(overview.user?.name).toBe('李四');
  });
});

/* ------------------------------------------------------------------ *
 * 客户切换：清空旧数据 + 旧响应失效
 * ------------------------------------------------------------------ */

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

class DeferredHome implements HomeService {
  private summaries = new Map<string, Deferred<HomeSummary>>();
  private recents = new Map<string, Deferred<HomeTransaction[]>>();
  active = '';

  private summaryFor(id: string) {
    if (!this.summaries.has(id)) this.summaries.set(id, deferred<HomeSummary>());
    return this.summaries.get(id)!;
  }

  resolveSummary(id: string, value: HomeSummary) {
    this.summaryFor(id).resolve(value);
  }

  resolveRecent(id: string, value: HomeTransaction[]) {
    this.recentFor(id).resolve(value);
  }

  private recentFor(id: string) {
    if (!this.recents.has(id)) this.recents.set(id, deferred<HomeTransaction[]>());
    return this.recents.get(id)!;
  }

  getSummary(customerId: string): Promise<HomeSummary> {
    this.active = customerId;
    return this.summaryFor(customerId).promise;
  }

  getUpcoming(): Promise<HomeUpcoming> {
    return Promise.resolve({ count: 2, totalCents: 4500, sourceSummary: '云音乐、视频会员' });
  }

  getRecentTransactions(): Promise<HomeTransaction[]> {
    return this.recentFor(this.active).promise;
  }

  getMonthlyBill(): Promise<HomeBill> {
    return Promise.resolve({ month: '2026-09', expenseCents: 562040 });
  }
}

function summaryFor(id: 'cust-zhang' | 'cust-li', name: string, total: number): HomeSummary {
  return {
    customer: { customerId: id, name, avatarText: name.slice(0, 1), summary: '' },
    currentCents: 6000_00,
    savingsCents: 20000_00,
    totalCents: total,
    holdingProductCount: 0,
    dataNote: '共享演示数据集',
  };
}

function HomeWithSwitcher() {
  const { login } = useSession();
  return (
    <>
      <button type="button" onClick={() => login({ customerId: 'cust-li', customerName: '李四' })}>
        切到李四
      </button>
      <HomePage />
    </>
  );
}

function renderSwitchable(service: HomeService) {
  window.localStorage.setItem(
    'sb-demo-session',
    JSON.stringify({ authenticated: true, customerId: 'cust-zhang', customerName: '张三' }),
  );
  return render(
    <SessionProvider initialStatus="authenticated">
      <MemoryRouter initialEntries={['/']}>
        <HomeServiceProvider service={service}>
          <HomeWithSwitcher />
        </HomeServiceProvider>
      </MemoryRouter>
    </SessionProvider>,
  );
}

describe('首页客户切换', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('切换到新客户时立即清除上一客户事实数据，新客户加载中显示加载态', async () => {
    const user = userEvent.setup();
    const service = new DeferredHome();
    renderSwitchable(service);

    act(() => {
      service.resolveSummary('cust-zhang', summaryFor('cust-zhang', '张三', 2_600_000));
      service.resolveRecent('cust-zhang', [
        { id: 'z1', name: '张三流水', time: '2026-09-27', category: '购物', amountCents: -1234, direction: 'out' },
      ]);
    });
    expect(await screen.findByTestId('home-total')).toHaveTextContent('26,000.00');
    expect(screen.getByText('张三流水')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '切到李四' }));

    // 李四数据未返回前：不得出现张三的任何事实数据
    await waitFor(() => expect(screen.queryByText('¥26,000.00')).not.toBeInTheDocument());
    expect(screen.queryByText('张三流水')).not.toBeInTheDocument();
    expect(screen.queryByText(/张三 · Everyday/)).not.toBeInTheDocument();
    expect(screen.getByText(/正在加载资产/)).toBeInTheDocument();
    // 顶部身份使用当前会话客户，而不是旧 summary
    expect(screen.getByText(/李四 · Everyday/)).toBeInTheDocument();

    act(() => {
      service.resolveSummary('cust-li', summaryFor('cust-li', '李四', 15_456_030));
      service.resolveRecent('cust-li', [
        { id: 'l1', name: '李四流水', time: '2026-09-26', category: '餐饮', amountCents: -2000, direction: 'out' },
      ]);
    });
    expect(await screen.findByTestId('home-total')).toHaveTextContent('154,560.30');
    expect(screen.getByText('李四流水')).toBeInTheDocument();
    expect(screen.queryByText('张三流水')).not.toBeInTheDocument();
  });

  it('上一客户的慢响应最后返回也不能覆盖新客户页面', async () => {
    const user = userEvent.setup();
    const service = new DeferredHome();
    renderSwitchable(service);

    await user.click(screen.getByRole('button', { name: '切到李四' }));
    expect(screen.getByText(/李四 · Everyday/)).toBeInTheDocument();

    act(() => {
      service.resolveSummary('cust-zhang', summaryFor('cust-zhang', '张三', 2_600_000));
    });
    await waitFor(() => expect(screen.queryByText('¥26,000.00')).not.toBeInTheDocument());
    expect(screen.queryByText(/张三 · Everyday/)).not.toBeInTheDocument();

    act(() => {
      service.resolveSummary('cust-li', summaryFor('cust-li', '李四', 15_456_030));
    });
    expect(await screen.findByTestId('home-total')).toHaveTextContent('154,560.30');
  });

  it('同一客户离线重试仍保留已加载数据', async () => {
    const user = userEvent.setup();
    const service = homeService();
    renderHome(service);
    await screen.findByTestId('home-total');

    await user.click(screen.getByRole('button', { name: '离线' }));
    expect(await screen.findByText(/当前离线，已加载内容会保留/)).toBeInTheDocument();
    expect(screen.getByTestId('home-total')).toHaveTextContent('26,000.00');
    expect(screen.queryByText(/正在加载资产/)).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ *
 * 金额隐藏后的可访问名称
 * ------------------------------------------------------------------ */

describe('金额隐藏与可访问名称', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('隐藏后待扣与账单按钮的可访问名称不含原始金额', async () => {
    window.localStorage.setItem('sb-amount-hidden', '1');
    const hiddenView = renderHome(homeService());

    await screen.findByRole('button', { name: /查看未来 7 天待扣款/ });
    // 隐藏状态：任何按钮的可访问名称都不得含金额
    expect(screen.queryByRole('button', { name: /¥\d/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看未来 7 天待扣款，共 2 笔，金额已隐藏' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /本月支出金额已隐藏/ })).toBeInTheDocument();
    hiddenView.unmount();

    // 显示状态：可访问名称重新带上真实金额
    window.localStorage.removeItem('sb-amount-hidden');
    renderHome(homeService());
    const upcomingBtn = await screen.findByRole('button', { name: /查看未来 7 天待扣款/ });
    expect(upcomingBtn.getAttribute('aria-label')).toMatch(/合计 ¥[\d,]+\.\d{2}/);
    const billBtn = await screen.findByRole('button', { name: /查看 2026-09 账单/ });
    expect(billBtn.getAttribute('aria-label')).toContain('¥5,620.40');
  });
});