import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation, useRoutes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { RouteObject } from 'react-router-dom';
import { routes } from '../app/router';
import { SessionProvider } from '../app/SessionProvider';
import type { SessionStatus } from '../session/session';
import { ThemeProvider } from '../app/ThemeProvider';
import { ToastProvider } from '../components/Toast';
import { TransferServiceProvider } from '../services/transfer/TransferService';
import { BillsServiceProvider } from '../services/bills/BillsService';
import { WealthServiceProvider } from '../services/wealth/WealthService';
import { CardsServiceProvider } from '../services/cards/CardsService';
import { SubscriptionsServiceProvider } from '../services/subscriptions/SubscriptionsService';
import { AssistantServiceProvider } from '../services/assistant/AssistantService';
import { ProfileServiceProvider } from '../services/profile/ProfileService';
import { AuthServiceProvider } from '../services/auth/AuthService';
import { HomeServiceProvider } from '../services/home/HomeService';
import { clearScrollMemory, getScroll, isVisitedPath, saveScroll } from '../app/scrollMemory';
import { savePendingApproval } from '../app/viewState';
import {
  markWelcomeSeenThisDocument,
  resetWelcomeLaunchForTests,
} from '../app/welcomeLaunch';

// 本文件验证路由/会话行为，不验证滚动（由 scrollRestore.test.tsx 负责），
// 这里把真实 scrollTo 替换为 no-op，避免 jsdom 的 not-implemented 噪音。
vi.mock('../app/scroll', () => ({
  applyScroll: () => {},
  readScrollTop: () => 0,
}));

let currentPathname = '/';
function LocationProbe() {
  currentPathname = useLocation().pathname;
  return null;
}

function AppUnderTest() {
  const element = useRoutes(routes satisfies RouteObject[]);
  return (
    <>
      {element}
      <LocationProbe />
    </>
  );
}

function renderRouter(
  initialEntries: string[],
  opts: { initialStatus?: SessionStatus; resolveDelayMs?: number } = {},
) {
  return render(
    <ThemeProvider>
      <SessionProvider initialStatus={opts.initialStatus} resolveDelayMs={opts.resolveDelayMs}>
        <AuthServiceProvider>
          <TransferServiceProvider>
          <BillsServiceProvider>
            <CardsServiceProvider>
              <SubscriptionsServiceProvider>
                <WealthServiceProvider>
                  <AssistantServiceProvider>
                    <HomeServiceProvider>
                      <ProfileServiceProvider>
                        <ToastProvider>
                          <MemoryRouter initialEntries={initialEntries}>
                            <AppUnderTest />
                          </MemoryRouter>
                        </ToastProvider>
                      </ProfileServiceProvider>
                    </HomeServiceProvider>
                  </AssistantServiceProvider>
                </WealthServiceProvider>
              </SubscriptionsServiceProvider>
            </CardsServiceProvider>
          </BillsServiceProvider>
          </TransferServiceProvider>
        </AuthServiceProvider>
      </SessionProvider>
    </ThemeProvider>,
  );
}

const AUTH = { initialStatus: 'authenticated' as SessionStatus };
const ANON = { initialStatus: 'anonymous' as SessionStatus };

function seedSession() {
  window.localStorage.setItem(
    'sb-demo-session',
    JSON.stringify({ authenticated: true, customerId: 'demo', customerName: '演示客户' }),
  );
}

/** 用默认客户张三的演示 PIN（111111）登录；6 位自动提交。 */
async function demoLogin(user: ReturnType<typeof userEvent.setup>) {
  // The PIN keypad renders before the async customer directory. Select a loaded customer first.
  await user.click(await screen.findByRole('button', { name: /张三/ }));
  for (const digit of ['1', '1', '1', '1', '1', '1']) {
    await user.click(screen.getByRole('button', { name: digit }));
  }
}

