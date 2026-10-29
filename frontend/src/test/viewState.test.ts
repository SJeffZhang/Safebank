import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearPendingApproval,
  readPendingApproval,
  savePendingApproval,
} from '../app/viewState';

describe('pending approval round-trip', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('save/read returns the exact id without extra quotes', () => {
    savePendingApproval('mock-apv-1');
    // 磁盘上是 JSON 字符串（带引号），读取时解析回原始 ID。
    expect(window.sessionStorage.getItem('sb-pending-approval')).toBe('"mock-apv-1"');
    expect(readPendingApproval()).toBe('mock-apv-1');
  });

  it('clear returns null', () => {
    savePendingApproval('mock-apv-2');
    clearPendingApproval();
    expect(readPendingApproval()).toBeNull();
  });

  it('corrupted or wrong-typed value safely falls back to null', () => {
    window.sessionStorage.setItem('sb-pending-approval', 'not-json');
    expect(readPendingApproval()).toBeNull();

    window.sessionStorage.setItem('sb-pending-approval', '42'); // 数字，非字符串
    expect(readPendingApproval()).toBeNull();

    window.sessionStorage.setItem('sb-pending-approval', '""'); // 空字符串
    expect(readPendingApproval()).toBeNull();

    window.sessionStorage.setItem('sb-pending-approval', '{"a":1}'); // 对象
    expect(readPendingApproval()).toBeNull();
  });
});