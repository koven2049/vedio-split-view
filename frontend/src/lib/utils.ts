import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function formatTimeRange(start: number, end: number): string {
  return `${formatDuration(start)} - ${formatDuration(end)}`;
}

export function generatePlaybackUrl(
  platform: string,
  videoId: string,
  startSeconds: number,
): string {
  if (platform === 'youtube') {
    return `https://www.youtube.com/watch?v=${videoId}&t=${startSeconds}`;
  }
  if (platform === 'bilibili') {
    return `https://www.bilibili.com/video/${videoId}?t=${startSeconds}`;
  }
  if (platform === 'xiaoyuzhou') {
    return `https://www.xiaoyuzhoufm.com/episode/${videoId}?t=${startSeconds}`;
  }
  if (platform === 'apple_podcasts') {
    // videoId is "showId:episodeId"; the slug is optional for redirects.
    const [showId = '', epId = ''] = videoId.split(':');
    return `https://podcasts.apple.com/us/podcast/id${showId}?i=${epId}`;
  }
  if (platform === 'x') {
    // X's web player has no timestamp parameter.
    return `https://x.com/i/status/${videoId}`;
  }
  return '';
}

/** True for X/Twitter status links (x.com, twitter.com and the
 *  fxtwitter/vxtwitter/fixupx/fixvx mirrors), including mobile./m./www. hosts,
 *  query strings and links pasted without a scheme. The status path accepts
 *  the /<user>/status/, /i/status/ and /i/web/status/ forms the backend
 *  recognizes; profile and search links are not analyzable. */
export function isXStatusUrl(url: string): boolean {
  return /^(?:https?:\/\/)?(?:www\.|mobile\.|m\.)?(?:x|twitter|fxtwitter|vxtwitter|fixupx|fixvx)\.com\/(?:[^/?#]+\/)?[^/?#]+\/status\/\d+(?:[/?#]\S*)?$/i.test(
    url.trim(),
  );
}

/** Linearize one sRGB channel for the WCAG relative-luminance formula. */
function linearizeChannel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** WCAG 2.x relative luminance for `#rrggbb`; null when the value is not hex. */
function relativeLuminance(hex: string): number | null {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return null;
  const value = Number.parseInt(match[1], 16);
  const r = linearizeChannel((value >> 16) & 0xff);
  const g = linearizeChannel((value >> 8) & 0xff);
  const b = linearizeChannel(value & 0xff);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * Chip style for a tag colour. Near-black colours (relative luminance below
 * 0.05, e.g. the X platform tag #000000) would render black text on the dark
 * theme, so they get a solid chip with white text and keep their brand colour;
 * every other colour keeps the existing 12.5%-alpha tint with matching text.
 * Unparseable values also keep the legacy tint (`color + '20'`).
 */
export function tagChipStyle(color: string): { background: string; color: string } {
  const luminance = relativeLuminance(color);
  if (luminance !== null && luminance < 0.05) {
    return { background: color, color: '#fff' };
  }
  return { background: color + '20', color };
}

export function platformLabel(platform: string): string {
  const labels: Record<string, string> = {
    youtube: 'YouTube',
    bilibili: 'Bilibili',
    xiaoyuzhou: '小宇宙',
    apple_podcasts: 'Apple Podcasts',
    x: 'X',
  };
  return labels[platform] || platform;
}

/** Split prose on blank lines into paragraphs; falls back to one paragraph. */
export function splitParagraphs(text: string): string[] {
  const parts = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  return parts.length ? parts : [text.trim()].filter(Boolean);
}

export function timeAgo(dateStr: string): string {
  const date = parseServerTime(dateStr);
  const diff = Date.now() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return date.toLocaleDateString();
}

/**
 * Parse a server timestamp. SQLite `CURRENT_TIMESTAMP` columns serialize
 * without a zone ("2026-11-03T04:46:00") but are UTC; `new Date()` would
 * read them as local time, so append Z when no offset is present.
 */
export function parseServerTime(value: string): Date {
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasZone ? value : `${value.replace(' ', 'T')}Z`);
}

/** "3 天前" / "3 days ago" style relative time; falls back to a date past 30 days. */
export function formatRelativeTime(date: Date, locale: string, now: Date = new Date()): string {
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(0, 'second');
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), 'hour');
  if (abs < 86400 * 30) return rtf.format(Math.round(seconds / 86400), 'day');
  return date.toLocaleDateString(locale);
}
