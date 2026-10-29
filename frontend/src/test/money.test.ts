import { describe, expect, it } from 'vitest';
import { addCents, formatCents, formatYuan, parseAmountToCents, subCents } from '../services/transfer/money';

describe('parseAmountToCents', () => {
  it('parses valid integer yuan', () => {
    expect(parseAmountToCents('3000')).toEqual({ cents: 300000 });
  });

  it('parses two decimals', () => {
    expect(parseAmountToCents('3000.45')).toEqual({ cents: 300045 });
  });

  it('parses one decimal by padding', () => {
    expect(parseAmountToCents('0.1')).toEqual({ cents: 10 });
  });

  it('parses boundary .99', () => {
    expect(parseAmountToCents('12.99')).toEqual({ cents: 1299 });
  });

  it('rejects empty', () => {
    expect(parseAmountToCents('')).toHaveProperty('error');
  });

  it('rejects negative / exponent / over-2-decimals / junk', () => {
    expect(parseAmountToCents('-5')).toHaveProperty('error');
    expect(parseAmountToCents('1e3')).toHaveProperty('error');
    expect(parseAmountToCents('1.999')).toHaveProperty('error');
    expect(parseAmountToCents('abc')).toHaveProperty('error');
    expect(parseAmountToCents('1,000')).toHaveProperty('error');
    expect(parseAmountToCents('.5')).toHaveProperty('error');
  });

  it('rejects zero or negative-sense', () => {
    expect(parseAmountToCents('0')).toHaveProperty('error');
    expect(parseAmountToCents('0.00')).toHaveProperty('error');
  });

  it('rejects non-safe integers', () => {
    expect(parseAmountToCents('9007199254740993')).toHaveProperty('error');
  });
});

describe('formatCents / integer math', () => {
  it('formats thousands + decimals without float error', () => {
    expect(formatCents(123456)).toBe('1,234.56');
    expect(formatCents(0)).toBe('0.00');
    expect(formatCents(5)).toBe('0.05');
    expect(formatYuan(123456)).toBe('¥1,234.56');
  });

  it('adds and subtracts exactly (no float drift)', () => {
    // 0.1 + 0.2 === 0.3 的浮点陷阱在整数分下不复现
    expect(addCents(1000, 2000)).toBe(3000);
    expect(subCents(10030, 999)).toBe(9031);
    // 6000.00 - 3000.00 = 3000.00（转后可用）
    expect(subCents(600000, 300000)).toBe(300000);
  });
});