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
  return '';
}

export function platformLabel(platform: string): string {
  const labels: Record<string, string> = {
    youtube: 'YouTube',
    bilibili: 'Bilibili',
    xiaoyuzhou: '小宇宙',
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
