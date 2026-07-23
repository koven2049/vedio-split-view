import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nContext, createT } from '../i18n'
import { useAnalysisStore } from '../stores/analysisStore'
import type { SlotState } from '../stores/analysisStore'
import { AnalysisSlotCard } from './AnalyzePage'

// Mount AnalysisSlotCard under the providers it actually uses:
//   - I18nContext  (useT)
//   - QueryClientProvider  (useQueryClient)
//   - analysisStore  (useAnalysisStore — a zustand global, no provider needed;
//     we set state directly before each test)
function renderCard(slot: Partial<SlotState> & { platform: string }, opts: { onRetry?: () => void } = {}) {
  const t = createT('en')
  const value = { locale: 'en' as const, t, setLocale: () => {} }
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const fullSlot: SlotState = {
    slotId: 'slot-test',
    taskId: null,
    analyzing: true,
    url: 'https://example.com',
    progress: null,
    stepLog: [],
    result: null,
    error: '',
    completedVideoId: null,
    pendingConfirm: null,
    reconnected: false,
    _sseAbort: null,
    ...slot,
  } as SlotState
  return render(
    <I18nContext.Provider value={value}>
      <QueryClientProvider client={queryClient}>
        <AnalysisSlotCard
          slot={fullSlot}
          lang="en"
          setLang={() => {}}
          onNavigateVideo={() => {}}
          canRetry={true}
          onRetry={opts.onRetry}
        />
      </QueryClientProvider>
    </I18nContext.Provider>,
  )
}

beforeEach(() => {
  // Store is read by AnalysisSlotCard (removeSlot/cancel paths); give it a
  // clean slate so tests don't bleed.
  useAnalysisStore.setState({ slots: {}, _nextId: 1, _initialized: false })
})

describe('AnalysisSlotCard — stage dots by platform (spec 3.8)', () => {
  it('hides the subtitle_check dot for xiaoyuzhou', () => {
    renderCard({
      platform: 'xiaoyuzhou',
      analyzing: true,
      progress: { stage: 'audio_download', progress: 30, message: 'Downloading' },
    })
    // "Subtitle Check" label must not appear for xiaoyuzhou.
    expect(screen.queryByText('Subtitle Check')).not.toBeInTheDocument()
    // Other stages still present.
    expect(screen.getByText('Audio Download')).toBeInTheDocument()
  })

  it('shows the subtitle_check dot for youtube', () => {
    renderCard({
      platform: 'youtube',
      analyzing: true,
      progress: { stage: 'audio_download', progress: 30, message: 'Downloading' },
    })
    expect(screen.getByText('Subtitle Check')).toBeInTheDocument()
  })
})

describe('AnalysisSlotCard — download MB caption (spec 3.2)', () => {
  it('renders "{downloaded} / {total} MB" when byte detail is present', () => {
    renderCard({
      platform: 'xiaoyuzhou',
      analyzing: true,
      progress: {
        stage: 'audio_download',
        progress: 35,
        message: 'Downloading',
        detail: {
          ratio: 0.5,
          downloaded_bytes: 5 * 1024 * 1024,
          total_bytes: 20 * 1024 * 1024,
        },
      },
    })
    expect(screen.getByText('5.0 / 20.0 MB')).toBeInTheDocument()
  })

  it('shows only downloaded MB when total is unknown', () => {
    renderCard({
      platform: 'xiaoyuzhou',
      analyzing: true,
      progress: {
        stage: 'audio_download',
        progress: 20,
        message: 'Downloading',
        detail: { downloaded_bytes: 3 * 1024 * 1024 },
      },
    })
    expect(screen.getByText('3.0 MB')).toBeInTheDocument()
  })

  it('omits the MB caption when no byte detail is sent (percent fallback)', () => {
    renderCard({
      platform: 'xiaoyuzhou',
      analyzing: true,
      progress: { stage: 'audio_download', progress: 20, message: 'Downloading' },
    })
    expect(screen.queryByText(/MB/)).not.toBeInTheDocument()
  })
})

describe('AnalysisSlotCard — typed Xiaoyuzhou error message (spec 3.4)', () => {
  it('replaces the generic error text with the cdn_expired message', () => {
    renderCard({
      platform: 'xiaoyuzhou',
      analyzing: false,
      error: 'backend raw message',
      errorCode: 'cdn_expired',
      progress: { stage: 'error', progress: 0, message: 'backend raw message' },
      stepLog: [],
    })
    expect(screen.getByText('Audio link expired. Click retry to fetch a fresh one.')).toBeInTheDocument()
    expect(screen.queryByText('backend raw message')).not.toBeInTheDocument()
  })

  it('falls back to the raw error when errorCode is absent', () => {
    renderCard({
      platform: 'xiaoyuzhou',
      analyzing: false,
      error: 'backend raw message',
      progress: { stage: 'error', progress: 0, message: 'backend raw message' },
      stepLog: [],
    })
    expect(screen.getByText('backend raw message')).toBeInTheDocument()
  })
})

describe('AnalysisSlotCard — retry button visibility on deterministic failures', () => {
  it('hides retry when duration_exceeded (retry can never succeed)', () => {
    renderCard({
      platform: 'youtube',
      taskId: 7,
      analyzing: false,
      error: 'Video is 4h0m, exceeding the 3h30m limit.',
      errorCode: 'duration_exceeded',
      progress: { stage: 'error', progress: 0, message: 'exceeded' },
      stepLog: [],
    }, { onRetry: () => {} })
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()
  })

  it('shows retry for transient failures (e.g. cdn_expired)', () => {
    renderCard({
      platform: 'xiaoyuzhou',
      taskId: 7,
      analyzing: false,
      error: 'cdn gone',
      errorCode: 'cdn_expired',
      progress: { stage: 'error', progress: 0, message: 'cdn gone' },
      stepLog: [],
    }, { onRetry: () => {} })
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
