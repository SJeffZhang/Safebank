import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { WealthPage } from '../pages/Wealth';
import { WealthHoldingsPage } from '../pages/WealthHoldings';
import { WealthServiceProvider } from '../services/wealth/WealthService';
import type { WealthService } from '../services/wealth/WealthService';
import { MockWealthService } from '../services/wealth/MockWealthService';
import type { WealthPlanView, WealthPreviewResult } from '../services/wealth/types';
import { GatewayOfflineError } from '../services/approval/shared';

function renderWealth(service: WealthService) {
  return render(
    <MemoryRouter initialEntries={['/wealth']}>
      <WealthServiceProvider service={service}>
        <WealthPage />
      </WealthServiceProvider>
    </MemoryRouter>,
  );
}

function renderHoldings(service: WealthService) {
  return render(
    <MemoryRouter initialEntries={['/wealth/holdings']}>
      <WealthServiceProvider service={service}>
        <WealthHoldingsPage />
      </WealthServiceProvider>
    </MemoryRouter>,
  );
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

const idToAmount = new Map<string, number>();
const idToPlan = new Map<string, { name?: string; productId?: string; operation?: 'purchase' | 'redemption' }>();

function planFixture(id: string): WealthPlanView {
  const meta = idToPlan.get(id) ?? {};
  const amountCents = idToAmount.get(id) ?? 0;
  return {
    approvalId: id,
    riskLevel: 'yellow',
    operation: meta.operation ?? 'purchase',
    productId: meta.productId ?? 'p2',
    productName: meta.name ?? '现金管理',
    riskBand: 'R1',
    amountCents,
    shares: meta.operation === 'redemption' ? 50 : undefined,
    estimatedAmountCents: meta.operation === 'redemption' ? 5_000 : undefined,
    netValueForDisplay: 1,
    sourceText: '可用余额',
    feeText: '申购费率 0%',
    arrivalText: '随时赎回，T+0 到账',
    effectText: '',
    riskNotes: [],
    ackText: '我已阅读并理解',
    ackRequired: false,
    expiresAtMillis: Date.now() + 60_000,
  };
}

/** 读方法复用真实 mock，写预演用可控 deferred。 */
function makeFakeWealth() {
  const real = new MockWealthService({}, 0);
  const defers: Array<ReturnType<typeof defer<WealthPreviewResult>>> = [];
  const redemptionDefers: Array<ReturnType<typeof defer<WealthPreviewResult>>> = [];
  const svc: WealthService = {
    getOverview: (p) => real.getOverview(p),
    getHoldings: () => real.getHoldings(),
    getProducts: () => real.getProducts(),
    preparePurchase: () => {
      const d = defer<WealthPreviewResult>();
      defers.push(d);
      return d.promise;
    },
    prepareRedemption: () => {
      const d = defer<WealthPreviewResult>();
      redemptionDefers.push(d);
      return d.promise;
    },
    getPlanView: async (id) => planFixture(id),
    confirmApproval: async () => ({ status: 'success' }),
    getApprovalStatus: async () => 'unknown',
    rejectApproval: async () => undefined,
  };
  return { svc, defers, redemptionDefers };
}

function svc(scenario = {}, delay = 0) {
  return new MockWealthService(scenario, delay);
}

describe('理财页 /wealth 持仓视图', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('持仓与发现互斥，并可用键盘切换', async () => {
    const user = userEvent.setup();
    renderWealth(svc());
    await screen.findByText('投资总资产 · CNY');

    const holdingsTab = screen.getByRole('tab', { name: /^持仓/ });
    expect(holdingsTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('group', { name: '产品分类' })).not.toBeInTheDocument();

    await user.click(holdingsTab);
    await user.keyboard('{ArrowRight}');
    const discoverTab = screen.getByRole('tab', { name: '发现' });
    expect(discoverTab).toHaveAttribute('aria-selected', 'true');
    // 发现视图出现，持仓卡片隐藏
    expect(await screen.findByRole('group', { name: '产品分类' })).toBeInTheDocument();
    expect(screen.queryByText('投资总资产 · CNY')).not.toBeInTheDocument();
  });

  it('金额隐私覆盖总资产、指标与持仓金额', async () => {
    const user = userEvent.setup();
    renderWealth(svc());
    await screen.findByText('投资总资产 · CNY');
    expect(screen.getByText('¥137,160.30')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '隐藏金额' }));
    expect(screen.queryByText('¥137,160.30')).not.toBeInTheDocument();
    expect(screen.queryAllByText(/¥\d/)).toHaveLength(0);
    expect(screen.getAllByText('••••').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: '显示金额' }));
    expect(screen.getByText('¥137,160.30')).toBeInTheDocument();
  });

  it('空持仓显示空态与去发现动作', async () => {
    const user = userEvent.setup();
    renderWealth(svc({ emptyHoldings: true }));
    expect(await screen.findByText('暂无持仓')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '去发现' }));
    expect(await screen.findByRole('group', { name: '产品分类' })).toBeInTheDocument();
  });

  it('产品加载失败不阻塞已加载成功的持仓事实', async () => {
    const user = userEvent.setup();
    renderWealth(svc({ productsFailOnce: true }));
    // 持仓事实仍在
    expect(await screen.findByText('投资总资产 · CNY')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '发现' }));
    expect(await screen.findByText('产品加载失败')).toBeInTheDocument();
    // 持仓分段未受影响
    await user.click(screen.getByRole('tab', { name: /^持仓/ }));
    expect(screen.getByText('投资总资产 · CNY')).toBeInTheDocument();
  });

  it('空趋势显示空态，不伪造平线', async () => {
    renderWealth(svc({ emptyTrend: true }));
    expect(await screen.findByText('暂无趋势数据')).toBeInTheDocument();
  });

  it('风险测评缺失/过期显示提示', async () => {
    renderWealth(svc({ riskAssessment: 'missing' }));
    expect(await screen.findByText(/还没有完成风险测评/)).toBeInTheDocument();
    screen.getByRole('button', { name: '去测评' });
  });

  it('快捷入口有明确反馈，不静默无响应', async () => {
    const user = userEvent.setup();
    renderWealth(svc());
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('button', { name: '我的订单' }));
    expect(screen.getByText(/我的订单：原型尚未开放/)).toBeInTheDocument();
  });
});