describe('cold start', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    clearScrollMemory();
    currentPathname = '/';
    resetWelcomeLaunchForTests();
  });

  it('does not flash welcome/login/home while resolving', () => {
    renderRouter(['/'], { initialStatus: 'resolving' });
    expect(screen.getByText('正在准备')).toBeInTheDocument();
    expect(screen.queryByText('WELCOME TO SAFE BANK')).not.toBeInTheDocument();
    expect(screen.queryByText('选择演示客户')).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument();
  });

  it('first launch with no session goes to /welcome', async () => {
    renderRouter(['/']);
    await waitFor(() => expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument());
  });

  it('visited but no session still shows welcome on a fresh document', async () => {
    window.localStorage.setItem('sb-has-visited', '1');
    renderRouter(['/']);
    await waitFor(() => expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument());
  });

  it('valid session shows welcome first, then continue returns home', async () => {
    const user = userEvent.setup();
    seedSession();
    renderRouter(['/']);
    await waitFor(() => expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: '继续使用' }));
    await waitFor(() =>
      expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument(),
    );
    expect(currentPathname).toBe('/');
  });

  it('authenticated deep link is intercepted by welcome once per document', async () => {
    const user = userEvent.setup();
    seedSession();
    renderRouter(['/login']);
    await waitFor(() => expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: '继续使用' }));
    await waitFor(() => expect(currentPathname).toBe('/'));
    expect(screen.queryByText('WELCOME TO SAFE BANK')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /理财/ }));
    expect(currentPathname).toBe('/wealth');
    expect(screen.queryByText('WELCOME TO SAFE BANK')).not.toBeInTheDocument();
  });

  it('anonymous user on a protected secondary page sees welcome, then login', async () => {
    const user = userEvent.setup();
    window.localStorage.setItem('sb-has-visited', '1');
    renderRouter(['/bills'], ANON);
    await waitFor(() => expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: '开始使用' }));
    await waitFor(() => expect(screen.getByText('选择演示客户')).toBeInTheDocument());
    expect(currentPathname).toBe('/login');
  });
});

