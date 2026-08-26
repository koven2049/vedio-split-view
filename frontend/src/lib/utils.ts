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

export function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}
