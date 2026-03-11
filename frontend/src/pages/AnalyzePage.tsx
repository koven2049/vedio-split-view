import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, Download, Play, Save, Share2, Loader2, AlertCircle, Trash2, Copy, CheckCircle2, FileAudio, TriangleAlert, Cookie } from 'lucide-react'
import { createSSE, api } from '../lib/api'
import { formatDuration, formatTimeRange, generatePlaybackUrl, platformLabel, cn } from '../lib/utils'
import { useAnalysisStore } from '../stores/analysisStore'
import LangToggle from '../components/LangToggle'
import UsageDisplay from '../components/UsageDisplay'
import { useLangPreference } from '../hooks/useLangPreference'

interface ChunkInfo {
  index: number; filename: string; path: string
  size_bytes: number; duration_seconds: number
}

interface DownloadResult {
  task_id: number; platform: string; title: string
  duration_seconds: number; audio_path: string
  audio_size_bytes: number; chunks: ChunkInfo[]
  chunk_duration_config: number
  quota_used: number; quota_max: number
}

interface CookiesStatus {
  configured: boolean
  file_exists: boolean
  earliest_expiry: string | null
  earliest_expiry_ts: number | null
  expired: boolean
  cookie_count: number
  domain_summary: string
}

const STAGES = ['metadata', 'subtitle_check', 'audio_download', 'transcription', 'analysis', 'complete']

const STAGE_LABELS: Record<string, string> = {
  metadata: 'Video Info',
  confirm_required: 'Confirmation',
  subtitle_check: 'Subtitle Check',
  audio_download: 'Audio Download',
  transcription: 'Transcription',
  analysis: 'AI Analysis',
  complete: 'Done',
}

function detectPlatform(url: string): 'youtube' | 'bilibili' | null {
  if (/youtube\.com|youtu\.be/.test(url)) return 'youtube'
  if (/bilibili\.com|b23\.tv/.test(url)) return 'bilibili'
  return null
}

