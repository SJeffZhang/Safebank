import { beforeEach, describe, expect, it } from 'vitest';
import {
  MockWealthService,
  clearMockWealthRecords,
  AVAILABLE_BALANCE_CENTS,
} from '../services/wealth/MockWealthService';

function svc(scenario = {}, delay = 0) {
  return new MockWealthService(scenario, delay);
}

describe('MockWealthService 数据一致性', () => {
  beforeEach(() => window.sessionStorage.clear());

  it('总览与持仓完全一致（总资产 = 持仓市值合计，盈亏 = 各项之和）', async () => {
    const s = svc();
    const [ov, holdings] = await Promise.all([s.getOverview('1M'), s.getHoldings()]);
    const sum = holdings.reduce((a, h) => a + h.marketValueCents, 0);
    const pnl = holdings.reduce((a, h) => a + h.pnlCents, 0);
    expect(ov.totalAssetCents).toBe(sum + AVAILABLE_BALANCE_CENTS + ov.inTransitCents);
    expect(ov.holdingValueCents).toBe(sum);
    expect(ov.holdingPnlCents).toBe(pnl);
    expect(ov.holdingsCount).toBe(holdings.length);
    expect(sum).toBe(12_856_030); // ¥128,560.30
    expect(pnl).toBe(23_618); // +¥236.18
    // 盈亏比例 = pnl / 成本
    expect(Math.abs(ov.holdingPnlPercent - pnl / (sum - pnl))).toBeLessThan(1e-9);
    // 占比合计为 1
    const shares = holdings.reduce((a, h) => a + h.sharePercent, 0);
    expect(Math.abs(shares - 1)).toBeLessThan(1e-9);
  });

  it('产品列表字段完整且有准确标签，不在售/不可申购带原因', async () => {
    const s = svc();
    const products = await s.getProducts();
    expect(products.length).toBeGreaterThanOrEqual(8);
    for (const p of products) {
      expect(p.productId).toBeTruthy();
      expect(p.metricLabel.length).toBeGreaterThan(0);
      expect(Number.isInteger(p.minPurchaseCents)).toBe(true);
      if (!p.purchasable) expect(p.unavailableReason).toBeTruthy();
    }
    expect(products.find((p) => p.productId === 'p6')?.purchasable).toBe(false);
    expect(products.find((p) => p.productId === 'p8')?.onSale).toBe(false);
  });

  it('趋势周期返回对应数据；空趋势返回空数组（不伪造平线）', async () => {
    const s = svc();
    const m1 = await s.getOverview('1M');
    const y1 = await s.getOverview('1Y');
    expect(m1.trend.length).toBeGreaterThan(0);
    expect(y1.trend.length).toBeGreaterThan(0);
    expect(m1.trend).not.toEqual(y1.trend);

    const empty = svc({ emptyTrend: true });
    expect((await empty.getOverview('1M')).trend).toEqual([]);
  });

  it('空持仓时总览为 0 且不报错', async () => {
    const s = svc({ emptyHoldings: true });
    const holdings = await s.getHoldings();
    const ov = await s.getOverview('1M');
    expect(holdings).toEqual([]);
    expect(ov.totalAssetCents).toBe(AVAILABLE_BALANCE_CENTS + ov.inTransitCents);
    expect(ov.holdingValueCents).toBe(0);
    expect(ov.holdingsCount).toBe(0);
    expect(ov.trend).toEqual([]);
  });
});

