import { beforeEach, describe, expect, it } from 'vitest';
import { MockTransferService, clearMockTransferRecords } from '../services/transfer/MockTransferService';
import type { TransferInput } from '../services/transfer/types';
import { MockHomeService } from '../services/home/MockHomeService';
import { MockProfileService } from '../services/profile/MockProfileService';
import { MockAuthService } from '../services/auth/MockAuthService';
import { MockWealthService } from '../services/wealth/MockWealthService';
import { MockSubscriptionsService } from '../services/subscriptions/MockSubscriptionsService';
import { MockBillsService } from '../services/bills/MockBillsService';

const NOW = Date.parse('2026-10-28T10:00:00Z');

function input(amountCents: number, fromAccountId = 'acc-current'): TransferInput {
  return { amountCents, fromAccountId, recipientId: 'rcp-wang', memo: '' };
}

async function preview(service: MockTransferService, amountCents: number, fromAccountId?: string) {
  const result = await service.previewTransfer(input(amountCents, fromAccountId));
  if (result.kind !== 'ok') throw new Error(`preview failed: ${JSON.stringify(result)}`);
  return result.approvalId;
}

async function currentBalance(service: MockTransferService, id = 'acc-current'): Promise<number> {
  const accounts = await service.listAccounts();
  return accounts.find((a) => a.id === id)?.availableCents ?? -1;
}

function stack(transfer: MockTransferService) {
  const auth = new MockAuthService({}, 0);
  const wealth = new MockWealthService({}, 0);
  const subscriptions = new MockSubscriptionsService({}, 0, () => NOW);
  const bills = new MockBillsService({}, 0);
  const home = new MockHomeService({ auth, transfer, wealth, subscriptions, bills }, {}, 0);
  const profile = new MockProfileService(wealth, {}, 0, auth, home);
  return { home, profile, wealth };
}

