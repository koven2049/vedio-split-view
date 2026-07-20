import type { TranslationKey } from '../i18n'
import type { AnalysisPlatform, DownloadProgressDetail, XiaoyuzhouErrorCode } from '../stores/analysisStore'

/**
 * Pure helpers extracted from AnalyzePage so they can be unit-tested in
 * isolation AND so AnalyzePage.tsx stays a component-only module (required
 * by react-refresh/only-export-components).
 *
 * Kept UI-agnostic: no React, no hooks, no side effects.
 */

export const STAGES = ['metadata', 'subtitle_check', 'audio_download', 'transcription', 'analysis', 'complete']

/** Stages that are meaningless for Xiaoyuzhou (no subtitles on podcasts). */
const HIDDEN_STAGES_BY_PLATFORM: Partial<Record<AnalysisPlatform, Set<string>>> = {
  xiaoyuzhou: new Set(['subtitle_check']),
}

/** Map backend XiaoyuzhouError codes to i18n keys (spec 3.4). */
export const XIAOYUZHOU_ERROR_KEYS: Record<XiaoyuzhouErrorCode, TranslationKey> = {
  cdn_expired: 'analyze.xiaoyuzhouErrorCdnExpired',
  paid_private: 'analyze.xiaoyuzhouErrorPaidPrivate',
  page_changed: 'analyze.xiaoyuzhouErrorPageChanged',
  not_episode: 'analyze.xiaoyuzhouErrorNotEpisode',
}

export function isXiaoyuzhouErrorCode(code: string): code is XiaoyuzhouErrorCode {
  return code in XIAOYUZHOU_ERROR_KEYS
}

function formatMb(bytes: number): number {
  return bytes / 1024 / 1024
}

/** Returns the localized "{downloaded} / {total} MB" string if the detail
 *  carries byte info, otherwise null (caller falls back to %). */
export function buildDownloadMbText(
  detail: DownloadProgressDetail | undefined,
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string,
): string | null {
  if (!detail) return null
  const downloaded = detail.downloaded_bytes
  const total = detail.total_bytes
  if (downloaded == null) return null
  if (total == null) {
    return t('analyze.downloadProgressDownloaded', { downloaded: formatMb(downloaded).toFixed(1) })
  }
  return t('analyze.downloadProgressBytes', {
    downloaded: formatMb(downloaded).toFixed(1),
    total: formatMb(total).toFixed(1),
  })
}

/** Stage list for the platform, filtering out stages that never apply
 *  (e.g. subtitle_check on Xiaoyuzhou — spec 3.8). */
export function stagesForPlatform(platform: string): string[] {
  const hidden = HIDDEN_STAGES_BY_PLATFORM[platform as AnalysisPlatform]
  if (!hidden || hidden.size === 0) return STAGES
  return STAGES.filter((s) => !hidden.has(s))
}

/** Pure Xiaoyuzhou URL validator (spec 3.3). Returns the i18n key for the
 *  specific failure, or null when the URL is a valid single-episode link. */
export function validateXiaoyuzhouUrl(url: string): TranslationKey | null {
  if (!url.includes('xiaoyuzhoufm.com')) return 'analyze.invalidXiaoyuzhou'
  if (/xiaoyuzhoufm\.com\/(podcast|user|category)\b/i.test(url)) return 'analyze.xiaoyuzhouNotEpisode'
  if (!/xiaoyuzhoufm\.com\/episode\/[0-9a-f]{24}/i.test(url)) return 'analyze.xiaoyuzhouBadFormat'
  return null
}
