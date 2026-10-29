import { beforeEach, describe, expect, it } from 'vitest';
import { MockBillsService } from '../services/bills/MockBillsService';
import { BillsOfflineError } from '../services/bills/BillsService';

describe('MockBillsService', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('returns three months with a normal / no-anomaly / empty month', async () => {
    const s = new MockBillsService({}, 0);
    const months = await s.getAvailableMonths();
    expect(months).toEqual(['2026-09', '2026-08', '2026-07']);

    const sep = await s.getMonthlyBill('2026-09');
    expect(sep.transactions.length).toBeGreaterThan(0);
    expect(sep.anomalies.length).toBeGreaterThan(0);
    expect(sep.categories.length).toBeGreaterThan(0);

    const aug = await s.getMonthlyBill('2026-08');
    expect(aug.transactions.length).toBeGreaterThan(0);
    expect(aug.anomalies).toHaveLength(0);

    const jul = await s.getMonthlyBill('2026-07');
    expect(jul.transactions).toHaveLength(0);
    expect(jul.anomalies).toHaveLength(0);
    expect(jul.totalExpenseCents).toBe(0);
  });

  it('amounts are integer cents (no float drift)', async () => {
    const s = new MockBillsService({}, 0);
    const sep = await s.getMonthlyBill('2026-09');
    for (const t of sep.transactions) {
      expect(Number.isInteger(t.amountCents)).toBe(true);
    }
    expect(Number.isInteger(sep.totalExpenseCents)).toBe(true);
  });

  it('failMonth throws for that month only', async () => {
    const s = new MockBillsService({ failMonth: '2026-08' }, 0);
    await expect(s.getMonthlyBill('2026-08')).rejects.toThrow();
    await expect(s.getMonthlyBill('2026-09')).resolves.toBeDefined();
  });

  it('initialFailOnce fails only the first call', async () => {
    const s = new MockBillsService({ initialFailOnce: true }, 0);
    await expect(s.getMonthlyBill('2026-09')).rejects.toThrow();
    await expect(s.getMonthlyBill('2026-09')).resolves.toBeDefined();
  });

  it('offline throws BillsOfflineError', async () => {
    const s = new MockBillsService({ offline: true }, 0);
    await expect(s.getAvailableMonths()).rejects.toBeInstanceOf(BillsOfflineError);
    await expect(s.getMonthlyBill('2026-09')).rejects.toBeInstanceOf(BillsOfflineError);
  });
});

describe('MockBillsService data consistency', () => {
  let s: MockBillsService;
  beforeEach(() => {
    window.sessionStorage.clear();
    s = new MockBillsService({}, 0);
  });

  it('total expense equals sum of posted expense transactions (pending excluded)', async () => {
    for (const month of ['2026-09', '2026-08', '2026-07']) {
      const bill = await s.getMonthlyBill(month);
      const sum = bill.transactions
        .filter((t) => t.status === 'posted' && t.kind === 'expense')
        .reduce((acc, t) => acc + Math.abs(t.amountCents), 0);
      expect(bill.totalExpenseCents).toBe(sum);
    }
  });

  it('income summary includes only posted income (pending refund excluded)', async () => {
    const sep = await s.getMonthlyBill('2026-09');
    const income = sep.transactions
      .filter((t) => t.status === 'posted' && t.kind === 'income')
      .reduce((acc, t) => acc + t.amountCents, 0);
    expect(sep.totalIncomeCents).toBe(income);
    expect(sep.totalIncomeCents).toBe(1200000);
    // 待入账退款不计入已到账
    expect(sep.transactions.find((t) => t.kind === 'refund')?.status).toBe('pending');
  });

  it('each category amount matches its posted expense sum, and category total = expense total', async () => {
    const sep = await s.getMonthlyBill('2026-09');
    const byCat = new Map<string, number>();
    for (const t of sep.transactions) {
      if (t.status === 'posted' && t.kind === 'expense') {
        byCat.set(t.category, (byCat.get(t.category) ?? 0) + Math.abs(t.amountCents));
      }
    }
    for (const c of sep.categories) {
      expect(c.amountCents).toBe(byCat.get(c.category));
    }
    const catSum = sep.categories.reduce((acc, c) => acc + c.amountCents, 0);
    expect(catSum).toBe(sep.totalExpenseCents);
  });

  it('shares sum to ~1 and each share = amount / total', async () => {
    const sep = await s.getMonthlyBill('2026-09');
    const total = sep.totalExpenseCents;
    const sumShare = sep.categories.reduce((acc, c) => acc + c.share, 0);
    expect(Math.abs(sumShare - 1)).toBeLessThan(1e-9);
    for (const c of sep.categories) {
      expect(Math.abs(c.share - c.amountCents / total)).toBeLessThan(1e-9);
    }
  });

  it('daily trend sums to total expense and matches daily posted expenses', async () => {
    const sep = await s.getMonthlyBill('2026-09');
    const byDate = new Map<string, number>();
    for (const t of sep.transactions) {
      if (t.status === 'posted' && t.kind === 'expense') {
        byDate.set(t.date, (byDate.get(t.date) ?? 0) + Math.abs(t.amountCents));
      }
    }
    for (const d of sep.dailyTrend) {
      expect(d.amountCents).toBe(byDate.get(d.date));
    }
    const trendSum = sep.dailyTrend.reduce((acc, d) => acc + d.amountCents, 0);
    expect(trendSum).toBe(sep.totalExpenseCents);
  });

  it('anomaly amounts match their transactions', async () => {
    const sep = await s.getMonthlyBill('2026-09');
    for (const a of sep.anomalies) {
      const tx = sep.transactions.find((t) => Math.abs(t.amountCents) === a.amountCents && t.merchant === a.merchant);
      expect(tx).toBeDefined();
    }
  });

  it('empty month (2026-07) has empty categories and trend and zero totals', async () => {
    const jul = await s.getMonthlyBill('2026-07');
    expect(jul.categories).toHaveLength(0);
    expect(jul.dailyTrend).toHaveLength(0);
    expect(jul.totalExpenseCents).toBe(0);
    expect(jul.totalIncomeCents).toBe(0);
  });
});