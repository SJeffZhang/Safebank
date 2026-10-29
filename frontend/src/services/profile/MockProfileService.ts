import type { AuditEntry, NotificationItem, ProfileOverview, ProfileUser } from './types';
import type { ProfileService } from './ProfileService';
import type { WealthService } from '../wealth/WealthService';
import type { AuthService } from '../auth/AuthService';
import type { HomeService } from '../home/HomeService';
import { GatewayOfflineError } from '../approval/shared';

export interface MockProfileScenario {
  overviewFailOnce: boolean;
  auditFailOnce: boolean;
  notificationsFailOnce: boolean;
  offline: boolean;
  emptyAudit: boolean;
  emptyNotifications: boolean;
  /** 用户资料为空（降级展示）。 */
  missingUser: boolean;
  /** 用户资料部分缺失（缺手机号）。 */
  partialUser: boolean;
  riskAssessment: 'valid' | 'missing' | 'expired';
}

export const DEFAULT_PROFILE_SCENARIO: MockProfileScenario = {
  overviewFailOnce: false,
  auditFailOnce: false,
  notificationsFailOnce: false,
  offline: false,
  emptyAudit: false,
  emptyNotifications: false,
  missingUser: false,
  partialUser: false,
  riskAssessment: 'valid',
};

const BASE_USER: ProfileUser = {
  name: '李四',
  avatarText: '李',
  maskedPhone: '138****6602',
  realNameVerified: true,
  level: 'L2',
  riskBand: 'R3',
  riskBandLabel: 'R3 稳健型',
};
const AUDIT: AuditEntry[] = [
  {
    id: 'aud-1',
    planId: 'plan_8f3a',
    title: '转账预演 · 王五 ¥3,000.00',
    result: '通过',
    tone: 'info',
    hashMasked: 'b71c…9e2a',
    time: '2026-10-27 09:41',
  },
  {
    id: 'aud-2',
    planId: 'plan_71bd',
    title: '卡片冻结 · Everyday 尾号 6094',
    result: '红级',
    tone: 'red',
    hashMasked: '8f02…41d7',
    time: '2026-10-26 21:08',
  },
  {
    id: 'aud-3',
    planId: 'plan_5c19',
    title: '订阅取消 · 视频会员',
    result: '通过',
    tone: 'success',
    hashMasked: '3a77…b015',
    time: '2026-10-25 18:22',
  },
];

const NOTIFICATIONS: NotificationItem[] = [
  {
    id: 'ntf-1',
    kind: 'block',
    kindLabel: '拦截',
    title: '凌晨大额 · 夜色酒吧',
    time: '09-07 02:13',
    targetPath: '/bills',
  },
  {
    id: 'ntf-2',
    kind: 'subscription',
    kindLabel: '订阅',
    title: '云音乐即将扣款 ¥15.00',
    time: '11-02',
    targetPath: '/subscriptions',
  },
  {
    id: 'ntf-3',
    kind: 'block',
    kindLabel: '拦截',
    title: '新商户大额 · 星耀电子',
    time: '09-22 19:05',
    targetPath: '/bills',
  },
  {
    id: 'ntf-4',
    kind: 'subscription',
    kindLabel: '订阅',
    title: '健身私教连续 90 天未使用',
    time: '10-20',
    targetNote: '该提醒暂无对应业务页',
  },
];

export class MockProfileService implements ProfileService {
  private scenario: MockProfileScenario;
  private delayMs: number;
  private wealth?: WealthService;
  private auth?: AuthService;
  private home?: HomeService;
  private overviewFailedOnce = false;
  private auditFailedOnce = false;
  private notificationsFailedOnce = false;

  constructor(
    wealth?: WealthService,
    scenario?: Partial<MockProfileScenario>,
    delayMs = 300,
    auth?: AuthService,
    home?: HomeService,
  ) {
    this.wealth = wealth;
    this.auth = auth;
    this.home = home;
    this.scenario = { ...DEFAULT_PROFILE_SCENARIO, ...scenario };
    this.delayMs = delayMs;
  }

  getScenario(): MockProfileScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockProfileScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private assertOnline(): void {
    if (this.scenario.offline) throw new GatewayOfflineError();
  }

  async getOverview(customerId?: string): Promise<ProfileOverview> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.overviewFailOnce && !this.overviewFailedOnce) {
      this.overviewFailedOnce = true;
      throw new Error('模拟账户摘要加载失败');
    }

    // 资产口径必须与首页完全一致：优先复用 HomeService.getSummary(customerId)。
    let totalAssetCents = 0;
    let holdingProductCount = 0;
    if (this.home && customerId) {
      const summary = await this.home.getSummary(customerId);
      totalAssetCents = summary.totalCents;
      holdingProductCount = summary.holdingProductCount;
    } else if (this.wealth) {
      const [ov, holdings] = await Promise.all([this.wealth.getOverview('1M'), this.wealth.getHoldings()]);
      totalAssetCents = ov.totalAssetCents;
      holdingProductCount = holdings.length;
    }

    // 当前登录客户的权威身份（来自认证目录）；查不到时使用演示默认画像。
    let customer: Awaited<ReturnType<AuthService['getCustomers']>>[number] | undefined;
    if (this.auth && customerId) {
      try {
        const catalog = await this.auth.getCustomers();
        customer = catalog.find((c) => c.customerId === customerId);
      } catch {
        customer = undefined;
      }
    }
    const baseUser: ProfileUser = customer
      ? {
          name: customer.name,
          avatarText: customer.avatarText,
          maskedPhone: customer.maskedPhone,
          realNameVerified: true,
          level: 'L2',
          riskBand: customer.riskBand,
          riskBandLabel: customer.riskBandLabel,
        }
      : BASE_USER;

    const user: ProfileUser | null = this.scenario.missingUser
      ? null
      : this.scenario.partialUser
        ? { ...baseUser, maskedPhone: '' }
        : { ...baseUser };

    return {
      user,
      stats: { totalAssetCents, holdingProductCount },
      tools: {
        subscriptionNote: '4 笔订阅 · 年化已折算',
        cardNote: 'Everyday · 尾号 6094',
      },
      riskAssessment: customer?.riskAssessment ?? this.scenario.riskAssessment,
    };
  }

  async getAudit(): Promise<AuditEntry[]> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.auditFailOnce && !this.auditFailedOnce) {
      this.auditFailedOnce = true;
      throw new Error('模拟审计加载失败');
    }
    if (this.scenario.emptyAudit) return [];
    return AUDIT.map((a) => ({ ...a }));
  }

  async getNotifications(): Promise<NotificationItem[]> {
    this.assertOnline();
    await this.sleep();
    if (this.scenario.notificationsFailOnce && !this.notificationsFailedOnce) {
      this.notificationsFailedOnce = true;
      throw new Error('模拟通知加载失败');
    }
    if (this.scenario.emptyNotifications) return [];
    return NOTIFICATIONS.map((n) => ({ ...n }));
  }
}