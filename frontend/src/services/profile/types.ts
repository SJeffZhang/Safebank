/** 个人中心领域类型。金额为整数分；所有文本按不可信纯文本渲染。 */

export type RiskBand = 'R1' | 'R2' | 'R3' | 'R4';

export interface ProfileUser {
  name: string;
  /** 头像单字。 */
  avatarText: string;
  /** 脱敏手机号，如 138****6602。 */
  maskedPhone: string;
  realNameVerified: boolean;
  level: string;
  /** 风险等级；未测评客户为 null（由 riskBandLabel 表达）。 */
  riskBand: RiskBand | null;
  riskBandLabel: string;
}

export interface ProfileStats {
  totalAssetCents: number;
  holdingProductCount: number;
}

export interface ProfileTools {
  subscriptionNote: string;
  cardNote: string;
}

export type RiskAssessmentStatus = 'valid' | 'missing' | 'expired';

export interface ProfileOverview {
  user: ProfileUser | null;
  stats: ProfileStats;
  tools: ProfileTools;
  riskAssessment: RiskAssessmentStatus;
}

export interface AuditEntry {
  id: string;
  planId: string;
  title: string;
  result: string;
  tone: 'info' | 'red' | 'copper' | 'success';
  /** 脱敏哈希，如 b71c…9e2a。 */
  hashMasked: string;
  time: string;
}

export interface NotificationItem {
  id: string;
  kind: 'block' | 'subscription';
  kindLabel: string;
  title: string;
  time: string;
  /** 明确对应的现有业务页；缺失时禁用跳转。 */
  targetPath?: string;
  /** 无目标时的说明。 */
  targetNote?: string;
}