export default function AnalyzePage() {
  const store = useAnalysisStore()

  const [activeTab, setActiveTab] = useState<'youtube' | 'bilibili'>('youtube')

  const slot = store.getSlot(activeTab)
  const { analyzing, progress, stepLog, result, error, completedVideoId, pendingConfirm } = slot

  const [url, setUrl] = useState(() => slot.url || '')
  const { lang, setLang } = useLangPreference()

  // Download-only local state
  const [downloading, setDownloading] = useState(false)
  const [downloadStatus, setDownloadStatus] = useState('')
  const [downloadProgress, setDownloadProgress] = useState(0)
  const [downloadElapsed, setDownloadElapsed] = useState(0)
  const [downloadEta, setDownloadEta] = useState('')
  const [downloadResult, setDownloadResult] = useState<DownloadResult | null>(null)
  const [, setDownloadSse] = useState<{ cancel: () => void } | null>(null)
  const [cleaning, setCleaning] = useState(false)
  const [confirmCleanup, setConfirmCleanup] = useState<number | null>(null)
  const [copiedPath, setCopiedPath] = useState('')
  const [downloadError, setDownloadError] = useState('')
  const [dlConfirm, setDlConfirm] = useState<{ taskId: number; title: string; durationSeconds: number; message: string } | null>(null)

  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: cookiesStatus } = useQuery({
    queryKey: ['youtube-cookies-status'],
    queryFn: () => api.get<CookiesStatus>('/youtube/cookies-status'),
    staleTime: 300_000,
    refetchInterval: 3_600_000,
    retry: false,
  })

  // Sync URL input when switching tabs or navigating back
  useEffect(() => {
    const tabSlot = store.getSlot(activeTab)
    if (tabSlot.url) {
      setUrl(tabSlot.url)
    } else {
      setUrl('')
    }
  }, [activeTab]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load existing downloaded task on mount
  useEffect(() => {
    api.get<Array<{
      task_id: number; url: string; platform: string; status: string
      title: string | null; audio_path: string | null; audio_size_mb: number | null
      chunks: ChunkInfo[] | null
    }>>('/debug/tasks').then((tasks) => {
      const downloaded = tasks.find((t) => t.status === 'downloaded')
      if (downloaded && downloaded.audio_path) {
        setDownloadResult({
          task_id: downloaded.task_id,
          platform: downloaded.platform,
          title: downloaded.title ?? 'Unknown',
          duration_seconds: 0,
          audio_path: downloaded.audio_path,
          audio_size_bytes: (downloaded.audio_size_mb ?? 0) * 1024 * 1024,
          chunks: downloaded.chunks ?? [],
          chunk_duration_config: 0,
          quota_used: tasks.length,
          quota_max: 3,
        })
      }
    }).catch(() => {})
  }, [])

  // Handle analysis completion: invalidate queries + fetch details
  useEffect(() => {
    if (completedVideoId) {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      store.fetchResultDetails(activeTab, completedVideoId)
    }
  }, [completedVideoId, queryClient, activeTab]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleUrlChange = (value: string) => {
    setUrl(value)
    if (downloadError) setDownloadError('')
    const detected = detectPlatform(value)
    if (detected) setActiveTab(detected)
  }

  const validatePlatformMatch = (): boolean => {
    const detected = detectPlatform(url)
    if (!detected) {
      setDownloadError(activeTab === 'youtube'
        ? '请输入有效的 YouTube 链接 (youtube.com / youtu.be)'
        : '请输入有效的 Bilibili 链接 (bilibili.com / b23.tv)')
      return false
    }
    if (detected !== activeTab) {
      const hint = detected === 'youtube' ? 'YouTube' : 'Bilibili'
      setDownloadError(`当前在 ${platformLabel(activeTab)} 标签页，但链接属于 ${hint}，请切换到对应标签页`)
      return false
    }
    setDownloadError('')
    return true
  }

  const handleAnalyze = () => {
    if (!validatePlatformMatch()) return
    store.startAnalysis(activeTab, url)
  }

  const handleAnalyzeDownloaded = () => {
    if (!downloadResult) return
    const platform = downloadResult.platform as 'youtube' | 'bilibili'
    setDownloadResult(null)
    store.startTaskRetry(platform, downloadResult.task_id)
  }

  const handleDownloadOnly = () => {
    if (!validatePlatformMatch()) return
    setDownloading(true)
    setDownloadResult(null)
    setDownloadProgress(0)
    setDownloadEta('')
    setDownloadStatus('Fetching video metadata...')
    setDownloadElapsed(0)

    const startTime = Date.now()
    const timer = setInterval(() => setDownloadElapsed(Math.floor((Date.now() - startTime) / 1000)), 1000)

    const samples: Array<{ time: number; pct: number }> = []

    const sse = createSSE('/debug/download', { url }, (_event, raw) => {
      const d = raw as { stage: string; progress: number; message: string; detail?: Record<string, unknown> }

      if (d.stage === 'error') {
        clearInterval(timer)
        setDownloading(false)
        setDownloadStatus('')
        setDownloadError(d.message)
        return
      }

      if (d.stage === 'confirm_required') {
        setDlConfirm({
          taskId: d.detail?.task_id as number,
          title: (d.detail?.title as string) ?? '',
          durationSeconds: (d.detail?.duration_seconds as number) ?? 0,
          message: d.message,
        })
        setDownloadStatus('Waiting for confirmation...')
        return
      }

      setDownloadProgress(d.progress)
      setDownloadStatus(d.message)

      if (d.stage === 'downloading' && d.detail?.download_percent != null) {
        const pct = d.detail.download_percent as number
        const now = Date.now()
        samples.push({ time: now, pct })
        if (samples.length >= 3) {
          const recent = samples.slice(-6)
          const dt = (recent[recent.length - 1].time - recent[0].time) / 1000
          const dp = recent[recent.length - 1].pct - recent[0].pct
          if (dt > 0 && dp > 0) {
            const speed = dp / dt
            const remaining = (100 - pct) / speed
            if (remaining < 60) {
              setDownloadEta(`~${Math.ceil(remaining)}s remaining`)
            } else {
              setDownloadEta(`~${Math.ceil(remaining / 60)}min remaining`)
            }
          }
        }
      }

      if (d.stage === 'splitting') {
        setDownloadEta('')
      }

      if (d.stage === 'complete' && d.detail) {
        clearInterval(timer)
        setDownloading(false)
        setDownloadStatus('')
        setDownloadEta('')
        setDownloadResult({
          task_id: d.detail.task_id as number,
          platform: d.detail.platform as string,
          title: d.detail.title as string,
          duration_seconds: d.detail.duration_seconds as number,
          audio_path: d.detail.audio_path as string,
          audio_size_bytes: d.detail.audio_size_bytes as number,
          chunks: d.detail.chunks as ChunkInfo[],
          chunk_duration_config: d.detail.chunk_duration_config as number,
          quota_used: d.detail.quota_used as number,
          quota_max: d.detail.quota_max as number,
        })
        queryClient.invalidateQueries({ queryKey: ['tasks'] })
      }
    })
    setDownloadSse(sse)
  }

  const handleCleanup = async (taskId: number) => {
    setCleaning(true)
    setConfirmCleanup(null)
    try {
      await api.delete(`/debug/tasks/${taskId}`)
      setDownloadResult(null)
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    } catch (e: unknown) {
      setDownloadError(e instanceof Error ? e.message : 'Cleanup failed')
    } finally {
      setCleaning(false)
    }
  }

  const handleDlConfirm = () => {
    if (!dlConfirm) return
    api.post(`/videos/tasks/${dlConfirm.taskId}/confirm`).catch(() => {})
    setDlConfirm(null)
  }

  const handleDlDecline = () => {
    if (!dlConfirm) return
    api.delete(`/videos/tasks/${dlConfirm.taskId}/cancel`).catch(() => {})
    setDlConfirm(null)
    setDownloading(false)
    setDownloadStatus('')
    setDownloadProgress(0)
  }

  const copyPath = (path: string) => {
    navigator.clipboard.writeText(path)
    setCopiedPath(path)
    setTimeout(() => setCopiedPath(''), 2000)
  }

  const currentStageIndex = progress ? STAGES.indexOf(progress.stage) : -1

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-1">Analyze Video</h1>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          Paste a video URL to extract topics and segments
        </p>
      </div>

      {/* Platform Tabs */}
      <div className="flex gap-1 p-1 rounded-lg w-fit" style={{ background: 'var(--color-bg-tertiary)' }}>
        {(['youtube', 'bilibili'] as const).map((tab) => {
          const tabSlot = store.getSlot(tab)
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn('px-4 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2', activeTab === tab ? 'shadow-sm' : 'opacity-60 hover:opacity-100')}
              style={activeTab === tab ? { background: 'var(--color-bg)', color: 'var(--color-primary)' } : {}}
            >
              {platformLabel(tab)}
              {tabSlot.analyzing && activeTab !== tab && (
                <Loader2 size={12} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
              )}
            </button>
          )
        })}
      </div>

      {/* YouTube Cookies Status */}
      {activeTab === 'youtube' && cookiesStatus && (() => {
        const cs = cookiesStatus
        if (!cs.configured || !cs.file_exists) return (
          <div className="flex items-center gap-2 text-xs font-medium" style={{ color: 'var(--color-danger)' }}>
            <Cookie size={13} />
            <span>{!cs.configured
              ? 'YouTube cookies 未配置 — 部分视频可能无法下载'
              : 'Cookies 文件不存在: 请检查 config/youtube_cookies.txt'}
            </span>
          </div>
        )
        if (cs.expired) {
          const expiryDate = cs.earliest_expiry ? new Date(cs.earliest_expiry).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '?'
          return (
            <div className="flex items-center gap-2 text-xs font-medium" style={{ color: 'var(--color-danger)' }}>
              <Cookie size={13} />
              <span>YouTube cookies 已过期 ({expiryDate}) — 请重新导出</span>
            </div>
          )
        }
        if (cs.earliest_expiry) {
          const expiryDate = new Date(cs.earliest_expiry).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
          const daysLeft = cs.earliest_expiry_ts
            ? Math.floor((cs.earliest_expiry_ts - Date.now() / 1000) / 86400)
            : null
          const isExpiringSoon = daysLeft !== null && daysLeft <= 7
          return (
            <div className="flex items-center gap-2 text-xs" style={{ color: isExpiringSoon ? 'var(--color-warning, #f59e0b)' : 'var(--color-text-secondary)' }}>
              <Cookie size={13} />
              <span>
                Cookies 有效 · 过期时间 {expiryDate}
                {daysLeft !== null && <span className="ml-1">（剩余 {daysLeft} 天）</span>}
                {cs.cookie_count > 0 && <span className="ml-1 opacity-60">· {cs.cookie_count} cookies</span>}
              </span>
            </div>
          )
        }
        return null
      })()}

      {/* URL Input */}
      <div className="flex gap-3">
        <div className="flex-1 relative">
          <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40" />
          <input
            type="text"
            value={url}
            onChange={(e) => handleUrlChange(e.target.value)}
            placeholder={activeTab === 'youtube' ? 'https://www.youtube.com/watch?v=...' : 'https://www.bilibili.com/video/BV...'}
            disabled={analyzing || downloading}
            className="w-full pl-10 pr-4 py-3 rounded-xl text-sm outline-none transition-all"
            style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
          />
        </div>
        <button
          onClick={handleAnalyze}
          disabled={analyzing || downloading || !url.trim()}
          className="px-6 py-3 rounded-xl text-sm font-medium text-white transition-colors disabled:opacity-40"
          style={{ background: 'var(--color-primary)' }}
        >
          {analyzing ? <Loader2 size={18} className="animate-spin" /> : 'Analyze'}
        </button>
        <button
          onClick={handleDownloadOnly}
          disabled={analyzing || downloading || !url.trim()}
          className="px-4 py-3 rounded-xl text-sm font-medium transition-colors disabled:opacity-40 flex items-center gap-2"
          style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
          title="Download audio only (for testing)"
        >
          {downloading ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
          <span className="hidden sm:inline">Download Only</span>
        </button>
      </div>

      {/* Download Progress */}
      {downloading && (
        <div className="rounded-xl overflow-hidden" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
          <div className="px-5 py-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Loader2 size={16} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
                <span className="text-sm font-medium">{downloadStatus}</span>
              </div>
              <div className="flex items-center gap-3 text-xs tabular-nums font-mono" style={{ color: 'var(--color-text-secondary)' }}>
                {downloadEta && <span style={{ color: 'var(--color-primary)' }}>{downloadEta}</span>}
                <span>{downloadElapsed}s</span>
              </div>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div
                className="h-full rounded-full transition-all duration-500 ease-out"
                style={{ background: 'var(--color-primary)', width: `${downloadProgress}%` }}
              />
            </div>
            <div className="flex items-center justify-between">
              <div className="flex gap-1.5 items-center">
                {([
                  { label: 'Metadata', active: downloadProgress >= 5 },
                  { label: 'Download', active: downloadProgress >= 12 },
                  { label: 'Split', active: downloadProgress >= 85 },
                ]).map(({ label, active }) => (
                  <div key={label} className="flex items-center gap-1.5">
                    <div className="w-2 h-2 rounded-full" style={{
                      background: active ? 'var(--color-primary)' : 'var(--color-bg-tertiary)',
                      transition: 'background 0.3s',
                    }} />
                    <span className="text-[10px] pr-2" style={{
                      color: active ? 'var(--color-primary)' : 'var(--color-text-secondary)',
                      fontWeight: active ? 600 : 400,
                    }}>{label}</span>
                  </div>
                ))}
              </div>
              {downloadProgress > 0 && (
                <span className="text-[11px] font-medium tabular-nums" style={{ color: 'var(--color-primary)' }}>
                  {downloadProgress}%
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Analysis Progress */}
      {(analyzing || stepLog.length > 0) && progress && (
        <div className="rounded-xl overflow-hidden" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
          {/* Header with progress bar */}
          <div className="px-5 pt-4 pb-3 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {analyzing && <Loader2 size={16} className="animate-spin" style={{ color: 'var(--color-primary)' }} />}
                <span className="text-sm font-medium">{progress.message}</span>
              </div>
              {analyzing && (
                <button onClick={() => store.cancelAnalysis(activeTab)} className="px-3 py-1 rounded-lg text-xs font-medium transition-opacity hover:opacity-70" style={{ border: '1px solid var(--color-danger)', color: 'var(--color-danger)' }}>
                  Cancel
                </button>
              )}
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="h-full rounded-full transition-all duration-500" style={{ width: `${progress.progress}%`, background: 'var(--color-primary)' }} />
            </div>
            {/* Stage dots */}
            <div className="flex gap-1.5 items-center">
              {STAGES.slice(0, -1).map((stage, i) => {
                const isCurrent = stage === progress.stage
                const isDone = i < currentStageIndex
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
                      {(isSkipped || isSkippedTranscript) ? <s>{STAGE_LABELS[stage]}</s> : STAGE_LABELS[stage]}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Method Badge */}
          {stepLog.some((s) => s.detail?.method) && (
            <div className="px-5 pb-2">
              {stepLog.filter((s) => s.detail?.method).slice(-1).map((s, i) => (
                <span key={i} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium"
                  style={{
                    background: s.detail?.method === 'subtitle' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(59, 130, 246, 0.1)',
                    color: s.detail?.method === 'subtitle' ? 'rgb(16, 185, 129)' : 'rgb(59, 130, 246)',
                  }}>
                  {s.detail?.method === 'subtitle' ? '📝 Using subtitles (fast, free)' : '🎤 Using Whisper transcription (slower, costs API credits)'}
                </span>
              ))}
            </div>
          )}

          {/* Step Timeline */}
          {stepLog.length > 0 && (
            <div className="px-5 pb-4">
              <div className="max-h-48 overflow-y-auto space-y-0.5 rounded-lg p-3" style={{ background: 'var(--color-bg-tertiary)' }}>
                {stepLog.map((entry, i) => (
                  <div key={i} className="flex items-start gap-2 py-1 text-xs" style={{ color: entry.stage === 'error' ? 'var(--color-danger)' : 'var(--color-text-secondary)' }}>
                    <span className="shrink-0 opacity-50 font-mono tabular-nums" style={{ minWidth: '52px' }}>
                      {new Date(entry.timestamp).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                    <span className="shrink-0 font-medium uppercase tracking-wider" style={{ minWidth: '90px', color: entry.stage === 'error' ? 'var(--color-danger)' : 'var(--color-primary)', fontSize: '10px', lineHeight: '16px' }}>
                      {STAGE_LABELS[entry.stage] ?? entry.stage}
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

      {/* Download Only Result */}
      {downloadResult && (
        <div className="rounded-xl overflow-hidden" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
          <div className="px-5 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <div className="flex items-center gap-3">
              <FileAudio size={20} style={{ color: 'var(--color-primary)' }} />
              <div>
                <h3 className="text-sm font-semibold">{downloadResult.title}</h3>
                <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {platformLabel(downloadResult.platform)} · {formatDuration(downloadResult.duration_seconds)} · {(downloadResult.audio_size_bytes / 1024 / 1024).toFixed(1)} MB · Quota {downloadResult.quota_used}/{downloadResult.quota_max}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleAnalyzeDownloaded}
                disabled={analyzing || cleaning}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
                style={{ background: 'var(--color-primary)' }}
              >
                <Play size={13} /> Analyze
              </button>
              <button
                onClick={() => setConfirmCleanup(downloadResult.task_id)}
                disabled={cleaning || analyzing}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-opacity hover:opacity-70 disabled:opacity-40"
                style={{ border: '1px solid var(--color-danger)', color: 'var(--color-danger)' }}
              >
                {cleaning ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                {cleaning ? 'Deleting...' : 'Cleanup'}
              </button>
            </div>
          </div>

          {/* Audio path */}
          <div className="px-5 py-3 space-y-2">
            <div className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--color-text-secondary)' }}>Audio File</div>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs px-3 py-2 rounded-lg overflow-x-auto" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}>
                {downloadResult.audio_path}
              </code>
              <button onClick={() => copyPath(downloadResult.audio_path)} className="p-1.5 rounded-lg hover:opacity-70 transition-opacity shrink-0" title="Copy path">
                {copiedPath === downloadResult.audio_path ? <CheckCircle2 size={14} style={{ color: 'rgb(16, 185, 129)' }} /> : <Copy size={14} style={{ color: 'var(--color-text-secondary)' }} />}
              </button>
            </div>
          </div>

          {/* Chunks */}
          {downloadResult.chunks.length > 0 && (
            <div className="px-5 pb-4 space-y-2">
              <div className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--color-text-secondary)' }}>
                Chunks ({downloadResult.chunks.length}){downloadResult.chunk_duration_config > 0 && ` · ${downloadResult.chunk_duration_config}s each`}
              </div>
              <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--color-border)' }}>
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ background: 'var(--color-bg-tertiary)' }}>
                      <th className="text-left px-3 py-2 font-medium" style={{ color: 'var(--color-text-secondary)' }}>#</th>
                      <th className="text-left px-3 py-2 font-medium" style={{ color: 'var(--color-text-secondary)' }}>File</th>
                      <th className="text-right px-3 py-2 font-medium" style={{ color: 'var(--color-text-secondary)' }}>Duration</th>
                      <th className="text-right px-3 py-2 font-medium" style={{ color: 'var(--color-text-secondary)' }}>Size</th>
                      <th className="px-3 py-2 w-8"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {downloadResult.chunks.map((chunk) => (
                      <tr key={chunk.index} className="hover:opacity-80" style={{ borderTop: '1px solid var(--color-border)' }}>
                        <td className="px-3 py-2 font-mono" style={{ color: 'var(--color-text-secondary)' }}>{chunk.index}</td>
                        <td className="px-3 py-2">
                          <code style={{ color: 'var(--color-text)' }}>{chunk.filename}</code>
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--color-text-secondary)' }}>
                          {chunk.duration_seconds.toFixed(1)}s
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--color-text-secondary)' }}>
                          {(chunk.size_bytes / 1024 / 1024).toFixed(2)} MB
                        </td>
                        <td className="px-3 py-2">
                          <button onClick={() => copyPath(chunk.path)} className="p-1 rounded hover:opacity-70 transition-opacity" title="Copy path">
                            {copiedPath === chunk.path ? <CheckCircle2 size={12} style={{ color: 'rgb(16, 185, 129)' }} /> : <Copy size={12} style={{ color: 'var(--color-text-secondary)' }} />}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {(error || downloadError) && (
        <div className="p-4 rounded-xl flex items-start gap-3" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-danger)' }}>
          <AlertCircle size={18} style={{ color: 'var(--color-danger)' }} className="mt-0.5 shrink-0" />
          <p className="text-sm">{error || downloadError}</p>
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
                    <a
                      href={generatePlaybackUrl(result.platform, result.videoId, 0)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="p-1.5 rounded-lg hover:opacity-70 transition-opacity shrink-0"
                      style={{ color: 'var(--color-primary)' }}
                      title="Play from beginning"
                    >
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
              <button onClick={() => result.video_id && navigate(`/video/${result.video_id}`)} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium text-white" style={{ background: 'var(--color-primary)' }}>
                <Save size={14} /> View Details
              </button>
              <button onClick={() => result.video_id && api.post(`/videos/${result.video_id}/share`)} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm" style={{ border: '1px solid var(--color-border)' }}>
                <Share2 size={14} /> Share
              </button>
            </div>
          </div>

          {/* Segments */}
          <div className="space-y-2">
            <h3 className="text-lg font-semibold">Segments ({result.segments.length})</h3>
            {result.segments.map((seg) => (
              <div key={seg.index} className="p-4 rounded-xl flex items-start gap-4 hover:shadow-sm transition-shadow"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
                <span className="text-xs font-mono px-2 py-1 rounded shrink-0" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}>
                  {formatTimeRange(seg.start_seconds, seg.end_seconds)}
                </span>
                <div className="flex-1 min-w-0">
                  <h4 className="font-medium text-sm mb-1">
                    {lang === 'en' && seg.title_en ? seg.title_en : seg.title}
                  </h4>
                  <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                    {lang === 'en' && seg.summary_en ? seg.summary_en : seg.summary}
                  </p>
                </div>
                <a
                  href={generatePlaybackUrl(result.platform, result.videoId, seg.start_seconds)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="p-2 rounded-lg hover:opacity-70 transition-opacity shrink-0"
                  style={{ color: 'var(--color-primary)' }}
                  title="Play from here"
                >
                  <Play size={18} />
                </a>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Confirm Duration Modal */}
      {pendingConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
          <div
            className="relative w-full max-w-sm mx-4 rounded-2xl shadow-2xl p-6 space-y-4"
            style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-full" style={{ background: 'rgba(245, 158, 11, 0.1)' }}>
                <AlertCircle size={20} style={{ color: 'var(--color-warning, #f59e0b)' }} />
              </div>
              <h3 className="text-base font-semibold">Video Duration Warning</h3>
            </div>
            <div className="space-y-2">
              {pendingConfirm.title && (
                <p className="text-sm font-medium">{pendingConfirm.title}</p>
              )}
              <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                Video duration is <strong>{formatDuration(pendingConfirm.durationSeconds)}</strong>.
                Processing may take a while and consume API credits. Continue?
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => store.declineTask(activeTab)}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-80"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
              >
                Cancel
              </button>
              <button
                onClick={() => store.confirmTask(activeTab)}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors hover:opacity-90"
                style={{ background: 'var(--color-primary)' }}
              >
                Continue
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Download Duration Confirm Modal */}
      {dlConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
          <div
            className="relative w-full max-w-sm mx-4 rounded-2xl shadow-2xl p-6 space-y-4"
            style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-full" style={{ background: 'rgba(245, 158, 11, 0.1)' }}>
                <AlertCircle size={20} style={{ color: 'var(--color-warning, #f59e0b)' }} />
              </div>
              <h3 className="text-base font-semibold">Video Duration Warning</h3>
            </div>
            <div className="space-y-2">
              {dlConfirm.title && (
                <p className="text-sm font-medium">{dlConfirm.title}</p>
              )}
              <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                Video duration is <strong>{formatDuration(dlConfirm.durationSeconds)}</strong>.
                Downloading may take a while. Continue?
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={handleDlDecline}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-80"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
              >
                Cancel
              </button>
              <button
                onClick={handleDlConfirm}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors hover:opacity-90"
                style={{ background: 'var(--color-primary)' }}
              >
                Continue
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Cleanup Modal */}
      {confirmCleanup !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setConfirmCleanup(null)}>
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
          <div
            className="relative w-full max-w-sm mx-4 rounded-2xl shadow-2xl p-6 space-y-4 animate-in fade-in zoom-in-95"
            style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-full" style={{ background: 'rgba(239, 68, 68, 0.1)' }}>
                <TriangleAlert size={20} style={{ color: 'var(--color-danger)' }} />
              </div>
              <h3 className="text-base font-semibold">Confirm Cleanup</h3>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
              This will permanently delete the task and all associated audio files. This action cannot be undone.
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setConfirmCleanup(null)}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-80"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
              >
                Cancel
              </button>
              <button
                onClick={() => handleCleanup(confirmCleanup)}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors hover:opacity-90"
                style={{ background: 'var(--color-danger)' }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
