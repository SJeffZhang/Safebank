import { beforeEach, describe, expect, it } from 'vitest';
import { MockTransferService, clearMockTransferRecords } from '../services/transfer/MockTransferService';
import { OfflineError } from '../services/transfer/TransferService';

const input = { amountCents: 100000, fromAccountId: 'acc-current', recipientId: 'rcp-wang', memo: '' };

function previewId(s: MockTransferService): Promise<string> {
  return s.previewTransfer(input).then((res) => (res.kind === 'ok' ? res.approvalId : ''));
}

describe('MockTransferService authoritative checks', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('confirmApproval rejects an unknown approval_id', async () => {
    const s = new MockTransferService({}, 0);
    await expect(s.confirmApproval('bogus', { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_NOT_FOUND' });
  });

  it('getApprovalStatus rejects an unknown approval_id (not scenario fallback)', async () => {
    const s = new MockTransferService({ afterUnknownStatus: 'success' }, 0);
    await expect(s.getApprovalStatus('bogus')).rejects.toMatchObject({ code: 'APPROVAL_NOT_FOUND' });
  });

  it('confirmApproval throws APPROVAL_EXPIRED once time advances past expiry', async () => {
    let now = 0;
    const s = new MockTransferService({}, 0, () => now);
    const id = await previewId(s);
    now = 5 * 60_000 + 1;
    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_EXPIRED' });
  });

  it('confirmApproval throws APPROVAL_ALREADY_PROCESSED on a second confirm', async () => {
    const s = new MockTransferService({ confirm: 'success' }, 0);
    const id = await previewId(s);
    await s.confirmApproval(id, { kind: 'yellow' });
    await expect(s.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_ALREADY_PROCESSED' });
  });

  it('clearMockTransferRecords wipes persisted mock records', async () => {
    const s1 = new MockTransferService({}, 0);
    const id = await previewId(s1);
    clearMockTransferRecords();
    const s2 = new MockTransferService({}, 0);
    await expect(s2.getApprovalStatus(id)).rejects.toMatchObject({ code: 'APPROVAL_NOT_FOUND' });
  });

  it('offline throws OfflineError on network calls', async () => {
    const s = new MockTransferService({ offline: true }, 0);
    await expect(s.listAccounts()).rejects.toBeInstanceOf(OfflineError);
  });
});

describe('MockTransferService refresh persistence', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('UNKNOWN → success survives a refresh with DEFAULT scenario', async () => {
    const s1 = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'success' }, 0);
    const id = await previewId(s1);
    await s1.confirmApproval(id, { kind: 'yellow' });

    const s2 = new MockTransferService(); // 默认场景，不重注入 afterUnknownStatus
    expect(await s2.getApprovalStatus(id)).toBe('success');
  });

  it('UNKNOWN → failed survives a refresh with DEFAULT scenario', async () => {
    const s1 = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'failed' }, 0);
    const id = await previewId(s1);
    await s1.confirmApproval(id, { kind: 'yellow' });

    const s2 = new MockTransferService();
    expect(await s2.getApprovalStatus(id)).toBe('failed');
  });

  it('persistent UNKNOWN stays unknown after refresh', async () => {
    const s1 = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'unknown' }, 0);
    const id = await previewId(s1);
    await s1.confirmApproval(id, { kind: 'yellow' });

    const s2 = new MockTransferService();
    expect(await s2.getApprovalStatus(id)).toBe('unknown');
  });

  it('does not reuse mock-apv-1 after refresh; both records stay queryable', async () => {
    const s1 = new MockTransferService({}, 0);
    const firstId = await previewId(s1);
    expect(firstId).toBe('mock-apv-1');

    const s2 = new MockTransferService({}, 0);
    const secondId = await previewId(s2);
    expect(secondId).not.toBe('mock-apv-1');

    // 两条记录均可独立查询
    await expect(s2.getPlanView('mock-apv-1')).resolves.toHaveProperty('approvalId', 'mock-apv-1');
    await expect(s2.getPlanView(secondId)).resolves.toHaveProperty('approvalId', secondId);
  });

  it('processed (success) approval stays already-processed after refresh', async () => {
    const s1 = new MockTransferService({ confirm: 'success' }, 0);
    const id = await previewId(s1);
    await s1.confirmApproval(id, { kind: 'yellow' });

    const s2 = new MockTransferService(); // 默认场景
    await expect(s2.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_ALREADY_PROCESSED' });
    expect(await s2.getApprovalStatus(id)).toBe('success');
  });

  it('UNKNOWN approval cannot be re-confirmed; query finalizes it', async () => {
    const s1 = new MockTransferService({ confirm: 'unknown', afterUnknownStatus: 'success' }, 0);
    const id = await previewId(s1);
    await s1.confirmApproval(id, { kind: 'yellow' });

    // 已提交但未知：不允许复用原授权再次确认
    await expect(s1.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_ALREADY_SUBMITTED' });

    // 查询到达最终状态并持久化
    expect(await s1.getApprovalStatus(id)).toBe('success');
    const s2 = new MockTransferService();
    expect(await s2.getApprovalStatus(id)).toBe('success');
    await expect(s2.confirmApproval(id, { kind: 'yellow' })).rejects.toMatchObject({ code: 'APPROVAL_ALREADY_PROCESSED' });
  });
});