describe('MockWealthService 申购与赎回', () => {
  beforeEach(() => window.sessionStorage.clear());

  it('申购校验：低于起购 / 超余额 / 不可申购 / 不在售 / 非法金额', async () => {
    const s = svc();
    expect(await s.preparePurchase('p1', 100)).toMatchObject({ kind: 'error', code: 'BELOW_MIN' });
    expect(await s.preparePurchase('p2', AVAILABLE_BALANCE_CENTS + 1)).toMatchObject({
      kind: 'error',
      code: 'INSUFFICIENT_BALANCE',
    });
    expect(await s.preparePurchase('p6', 1_000_00)).toMatchObject({ kind: 'error', code: 'NOT_PURCHASABLE' });
    expect(await s.preparePurchase('p8', 1_000_00)).toMatchObject({ kind: 'error', code: 'NOT_ON_SALE' });
    expect(await s.preparePurchase('p1', 0)).toMatchObject({ kind: 'error', code: 'INVALID_AMOUNT' });
    expect(await s.preparePurchase('p1', 10.5)).toMatchObject({ kind: 'error', code: 'INVALID_AMOUNT' });
  });

  it('赎回校验：不可赎回 / 超出份额 / 非法份额', async () => {
    const s = svc();
    expect(await s.prepareRedemption('h4', 10)).toMatchObject({ kind: 'error', code: 'NOT_REDEEMABLE' });
    expect(await s.prepareRedemption('h1', 999999)).toMatchObject({ kind: 'error', code: 'EXCEEDS_SHARES' });
    expect(await s.prepareRedemption('h1', 0)).toMatchObject({ kind: 'error', code: 'INVALID_SHARES' });
    expect(await s.prepareRedemption('nope', 1)).toMatchObject({ kind: 'error', code: 'NO_HOLDING' });
  });

  it('申购成功（资金整数分相加）后持仓与总览反映 service 结果，且资金守恒', async () => {
    const s = svc({ confirm: 'success' });
    const before = await s.getHoldings();
    const beforeTotal = before.reduce((a, h) => a + h.marketValueCents, 0);
    const res = await s.preparePurchase('p2', 100_000);
    const id = res.kind === 'ok' ? res.approvalId : '';
    const plan = await s.getPlanView(id);
    expect(plan.operation).toBe('purchase');
    expect(plan.amountCents).toBe(100_000);
    await s.confirmApproval(id, { kind: 'yellow' });
    const after = await s.getHoldings();
    const afterTotal = after.reduce((a, h) => a + h.marketValueCents, 0);
    expect(afterTotal).toBe(beforeTotal + 100_000);

    const ov = await s.getOverview('1M');
    expect(ov.holdingValueCents).toBe(afterTotal);
    // 可用余额扣减，总资产不变（资金守恒）
    expect(ov.availableBalanceCents).toBe(AVAILABLE_BALANCE_CENTS - 100_000);
    expect(ov.totalAssetCents).toBe(afterTotal + ov.availableBalanceCents + ov.inTransitCents);
  });

  it('赎回成功减少份额与市值，总览同步', async () => {
    const s = svc({ confirm: 'success' });
    const before = (await s.getHoldings()).find((h) => h.holdingId === 'h1')!;
    const res = await s.prepareRedemption('h1', 100);
    const id = res.kind === 'ok' ? res.approvalId : '';
    const plan = await s.getPlanView(id);
    expect(plan.shares).toBe(100);
    expect(plan.estimatedAmountCents).toBe(Math.round(100 * before.netValue * 100));
    await s.confirmApproval(id, { kind: 'red', otp: '123456' });
    const after = (await s.getHoldings()).find((h) => h.holdingId === 'h1')!;
    expect(after.shares).toBeCloseTo(before.shares - 100, 2);
    expect(after.marketValueCents).toBeLessThan(before.marketValueCents);
  });

  it('风险等级由 service 决定：R3/R4 申购为红级，R1 小额为黄级', async () => {
    const s = svc();
    const red = await s.preparePurchase('p1', 1_000_00); // R3
    const redPlan = await s.getPlanView(red.kind === 'ok' ? red.approvalId : '');
    expect(redPlan.riskLevel).toBe('red');
    expect(redPlan.ackRequired).toBe(true);

    const yellow = await s.preparePurchase('p2', 1_000_00); // R1 小额
    const yellowPlan = await s.getPlanView(yellow.kind === 'ok' ? yellow.approvalId : '');
    expect(yellowPlan.riskLevel).toBe('yellow');
    expect(yellowPlan.ackRequired).toBe(false);
  });

  it('赎回红/黄级也由持仓风险等级决定', async () => {
    const s = svc();
    const red = await s.prepareRedemption('h1', 10); // R3
    expect((await s.getPlanView(red.kind === 'ok' ? red.approvalId : '')).riskLevel).toBe('red');
    const yellow = await s.prepareRedemption('h2', 10); // R1
    expect((await s.getPlanView(yellow.kind === 'ok' ? yellow.approvalId : '')).riskLevel).toBe('yellow');
  });

  it('OTP 错误返回剩余次数（来自 service）', async () => {
    const s = svc({ otpErrorOnce: true });
    const res = await s.preparePurchase('p1', 1_000_00);
    const id = res.kind === 'ok' ? res.approvalId : '';
    await expect(s.confirmApproval(id, { kind: 'red', otp: '123456' })).rejects.toMatchObject({
      code: 'OTP_INVALID',
      message: expect.stringContaining('还可尝试 2 次'),
    });
    // 再次确认成功（剩余次数由 service 决定，前端不推算）
    await expect(s.confirmApproval(id, { kind: 'red', otp: '123456' })).resolves.toMatchObject({ status: 'success' });
  });
});

