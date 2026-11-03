import { describe, expect, it } from 'vitest';
import { formatRelativeTime, parseServerTime, splitParagraphs, timeAgo } from './utils';

describe('splitParagraphs', () => {
  it('splits prose on blank lines and trims parts', () => {
    expect(splitParagraphs('第一段。\n\n第二段。\n \n第三段。')).toEqual([
      '第一段。',
      '第二段。',
      '第三段。',
    ]);
  });

  it('falls back to a single paragraph when there are no blank lines', () => {
    expect(splitParagraphs('一整块没有分段的文字。')).toEqual(['一整块没有分段的文字。']);
  });

  it('drops empty-only parts and returns nothing for empty input', () => {
    expect(splitParagraphs('\n\n\n')).toEqual([]);
    expect(splitParagraphs('')).toEqual([]);
  });
});

describe('parseServerTime', () => {
  it('treats zone-less server timestamps as UTC', () => {
    expect(parseServerTime('2026-11-03T04:46:00').toISOString()).toBe('2026-11-03T04:46:00.000Z');
    expect(parseServerTime('2026-11-03 04:46:00').toISOString()).toBe('2026-11-03T04:46:00.000Z');
  });

  it('keeps explicit offsets', () => {
    expect(parseServerTime('2026-11-03T04:46:00+00:00').toISOString()).toBe(
      '2026-11-03T04:46:00.000Z',
    );
    expect(parseServerTime('2026-11-03T12:46:00+08:00').toISOString()).toBe(
      '2026-11-03T04:46:00.000Z',
    );
  });
});

describe('formatRelativeTime', () => {
  const now = new Date('2026-11-03T12:00:00Z');
  it('formats recent times relatively', () => {
    expect(formatRelativeTime(new Date('2026-11-03T11:59:30Z'), 'en', now)).toBe('now');
    expect(formatRelativeTime(new Date('2026-11-03T11:55:00Z'), 'en', now)).toBe('5 minutes ago');
    expect(formatRelativeTime(new Date('2026-10-31T12:00:00Z'), 'en', now)).toBe('3 days ago');
    expect(formatRelativeTime(new Date('2026-11-02T12:00:00Z'), 'zh', now)).toBe('昨天');
  });
});

describe('timeAgo', () => {
  it('reads zone-less server timestamps as UTC (was 8h off in UTC+8)', () => {
    expect(new Date().getTimezoneOffset()).toBe(-480); // test env pinned to Asia/Shanghai
    const twoHoursAgoNaiveUtc = new Date(Date.now() - 2 * 3600_000).toISOString().slice(0, 19);
    expect(timeAgo(twoHoursAgoNaiveUtc)).toBe('2h ago');
  });
});
