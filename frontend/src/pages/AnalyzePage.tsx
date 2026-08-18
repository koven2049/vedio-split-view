import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, Play, Save, Share2, Loader2, AlertCircle, Cookie, X, RefreshCw, Headphones } from 'lucide-react'
import { api } from '../lib/api'
import { formatDuration, formatTimeRange, generatePlaybackUrl, platformLabel, cn } from '../lib/utils'
import { useAnalysisStore, type AnalysisPlatform, type SlotState, type DownloadProgressDetail } from '../stores/analysisStore'
import LangToggle from '../components/LangToggle'
import UsageDisplay from '../components/UsageDisplay'
import { useLangPreference } from '../hooks/useLangPreference'
import { useT, type TranslationKey } from '../i18n'
import {
  XIAOYUZHOU_ERROR_KEYS,
  buildDownloadMbText,
  isXiaoyuzhouErrorCode,
  stagesForPlatform,
  validateXiaoyuzhouUrl,
} from './analyzeHelpers'

interface CookiesStatus {
  configured: boolean
  file_exists: boolean
  earliest_expiry: string | null
  earliest_expiry_ts: number | null
  expired: boolean
  cookie_count: number
  domain_summary: string
  usability_checked: boolean
  usable: boolean | null
  usability_message: string
  checked_at: string | null
}

interface PreferencesData {
  max_duration_seconds: number
  confirm_threshold_seconds: number
  max_concurrent_analyses: number
  defaults: { max_duration_seconds: number; confirm_threshold_seconds: number; max_concurrent_analyses: number }
}

const STAGE_LABELS_KEYS: Record<string, TranslationKey> = {
  metadata: 'analyze.videoInfo',
  confirm_required: 'analyze.confirmation',
  subtitle_check: 'analyze.subtitleCheck',
  audio_download: 'analyze.audioDownload',
  transcription: 'analyze.transcription',
  analysis: 'analyze.aiAnalysis',
  complete: 'analyze.done',
}

function detectPlatform(url: string): AnalysisPlatform | null {
  if (/youtube\.com|youtu\.be/.test(url)) return 'youtube'
  if (/bilibili\.com|b23\.tv/.test(url)) return 'bilibili'
  if (/xiaoyuzhoufm\.com/.test(url)) return 'xiaoyuzhou'
  return null
}