describe('MockWealthService 审批记录持久化', () => {
  beforeEach(() => window.sessionStorage.clear());

  it('已过期审批不能确认', async () => {
    let now = 0;
    const s = new MockWealthService({}, 0, () => now);
    const res = await s.preparePurchase('p2', 1_000_00);
    const id = res.kind === 'ok' ? res.approvalId : '';
    now = 5 * 60_000 + 1;
    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_EXPIRED' });
  });

  it('已处理审批不能重复执行', async () => {
    const s = svc({ confirm: 'success' });
    const res = await s.preparePurchase('p2', 1_000_00);
    const id = res.kind === 'ok' ? res.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({
      code: 'APPROVAL_ALREADY_PROCESSED',
    });
  });

  it('未知 ID 报错（不回落场景）', async () => {
    const s = svc();
    await expect(s.getPlanView('bogus')).rejects.toMatchObject({ code: 'APPROVAL_NOT_FOUND' });
    await expect(s.getApprovalStatus('bogus')).rejects.toMatchObject({ code: 'APPROVAL_NOT_FOUND' });
  });

  it('刷新后：UNKNOWN 后续结果不丢、ID 不冲突', async () => {
    const s1 = svc({ confirm: 'unknown', afterUnknownStatus: 'success' });
    const res = await s1.preparePurchase('p2', 1_000_00);
    const id1 = res.kind === 'ok' ? res.approvalId : '';
    await s1.confirmApproval(id1, { kind: 'yellow' });

    const s2 = new MockWealthService(); // 默认场景
    expect(await s2.getApprovalStatus(id1)).toBe('success');

    const res2 = await s2.preparePurchase('p2', 1_000_00);
    const id2 = res2.kind === 'ok' ? res2.approvalId : '';
    expect(id2).not.toBe(id1);
  });

  it('PlanView 失败一次后可重试成功', async () => {
    const s = svc({ planViewFailOnce: true });
    const res = await s.preparePurchase('p2', 1_000_00);
    const id = res.kind === 'ok' ? res.approvalId : '';
    await expect(s.getPlanView(id)).rejects.toMatchObject({ code: 'PLANVIEW_LOAD_FAILED' });
    await expect(s.getPlanView(id)).resolves.toMatchObject({ approvalId: id });
  });

  it('clearMockWealthRecords 清除记录与已应用操作', async () => {
    const s = svc({ confirm: 'success' });
    const res = await s.preparePurchase('p2', 100_000);
    const id = res.kind === 'ok' ? res.approvalId : '';
    await s.confirmApproval(id, { kind: 'yellow' });
    expect((await s.getHoldings()).find((h) => h.holdingId === 'h2')!.marketValueCents).toBe(3_118_000);

    clearMockWealthRecords();
    const s2 = svc();
    expect((await s2.getHoldings()).find((h) => h.holdingId === 'h2')!.marketValueCents).toBe(3_018_000);
    await expect(s2.getApprovalStatus(id)).rejects.toMatchObject({ code: 'APPROVAL_NOT_FOUND' });
  });

  it('离线抛出 GatewayOfflineError', async () => {
    const s = svc({ offline: true });
    await expect(s.getOverview('1M')).rejects.toBeInstanceOf(Error);
    await expect(s.getProducts()).rejects.toBeInstanceOf(Error);
  });
});