describe('理财申购与红级确认', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('申购金额低于起购时提示且不发起预演', async () => {
    const user = userEvent.setup();
    renderWealth(svc());
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 稳健增利' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    expect(await screen.findByText(/低于起购金额/)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('金额变化使旧预演失效；旧成功与旧失败都不能覆盖新操作', async () => {
    const user = userEvent.setup();
    const fake = makeFakeWealth();
    renderWealth(fake.svc);
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 现金管理' }));

    const input = screen.getByLabelText('申购金额（元）');
    await user.type(input, '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    expect(fake.defers).toHaveLength(1);

    // 修改金额 → 旧预演作废
    await user.clear(input);
    await user.type(input, '2000');
    idToAmount.set('old-1', 100_000);
    await act(async () => {
      fake.defers[0].resolve({ kind: 'ok', approvalId: 'old-1' });
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // 重新预演，只接受最新
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    expect(fake.defers).toHaveLength(2);
    idToAmount.set('new-1', 2_000_00);
    await act(async () => {
      fake.defers[1].resolve({ kind: 'ok', approvalId: 'new-1' });
    });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('¥2,000.00')).toBeInTheDocument();

    // 旧失败同样不能覆盖
    await act(async () => {
      fake.defers[0].reject(new Error('old failure'));
    });
    expect(within(dialog).getByText('¥2,000.00')).toBeInTheDocument();
  });

  it('PlanView 失败页面可见且可重试，不出现空白确认面板', async () => {
    const user = userEvent.setup();
    renderWealth(svc({ planViewFailOnce: true }));
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 现金管理' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));

    expect(await screen.findByText('预演详情加载失败，请重试')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试预演详情' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('红级申购：未勾选或 OTP 不完整不可确认，且无重发入口', async () => {
    const user = userEvent.setup();
    renderWealth(svc());
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 稳健增利' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/红级/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole('button', { name: '验证并确认' });
    expect(confirm).toBeDisabled();

    await user.click(within(dialog).getByRole('checkbox'));
    expect(confirm).toBeDisabled(); // OTP 仍不完整

    await user.type(within(dialog).getByLabelText('短信验证码（6 位）'), '123456');
    expect(confirm).toBeEnabled();
    expect(within(dialog).queryByText(/重新发送/)).not.toBeInTheDocument();

    await user.click(confirm);
    expect(await within(dialog).findByText('操作已完成')).toBeInTheDocument();
  });

  it('过期后「重新预演」使用当前输入并生成新的 approval_id', async () => {
    const user = userEvent.setup();
    const service = svc({ planViewExpired: true });
    renderWealth(service);
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 现金管理' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));

    expect(await screen.findByText('预演已过期，请重新预演')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const recordsBefore = JSON.parse(window.sessionStorage.getItem('mock-wealth-records') ?? '[]').length;

    service.setScenario({ planViewExpired: false });
    await user.click(screen.getByRole('button', { name: '重新预演' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('¥1,000.00')).toBeInTheDocument();
    const recordsAfter = JSON.parse(window.sessionStorage.getItem('mock-wealth-records') ?? '[]').length;
    expect(recordsAfter).toBe(recordsBefore + 1); // 真正新建审批
  });

  it('UNKNOWN 保存 pending，卸载后重新挂载可查询恢复', async () => {
    const user = userEvent.setup();
    const first = svc({ confirm: 'unknown', afterUnknownStatus: 'success' });
    const view = renderWealth(first);
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 现金管理' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
    expect(await within(dialog).findByText('结果待确认')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-wealth-pending-approval')).not.toBeNull();

    view.unmount();
    renderWealth(new MockWealthService()); // 刷新：新实例默认场景
    expect(await screen.findByText('有一项待确认的理财操作')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-wealth-pending-approval')).toBeNull();
  });

  it('查询离线保留待确认入口，恢复后可查询成功', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('sb-wealth-pending-approval', JSON.stringify('apv-9'));
    let offline = true;
    const real = new MockWealthService({}, 0);
    const svc2: WealthService = {
      getOverview: (p) => real.getOverview(p),
      getHoldings: () => real.getHoldings(),
      getProducts: () => real.getProducts(),
      preparePurchase: () => real.preparePurchase('p2', 100_000),
      prepareRedemption: () => real.prepareRedemption('h1', 1),
      getPlanView: (id) => real.getPlanView(id),
      confirmApproval: (id, a) => real.confirmApproval(id, a),
      getApprovalStatus: async () => {
        if (offline) throw new GatewayOfflineError();
        return 'success';
      },
      rejectApproval: (id) => real.rejectApproval(id),
    };
    renderWealth(svc2);
    expect(await screen.findByText('有一项待确认的理财操作')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText(/查询未完成|网络不可用/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看状态' })).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-wealth-pending-approval')).not.toBeNull();

    offline = false;
    await user.click(screen.getByRole('button', { name: '查看状态' }));
    expect(await screen.findByText('操作已完成')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('sb-wealth-pending-approval')).toBeNull();
  });

  it('操作成功但资产刷新失败时分别表达两种事实', async () => {
    const user = userEvent.setup();
    const service = svc({ confirm: 'success' });
    renderWealth(service);
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 现金管理' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    const dialog = await screen.findByRole('dialog');

    service.setScenario({ refreshFailOnce: true });
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
    expect(await within(dialog).findByText('操作已完成')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '完成' }));

    expect(await screen.findByText('操作已完成，但最新资产暂未刷新，可重试')).toBeInTheDocument();
    expect(screen.queryByText('操作未完成')).not.toBeInTheDocument();
  });

  it('预演中改选产品：旧成功与旧失败都不出现，只显示新产品与新金额', async () => {
    const user = userEvent.setup();
    const fake = makeFakeWealth();
    renderWealth(fake.svc);
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));

    // A：稳健增利
    await user.click(await screen.findByRole('button', { name: '申购 稳健增利' }));
    const input = screen.getByLabelText('申购金额（元）');
    await user.type(input, '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    expect(fake.defers).toHaveLength(1);

    // 预演在途时改选 B：现金管理
    await user.click(screen.getByRole('button', { name: '申购 现金管理' }));
    expect(screen.getByLabelText('申购金额（元）')).toHaveValue(''); // 金额已清空

    // 释放 A 的成功响应 → 不得打开面板
    idToPlan.set('A-1', { name: '稳健增利', productId: 'p1' });
    await act(async () => {
      fake.defers[0].resolve({ kind: 'ok', approvalId: 'A-1' });
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // A 的失败响应同样不能覆盖
    await user.click(screen.getByRole('button', { name: '申购 稳健增利' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    await user.click(screen.getByRole('button', { name: '申购 现金管理' }));
    await act(async () => {
      fake.defers[1].reject(new Error('A failed'));
    });
    await waitFor(() => expect(screen.queryByText(/预演失败/)).not.toBeInTheDocument());

    // 为 B 预演：只显示 B 与 B 的最新金额
    await user.type(screen.getByLabelText('申购金额（元）'), '2500');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    idToAmount.set('B-1', 2_500_00);
    idToPlan.set('B-1', { name: '现金管理', productId: 'p2' });
    await act(async () => {
      fake.defers[2].resolve({ kind: 'ok', approvalId: 'B-1' });
    });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('现金管理')).toBeInTheDocument();
    expect(within(dialog).getByText('¥2,500.00')).toBeInTheDocument();
    expect(within(dialog).queryByText('稳健增利')).not.toBeInTheDocument();
  });

  it('过期后改选产品：不再保留旧产品的过期提示与重试入口', async () => {
    const user = userEvent.setup();
    const service = svc({ planViewExpired: true });
    renderWealth(service);
    await screen.findByText('投资总资产 · CNY');
    await user.click(screen.getByRole('tab', { name: '发现' }));
    await user.click(await screen.findByRole('button', { name: '申购 稳健增利' }));
    await user.type(screen.getByLabelText('申购金额（元）'), '1000');
    await user.click(screen.getByRole('button', { name: '预演申购' }));
    expect(await screen.findByText('预演已过期，请重新预演')).toBeInTheDocument();

    const before = JSON.parse(window.sessionStorage.getItem('mock-wealth-records') ?? '[]').length;
    await user.click(screen.getByRole('button', { name: '申购 现金管理' }));

    expect(screen.queryByText('预演已过期，请重新预演')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新预演' })).not.toBeInTheDocument();
    // 没有为旧产品重新发起预演
    const after = JSON.parse(window.sessionStorage.getItem('mock-wealth-records') ?? '[]').length;
    expect(after).toBe(before);
  });
});

describe('全部持仓页 /wealth/holdings', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it('三个分段互斥展示', async () => {
    const user = userEvent.setup();
    renderHoldings(svc());
    expect(await screen.findByRole('button', { name: '赎回 稳健增利' })).toBeInTheDocument();
    expect(screen.queryByLabelText('搜索产品或风险等级')).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: '全部产品' }));
    expect(screen.getByLabelText('搜索产品或风险等级')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '赎回 稳健增利' })).not.toBeInTheDocument();
  });

  it('产品搜索、无结果与清除恢复', async () => {
    const user = userEvent.setup();
    renderHoldings(svc());
    await screen.findByRole('button', { name: '赎回 稳健增利' });
    await user.click(screen.getByRole('tab', { name: '全部产品' }));

    const search = screen.getByLabelText('搜索产品或风险等级');
    await user.type(search, 'R4');
    expect(screen.getByText('黄金积存')).toBeInTheDocument();
    expect(screen.queryByText('稳健增利')).not.toBeInTheDocument();

    await user.clear(search);
    await user.type(search, '不存在的产品');
    expect(await screen.findByText('没有匹配的产品')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '清除搜索 · 查看全部' }));
    expect(screen.getByText('稳健增利')).toBeInTheDocument();
  });

  it('比较最多 4 支，第 5 支不可加入并显示原因', async () => {
    const user = userEvent.setup();
    renderHoldings(svc());
    await screen.findByRole('button', { name: '赎回 稳健增利' });
    await user.click(screen.getByRole('tab', { name: '全部产品' }));

    const picks = ['稳健增利', '现金管理', '优选债基', '中证红利'].map((n) =>
      screen.getByRole('button', { name: `加入比较 ${n}` }),
    );
    for (const b of picks) await user.click(b);
    expect(screen.getByText(/已选 4 \/ 4 支/)).toBeInTheDocument();

    const fifth = screen.getByRole('button', { name: '加入比较 黄金积存' });
    expect(fifth).toBeDisabled();

    // 取消选择后可继续添加
    await user.click(screen.getByRole('button', { name: '取消比较 稳健增利' }));
    expect(screen.getByRole('button', { name: '加入比较 黄金积存' })).toBeEnabled();
    expect(screen.getByText(/已选 3 \/ 4 支/)).toBeInTheDocument();
  });

  it('比较视图按 productId 对齐字段且不排名', async () => {
    const user = userEvent.setup();
    renderHoldings(svc());
    await screen.findByRole('button', { name: '赎回 稳健增利' });
    await user.click(screen.getByRole('tab', { name: '全部产品' }));
    await user.click(screen.getByRole('button', { name: '加入比较 稳健增利' }));
    await user.click(screen.getByRole('button', { name: '加入比较 现金管理' }));
    await user.click(screen.getByRole('tab', { name: '比较' }));

    const table = screen.getByRole('table');
    expect(within(table).getByText('风险等级')).toBeInTheDocument();
    expect(within(table).getByText('起购金额')).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: '稳健增利' })).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: '现金管理' })).toBeInTheDocument();
    expect(screen.queryByText(/最值得买|推荐排名/)).not.toBeInTheDocument();
  });

  it('赎回：份额校验、预演与成功刷新', async () => {
    const user = userEvent.setup();
    renderHoldings(svc({ confirm: 'success' }));
    await user.click(await screen.findByRole('button', { name: '赎回 现金管理' }));

    const input = screen.getByLabelText('赎回份额');
    await user.type(input, '999999');
    await user.click(screen.getByRole('button', { name: '预演赎回' }));
    expect(await screen.findByText('超出可赎回份额')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.clear(input);
    await user.type(input, '100');
    await user.click(screen.getByRole('button', { name: '预演赎回' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('100.00 份')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
    expect(await within(dialog).findByText('操作已完成')).toBeInTheDocument();
  });

  it('红级赎回需要风险勾选与 OTP', async () => {
    const user = userEvent.setup();
    renderHoldings(svc({ confirm: 'success' }));
    await user.click(await screen.findByRole('button', { name: '赎回 稳健增利' }));
    const input = screen.getByLabelText('赎回份额');
    await user.type(input, '100');
    await user.click(screen.getByRole('button', { name: '预演赎回' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/红级/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole('button', { name: '验证并确认' });
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole('checkbox'));
    await user.type(within(dialog).getByLabelText('短信验证码（6 位）'), '123456');
    expect(confirm).toBeEnabled();
    expect(within(dialog).queryByText(/重新发送/)).not.toBeInTheDocument();
  });

  it('不可赎回持仓的赎回按钮禁用并说明原因', async () => {
    renderHoldings(svc());
    const btn = await screen.findByRole('button', { name: '不可赎回 中证红利' });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/封闭期/)).toBeInTheDocument();
  });

  it('产品列表失败可重试；空持仓与产品失败互不误判', async () => {
    const user = userEvent.setup();
    renderHoldings(svc({ productsFailOnce: true }));
    await screen.findByRole('button', { name: '赎回 稳健增利' });
    await user.click(screen.getByRole('tab', { name: '全部产品' }));
    expect(await screen.findByText('产品加载失败')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('稳健增利')).toBeInTheDocument();
  });

  it('空持仓显示空态且不被当作加载失败', async () => {
    renderHoldings(svc({ emptyHoldings: true }));
    expect(await screen.findByText('暂无持仓')).toBeInTheDocument();
    expect(screen.queryByText('持仓加载失败')).not.toBeInTheDocument();
  });

  it('赎回中改选持仓：旧慢响应不覆盖新持仓', async () => {
    const user = userEvent.setup();
    const fake = makeFakeWealth();
    renderHoldings(fake.svc);

    // A 持仓：稳健增利
    await user.click(await screen.findByRole('button', { name: '赎回 稳健增利' }));
    await user.type(screen.getByLabelText('赎回份额'), '100');
    await user.click(screen.getByRole('button', { name: '预演赎回' }));
    expect(fake.redemptionDefers).toHaveLength(1);

    // 预演在途时改选 B 持仓：现金管理
    await user.click(screen.getByRole('button', { name: '赎回 现金管理' }));
    expect(screen.getByLabelText('赎回份额')).toHaveValue('');

    // 释放 A 的慢响应 → 不得打开面板
    idToPlan.set('R-A', { name: '稳健增利', productId: 'p1', operation: 'redemption' });
    await act(async () => {
      fake.redemptionDefers[0].resolve({ kind: 'ok', approvalId: 'R-A' });
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // 为 B 预演 → 只显示 B
    await user.type(screen.getByLabelText('赎回份额'), '50');
    await user.click(screen.getByRole('button', { name: '预演赎回' }));
    idToPlan.set('R-B', { name: '现金管理', productId: 'p2', operation: 'redemption' });
    await act(async () => {
      fake.redemptionDefers[1].resolve({ kind: 'ok', approvalId: 'R-B' });
    });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('现金管理')).toBeInTheDocument();
    expect(within(dialog).queryByText('稳健增利')).not.toBeInTheDocument();
  });
});