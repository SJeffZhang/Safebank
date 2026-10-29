import type {
  AssistantHint,
  AssistantIntent,
  AssistantPlan,
  AssistantReply,
  AssistantStep,
  Clarification,
  InstructionInput,
  TransferTarget,
} from './types';
import { AssistantError, AssistantOfflineError } from './AssistantService';
import type { AssistantService } from './AssistantService';

export type MockAssistantMode = 'normal' | 'homophone' | 'noMatch' | 'unplannable' | 'autoInvest';

export interface MockAssistantScenario {
  mode: MockAssistantMode;
  /** 首次生成失败一次（保留上下文，可重试）。 */
  generateFailOnce: boolean;
  /** 手动补充线索首次校验失败一次。 */
  manualFailOnce: boolean;
  /** 澄清已过期：选择候选会被拒绝，需要重新发起。 */
  clarificationExpired: boolean;
  /** 首次指令响应延迟（毫秒），用于验证旧响应失效。 */
  slowFirstMs: number;
  offline: boolean;
  /** 转账金额（分），用于生成黄级/红级计划。 */
  amountCents: number;
}

export const DEFAULT_ASSISTANT_SCENARIO: MockAssistantScenario = {
  mode: 'normal',
  generateFailOnce: false,
  manualFailOnce: false,
  clarificationExpired: false,
  slowFirstMs: 0,
  offline: false,
  amountCents: 3000_00,
};

const HINTS: AssistantHint[] = [
  { hintId: 'hint-transfer', title: '给王五转 3,000 元', intent: 'transfer' },
  { hintId: 'hint-homophone', title: '给王武转 3,000 元', intent: 'transfer' },
  { hintId: 'hint-auto', title: '每月工资 30% 自动申购', intent: 'autoInvest' },
  { hintId: 'hint-card', title: '锁定我的卡片', intent: 'cardLock' },
];

const HINT_TEXT: Record<string, string> = {
  'hint-transfer': '给王五转 3000',
  'hint-homophone': '给王武转 3000',
  'hint-auto': '把每月工资的 30% 自动申购稳健产品',
  'hint-card': '锁定我的卡片',
};

const HINT_INTENT: Record<string, AssistantIntent> = {
  'hint-transfer': 'transfer',
  'hint-homophone': 'transfer',
  'hint-auto': 'autoInvest',
  'hint-card': 'cardLock',
};

function hex(len: number): string {
  let out = '';
  const chars = '0123456789abcdef';
  for (let i = 0; i < len; i += 1) out += chars[Math.floor(Math.random() * 16)];
  return out;
}