describe('MockWealthService 权威授权校验', () => {
  beforeEach(() => window.sessionStorage.clear());

  it('红级申购传 yellow 授权被拒绝，资产不变，且记录未被消费', async () => {
    const s = svc();
    const before = await s.getOverview('1M');
    const res = await s.preparePurchase('p1', 1_000_00); // R3 → 红级
    const id = res.kind === 'ok' ? res.approvalId : '';
    expect((await s.getPlanView(id)).riskLevel).toBe('red');

    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({
      code: 'AUTHORIZATION_REQUIRED',
    });
    const after = await s.getOverview('1M');
    expect(after.totalAssetCents).toBe(before.totalAssetCents);
    expect(after.holdingValueCents).toBe(before.holdingValueCents);
    expect(after.availableBalanceCents).toBe(before.availableBalanceCents);

    // 未被消费：仍可用正确授权成功
    await expect(s.confirmApproval(id, { kind: 'red', otp: '123456' })).resolves.toMatchObject({
      status: 'success',
    });
  });

  it('红级赎回传 yellow 授权被拒绝，份额与余额不变', async () => {
    const s = svc();
    const beforeHolding = (await s.getHoldings()).find((h) => h.holdingId === 'h1')!;
    const beforeOverview = await s.getOverview('1M');
    const res = await s.prepareRedemption('h1', 100); // R3 → 红级
    const id = res.kind === 'ok' ? res.approvalId : '';

    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({
      code: 'AUTHORIZATION_REQUIRED',
    });
    const afterHolding = (await s.getHoldings()).find((h) => h.holdingId === 'h1')!;
    const afterOverview = await s.getOverview('1M');
    expect(afterHolding.shares).toBe(beforeHolding.shares);
    expect(afterOverview.availableBalanceCents).toBe(beforeOverview.availableBalanceCents);

    await expect(s.confirmApproval(id, { kind: 'red', otp: '123456' })).resolves.toMatchObject({
      status: 'success',
    });
  });

  it('红级 OTP 非 6 位或错误被拒绝，记录未提交未处理，之后正确 OTP 仍可成功', async () => {
    const s = svc({ otpErrorOnce: true });
    const res = await s.preparePurchase('p1', 1_000_00);
    const id = res.kind === 'ok' ? res.approvalId : '';

    await expect(s.confirmApproval(id, { kind: 'red', otp: '123' })).rejects.toMatchObject({
      code: 'OTP_INVALID',
    });
    await expect(s.confirmApproval(id, { kind: 'red', otp: '000000' })).rejects.toMatchObject({
      code: 'OTP_INVALID',
      message: expect.stringContaining('还可尝试'),
    });

    const recs = JSON.parse(window.sessionStorage.getItem('mock-wealth-records') ?? '[]');
    const rec = recs.find((r: { id: string }) => r.id === id);
    expect(rec.submitted).toBe(false);
    expect(rec.processed).toBe(false);
    expect(rec.finalStatus).toBeNull();

    await expect(s.confirmApproval(id, { kind: 'red', otp: '123456' })).resolves.toMatchObject({
      status: 'success',
    });
  });

  it('错误授权被拒后不会把记录标记为已提交', async () => {
    const s = svc();
    const res = await s.preparePurchase('p4', 1_000_00); // R3 → 红级
    const id = res.kind === 'ok' ? res.approvalId : '';
    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({
      code: 'AUTHORIZATION_REQUIRED',
    });
    const recs = JSON.parse(window.sessionStorage.getItem('mock-wealth-records') ?? '[]');
    const rec = recs.find((r: { id: string }) => r.id === id);
    expect(rec.submitted).toBe(false);
    expect(rec.processed).toBe(false);
  });

  it('黄级申购与黄级赎回的现有成功路径不受影响', async () => {
    const buy = svc({ confirm: 'success' });
    const r1 = await buy.preparePurchase('p2', 1_000_00); // R1 小额 → 黄级
    const id1 = r1.kind === 'ok' ? r1.approvalId : '';
    expect((await buy.getPlanView(id1)).riskLevel).toBe('yellow');
    await expect(buy.confirmApproval(id1, { kind: 'yellow' })).resolves.toMatchObject({ status: 'success' });

    const sell = svc({ confirm: 'success' });
    const r2 = await sell.prepareRedemption('h2', 100); // R1 → 黄级
    const id2 = r2.kind === 'ok' ? r2.approvalId : '';
    expect((await sell.getPlanView(id2)).riskLevel).toBe('yellow');
    await expect(sell.confirmApproval(id2, { kind: 'yellow' })).resolves.toMatchObject({ status: 'success' });
  });

  it('黄级审批也接受完整红级授权', async () => {
    const s = svc({ confirm: 'success' });
    const r = await s.preparePurchase('p2', 1_000_00);
    const id = r.kind === 'ok' ? r.approvalId : '';
    await expect(s.confirmApproval(id, { kind: 'red', otp: '123456' })).resolves.toMatchObject({
      status: 'success',
    });
  });
});