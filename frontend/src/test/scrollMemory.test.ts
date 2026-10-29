import { beforeEach, describe, expect, it } from 'vitest';
import { clearScrollMemory, getScroll, isVisitedPath, saveScroll } from '../app/scrollMemory';

describe('scroll memory', () => {
  beforeEach(() => clearScrollMemory());

  it('returns 0 for a path never visited (fresh page starts at top)', () => {
    expect(isVisitedPath('/transfer')).toBe(false);
    expect(getScroll('/transfer')).toBe(0);
  });

  it('remembers a saved position and marks the path visited', () => {
    saveScroll('/bills', 540);
    expect(isVisitedPath('/bills')).toBe(true);
    expect(getScroll('/bills')).toBe(540);
  });

  it('keeps per-path state independent across routes', () => {
    saveScroll('/wealth', 320);
    saveScroll('/wealth/holdings', 120);
    expect(getScroll('/wealth')).toBe(320);
    expect(getScroll('/wealth/holdings')).toBe(120);
  });
});