describe('转账成功后权威扣减余额', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    clearMockTransferRecords();
  });

  it('张三活期 600,000 分，成功转账 300,000 分后为 300,000 分', async () => {
    const service = new MockTransferService({ confirm: 'success' }, 0);
    expect(await currentBalance(service)).toBe(600_000);
    const id = await preview(service, 300_000);
    await expect(service.confirmApproval(id, { kind: 'yellow' })).resolves.toMatchObject({ status: 'success' });
    expect(await currentBalance(service)).toBe(300_000);
  });

  it('首页总资产 2,600,000 → 2,300,000，个人中心与首页一致', async () => {
    const service = new MockTransferService({ confirm: 'success' }, 0);
    const { home, profile } = stack(service);
    expect((await home.getSummary('cust-zhang')).totalCents).toBe(2_600_000);

    const id = await preview(service, 300_000);
    await service.confirmApproval(id, { kind: 'yellow' });

    const summary = await home.getSummary('cust-zhang');
    const overview = await profile.getOverview('cust-zhang');
    expect(summary.totalCents).toBe(2_300_000);
    expect(summary.currentCents).toBe(300_000);
    expect(overview.stats.totalAssetCents).toBe(summary.totalCents);
  });

  it('重复 confirm 不重复扣款', async () => {
    const service = new MockTransferService({ confirm: 'success' }, 0);
    const id = await preview(service, 300_000);
    await service.confirmApproval(id, { kind: 'yellow' });
    await expect(service.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({
      code: 'APPROVAL_ALREADY_PROCESSED',
    });
    expect(await currentBalance(service)).toBe(300_000);
  });

  it('FAILED、UNKNOWN 未定、过期与取消均不扣款', async () => {
    const failed = new MockTransferService({ confirm: 'failedNoFunds' }, 0);
    const fId = await preview(failed, 100_000);
    await failed.confirmApproval(fId, { kind: 'yellow' });
    expect(await currentBalance(failed)).toBe(600_000);

    const unknown = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'unknown' }, 0);
    const uId = await preview(unknown, 100_000);
    await unknown.confirmApproval(uId, { kind: 'yellow' });
    expect(await unknown.getApprovalStatus(uId)).toBe('unknown');
    expect(await currentBalance(unknown)).toBe(600_000);

    let now = NOW;
    const expiring = new MockTransferService({ confirm: 'success' }, 0, () => now);
    const eId = await preview(expiring, 100_000);
    now += 10 * 60 * 1000;
    await expect(expiring.confirmApproval(eId, { kind: 'yellow' })).rejects.toMatchObject({
      code: 'APPROVAL_EXPIRED',
    });
    expect(await currentBalance(expiring)).toBe(600_000);

    // 取消/未确认：仅预演，不扣款
    const cancelled = new MockTransferService({ confirm: 'success' }, 0);
    await preview(cancelled, 100_000);
    expect(await currentBalance(cancelled)).toBe(600_000);
  });

  it('UNKNOWN 查询得到 SUCCESS 时只扣一次', async () => {
    const service = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'success' }, 0);
    const id = await preview(service, 200_000);
    await expect(service.confirmApproval(id, { kind: 'yellow' })).resolves.toMatchObject({ status: 'unknown' });
    expect(await currentBalance(service)).toBe(600_000);

    await expect(service.getApprovalStatus(id)).resolves.toBe('success');
    expect(await currentBalance(service)).toBe(400_000);

    await expect(service.getApprovalStatus(id)).resolves.toBe('success');
    expect(await currentBalance(service)).toBe(400_000);
  });

  it('UNKNOWN 查询得到 FAILED 时不扣款', async () => {
    const service = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'failed' }, 0);
    const id = await preview(service, 200_000);
    await service.confirmApproval(id, { kind: 'yellow' });
    await expect(service.getApprovalStatus(id)).resolves.toBe('failed');
    expect(await currentBalance(service)).toBe(600_000);
  });

  it('新实例恢复余额与审批状态，且不会再次扣款', async () => {
    const first = new MockTransferService({ confirm: 'success' }, 0);
    const id = await preview(first, 300_000);
    await first.confirmApproval(id, { kind: 'yellow' });

    const restored = new MockTransferService({}, 0);
    expect(await currentBalance(restored)).toBe(300_000);
    await expect(restored.getApprovalStatus(id)).resolves.toBe('success');
    await expect(restored.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({
      code: 'APPROVAL_ALREADY_PROCESSED',
    });
    expect(await currentBalance(restored)).toBe(300_000);
  });

  it('第二笔转账基于第一笔后的余额校验', async () => {
    const service = new MockTransferService({ confirm: 'success' }, 0);
    const first = await preview(service, 300_000);
    await service.confirmApproval(first, { kind: 'yellow' });
    expect(await currentBalance(service)).toBe(300_000);

    // 剩余 300,000 分，再转 350,000 分必须被拒绝
    const rejected = await service.previewTransfer(input(350_000));
    expect(rejected).toMatchObject({ kind: 'error', code: 'INSUFFICIENT' });

    // 连续多笔按累计余额扣减
    const second = await preview(service, 100_000);
    await service.confirmApproval(second, { kind: 'yellow' });
    const third = await preview(service, 200_000);
    await service.confirmApproval(third, { kind: 'yellow' });
    expect(await currentBalance(service)).toBe(0);
  });

  it('退出登录清理后回到干净演示状态', async () => {
    const service = new MockTransferService({ confirm: 'success' }, 0);
    const id = await preview(service, 300_000);
    await service.confirmApproval(id, { kind: 'yellow' });
    expect(await currentBalance(service)).toBe(300_000);

    clearMockTransferRecords();
    const fresh = new MockTransferService({}, 0);
    expect(await currentBalance(fresh)).toBe(600_000);
    await expect(fresh.getApprovalStatus(id)).rejects.toBeTruthy();
  });
});