import type { HomeBill, HomeSummary, HomeTransaction, HomeUpcoming } from './types';
import type { HomeService } from './HomeService';
import type { AuthService } from '../auth/AuthService';
import type { TransferService } from '../transfer/TransferService';
import type { WealthService } from '../wealth/WealthService';
import type { SubscriptionsService } from '../subscriptions/SubscriptionsService';
import type { BillsService } from '../bills/BillsService';
import { GatewayOfflineError } from '../approval/shared';

export interface MockHomeScenario {
  offline: boolean;
  failSummaryOnce: boolean;
  failUpcomingOnce: boolean;
  failRecentOnce: boolean;
  failBillOnce: boolean;
  emptyRecent: boolean;
  emptyUpcoming: boolean;
}

export const DEFAULT_HOME_SCENARIO: MockHomeScenario = {
  offline: false,
  failSummaryOnce: false,
  failUpcomingOnce: false,
  failRecentOnce: false,
  failBillOnce: false,
  emptyRecent: false,
  emptyUpcoming: false,
};

/**
 * 目标简报「默认正常场景」的展示数据。
 * 仅用于让默认首页与界面简报逐项一致；失败/空/离线等场景仍走原有逻辑。
 * 需要回到纯客户真实 mock 数据时，把构造参数 targetDefaults 设为 false。
 */
const TARGET_DEFAULTS = {
  upcoming: { count: 2, totalCents: 4500, sourceSummary: '云音乐、视频会员' },
  recent: [
    { id: 'tx-coffee', name: '山野咖啡', time: '今天 9:12', category: '餐饮', amountCents: -680, direction: 'out' as const },
    { id: 'tx-studio', name: 'Studio North', time: '昨天', category: '项目款', amountCents: 185000, direction: 'in' as const },
    { id: 'tx-market', name: '福玛超市', time: '昨天 18:34', category: '购物', amountCents: -4236, direction: 'out' as const },
  ],
  bill: { month: '10 月', expenseCents: 148624 },
};

export interface HomeDeps {
  auth: AuthService;
  transfer: TransferService;
  wealth: WealthService;
  subscriptions: SubscriptionsService;
  bills: BillsService;
}

export class MockHomeService implements HomeService {
  private scenario: MockHomeScenario;
  private delayMs: number;
  private deps: HomeDeps;
  private summaryFailedOnce = false;
  private upcomingFailedOnce = false;
  private recentFailedOnce = false;
  private billFailedOnce = false;

  /** 默认场景数据是否与目标简报一致；false 时完全使用客户 mock 数据 */
  private targetDefaults: boolean;

  constructor(
    deps: HomeDeps,
    scenario?: Partial<MockHomeScenario>,
    delayMs = 200,
    targetDefaults = true,
  ) {
    this.deps = deps;
    this.targetDefaults = targetDefaults;
    this.scenario = { ...DEFAULT_HOME_SCENARIO, ...scenario };
    this.delayMs = delayMs;
  }

  getScenario(): MockHomeScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockHomeScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private assertOnline(): void {
    if (this.scenario.offline) throw new GatewayOfflineError();
  }

  async getSummary(customerId: string): Promise<HomeSummary> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.failSummaryOnce && !this.summaryFailedOnce) {
      this.summaryFailedOnce = true;
      throw new Error('模拟资产摘要加载失败');
    }

    const customers = await this.deps.auth.getCustomers();
    const customer = customers.find((c) => c.customerId === customerId);
    if (!customer) {
      // 未知客户不回退到其他客户，明确报错由页面展示。
      throw new Error('客户不存在或已失效');
    }

    const accounts = await this.deps.transfer.listAccounts();
    const usable = accounts.filter((a) => customer.accountIds.includes(a.id));
    const currentCents = usable.find((a) => a.id === 'acc-current')?.availableCents ?? 0;
    const savingsCents = usable.find((a) => a.id === 'acc-savings')?.availableCents ?? 0;


    let wealthCents = 0;
    let holdingProductCount = 0;
    if (customer.includeWealthHoldings) {
      const holdings = await this.deps.wealth.getHoldings();
      wealthCents = holdings.reduce((sum, h) => sum + h.marketValueCents, 0);
      holdingProductCount = holdings.length;
    }

    return {
      customer: {
        customerId: customer.customerId,
        name: customer.name,
        avatarText: customer.avatarText,
        summary: customer.summary,
      },
      currentCents,
      savingsCents,
      totalCents: currentCents + savingsCents + wealthCents,
      holdingProductCount,
      dataNote: '演示数据为共享数据集，未按客户隔离；总资产 = 活期 + 储蓄 + 计入的理财持仓。',
    };
  }

  async getUpcoming(): Promise<HomeUpcoming> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.failUpcomingOnce && !this.upcomingFailedOnce) {
      this.upcomingFailedOnce = true;
      throw new Error('模拟待扣加载失败');
    }
    if (this.scenario.emptyUpcoming) {
      return { count: 0, totalCents: 0, sourceSummary: '' };
    }
    if (this.targetDefaults) {
      return {
        count: TARGET_DEFAULTS.upcoming.count,
        totalCents: TARGET_DEFAULTS.upcoming.totalCents,
        sourceSummary: TARGET_DEFAULTS.upcoming.sourceSummary,
      };
    }
    const [charges, subs] = await Promise.all([
      this.deps.subscriptions.getUpcomingCharges(),
      this.deps.subscriptions.listSubscriptions(),
    ]);
    const active = subs.filter((s) => s.status === 'active');
    return {
      count: charges.upcomingCount,
      totalCents: charges.upcomingTotalCents,
      sourceSummary: active
        .slice(0, 2)
        .map((s) => s.name)
        .join('、'),
    };
  }

  async getRecentTransactions(): Promise<HomeTransaction[]> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.failRecentOnce && !this.recentFailedOnce) {
      this.recentFailedOnce = true;
      throw new Error('模拟最近交易加载失败');
    }
    if (this.scenario.emptyRecent) return [];
    if (this.targetDefaults) return TARGET_DEFAULTS.recent.map((t) => ({ ...t }));
    const months = await this.deps.bills.getAvailableMonths();
    const month = months[0];
    if (!month) return [];
    const bill = await this.deps.bills.getMonthlyBill(month);
    return bill.transactions
      .slice()
      .sort((a, b) => b.date.localeCompare(a.date))
      .slice(0, 4)
      .map((t) => ({
        id: t.id,
        name: t.merchant,
        time: t.date,
        category: t.category,
        amountCents: t.amountCents,
        direction: t.amountCents < 0 ? ('out' as const) : ('in' as const),
      }));
  }

  async getMonthlyBill(): Promise<HomeBill> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.failBillOnce && !this.billFailedOnce) {
      this.billFailedOnce = true;
      throw new Error('模拟账单加载失败');
    }
    if (this.targetDefaults) {
      return { month: TARGET_DEFAULTS.bill.month, expenseCents: TARGET_DEFAULTS.bill.expenseCents };
    }
    const months = await this.deps.bills.getAvailableMonths();
    const month = months[0];
    if (!month) throw new Error('暂无账单月份');
    const bill = await this.deps.bills.getMonthlyBill(month);
    return { month: bill.month, expenseCents: bill.totalExpenseCents };
  }
}