function yuan(cents: number): string {
  return (cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 六个固定节点：理解 → 查询 → 计划 → 评估 → 执行 → 回复。 */
function buildSteps(opts: {
  understand: string;
  lookup: string;
  lookupState?: AssistantStep['state'];
  plan: string;
  assess: string;
  assessState?: AssistantStep['state'];
  assessText?: string;
  execute: string;
  executeState?: AssistantStep['state'];
  executeText?: string;
  reply: string;
  replyState?: AssistantStep['state'];
  replyText?: string;
}): AssistantStep[] {
  return [
    {
      key: 'understand',
      label: '理解',
      state: 'done',
      stateText: '完成',
      evidence: [
        { label: '结论', value: opts.understand },
        { label: '来源', value: '你本次输入的指令（纯文本）' },
      ],
    },
    {
      key: 'lookup',
      label: '查询',
      state: opts.lookupState ?? 'passed',
      stateText: opts.lookupState === 'needsClarification' ? '需澄清' : '通过',
      evidence: [
        { label: '结论', value: opts.lookup },
        { label: '来源', value: '账户与收款人目录（演示数据）' },
      ],
    },
    {
      key: 'plan',
      label: '计划',
      state: 'done',
      stateText: '完成',
      evidence: [
        { label: '结论', value: opts.plan },
        { label: '来源', value: '步骤编排（不含任何隐式执行）' },
      ],
    },
    {
      key: 'assess',
      label: '评估',
      state: opts.assessState ?? 'passed',
      stateText: opts.assessText ?? '通过',
      evidence: [
        { label: '结论', value: opts.assess },
        { label: '来源', value: '安全网关规则与账户可用余额' },
      ],
    },
    {
      key: 'execute',
      label: '执行',
      state: opts.executeState ?? 'awaitingConfirm',
      stateText: opts.executeText ?? '待确认',
      evidence: [
        { label: '结论', value: opts.execute },
        { label: '来源', value: '必须由你在确认面板签字后才会执行' },
      ],
    },
    {
      key: 'reply',
      label: '回复',
      state: opts.replyState ?? 'pending',
      stateText: opts.replyText ?? '待执行',
      evidence: [
        { label: '结论', value: opts.reply },
        { label: '来源', value: '服务端结果回执（当前尚未执行）' },
      ],
    },
  ];
}

function transferPlan(amountCents: number, recipientName = '王五', recipientId = 'rcp-wang', mask = '8891'): AssistantPlan {
  const red = amountCents > 5000_00;
  const target: TransferTarget = {
    kind: 'transfer',
    amountCents,
    fromAccountId: 'acc-current',
    fromAccountName: '活期 · 尾号 1234',
    recipientId,
    recipientName,
    recipientAccount: `本行 · 尾号 ${mask}`,
    memo: '小安代为规划',
  };
  return {
    planId: `plan_${hex(6)}`,
    intent: 'transfer',
    title: `转账给${recipientName} ¥${yuan(amountCents)}`,
    steps: buildSteps({
      understand: `识别为「转账」意图，金额 ¥${yuan(amountCents)}`,
      lookup: `匹配到收款人 ${recipientName}（本行 · 尾号 ${mask}）`,
      plan: '计划 4 步：校验余额 → 校验限额 → 风险评级 → 交给你确认',
      assess: red
        ? `金额超过 5,000 元，判定为红级，需要短信验证码`
        : '金额与账户状态通过校验，判定为黄级',
      assessText: red ? '通过（红级）' : '通过（黄级）',
      execute: '等待你在确认面板签字；小安不会直接转账',
      reply: '执行后由安全网关返回结果，小安只转述服务端结果',
    }),
    summary: `从活期账户转出 ¥${yuan(amountCents)} 给${recipientName}，需你确认后才会进入安全网关执行。`,
    fundingPath: `活期 · 尾号 1234 → ${recipientName} · 尾号 ${mask}`,
    feeNote: '本行转账手续费 0.00 元（演示费率）',
    riskNote: red ? '红级：需要短信验证码（OTP）' : '黄级：仅需确认',
    target,
    executable: true,
  };
}

function autoInvestPlan(): AssistantPlan {
  return {
    planId: `plan_${hex(6)}`,
    intent: 'autoInvest',
    title: '每月工资 30% 自动申购（长期计划）',
    steps: buildSteps({
      understand: '识别为「定期定额 / 自动申购」意图',
      lookup: '已比较 3 支稳健类产品（演示数据）',
      plan: '计划：每月自动扣款并申购，属于长期自动化计划',
      assess: '风险校验：R3 稳健型，符合当前风险等级',
      execute: '当前原型尚未开放自动申购执行，不会产生任何扣款',
      executeState: 'insufficient',
      executeText: '原型未开放',
      reply: '需要在业务页手动完成每一笔申购',
      replyState: 'pending',
      replyText: '未执行',
    }),
    summary:
      '这是一个长期自动申购计划。小安可以规划与解释，但当前原型不支持自动扣款，也不会伪造申购成功。',
    fundingPath: '活期 → 稳健类产品（每月）',
    feeNote: '费率以产品页面为准（演示）',
    riskNote: '长期自动化计划需你在业务页逐笔确认',
    executable: false,
    unsupportedReason: '当前原型尚未开放「自动申购」执行，请前往理财页手动申购。',
  };
}

function cardLockPlan(): AssistantPlan {
  return {
    planId: `plan_${hex(6)}`,
    intent: 'cardLock',
    title: '锁定卡片（需在卡片页确认）',
    steps: buildSteps({
      understand: '识别为「卡片锁定」意图',
      lookup: '匹配到 Everyday 借记卡（尾号 6094）',
      plan: '计划：锁定卡片，属于卡片业务写操作',
      assess: '卡片当前状态正常，可锁定',
      execute: '请在卡片页发起预演并确认；小安不直接执行',
      executeState: 'insufficient',
      executeText: '需业务页',
      reply: '未执行',
      replyState: 'pending',
      replyText: '未执行',
    }),
    summary: '锁定卡片需要走卡片业务的安全审批，小安只提供计划与入口。',
    fundingPath: '不涉及资金变动',
    feeNote: '无费用',
    riskNote: '卡片操作需在卡片页确认',
    executable: false,
    unsupportedReason: '请在卡片页发起锁定，小安不直接执行卡片操作。',
  };
}

export class MockAssistantService implements AssistantService {
  private scenario: MockAssistantScenario;
  private delayMs: number;
  private instructions = new Map<string, { text: string; intent: AssistantIntent }>();
  private clarifications = new Map<string, { text: string; candidates: Clarification['candidates'] }>();
  private generateFailedOnce = false;
  private manualFailedOnce = false;
  private slowUsed = false;

  constructor(scenario?: Partial<MockAssistantScenario>, delayMs = 300) {
    this.scenario = { ...DEFAULT_ASSISTANT_SCENARIO, ...scenario };
    this.delayMs = delayMs;
  }

  getScenario(): MockAssistantScenario {
    return { ...this.scenario };
  }

  setScenario(patch: Partial<MockAssistantScenario>): void {
    this.scenario = { ...this.scenario, ...patch };
  }

  private sleep(ms = this.delayMs): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private assertOnline(): void {
    if (this.scenario.offline) throw new AssistantOfflineError();
  }

  async getHints(): Promise<AssistantHint[]> {
    this.assertOnline();
    await this.sleep();
    return HINTS.map((h) => ({ ...h }));
  }

  async submitInstruction(input: InstructionInput): Promise<AssistantReply> {
    this.assertOnline();
    if (this.scenario.slowFirstMs > 0 && !this.slowUsed) {
      this.slowUsed = true;
      await this.sleep(this.scenario.slowFirstMs);
    } else {
      await this.sleep();
    }
    if (this.scenario.generateFailOnce && !this.generateFailedOnce) {
      this.generateFailedOnce = true;
      throw new AssistantError('GENERATE_FAILED', '计划生成失败，请重试');
    }

    const hintId = input.hintId;
    const text = (hintId ? HINT_TEXT[hintId] ?? '' : input.text ?? '').trim();
    const intent = hintId ? HINT_INTENT[hintId] ?? 'unknown' : 'transfer';
    if (!text) throw new AssistantError('EMPTY', '请输入你的需求');

    if (this.scenario.mode === 'unplannable' || (!hintId && /天气|股票代码|帮我写/.test(text))) {
      return {
        kind: 'unplannable',
        message: '我无法把这句话变成可执行的银行业务计划。小安只处理账户、转账、卡片、账单、订阅与理财相关的请求。',
        suggestions: ['试试「给王五转 3000 元」', '或从下方快捷指令中选择一项'],
      };
    }

    if (this.scenario.mode === 'homophone' || /王武/.test(text)) {
      const clarificationId = `clr_${hex(6)}`;
      const candidates =
        this.scenario.mode === 'noMatch'
          ? []
          : [
              {
                candidateId: 'cand-2210',
                name: '王武',
                bankLabel: '本行',
                mask: '2210',
                hint: '最近转账 2025-08',
              },
              { candidateId: 'cand-8873', name: '王伍', bankLabel: '本行', mask: '8873', hint: '新收款人' },
            ];
      this.clarifications.set(clarificationId, { text, candidates });
      this.instructions.set(clarificationId, { text, intent });
      return {
        kind: 'clarification',
        message:
          candidates.length === 0
            ? '没有找到读音匹配的收款人。你可以手动填写收款人，或换一种说法。'
            : '收款人里有两个读音相同的名字。请确认是哪一位，我不会替你猜。',
        clarification: {
          clarificationId,
          question: '请选择收款人',
          reason:
            candidates.length === 0
              ? '按姓名检索没有匹配结果；手动填写需要能唯一定位到一位收款人。'
              : '两个名字同音、尾号不同；最近一次往来只指向王武。',
          candidates,
          allowManual: true,
        },
      };
    }

    if (this.scenario.mode === 'autoInvest' || intent === 'autoInvest') {
      return { kind: 'plan', plan: autoInvestPlan(), message: '这是长期计划，我先给你完整方案，不会直接扣款。' };
    }
    if (intent === 'cardLock') return { kind: 'plan', plan: cardLockPlan(), message: '卡片操作需要在卡片页完成，我先说明计划。' };

    const plan = transferPlan(this.scenario.amountCents);
    return { kind: 'plan', plan, message: '明白。我先说明计划，再把完整内容交给你确认，不会直接转账。' };
  }

  async selectCandidate(clarificationId: string, candidateId: string): Promise<AssistantReply> {
    this.assertOnline();
    await this.sleep();
    const record = this.clarifications.get(clarificationId);
    if (!record) throw new AssistantError('CLARIFICATION_NOT_FOUND', '澄清已失效，请重新发起');
    if (this.scenario.clarificationExpired) {
      throw new AssistantError('CLARIFICATION_EXPIRED', '澄清已过期，请重新发起');
    }
    const candidate = record.candidates.find((c) => c.candidateId === candidateId);
    if (!candidate) throw new AssistantError('CANDIDATE_NOT_FOUND', '候选项已失效，请重新选择');
    const plan = transferPlan(this.scenario.amountCents, candidate.name, `rcp-${candidate.mask}`, candidate.mask);
    return { kind: 'plan', plan, message: `已按你选择的 ${candidate.name}（尾号 ${candidate.mask}）生成计划，仍需你确认。` };
  }

  async submitClarificationAnswer(clarificationId: string, answer: string): Promise<AssistantReply> {
    this.assertOnline();
    await this.sleep();
    const record = this.clarifications.get(clarificationId);
    if (!record) throw new AssistantError('CLARIFICATION_NOT_FOUND', '澄清已失效，请重新发起');
    if (this.scenario.clarificationExpired) {
      throw new AssistantError('CLARIFICATION_EXPIRED', '澄清已过期，请重新发起');
    }
    const text = answer.trim();
    if (!text) throw new AssistantError('EMPTY_ANSWER', '请输入补充线索');
    if (this.scenario.manualFailOnce && !this.manualFailedOnce) {
      this.manualFailedOnce = true;
      throw new AssistantError('MANUAL_UNRESOLVED', '未能确认收款人，请补充更明确的线索');
    }
    if (!/王武|2210|王伍|8873/.test(text)) {
      throw new AssistantError('MANUAL_UNRESOLVED', '未能确认收款人，请补充更明确的线索');
    }
    const mask = /8873|王伍/.test(text) ? '8873' : '2210';
    const name = mask === '8873' ? '王伍' : '王武';
    const plan = transferPlan(this.scenario.amountCents, name, `rcp-${mask}`, mask);
    return { kind: 'plan', plan, message: `已按补充线索定位到 ${name}（尾号 ${mask}），请确认计划。` };
  }

  async restartClarification(clarificationId: string): Promise<AssistantReply> {
    this.assertOnline();
    await this.sleep();
    const record = this.clarifications.get(clarificationId) ?? this.instructions.get(clarificationId);
    if (!record) throw new AssistantError('CLARIFICATION_NOT_FOUND', '澄清已失效，请重新发起');
    this.scenario = { ...this.scenario, clarificationExpired: false };
    const clarificationId2 = `clr_${hex(6)}`;
    const candidates = this.clarifications.get(clarificationId)?.candidates ?? [];
    this.clarifications.set(clarificationId2, { text: record.text, candidates });
    return {
      kind: 'clarification',
      message: '已重新发起澄清，请再次选择收款人。',
      clarification: {
        clarificationId: clarificationId2,
        question: '请选择收款人',
        reason: '已重新发起，旧澄清不再可用。',
        candidates,
        allowManual: true,
      },
    };
  }

  async cancelPlan(_planId: string): Promise<void> {
    this.assertOnline();
    await this.sleep();
  }
}