describe('navigation', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    clearScrollMemory();
    currentPathname = '/';
    markWelcomeSeenThisDocument();
  });

  it('welcome action writes visited flag and goes to /login', async () => {
    const user = userEvent.setup();
    resetWelcomeLaunchForTests();
    renderRouter(['/']);
    await waitFor(() => expect(screen.getByText('WELCOME TO SAFE BANK')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: '开始使用' }));

    expect(window.localStorage.getItem('sb-has-visited')).toBe('1');
    expect(screen.getByText('选择演示客户')).toBeInTheDocument();
    expect(currentPathname).toBe('/login');
  });

  it('demo login lands on /', async () => {
    const user = userEvent.setup();
    renderRouter(['/login'], ANON);
    await waitFor(() => expect(screen.getByText('选择演示客户')).toBeInTheDocument());
    await demoLogin(user);

    await waitFor(
      () => expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument(),
      { timeout: 8000 },
    );
    expect(currentPathname).toBe('/');
    expect(screen.queryByText('选择演示客户')).not.toBeInTheDocument();
  });

  it('primary tabs navigate and set aria-current', async () => {
    const user = userEvent.setup();
    renderRouter(['/'], AUTH);
    const navRegion = screen.getByRole('navigation', { name: '主导航' });
    expect(within(navRegion).getByRole('button', { name: '首页' })).toHaveAttribute(
      'aria-current',
      'page',
    );

    await user.click(within(navRegion).getByRole('button', { name: '小安' }));
    expect(currentPathname).toBe('/assistant');
    expect(within(navRegion).getByRole('button', { name: '小安' })).toHaveAttribute(
      'aria-current',
      'page',
    );

    await user.click(within(navRegion).getByRole('button', { name: '个人' }));
    expect(currentPathname).toBe('/profile');
    expect(within(navRegion).getByRole('button', { name: '个人' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('secondary pages hide BottomNav and show a back button', () => {
    renderRouter(['/bills'], AUTH);
    expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument();
  });

  it('secondary page back returns to the real source page', async () => {
    const user = userEvent.setup();
    renderRouter(['/'], AUTH);
    await user.click(screen.getByRole('button', { name: /理财/ }));
    expect(currentPathname).toBe('/wealth');

    await user.click(screen.getByRole('button', { name: '全部持仓' }));
    expect(currentPathname).toBe('/wealth/holdings');

    await user.click(screen.getByRole('button', { name: '返回' }));
    expect(currentPathname).toBe('/wealth');
  });

  it('deep link without source uses the fallback back target', async () => {
    const user = userEvent.setup();
    renderRouter(['/bills'], AUTH);
    await user.click(screen.getByRole('button', { name: '返回' }));
    expect(currentPathname).toBe('/');
  });

  it('/wealth is its own route', () => {
    renderRouter(['/wealth'], AUTH);
    expect(screen.getByRole('tablist', { name: '理财视图' })).toBeInTheDocument();
    expect(currentPathname).toBe('/wealth');
  });

  it('/wealth/holdings is a distinct route', () => {
    renderRouter(['/wealth/holdings'], AUTH);
    expect(screen.getByRole('tablist', { name: '全部持仓视图' })).toBeInTheDocument();
    expect(currentPathname).toBe('/wealth/holdings');
  });

  it('CLARIFICATION does not create a new URL', async () => {
    const user = userEvent.setup();
    renderRouter(['/assistant'], AUTH);
    await user.type(screen.getByLabelText('告诉小安你的需求'), '给王武转 3000');
    await user.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('需要澄清')).toBeInTheDocument();
    expect(currentPathname).toBe('/assistant');
    expect(screen.queryByRole('navigation', { name: '主导航' })).toBeInTheDocument();
  });

  it('transfer page renders under auth at /transfer', () => {
    renderRouter(['/transfer'], AUTH);
    expect(currentPathname).toBe('/transfer');
    expect(screen.getByRole('button', { name: /预演这笔转账/ })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument();
  });

  it('unknown route renders 404 with a safe return entry', async () => {
    const user = userEvent.setup();
    renderRouter(['/does-not-exist'], AUTH);
    expect(screen.getByText('页面不存在')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '返回首页' }));
    expect(currentPathname).toBe('/');
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
  });

  it('logout clears the session but keeps theme and visited flag', async () => {
    const user = userEvent.setup();
    window.localStorage.setItem('sb-theme', 'dark');
    window.localStorage.setItem('sb-has-visited', '1');
    seedSession();

    renderRouter(['/profile'], AUTH);
    await user.click(screen.getByRole('button', { name: '退出登录' }));

    await waitFor(() => expect(screen.getByText('选择演示客户')).toBeInTheDocument());
    expect(currentPathname).toBe('/login');
    expect(window.localStorage.getItem('sb-demo-session')).toBeNull();
    expect(window.localStorage.getItem('sb-theme')).toBe('dark');
    expect(window.localStorage.getItem('sb-has-visited')).toBe('1');
  });
});

describe('view state', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    clearScrollMemory();
    currentPathname = '/';
    markWelcomeSeenThisDocument();
  });

  it('preserves the wealth product search term while switching segments', async () => {
    const user = userEvent.setup();
    renderRouter(['/wealth/holdings'], AUTH);

    await user.click(screen.getByRole('tab', { name: '全部产品' }));
    await user.type(screen.getByLabelText('搜索产品或风险等级'), '稳健');
    expect(screen.getByLabelText('搜索产品或风险等级')).toHaveValue('稳健');
    expect(window.sessionStorage.getItem('sb-wealth-search')).toBe('"稳健"');

    // 切换分段不丢失搜索词
    await user.click(screen.getByRole('tab', { name: '比较' }));
    await user.click(screen.getByRole('tab', { name: '全部产品' }));
    expect(screen.getByLabelText('搜索产品或风险等级')).toHaveValue('稳健');
  });

  it('preserves the wealth segment after returning', async () => {
    const user = userEvent.setup();
    renderRouter(['/wealth'], AUTH);

    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(screen.getByRole('button', { name: '全部持仓' }));
    expect(currentPathname).toBe('/wealth/holdings');

    await user.click(screen.getByRole('button', { name: '返回' }));
    expect(currentPathname).toBe('/wealth');
    expect(screen.getByRole('tab', { name: '发现' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('logout cleanup', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    clearScrollMemory();
    currentPathname = '/';
    markWelcomeSeenThisDocument();
  });

  it('clears session, business view state and scroll memory while keeping preferences', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('sb-wealth-tab', JSON.stringify('discover'));
    window.sessionStorage.setItem('sb-wealth-search', JSON.stringify('稳健'));
    window.sessionStorage.setItem('sb-wealth-holdings-tab', JSON.stringify('compare'));
    window.sessionStorage.setItem('sb-wealth-sections', JSON.stringify(['products']));
    window.sessionStorage.setItem('sb-wealth-compare', JSON.stringify(['p1']));
    window.sessionStorage.setItem('sb-wealth-pending-approval', JSON.stringify('mock-wealth-apv-1'));
    window.sessionStorage.setItem('mock-wealth-records', JSON.stringify([{ id: 'mock-wealth-apv-1' }]));
    window.sessionStorage.setItem('mock-wealth-state', JSON.stringify([]));
    window.sessionStorage.setItem('sb-assistant-view', JSON.stringify({ phase: 'READY', messages: [] }));
    window.sessionStorage.setItem('sb-profile-sections', JSON.stringify(['audit']));
    savePendingApproval('mock-apv-1');
    window.sessionStorage.setItem('sb-bill-month', JSON.stringify('2026-09'));
    window.sessionStorage.setItem('sb-bill-category', JSON.stringify('购物'));
    window.localStorage.setItem('sb-theme', 'dark');
    window.localStorage.setItem('sb-has-visited', '1');
    window.localStorage.setItem('sb-amount-hidden', '1'); // 金额隐私（代表性偏好键）
    seedSession();
    saveScroll('/wealth', 777); // 非当前路径，用于验证滚动记忆被清除

    renderRouter(['/profile'], AUTH);
    await user.click(screen.getByRole('button', { name: '退出登录' }));

    await waitFor(() => expect(screen.getByText('选择演示客户')).toBeInTheDocument());
    expect(currentPathname).toBe('/login');

    // 已清除
    expect(window.localStorage.getItem('sb-demo-session')).toBeNull();
    expect(window.sessionStorage.getItem('sb-wealth-tab')).toBeNull();
    expect(window.sessionStorage.getItem('sb-wealth-search')).toBeNull();
    expect(window.sessionStorage.getItem('sb-pending-approval')).toBeNull();
    expect(window.sessionStorage.getItem('sb-wealth-holdings-tab')).toBeNull();
    expect(window.sessionStorage.getItem('sb-wealth-sections')).toBeNull();
    expect(window.sessionStorage.getItem('sb-wealth-compare')).toBeNull();
    expect(window.sessionStorage.getItem('sb-wealth-pending-approval')).toBeNull();
    expect(window.sessionStorage.getItem('mock-wealth-records')).toBeNull();
    expect(window.sessionStorage.getItem('mock-wealth-state')).toBeNull();
    expect(window.sessionStorage.getItem('sb-assistant-view')).toBeNull();
    expect(window.sessionStorage.getItem('sb-profile-sections')).toBeNull();
    expect(window.sessionStorage.getItem('sb-bill-month')).toBeNull();
    expect(window.sessionStorage.getItem('sb-bill-category')).toBeNull();
    expect(getScroll('/wealth')).toBe(0);
    expect(isVisitedPath('/wealth')).toBe(false);

    // 已保留
    expect(window.localStorage.getItem('sb-theme')).toBe('dark');
    expect(window.localStorage.getItem('sb-has-visited')).toBe('1');
    expect(window.localStorage.getItem('sb-amount-hidden')).toBe('1');
  });

  it('after re-login wealth defaults to holdings with empty search', async () => {
    const user = userEvent.setup();
    renderRouter(['/login'], ANON);

    await waitFor(() => expect(screen.getByText('选择演示客户')).toBeInTheDocument());
    await demoLogin(user);
    await waitFor(
      () => expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument(),
      { timeout: 8000 },
    );

    await user.click(screen.getByRole('button', { name: /理财/ }));
    expect(currentPathname).toBe('/wealth');
    expect(screen.getByRole('tab', { name: /^持仓/ })).toHaveAttribute('aria-selected', 'true');
    expect(window.sessionStorage.getItem('sb-wealth-search')).toBeNull();
  });
});