export function AnalysisSlotCard({
  slot,
  lang,
  setLang,
  onNavigateVideo,
  onRetry,
  canRetry,
}: {
  slot: SlotState
  lang: 'zh' | 'en'
  setLang: (l: 'zh' | 'en') => void
  onNavigateVideo: (videoId: number) => void
  onRetry?: () => void
  canRetry: boolean
}) {
  const t = useT()
  const store = useAnalysisStore()
  const queryClient = useQueryClient()
  const { analyzing, progress, stepLog, result, error, errorCode, completedVideoId, pendingConfirm, slotId } = slot

  const stageLabel = (stage: string) => {
    const key = STAGE_LABELS_KEYS[stage]
    return key ? t(key) : stage
  }

  // Stages to render as dots. Xiaoyuzhou never has subtitles, so we hide
  // the subtitle_check dot to avoid showing a permanently "skipped" step
  // (spec 3.8). Backend still emits the event; this is display-only.
  const visibleStages = stagesForPlatform(slot.platform)
  const visibleStageIndex = progress ? visibleStages.indexOf(progress.stage) : -1

  // Localized "{downloaded} / {total} MB" caption under the progress bar.
  // Null when the backend hasn't sent byte info — caller renders nothing.
  const downloadMbText = progress?.stage === 'audio_download'
    ? buildDownloadMbText(progress?.detail as DownloadProgressDetail | undefined, t)
    : null

  // For Xiaoyuzhou failures, surface the typed error code as a friendly,
  // actionable message instead of the generic backend text (spec 3.4).
  const errorDisplay = (() => {
    if (!error) return ''
    if (slot.platform === 'xiaoyuzhou' && errorCode && isXiaoyuzhouErrorCode(errorCode)) {
      return t(XIAOYUZHOU_ERROR_KEYS[errorCode])
    }
    return error
  })()

  // Deterministic failures (e.g. duration over the hard limit) can never
  // succeed on retry — hide the retry button instead of inviting a no-op.
  const retryable = errorCode !== 'duration_exceeded'

  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!analyzing) return
    setElapsed(0)
    const tick = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(tick)
  }, [analyzing])

  useEffect(() => {
    if (completedVideoId) {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      store.fetchResultDetails(slotId, completedVideoId)
    }
  }, [completedVideoId]) // eslint-disable-line react-hooks/exhaustive-deps

  const hasMetadata = !!(slot.title || slot.uploader || slot.durationSeconds)

  return (
    <div className="space-y-4">
      {/* Header with metadata */}
      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--color-border)' }}>
        <div className="flex items-start gap-3 px-4 py-3" style={{ background: 'var(--color-bg-secondary)' }}>
          {slot.thumbnailUrl && (
            <img src={slot.thumbnailUrl} alt="" className="w-24 h-14 object-cover rounded-md shrink-0 mt-0.5" />
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: 'rgba(59,130,246,0.1)', color: 'var(--color-primary)' }}>
                {platformLabel(slot.platform)}
              </span>
              {slot.status && !analyzing && (
                <span
                  className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                  style={{
                    background: slot.status.startsWith('failed') ? 'rgba(239, 68, 68, 0.1)' : 'rgba(59,130,246,0.1)',
                    color: slot.status.startsWith('failed') ? 'var(--color-danger)' : 'rgb(59,130,246)',
                  }}
                >
                  {slot.status}
                </span>
              )}
              {slot.reconnected && (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: 'rgba(59,130,246,0.1)', color: 'rgb(59,130,246)' }}>
                  {t('analyze.reconnected')}
                </span>
              )}
              <div className="flex-1" />
              {!analyzing && (
                <button onClick={() => store.removeSlot(slotId)} className="p-1 rounded hover:opacity-70 shrink-0" title={t('analyze.dismiss')}>
                  <X size={14} />
                </button>
              )}
            </div>
            <p className="text-sm font-medium truncate" style={{ color: 'var(--color-text)' }}>
              {slot.title || slot.url}
            </p>
            {hasMetadata && (
              <div className="flex items-center gap-3 mt-1 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                {slot.uploader && <span>{slot.uploader}</span>}
                {slot.durationSeconds != null && slot.durationSeconds > 0 && <span>{formatDuration(slot.durationSeconds)}</span>}
                {slot.uploadDate && <span>{slot.uploadDate}</span>}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Progress */}
      {(analyzing || stepLog.length > 0) && progress && (
        <div className="rounded-xl overflow-hidden" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
          <div className="px-5 pt-4 pb-3 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {analyzing && <Loader2 size={16} className="animate-spin" style={{ color: 'var(--color-primary)' }} />}
                <span className="text-sm font-medium">{progress.message}</span>
              </div>
              <div className="flex items-center gap-3">
                {analyzing && elapsed > 0 && (
                  <span className="text-xs tabular-nums font-mono" style={{ color: 'var(--color-text-secondary)' }}>
                    {elapsed >= 60 ? `${Math.floor(elapsed / 60)}m${(elapsed % 60).toString().padStart(2, '0')}s` : `${elapsed}s`}
                  </span>
                )}
                {analyzing && (
                  <button onClick={() => store.cancelAnalysis(slotId)} className="px-3 py-1 rounded-lg text-xs font-medium transition-opacity hover:opacity-70" style={{ border: '1px solid var(--color-danger)', color: 'var(--color-danger)' }}>
                    {t('analyze.cancel')}
                  </button>
                )}
              </div>
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="h-full rounded-full transition-all duration-500" style={{ width: `${progress.progress}%`, background: 'var(--color-primary)' }} />
            </div>
            <div className="flex gap-1.5 items-center">
              {visibleStages.slice(0, -1).map((stage, i) => {
                const isCurrent = stage === progress.stage
                const isDone = i < visibleStageIndex
                const isSkipped = stage === 'audio_download' && stepLog.some((s) => s.detail?.method === 'subtitle')
                const isSkippedTranscript = stage === 'transcription' && stepLog.some((s) => s.detail?.method === 'subtitle')
                return (
                  <div key={stage} className="flex items-center gap-1.5">
                    <div className={cn('rounded-full transition-all', isCurrent ? 'w-2.5 h-2.5' : 'w-2 h-2')}
                      style={{
                        background: (isSkipped || isSkippedTranscript)
                          ? 'var(--color-text-secondary)'
                          : (isDone || isCurrent) ? 'var(--color-primary)' : 'var(--color-bg-tertiary)',
                        opacity: (isSkipped || isSkippedTranscript) ? 0.4 : 1,
                      }} />
                    <span className="text-[10px] pr-2" style={{ color: isCurrent ? 'var(--color-primary)' : 'var(--color-text-secondary)', fontWeight: isCurrent ? 600 : 400 }}>
                      {(isSkipped || isSkippedTranscript) ? <s>{stageLabel(stage)}</s> : stageLabel(stage)}
                    </span>
                  </div>
                )
              })}
            </div>
            {downloadMbText && (
              <div className="text-xs tabular-nums font-mono" style={{ color: 'var(--color-text-secondary)' }}>
                {downloadMbText}
              </div>
            )}
          </div>

          {stepLog.some((s) => s.detail?.method) && (
            <div className="px-5 pb-2">
              {stepLog.filter((s) => s.detail?.method).slice(-1).map((s, i) => (
                <span key={i} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium"
                  style={{
                    background: s.detail?.method === 'subtitle' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(59, 130, 246, 0.1)',
                    color: s.detail?.method === 'subtitle' ? 'rgb(16, 185, 129)' : 'rgb(59, 130, 246)',
                  }}>
                  {s.detail?.method === 'subtitle' ? t('analyze.usingSubtitles') : t('analyze.usingWhisper')}
                </span>
              ))}
            </div>
          )}

          {stepLog.length > 0 && (
            <div className="px-5 pb-4">
              <div className="max-h-48 overflow-y-auto space-y-0.5 rounded-lg p-3" style={{ background: 'var(--color-bg-tertiary)' }}>
                {stepLog.map((entry, i) => (
                  <div key={i} className="flex items-start gap-2 py-1 text-xs" style={{ color: entry.stage === 'error' ? 'var(--color-danger)' : 'var(--color-text-secondary)' }}>
                    <span className="shrink-0 opacity-50 font-mono tabular-nums" style={{ minWidth: '52px' }}>
                      {new Date(entry.timestamp).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                    <span className="shrink-0 font-medium uppercase tracking-wider" style={{ minWidth: '90px', color: entry.stage === 'error' ? 'var(--color-danger)' : 'var(--color-primary)', fontSize: '10px', lineHeight: '16px' }}>
                      {stageLabel(entry.stage)}
                    </span>
                    <span style={{ color: entry.stage === 'error' ? 'var(--color-danger)' : 'var(--color-text)' }}>
                      {entry.message}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="p-4 rounded-xl flex items-start gap-3" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-danger)' }}>
          <AlertCircle size={18} style={{ color: 'var(--color-danger)' }} className="mt-0.5 shrink-0" />
          <p className="text-sm flex-1">{errorDisplay}</p>
          {slot.taskId && onRetry && retryable && (
            <button
              onClick={onRetry}
              disabled={!canRetry}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40 shrink-0"
              style={{ background: 'var(--color-primary)' }}
            >
              <RefreshCw size={13} /> {t('analyze.retry')}
            </button>
          )}
        </div>
      )}

      {/* Confirm Modal */}
      {pendingConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
          <div className="relative w-full max-w-sm mx-4 rounded-2xl shadow-2xl p-6 space-y-4" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }} onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-full" style={{ background: 'rgba(245, 158, 11, 0.1)' }}>
                <AlertCircle size={20} style={{ color: 'var(--color-warning, #f59e0b)' }} />
              </div>
              <h3 className="text-base font-semibold">{t('analyze.durationWarning')}</h3>
            </div>
            <div className="space-y-2">
              {pendingConfirm.title && <p className="text-sm font-medium">{pendingConfirm.title}</p>}
              <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                {t('analyze.videoDuration')}{' '}
                <strong>{formatDuration(pendingConfirm.durationSeconds)}</strong>.
                {' '}
                {t('analyze.durationConfirm')}
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button onClick={() => store.declineTask(slotId)} className="px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-80" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>{t('common.cancel')}</button>
              <button onClick={() => store.confirmTask(slotId)} className="px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors hover:opacity-90" style={{ background: 'var(--color-primary)' }}>{t('analyze.continue')}</button>
            </div>
          </div>
        </div>
      )}

      {/* Result */}
      {result && result.title && (
        <div className="space-y-6">
          <div className="p-5 rounded-xl" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
            <div className="flex gap-4">
              {result.thumbnail_url && (
                <img src={result.thumbnail_url} alt="" className="w-48 h-28 object-cover rounded-lg shrink-0" />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2 min-w-0">
                    <h2 className="text-lg font-semibold truncate">{result.title}</h2>
                    <a href={generatePlaybackUrl(result.platform, result.videoId, 0)} target="_blank" rel="noopener noreferrer" className="p-1.5 rounded-lg hover:opacity-70 transition-opacity shrink-0" style={{ color: 'var(--color-primary)' }} title={t('detail.playFromBeginning')}>
                      <Play size={16} />
                    </a>
                  </div>
                  {result.summary_en && <LangToggle lang={lang} onChange={setLang} className="shrink-0 ml-3" />}
                </div>
                <div className="flex items-center gap-3 text-sm mb-3" style={{ color: 'var(--color-text-secondary)' }}>
                  <span>{platformLabel(result.platform)}</span>
                  <span>{formatDuration(result.duration_seconds)}</span>
                  {result.upload_date && <span>{result.upload_date}</span>}
                </div>
                <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                  {lang === 'en' && result.summary_en ? result.summary_en : result.summary}
                </p>
              </div>
            </div>
            {result.usage && (
              <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--color-border)' }}>
                <UsageDisplay usage={result.usage} />
              </div>
            )}
            <div className="flex gap-2 mt-4 pt-4" style={{ borderTop: '1px solid var(--color-border)' }}>
              <button onClick={() => result.video_id && onNavigateVideo(result.video_id)} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium text-white" style={{ background: 'var(--color-primary)' }}>
                <Save size={14} /> {t('analyze.viewDetails')}
              </button>
              <button onClick={() => result.video_id && api.post(`/videos/${result.video_id}/share`)} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm" style={{ border: '1px solid var(--color-border)' }}>
                <Share2 size={14} /> {t('analyze.share')}
              </button>
            </div>
          </div>

          <div className="space-y-2">
            <h3 className="text-lg font-semibold">{t('analyze.segments')} ({result.segments.length})</h3>
            {result.segments.map((seg) => (
              <div key={seg.index} className="p-4 rounded-xl flex items-start gap-4 hover:shadow-sm transition-shadow" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
                <span className="text-xs font-mono px-2 py-1 rounded shrink-0" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}>
                  {formatTimeRange(seg.start_seconds, seg.end_seconds)}
                </span>
                <div className="flex-1 min-w-0">
                  <h4 className="font-medium text-sm mb-1">{lang === 'en' && seg.title_en ? seg.title_en : seg.title}</h4>
                  <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>{lang === 'en' && seg.summary_en ? seg.summary_en : seg.summary}</p>
                </div>
                <a href={generatePlaybackUrl(result.platform, result.videoId, seg.start_seconds)} target="_blank" rel="noopener noreferrer" className="p-2 rounded-lg hover:opacity-70 transition-opacity shrink-0" style={{ color: 'var(--color-primary)' }} title={t('detail.playFromHere')}>
                  <Play size={18} />
                </a>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}


export default function AnalyzePage() {
  const t = useT()
  const store = useAnalysisStore()

  const [activeTab, setActiveTab] = useState<AnalysisPlatform>('youtube')
  const [draftUrls, setDraftUrls] = useState<string[]>([''])
  const { lang, setLang } = useLangPreference()


  const [validationError, setValidationError] = useState('')

  const navigate = useNavigate()

  const { data: cookiesStatus } = useQuery({
    queryKey: ['youtube-cookies-status'],
    queryFn: () => api.get<CookiesStatus>('/youtube/cookies-status'),
    staleTime: 300_000,
    refetchInterval: 3_600_000,
    retry: false,
  })

  const { data: prefs } = useQuery({
    queryKey: ['user-preferences'],
    queryFn: () => api.get<PreferencesData>('/auth/preferences'),
    staleTime: 60_000,
  })

  const maxConcurrent = prefs?.max_concurrent_analyses ?? 3
  const allActiveSlots = store.getActiveSlots()
  const activeSlots = allActiveSlots.filter((s) => s.platform === activeTab)
  const analyzingCount = activeSlots.filter((s) => s.analyzing).length
  const availableSlotCount = Math.max(0, maxConcurrent - analyzingCount)

  const MAX_FAILED_DISPLAY = 3
  const runningOrDoneSlots = activeSlots.filter((s) => s.analyzing || (!s.error && s.result))
  const failedSlots = activeSlots.filter((s) => !s.analyzing && !!s.error)
  const displaySlots = [...runningOrDoneSlots, ...failedSlots.slice(0, MAX_FAILED_DISPLAY)]
  const visibleInputCount = Math.max(1, availableSlotCount)

  useEffect(() => {
    setDraftUrls((prev) => {
      const next = prev.slice(0, visibleInputCount)
      while (next.length < visibleInputCount) next.push('')
      return next
    })
  }, [visibleInputCount])

  useEffect(() => {
    store.reconnectActiveTasks()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleUrlChange = (index: number, value: string) => {
    setDraftUrls((prev) => prev.map((item, itemIndex) => (itemIndex === index ? value : item)))
    if (validationError) setValidationError('')
    const detected = detectPlatform(value)
    if (detected) setActiveTab(detected)
  }

  const validatePlatformMatch = (inputUrl: string): boolean => {
    const trimmed = inputUrl.trim()
    if (activeTab === 'xiaoyuzhou') {
      // Delegates to the pure validator so it stays unit-testable.
      const errKey = validateXiaoyuzhouUrl(trimmed)
      if (errKey) {
        setValidationError(t(errKey))
        return false
      }
    }
    const detected = detectPlatform(trimmed)
    if (!detected) {
      const msg =
        activeTab === 'youtube'
          ? t('analyze.invalidYoutube')
          : activeTab === 'bilibili'
            ? t('analyze.invalidBilibili')
            : t('analyze.invalidXiaoyuzhou')
      setValidationError(msg)
      return false
    }
    if (detected !== activeTab) {
      setValidationError(
        t('analyze.wrongPlatform', { current: platformLabel(activeTab), detected: platformLabel(detected) }),
      )
      return false
    }
    setValidationError('')
    return true
  }

  const handleAnalyze = async (index: number) => {
    const inputUrl = draftUrls[index]?.trim() || ''
    if (!validatePlatformMatch(inputUrl)) return
    if (analyzingCount >= maxConcurrent) {
      setValidationError(t('analyze.concurrentLimitFull', { limit: maxConcurrent }))
      return
    }
    await store.startAnalysis(activeTab, inputUrl)
    setDraftUrls((prev) => prev.map((item, itemIndex) => (itemIndex === index ? '' : item)))
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-1">{t('analyze.title')}</h1>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          {t('analyze.subtitle')}
          {maxConcurrent > 1 && (
            <span className="ml-2 opacity-60">
              ({t('analyze.running')}: {analyzingCount}/{maxConcurrent}, {t('analyze.available')}: {availableSlotCount})
            </span>
          )}
        </p>
      </div>

      {/* Platform Tabs */}
      <div className="flex gap-1 p-1 rounded-lg w-fit" style={{ background: 'var(--color-bg-tertiary)' }}>
        {(['youtube', 'bilibili', 'xiaoyuzhou'] as const).map((tab) => {
          const tabAnalyzing = allActiveSlots.some((s) => s.platform === tab && s.analyzing)
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn('px-4 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2', activeTab === tab ? 'shadow-sm' : 'opacity-60 hover:opacity-100')}
              style={activeTab === tab ? { background: 'var(--color-bg)', color: 'var(--color-primary)' } : {}}
            >
              {tab === 'xiaoyuzhou' && (
                <Headphones size={16} className="shrink-0 opacity-90" aria-hidden />
              )}
              {platformLabel(tab)}
              {tabAnalyzing && activeTab !== tab && (
                <Loader2 size={12} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
              )}
            </button>
          )
        })}
      </div>

      {/* Xiaoyuzhou tab scope hint */}
      {activeTab === 'xiaoyuzhou' && (
        <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          <Headphones size={13} className="shrink-0 opacity-70" aria-hidden />
          <span>{t('analyze.xiaoyuzhouTabHint')}</span>
        </div>
      )}

      {/* YouTube Cookies Status */}
      {activeTab === 'youtube' && cookiesStatus && (() => {
        const cs = cookiesStatus
        if (!cs.configured || !cs.file_exists) return (
          <div className="flex items-center gap-2 text-xs font-medium" style={{ color: 'var(--color-danger)' }}>
            <Cookie size={13} />
            <span>{!cs.configured ? t('analyze.cookiesNotConfigured') : t('analyze.cookiesFileMissing')}</span>
          </div>
        )
        if (cs.expired) {
          const expiryDate = cs.earliest_expiry
            ? new Date(cs.earliest_expiry).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US', lang === 'zh' ? { timeZone: 'Asia/Shanghai' } : undefined)
            : '?'
          return (
            <div className="flex items-center gap-2 text-xs font-medium" style={{ color: 'var(--color-danger)' }}>
              <Cookie size={13} />
              <span>{t('analyze.cookiesExpiredWithDate', { date: expiryDate })}</span>
            </div>
          )
        }
        if (cs.usability_checked && cs.usable === false) {
          return (
            <div className="flex items-center gap-2 text-xs font-medium" style={{ color: 'var(--color-danger)' }}>
              <Cookie size={13} />
              <span>{cs.usability_message || t('analyze.cookiesNotUsable')}</span>
            </div>
          )
        }
        if (cs.usability_checked && cs.usable === null) {
          return (
            <div className="flex items-center gap-2 text-xs font-medium" style={{ color: 'var(--color-warning, #f59e0b)' }}>
              <Cookie size={13} />
              <span>{cs.usability_message || t('analyze.cookiesInconclusive')}</span>
            </div>
          )
        }
        if (cs.earliest_expiry) {
          const expiryDate = new Date(cs.earliest_expiry).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US', lang === 'zh' ? { timeZone: 'Asia/Shanghai' } : undefined)
          const daysLeft = cs.earliest_expiry_ts
            ? Math.floor((cs.earliest_expiry_ts - Date.now() / 1000) / 86400)
            : null
          const isExpiringSoon = daysLeft !== null && daysLeft <= 7
          return (
            <div className="flex items-center gap-2 text-xs" style={{ color: isExpiringSoon ? 'var(--color-warning, #f59e0b)' : 'var(--color-text-secondary)' }}>
              <Cookie size={13} />
              <span>
                {t('analyze.cookiesValid')} {expiryDate}
                {daysLeft !== null && <span className="ml-1">（{t('analyze.cookiesRemaining', { days: daysLeft })}）</span>}
                {cs.cookie_count > 0 && <span className="ml-1 opacity-60">{t('analyze.cookieCount', { count: cs.cookie_count })}</span>}
              </span>
            </div>
          )
        }
        return null
      })()}

      {/* URL Inputs */}
      <div className="space-y-3">
        {draftUrls.map((draftUrl, index) => (
          <div key={`input-${index}`} className="flex gap-3">
            <div className="flex-1 relative">
              <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40" />
              <input
                type="text"
                value={draftUrl}
                onChange={(e) => handleUrlChange(index, e.target.value)}
                placeholder={
                  activeTab === 'youtube'
                    ? t('analyze.placeholderYoutube')
                    : activeTab === 'bilibili'
                      ? t('analyze.placeholderBilibili')
                      : t('analyze.placeholderXiaoyuzhou')
                }
                className="w-full pl-10 pr-4 py-3 rounded-xl text-sm outline-none transition-all"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
              />
            </div>
            <button
              onClick={() => void handleAnalyze(index)}
              disabled={!draftUrl.trim() || analyzingCount >= maxConcurrent}
              className="px-6 py-3 rounded-xl text-sm font-medium text-white transition-colors disabled:opacity-40"
              style={{ background: 'var(--color-primary)' }}
            >
              {t('analyze.analyze')}
            </button>
          </div>
        ))}
      </div>

      {validationError && (
        <div className="p-4 rounded-xl flex items-start gap-3" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-danger)' }}>
          <AlertCircle size={18} style={{ color: 'var(--color-danger)' }} className="mt-0.5 shrink-0" />
          <p className="text-sm" style={{ color: 'var(--color-danger)' }}>{validationError}</p>
        </div>
      )}

      {/* Active Analysis Slots */}
      {displaySlots.length > 0 && (
        <div className="space-y-6">
          {displaySlots.map((slot) => (
            <AnalysisSlotCard
              key={slot.slotId}
              slot={slot}
              lang={lang}
              setLang={setLang}
              onNavigateVideo={(id) => navigate(`/video/${id}`)}
              canRetry={analyzingCount < maxConcurrent}
              onRetry={slot.taskId ? () => {
                store.dismissSlot(slot.slotId)
                void store.retryTask(slot.platform, slot.taskId!, slot.url, slot.status)
              } : undefined}
            />
          ))}
          {failedSlots.length > MAX_FAILED_DISPLAY && (
            <p className="text-xs text-center" style={{ color: 'var(--color-text-secondary)' }}>
              {t('analyze.moreFailed', { count: failedSlots.length - MAX_FAILED_DISPLAY })}
            </p>
          )}
        </div>
      )}

    </div>
  )
}
