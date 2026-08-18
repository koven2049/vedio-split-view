import { describe, it, expect } from 'vitest'
import { buildDownloadMbText } from './analyzeHelpers'
import { createT } from '../i18n'
import type { DownloadProgressDetail } from '../stores/analysisStore'

const t = createT('en')

describe('buildDownloadMbText', () => {
  it('returns null when detail is missing (caller falls back to %)', () => {
    expect(buildDownloadMbText(undefined, t)).toBeNull()
  })

  it('returns null when downloaded_bytes is missing', () => {
    const detail: DownloadProgressDetail = { ratio: 0.5, total_bytes: 100 }
    expect(buildDownloadMbText(detail, t)).toBeNull()
  })

  it('shows only downloaded MB when total is unknown', () => {
    // 10 MB in bytes
    const detail: DownloadProgressDetail = { downloaded_bytes: 10 * 1024 * 1024 }
    expect(buildDownloadMbText(detail, t)).toBe('10.0 MB')
  })

  it('shows "downloaded / total MB" when both are present (spec 3.2)', () => {
    // 5 MB / 20 MB
    const detail: DownloadProgressDetail = {
      downloaded_bytes: 5 * 1024 * 1024,
      total_bytes: 20 * 1024 * 1024,
    }
    expect(buildDownloadMbText(detail, t)).toBe('5.0 / 20.0 MB')
  })

  it('rounds to one decimal place', () => {
    // 1.5 MB / 3.25 MB
    const detail: DownloadProgressDetail = {
      downloaded_bytes: 1572864, // 1.5 MB
      total_bytes: 3407872, // 3.25 MB
    }
    expect(buildDownloadMbText(detail, t)).toBe('1.5 / 3.3 MB')
  })
})
