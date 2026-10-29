import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { BillsPage } from '../pages/Bills';
import { BillsServiceProvider, BillsOfflineError } from '../services/bills/BillsService';
import type { BillsService } from '../services/bills/BillsService';
import { MockBillsService } from '../services/bills/MockBillsService';

let currentPath = '/bills';
function LocationProbe() {
  currentPath = useLocation().pathname;
  return null;
}

function renderBills(service: BillsService, initialEntry = '/bills') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <BillsServiceProvider service={service}>
        <BillsPage />
        <LocationProbe />
      </BillsServiceProvider>
    </MemoryRouter>,
  );
}

function mockService() {
  return new MockBillsService({}, 0);
}

describe('bills page', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    currentPath = '/bills';
  });

  it('initial loading shows skeleton, not 0 yuan or empty state', () => {
    const s = new MockBillsService({ slowMonth: '2026-09' }, 0);
    renderBills(s);
    expect(screen.getByText('正在加载账单…')).toBeInTheDocument();
    expect(screen.queryByText('本月无交易')).not.toBeInTheDocument();
  });

  it('normal month shows summary, categories, ledger and anomalies', async () => {
    renderBills(mockService());
    expect(await screen.findByText('总支出 · 2026-09')).toBeInTheDocument();
    expect(await screen.findByText('异常交易')).toBeInTheDocument();
    expect(screen.getByText('3 笔')).toBeInTheDocument();
    expect(screen.getAllByText('夜色酒吧').length).toBeGreaterThan(0);
    expect(screen.getByText('流水')).toBeInTheDocument();
  });

  it('empty month shows empty state with month-switch exit', async () => {
    const user = userEvent.setup();
    renderBills(mockService());
    await screen.findByText('总支出 · 2026-09');

    await user.selectOptions(screen.getByLabelText('月份'), '2026-07');
    expect(await screen.findByText('2026-07 暂无交易')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /切换到有流水的月份/ })).toBeInTheDocument();
  });

  it('no-anomaly month does not render large anomaly placeholder', async () => {
    const user = userEvent.setup();
    renderBills(mockService());
    await screen.findByText('总支出 · 2026-09');

    await user.selectOptions(screen.getByLabelText('月份'), '2026-08');
    expect(await screen.findByText(/暂未命中异常规则/)).toBeInTheDocument();
  });

  it('category filter updates count and filters ledger', async () => {
    const user = userEvent.setup();
    renderBills(mockService());
    await screen.findByText('总支出 · 2026-09');

    await user.click(screen.getByRole('button', { name: '购物' }));
    expect(screen.getByText(/显示 \d+ 笔 \/ 共 10 笔/)).toBeInTheDocument();
  });

  it('filter with no result can be cleared', async () => {
    const user = userEvent.setup();
    // 假服务：分类含「医疗」，但没有任何「医疗」流水 → 筛选无结果
    const fake: BillsService = {
      getAvailableMonths: async () => ['2026-09'],
      getMonthlyBill: async () => ({
        month: '2026-09',
        totalExpenseCents: 3250,
        totalIncomeCents: 0,
        categories: [
          { category: '餐饮', amountCents: 2500, share: 0.77 },
          { category: '医疗', amountCents: 0, share: 0 },
        ],
        dailyTrend: [],
        transactions: [
          {
            id: 'x1',
            date: '2026-09-27',
            merchant: '全家便利店',
            memo: '',
            category: '餐饮',
            amountCents: -3250,
            status: 'posted',
            kind: 'expense',
          },
        ],
        anomalies: [],
      }),
    };
    renderBills(fake);
    await user.click(await screen.findByRole('button', { name: '医疗' }));

    expect(screen.getByText('没有符合筛选的流水')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '清除筛选' }));
    expect(screen.getByText(/共 1 笔/)).toBeInTheDocument();
  });

  it('initial load failure can retry', async () => {
    const user = userEvent.setup();
    renderBills(new MockBillsService({ initialFailOnce: true }, 0));
    expect(await screen.findByText('账单加载失败')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('总支出 · 2026-09')).toBeInTheDocument();
  });

  it('new month failure keeps old month content, not relabeled', async () => {
    const user = userEvent.setup();
    renderBills(new MockBillsService({ failMonth: '2026-08' }, 0));
    await screen.findByText('总支出 · 2026-09');

    await user.selectOptions(screen.getByLabelText('月份'), '2026-08');
    expect(await screen.findByText('2026-08 加载失败，请重试')).toBeInTheDocument();
    // 旧内容仍展示，不把旧月标成新月
    expect(screen.getByText('总支出 · 2026-09')).toBeInTheDocument();
  });

  it('stale month response is discarded on rapid switch', async () => {
    const user = userEvent.setup();
    // 09 慢（初始加载慢），切到 08（快）
    renderBills(new MockBillsService({ slowMonth: '2026-09' }, 0));
    // 初始 09 已在途；立即切到 08
    await waitFor(() => expect(screen.getByLabelText('月份')).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('月份'), '2026-08');
    expect(await screen.findByText('总支出 · 2026-08')).toBeInTheDocument();

    // 等待旧 09 慢响应返回（不覆盖 08）
    await new Promise((r) => setTimeout(r, 1300));
    expect(screen.getByText('总支出 · 2026-08')).toBeInTheDocument();
    expect(screen.queryByText('总支出 · 2026-09')).not.toBeInTheDocument();
  });

  it('anomaly evidence expands without changing URL and syncs aria-expanded', async () => {
    const user = userEvent.setup();
    renderBills(mockService());
    await screen.findByText('异常交易');

    const list = screen.getByTestId('anomaly-list');
    const head = within(list).getByRole('button', { name: /夜色酒吧/ });
    expect(head).toHaveAttribute('aria-expanded', 'false');
    await user.click(head);
    expect(head).toHaveAttribute('aria-expanded', 'true');
    expect(within(list).getByText(/凌晨 2–6 点大额/)).toBeInTheDocument();
    expect(currentPath).toBe('/bills');
  });

  it('HTML-looking merchant/memo render as plain text, no executable content', async () => {
    renderBills(mockService());
    await screen.findByText('总支出 · 2026-09');

    expect(screen.getAllByText('<script>alert(1)</script>').length).toBeGreaterThan(0);
    expect(document.getElementsByTagName('script').length).toBe(0);
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('script[src]')).toBeNull();
  });

  it('pending transactions show 待入账 and are not counted as deducted text', async () => {
    renderBills(mockService());
    await screen.findByText('总支出 · 2026-09');
    expect(screen.getAllByText(/待入账/).length).toBeGreaterThan(0);
  });

  it('amount hide masks summary, chart, ledger and anomaly amounts', async () => {
    const user = userEvent.setup();
    renderBills(mockService());
    await screen.findByText('总支出 · 2026-09');
    expect(screen.getByText('¥5,620.40')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '隐藏金额' }));
    expect(screen.queryByText('¥5,620.40')).not.toBeInTheDocument();
    expect(screen.getAllByText('••••').length).toBeGreaterThan(0);
    expect(screen.queryByText(/¥\d/)).not.toBeInTheDocument();
  });

  it('offline during month switch keeps old content and never mislabels it', async () => {
    const user = userEvent.setup();
    const s = new MockBillsService({}, 0);
    renderBills(s);
    await screen.findByText('总支出 · 2026-09');

    s.setScenario({ offline: true });
    await user.selectOptions(screen.getByLabelText('月份'), '2026-08');

    expect(await screen.findByText(/当前离线/)).toBeInTheDocument();
    expect(screen.getByText('总支出 · 2026-09')).toBeInTheDocument(); // 旧内容保留
    expect(screen.queryByText('总支出 · 2026-08')).not.toBeInTheDocument(); // 不把旧月误标为新月
    expect(screen.queryByText('2026-08 暂无交易')).not.toBeInTheDocument(); // 离线 ≠ 空月
  });

  it('month and category restore after navigating away and back', async () => {
    const user = userEvent.setup();
    const { unmount } = renderBills(mockService());
    await screen.findByText('总支出 · 2026-09');

    await user.selectOptions(screen.getByLabelText('月份'), '2026-08');
    await screen.findByText('总支出 · 2026-08');
    await user.click(screen.getByRole('button', { name: '购物' }));

    unmount();

    // 再次进入（重新挂载）应从 viewState 恢复月份与筛选
    renderBills(mockService());
    await screen.findByText('总支出 · 2026-08');
    expect(screen.getByLabelText('月份')).toHaveValue('2026-08');
    expect(screen.getByRole('button', { name: '购物' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('percentage is formatted as percent, not currency', async () => {
    const user = userEvent.setup();
    const fake: BillsService = {
      getAvailableMonths: async () => ['2026-09'],
      getMonthlyBill: async () => ({
        month: '2026-09',
        totalExpenseCents: 10000,
        totalIncomeCents: 0,
        categories: [{ category: '餐饮', amountCents: 3400, share: 0.34 }],
        dailyTrend: [],
        transactions: [
          {
            id: 'x',
            date: '2026-09-01',
            merchant: '测试商户',
            memo: '',
            category: '餐饮',
            amountCents: -3400,
            status: 'posted',
            kind: 'expense',
          },
        ],
        anomalies: [],
      }),
    };
    renderBills(fake);
    await screen.findByText(/34%/);
    expect(screen.queryByText(/¥0\.34%/)).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: /餐饮 34%/ })).toBeInTheDocument();

    // 隐藏金额后百分比仍正常显示
    await user.click(screen.getByRole('button', { name: '隐藏金额' }));
    expect(screen.getByText(/34%/)).toBeInTheDocument();
  });

  it('months list failing first then retry completes full initialization', async () => {
    const user = userEvent.setup();
    let failMonths = true;
    const fake: BillsService = {
      getAvailableMonths: async () => {
        if (failMonths) throw new Error('months fail');
        return ['2026-09', '2026-08'];
      },
      getMonthlyBill: async (m) => (m === '2026-09' ? mockService().getMonthlyBill(m) : mockService().getMonthlyBill(m)),
    };
    renderBills(fake);
    expect(await screen.findByText('账单月份加载失败，请重试')).toBeInTheDocument();
    expect(screen.queryByText(/暂无交易/)).not.toBeInTheDocument();

    failMonths = false;
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('总支出 · 2026-09')).toBeInTheDocument();
    expect(screen.getByLabelText('月份')).toBeInTheDocument();
  });

  it('initial offline then recover online and retry without refresh', async () => {
    const user = userEvent.setup();
    let offline = true;
    const fake: BillsService = {
      getAvailableMonths: async () => {
        if (offline) throw new BillsOfflineError();
        return ['2026-09', '2026-08'];
      },
      getMonthlyBill: async (m) => {
        if (offline) throw new BillsOfflineError();
        return mockService().getMonthlyBill(m);
      },
    };
    renderBills(fake);
    expect(await screen.findByText(/当前离线/)).toBeInTheDocument();

    offline = false;
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('总支出 · 2026-09')).toBeInTheDocument();
  });

  it('invalid persisted month falls back to a valid month and updates view state', async () => {
    window.sessionStorage.setItem('sb-bill-month', JSON.stringify('1999-01'));
    renderBills(mockService());
    expect(await screen.findByText('总支出 · 2026-09')).toBeInTheDocument();
    expect(screen.getByLabelText('月份')).toHaveValue('2026-09');
    expect(window.sessionStorage.getItem('sb-bill-month')).toBe('"2026-09"');
  });
});