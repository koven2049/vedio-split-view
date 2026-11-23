import { describe, expect, it } from 'vitest';
import {
  formatRelativeTime,
  generatePlaybackUrl,
  isXStatusUrl,
  parseServerTime,
  platformLabel,
  splitParagraphs,
  tagChipStyle,
  timeAgo,
} from './utils';

describe('tagChipStyle', () => {
  it('renders near-black tags as a solid chip with white text', () => {
    expect(tagChipStyle('#000000')).toEqual({ background: '#000000', color: '#fff' });
  });

  it('keeps the tinted style for bright brand colours', () => {
    expect(tagChipStyle('#ff0000')).toEqual({ background: '#ff000020', color: '#ff0000' });
    expect(tagChipStyle('#7c3aed')).toEqual({ background: '#7c3aed20', color: '#7c3aed' });
  });

  it('falls back to the tinted style for unparseable colours', () => {
    expect(tagChipStyle('not-a-color')).toEqual({
      background: 'not-a-color20',
      color: 'not-a-color',
    });
  });
});

describe('isXStatusUrl', () => {
  it('accepts status links on every supported X/Twitter domain', () => {
    const accepted = [
      'https://x.com/rchen1386/status/2124465845284008268?a=F25JJuNRRsuJxjooklI&s=20',
      'https://twitter.com/foo/status/123',
      'https://www.x.com/foo/status/123',
      'https://mobile.x.com/foo/status/123',
      'https://m.twitter.com/foo/status/123',
      'https://fxtwitter.com/foo/status/123',
      'https://vxtwitter.com/foo/status/123',
      'https://fixupx.com/foo/status/123',
      'https://fixvx.com/foo/status/123',
      'https://x.com/i/status/123',
      'https://twitter.com/i/web/status/123',
      'https://x.com/foo/status/123/',
      'x.com/foo/status/123',
    ];
    for (const url of accepted) {
      expect(isXStatusUrl(url), url).toBe(true);
    }
  });

  it('rejects links without a numeric status id and foreign domains', () => {
    const rejected = [
      'https://x.com/foo',
      'https://x.com/foo/status/',
      'https://x.com/foo/status/abc',
      'https://x.com/foo/status/123abc',
      'https://example.com/foo/status/123',
      'https://x.com.evil.com/foo/status/123',
      '',
    ];
    for (const url of rejected) {
      expect(isXStatusUrl(url), url).toBe(false);
    }
  });
});

describe('platformLabel', () => {
  it('labels x as X', () => {
    expect(platformLabel('x')).toBe('X');
  });
});

describe('generatePlaybackUrl', () => {
  it('links X videos to the status page', () => {
    expect(generatePlaybackUrl('x', '2124465845284008268', 90)).toBe(
      'https://x.com/i/status/2124465845284008268',
    );
  });
});

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
