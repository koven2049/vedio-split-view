import { useState, useEffect, type ButtonHTMLAttributes } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { User, Link2, Loader2, CheckCircle, RefreshCw, X, Key, Copy, Check, Plus, Trash2, Shield, AlertTriangle, Cookie, SlidersHorizontal, Save, BarChart3, type LucideIcon } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import { useAuthStore } from '../stores/authStore'

interface BiliStatus { connected: boolean; bilibili_username: string; expired: boolean }
interface QRData { qr_key: string; qr_url: string; qr_image_base64: string }
interface TaskItem { id: number; url: string; status: string; video_title: string; error_message: string }
interface ApiTokenItem { id: number; name: string; key_prefix: string; is_active: boolean; last_used_at: string | null; created_at: string }
interface ApiTokenCreated extends ApiTokenItem { full_key: string }
interface AdminCleanupSummary { orphan_exports: number; orphan_thumbnails: number; orphan_task_dirs: number; total_items: number }
interface AdminCleanupResult extends AdminCleanupSummary { removed_exports: number; removed_thumbnails: number; removed_task_dirs: number; removed_total: number; errors: string[] }
interface YoutubeCookiesStatus {
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

const TOKEN_ENDPOINT = '/settings/tokens'
const LEGACY_TOKEN_ENDPOINT = '/settings/api-keys'
const TOKEN_HEADER_EXAMPLE = 'X-API-Key: <your-token>'

async function withTokenEndpoint<T>(request: (path: string) => Promise<T>): Promise<T> {
  try {
    return await request(TOKEN_ENDPOINT)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return request(LEGACY_TOKEN_ENDPOINT)
    }
    throw error
  }
}

export default function SettingsPage() {
  const { username, role } = useAuthStore()
  const queryClient = useQueryClient()

  const biliQuery = useQuery({
    queryKey: ['bilibili-status'],
    queryFn: () => api.get<BiliStatus>('/bilibili/status'),
  })

  const youtubeCookiesQuery = useQuery({
    queryKey: ['youtube-cookies-status'],
    queryFn: () => api.get<YoutubeCookiesStatus>('/youtube/cookies-status'),
    staleTime: 300_000,
    refetchInterval: 3_600_000,
    retry: false,
  })

  const tasksQuery = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<TaskItem[]>('/videos/tasks'),
  })

  const [showQR, setShowQR] = useState(false)
  const [qrData, setQrData] = useState<QRData | null>(null)
  const [polling, setPolling] = useState(false)

  const generateQR = useMutation({
    mutationFn: () => api.post<QRData>('/bilibili/qr/generate'),
    onSuccess: (data) => { setQrData(data); setShowQR(true); setPolling(true) },
  })

  useEffect(() => {
    if (!polling || !qrData) return
    const interval = setInterval(async () => {
      try {
        const result = await api.get<{ status: string }>(`/bilibili/qr/poll/${qrData.qr_key}`)
        if (result.status === 'confirmed') {
          setPolling(false)
          setShowQR(false)
          queryClient.invalidateQueries({ queryKey: ['bilibili-status'] })
        } else if (result.status === 'expired') {
          setPolling(false)
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(interval)
  }, [polling, qrData, queryClient])

  const disconnectMutation = useMutation({
    mutationFn: () => api.delete('/bilibili/disconnect'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['bilibili-status'] }),
  })

  const discardTaskMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/videos/tasks/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tasks'] }),
  })

  const biliStatus = biliQuery.data
  const tasks = tasksQuery.data?.filter((t) => t.status.startsWith('failed')) || []

  return (
    <div className="space-y-8 max-w-4xl">
      <h1 className="text-2xl font-bold">Settings</h1>

      {/* Profile */}
      <section className="p-5 rounded-xl space-y-3" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <h2 className="font-semibold flex items-center gap-2"><User size={18} /> Profile</h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>Username: <strong>{username}</strong></p>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>Role: <strong>{role}</strong></p>
      </section>

      <YoutubeCookiesSection
        status={youtubeCookiesQuery.data}
        isLoading={youtubeCookiesQuery.isPending}
        isRefreshing={youtubeCookiesQuery.isFetching}
        error={youtubeCookiesQuery.error as Error | null}
        onRefresh={() => { void youtubeCookiesQuery.refetch() }}
      />

      {role === 'admin' && <AdminCleanupSection />}

      {role !== 'user' ? null : (
        <>
      {/* Bilibili Connection */}
      <section className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1 flex-1 min-w-0">
            <h2 className="font-semibold flex items-center gap-2"><Link2 size={18} /> Bilibili Account</h2>
            <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              Connect your Bilibili account to directly fetch subtitles (faster analysis, no audio download needed).
            </p>
          </div>
          {biliStatus?.connected ? (
            <SectionActionButton
              type="button"
              onClick={() => disconnectMutation.mutate()}
              disabled={disconnectMutation.isPending}
              icon={disconnectMutation.isPending ? Loader2 : Link2}
              spinning={disconnectMutation.isPending}
              label={disconnectMutation.isPending ? 'Disconnecting...' : 'Disconnect'}
              variant="outline"
              danger
            />
          ) : (
            <SectionActionButton
              type="button"
              onClick={() => generateQR.mutate()}
              disabled={generateQR.isPending}
              icon={generateQR.isPending ? Loader2 : Link2}
              spinning={generateQR.isPending}
              label={generateQR.isPending ? 'Connecting...' : 'Connect Bilibili'}
              variant="primary"
            />
          )}
        </div>

        {biliStatus?.connected ? (
          <div className="flex items-center gap-2 p-3 rounded-lg text-sm" style={{ background: 'var(--color-bg-tertiary)' }}>
            <CheckCircle size={16} style={{ color: 'var(--color-success)' }} />
            <span className="font-medium">Connected{biliStatus.bilibili_username ? ` — @${biliStatus.bilibili_username}` : ''}</span>
          </div>
        ) : (
          <div className="p-3 rounded-lg text-sm" style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}>
            Not connected yet. Connect your Bilibili account before importing videos that rely on subtitle access.
          </div>
        )}

        {/* QR Code Dialog */}
        {showQR && qrData && (
          <div className="fixed inset-0 flex items-center justify-center z-50" style={{ background: 'rgba(0,0,0,0.5)' }}>
            <div className="p-6 rounded-2xl w-80 text-center space-y-4 relative" style={{ background: 'var(--color-bg)' }}>
              <button onClick={() => { setShowQR(false); setPolling(false) }} className="absolute top-3 right-3 opacity-50 hover:opacity-100">
                <X size={18} />
              </button>
              <h3 className="font-semibold">Scan with Bilibili App</h3>
              <img src={`data:image/png;base64,${qrData.qr_image_base64}`} alt="QR Code" className="w-48 h-48 mx-auto" />
              {polling ? (
                <p className="text-sm flex items-center justify-center gap-2" style={{ color: 'var(--color-text-secondary)' }}>
                  <Loader2 size={14} className="animate-spin" /> Waiting for scan...
                </p>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm" style={{ color: 'var(--color-danger)' }}>QR code expired</p>
                  <button onClick={() => generateQR.mutate()} className="text-sm underline" style={{ color: 'var(--color-primary)' }}>
                    Generate new QR code
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      {/* Analysis Limits */}
      <AnalysisLimitsSection />

      {/* Cumulative Usage */}
      <CumulativeUsageSection />

      {/* API Tokens */}
      <ApiTokensSection />

      {/* Pending Tasks */}
      <section className="p-5 rounded-xl space-y-3" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <h2 className="font-semibold flex items-center gap-2"><RefreshCw size={18} /> Unfinished Tasks</h2>
        {tasks.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>No unfinished tasks.</p>
        ) : (
          <div className="space-y-2">
            {tasks.map((t) => (
              <div key={t.id} className="flex items-center justify-between py-2 px-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
                <div className="flex-1 min-w-0">
                  <p className="text-sm truncate">{t.video_title || t.url}</p>
                  <p className="text-xs" style={{ color: 'var(--color-danger)' }}>{t.status}: {t.error_message}</p>
                </div>
                <button onClick={() => discardTaskMutation.mutate(t.id)} className="text-xs px-2 py-1 ml-2 shrink-0" style={{ color: 'var(--color-danger)' }}>
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
        </>
      )}
    </div>
  )
}


function SectionActionButton({
  icon: Icon,
  label,
  variant = 'primary',
  danger = false,
  spinning = false,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon
  label: string
  variant?: 'primary' | 'outline'
  danger?: boolean
  spinning?: boolean
}) {
  const textColor = variant === 'primary'
    ? '#fff'
    : danger
      ? 'var(--color-danger)'
      : 'var(--color-text)'
  const background = variant === 'primary' ? 'var(--color-primary)' : 'transparent'
  const borderColor = danger ? 'rgba(239, 68, 68, 0.25)' : 'var(--color-border)'

  return (
    <button
      className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-60 shrink-0"
      style={{
        background,
        color: textColor,
        border: variant === 'outline' ? `1px solid ${borderColor}` : '1px solid transparent',
      }}
      {...props}
    >
      <Icon size={14} className={spinning ? 'animate-spin' : ''} />
      {label}
    </button>
  )
}


function YoutubeCookiesSection({
  status,
  isLoading,
  isRefreshing,
  error,
  onRefresh,
}: {
  status?: YoutubeCookiesStatus
  isLoading: boolean
  isRefreshing: boolean
  error: Error | null
  onRefresh: () => void
}) {
  const expiryText = status?.earliest_expiry
    ? new Date(status.earliest_expiry).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    : ''
  const checkedText = status?.checked_at
    ? new Date(status.checked_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    : ''
  const daysLeft = status?.earliest_expiry_ts
    ? Math.floor((status.earliest_expiry_ts - Date.now() / 1000) / 86400)
    : null

  let tone: string = 'var(--color-text-secondary)'
  let message = 'YouTube cookies status unavailable.'
  if (!status?.configured) {
    tone = 'var(--color-danger)'
    message = 'YouTube cookies 未配置，部分视频可能会被要求登录验证。'
  } else if (!status.file_exists) {
    tone = 'var(--color-danger)'
    message = 'YouTube cookies 文件不存在，请检查 config 目录。'
  } else if (status.expired) {
    tone = 'var(--color-danger)'
    message = expiryText
      ? `YouTube cookies 已过期（${expiryText}）。`
      : 'YouTube cookies 已过期。'
  } else if (status.usability_checked && status.usable === false) {
    tone = 'var(--color-danger)'
    message = status.usability_message || 'Cookies 已配置，但当前不可用。'
  } else if (status.usability_checked && status.usable === true) {
    tone = 'var(--color-success)'
    message = status.usability_message || 'Cookies 可用于当前 YouTube 元数据请求。'
  } else if (status.usability_checked && status.usable === null) {
    tone = 'var(--color-warning, #f59e0b)'
    message = status.usability_message || 'Cookies 探测未得到确定结果。'
  } else if (status?.file_exists) {
    tone = 'var(--color-text-secondary)'
    message = 'Cookies 文件已配置。'
  }

  return (
    <section className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1 flex-1 min-w-0">
          <h2 className="font-semibold flex items-center gap-2"><Cookie size={18} /> YouTube Cookies</h2>
          <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            Checks whether the configured cookies are present, unexpired, and still accepted by YouTube.
          </p>
        </div>
        <SectionActionButton
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          icon={isRefreshing ? Loader2 : RefreshCw}
          spinning={isRefreshing}
          label={isRefreshing ? 'Testing...' : 'Connection Test'}
          variant="primary"
        />
      </div>

      {isLoading ? (
        <p className="text-sm flex items-center gap-2" style={{ color: 'var(--color-text-secondary)' }}>
          <Loader2 size={14} className="animate-spin" /> Checking YouTube cookies...
        </p>
      ) : error ? (
        <div className="p-3 rounded-lg text-sm" style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', color: 'var(--color-danger)' }}>
          {error.message}
        </div>
      ) : status ? (
        <>
          <div className="p-3 rounded-lg text-sm" style={{ background: 'var(--color-bg-tertiary)', color: tone }}>
            {message}
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>Configured</p>
              <p className="font-semibold">{status.configured ? 'Yes' : 'No'}</p>
            </div>
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>Cookie file</p>
              <p className="font-semibold">{status.file_exists ? 'Found' : 'Missing'}</p>
            </div>
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>Expiry</p>
              <p className="font-semibold">{expiryText || 'Unknown'}</p>
              {daysLeft !== null && !status.expired && (
                <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>Remaining {daysLeft} days</p>
              )}
            </div>
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>Usability probe</p>
              <p className="font-semibold">
                {status.usability_checked
                  ? status.usable === true ? 'Usable' : status.usable === false ? 'Rejected' : 'Inconclusive'
                  : 'Skipped'}
              </p>
            </div>
          </div>
          <div className="text-xs space-y-1" style={{ color: 'var(--color-text-secondary)' }}>
            <p>{status.domain_summary || `${status.cookie_count} cookies loaded`}</p>
            {checkedText && <p>Last checked: {checkedText}</p>}
          </div>
        </>
      ) : null}
    </section>
  )
}


function ApiTokensSection() {
  const queryClient = useQueryClient()
  const [newName, setNewName] = useState('')
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [headerCopied, setHeaderCopied] = useState(false)

  const keysQuery = useQuery({
    queryKey: ['api-tokens'],
    queryFn: () => withTokenEndpoint((path) => api.get<ApiTokenItem[]>(path)),
  })

  const createMutation = useMutation({
    mutationFn: (name: string) => withTokenEndpoint((path) => api.post<ApiTokenCreated>(path, { name })),
    onSuccess: (data) => {
      setCreatedKey(data.full_key)
      setNewName('')
      queryClient.invalidateQueries({ queryKey: ['api-tokens'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => withTokenEndpoint((path) => api.delete(`${path}/${id}`)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['api-tokens'] }),
  })

  const handleCopyKey = async () => {
    if (!createdKey) return
    await navigator.clipboard.writeText(createdKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleCopyHeader = async () => {
    await navigator.clipboard.writeText(TOKEN_HEADER_EXAMPLE)
    setHeaderCopied(true)
    setTimeout(() => setHeaderCopied(false), 2000)
  }

  const keys = keysQuery.data || []
  const tokenError =
    (keysQuery.error as Error | null)
    || (createMutation.error as Error | null)
    || (deleteMutation.error as Error | null)

  return (
    <section className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
      <h2 className="font-semibold flex items-center gap-2"><Key size={18} /> API Tokens</h2>
      <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
        Your browser login uses a session JWT. For scripts and external tools, issue a long-lived API token here.
      </p>

      <div className="p-3 rounded-lg space-y-2" style={{ background: 'var(--color-bg-tertiary)' }}>
        <p className="text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--color-text-secondary)' }}>
          Request Header
        </p>
        <div className="flex items-center gap-2">
          <code className="text-xs flex-1 font-mono px-2 py-1.5 rounded" style={{ background: 'var(--color-bg)' }}>
            {TOKEN_HEADER_EXAMPLE}
          </code>
          <button onClick={handleCopyHeader} className="p-1.5 rounded-md" style={{ color: headerCopied ? 'var(--color-success)' : 'var(--color-text-secondary)' }}>
            {headerCopied ? <Check size={16} /> : <Copy size={16} />}
          </button>
        </div>
      </div>

      {tokenError && (
        <div className="p-3 rounded-lg text-sm" style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', color: 'var(--color-danger)' }}>
          {tokenError.message}
        </div>
      )}

      {/* Created key banner */}
      {createdKey && (
        <div className="p-3 rounded-lg space-y-2" style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-success)' }}>
          <p className="text-sm font-medium" style={{ color: 'var(--color-success)' }}>
            API token created! Copy it now — it won't be shown again.
          </p>
          <div className="flex items-center gap-2">
            <code className="text-xs flex-1 font-mono px-2 py-1.5 rounded" style={{ background: 'var(--color-bg)' }}>
              {createdKey}
            </code>
            <button onClick={handleCopyKey} className="p-1.5 rounded-md" style={{ color: copied ? 'var(--color-success)' : 'var(--color-text-secondary)' }}>
              {copied ? <Check size={16} /> : <Copy size={16} />}
            </button>
          </div>
          <button onClick={() => setCreatedKey(null)} className="text-xs underline" style={{ color: 'var(--color-text-secondary)' }}>
            Dismiss
          </button>
        </div>
      )}

      {/* Create form */}
      <div className="flex gap-2">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Token name (e.g. automation-bot)"
          className="flex-1 text-sm px-3 py-2 rounded-lg"
          style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
          onKeyDown={(e) => { if (e.key === 'Enter' && newName.trim()) createMutation.mutate(newName.trim()) }}
        />
        <button
          onClick={() => newName.trim() && createMutation.mutate(newName.trim())}
          disabled={!newName.trim() || createMutation.isPending}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
          style={{ background: 'var(--color-primary)' }}
        >
          {createMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          Issue token
        </button>
      </div>

      {/* Keys list */}
      {keysQuery.isPending ? (
        <p className="text-sm flex items-center gap-2" style={{ color: 'var(--color-text-secondary)' }}>
          <Loader2 size={14} className="animate-spin" /> Loading tokens...
        </p>
      ) : keys.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>No API tokens yet.</p>
      ) : (
        <div className="space-y-2">
          {keys.map((k) => (
            <div key={k.id} className="flex items-center justify-between py-2 px-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">{k.name}</p>
                <p className="text-xs font-mono" style={{ color: 'var(--color-text-secondary)' }}>
                  {k.key_prefix}
                  {k.last_used_at && <span className="ml-2">· Last used: {new Date(k.last_used_at).toLocaleDateString()}</span>}
                </p>
              </div>
              <button
                onClick={() => deleteMutation.mutate(k.id)}
                disabled={deleteMutation.isPending}
                className="p-1.5 rounded hover:opacity-70"
                style={{ color: 'var(--color-danger)' }}
                title="Delete token"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}


interface PreferencesData {
  max_duration_seconds: number
  confirm_threshold_seconds: number
  max_concurrent_analyses: number
  defaults: { max_duration_seconds: number; confirm_threshold_seconds: number; max_concurrent_analyses: number }
}

function formatMinutes(seconds: number) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}h${m > 0 ? `${m}m` : ''}` : `${m}m`
}

function secondsToMinutes(seconds: number) {
  return Math.floor(seconds / 60)
}

function AnalysisLimitsSection() {
  const queryClient = useQueryClient()
  const { data: prefs, isPending } = useQuery({
    queryKey: ['user-preferences'],
    queryFn: () => api.get<PreferencesData>('/auth/preferences'),
  })

  const [maxDuration, setMaxDuration] = useState('')
  const [confirmThreshold, setConfirmThreshold] = useState('')
  const [maxConcurrent, setMaxConcurrent] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (prefs) {
      setMaxDuration(String(secondsToMinutes(prefs.max_duration_seconds)))
      setConfirmThreshold(String(secondsToMinutes(prefs.confirm_threshold_seconds)))
      setMaxConcurrent(String(prefs.max_concurrent_analyses))
    }
  }, [prefs])

  const mutation = useMutation({
    mutationFn: (body: { max_duration_seconds: number; confirm_threshold_seconds: number; max_concurrent_analyses: number }) =>
      api.put<PreferencesData>('/auth/preferences', body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-preferences'] })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    },
  })

  const handleSave = () => {
    const dur = (parseInt(maxDuration) || 0) * 60
    const thresh = (parseInt(confirmThreshold) || 0) * 60
    const conc = parseInt(maxConcurrent) || 0
    mutation.mutate({ max_duration_seconds: dur, confirm_threshold_seconds: thresh, max_concurrent_analyses: conc })
  }

  if (isPending) return null

  const defs = prefs?.defaults

  return (
    <section className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h2 className="font-semibold flex items-center gap-2"><SlidersHorizontal size={18} /> Video Limits</h2>
          <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            Adjust the per-account limits used by analysis. The max duration here controls errors like "exceeding the 1h0m limit".
          </p>
        </div>
        <SectionActionButton
          type="button"
          onClick={handleSave}
          disabled={mutation.isPending}
          icon={saved ? Check : (mutation.isPending ? Loader2 : Save)}
          spinning={mutation.isPending}
          label={saved ? 'Saved' : 'Save'}
          variant="primary"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-1">
          <label className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
            Max Video Duration
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={720}
              step={1}
              value={maxDuration}
              onChange={e => setMaxDuration(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            />
            <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-text-secondary)' }}>min</span>
          </div>
          {defs && (
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              Default: {formatMinutes(defs.max_duration_seconds)} ({defs.max_duration_seconds}s)
            </p>
          )}
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
            Confirmation Threshold
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={720}
              step={1}
              value={confirmThreshold}
              onChange={e => setConfirmThreshold(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            />
            <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-text-secondary)' }}>min</span>
          </div>
          {defs && (
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              Default: {formatMinutes(defs.confirm_threshold_seconds)} ({defs.confirm_threshold_seconds}s)
            </p>
          )}
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
            Max Concurrent Analyses
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={10}
              value={maxConcurrent}
              onChange={e => setMaxConcurrent(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            />
            <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-text-secondary)' }}>slots</span>
          </div>
          {defs && (
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              Default: {defs.max_concurrent_analyses}
            </p>
          )}
        </div>
      </div>

      {mutation.isError && (
        <p className="text-xs" style={{ color: 'var(--color-danger)' }}>
          Failed to save. Please try again.
        </p>
      )}
    </section>
  )
}


interface UsageStats {
  asr?: Record<string, { total_seconds: number; requests: number }>
  llm?: Record<string, { prompt_tokens: number; completion_tokens: number; total_tokens: number; requests: number }>
}

function CumulativeUsageSection() {
  const { data: stats, isPending } = useQuery({
    queryKey: ['usage-stats'],
    queryFn: () => api.get<UsageStats>('/auth/usage-stats'),
  })

  const asrModels = stats?.asr ? Object.entries(stats.asr) : []
  const llmModels = stats?.llm ? Object.entries(stats.llm) : []
  const hasData = asrModels.length > 0 || llmModels.length > 0

  return (
    <section className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
      <div className="space-y-1">
        <h2 className="font-semibold flex items-center gap-2"><BarChart3 size={18} /> Cumulative Usage</h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          Total API consumption across all your analyses. This data persists even after videos are deleted.
        </p>
      </div>

      {isPending ? (
        <p className="text-sm flex items-center gap-2" style={{ color: 'var(--color-text-secondary)' }}>
          <Loader2 size={14} className="animate-spin" /> Loading usage...
        </p>
      ) : !hasData ? (
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>No usage data yet. Complete an analysis to start tracking.</p>
      ) : (
        <div className="space-y-4">
          {llmModels.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--color-text-secondary)' }}>LLM Models</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {llmModels.map(([model, data]) => (
                  <div key={model} className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
                    <p className="text-sm font-medium mb-2">{model}</p>
                    <div className="grid grid-cols-2 gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                      <div>
                        <p className="opacity-60">Prompt</p>
                        <p className="font-medium tabular-nums" style={{ color: 'var(--color-text)' }}>{data.prompt_tokens.toLocaleString()}</p>
                      </div>
                      <div>
                        <p className="opacity-60">Completion</p>
                        <p className="font-medium tabular-nums" style={{ color: 'var(--color-text)' }}>{data.completion_tokens.toLocaleString()}</p>
                      </div>
                      <div>
                        <p className="opacity-60">Total Tokens</p>
                        <p className="font-medium tabular-nums" style={{ color: 'var(--color-primary)' }}>{data.total_tokens.toLocaleString()}</p>
                      </div>
                      <div>
                        <p className="opacity-60">Requests</p>
                        <p className="font-medium tabular-nums" style={{ color: 'var(--color-text)' }}>{data.requests}</p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {asrModels.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--color-text-secondary)' }}>ASR Models</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {asrModels.map(([model, data]) => (
                  <div key={model} className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
                    <p className="text-sm font-medium mb-2">{model}</p>
                    <div className="grid grid-cols-2 gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                      <div>
                        <p className="opacity-60">Total Duration</p>
                        <p className="font-medium tabular-nums" style={{ color: 'var(--color-text)' }}>{Math.round(data.total_seconds).toLocaleString()}s</p>
                      </div>
                      <div>
                        <p className="opacity-60">Requests</p>
                        <p className="font-medium tabular-nums" style={{ color: 'var(--color-text)' }}>{data.requests}</p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}


function AdminCleanupSection() {
  const queryClient = useQueryClient()
  const [showConfirm, setShowConfirm] = useState(false)
  const [lastResult, setLastResult] = useState<AdminCleanupResult | null>(null)

  const summaryQuery = useQuery({
    queryKey: ['admin-cleanup-summary'],
    queryFn: () => api.get<AdminCleanupSummary>('/admin/cleanup/summary'),
  })

  const cleanupMutation = useMutation({
    mutationFn: () => api.post<AdminCleanupResult>('/admin/cleanup/run'),
    onSuccess: (data) => {
      setLastResult(data)
      setShowConfirm(false)
      queryClient.invalidateQueries({ queryKey: ['admin-cleanup-summary'] })
    },
  })

  const summary = summaryQuery.data
  const activeError = (summaryQuery.error as Error | null) || (cleanupMutation.error as Error | null)

  return (
    <section className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
      <div className="space-y-1">
        <h2 className="font-semibold flex items-center gap-2"><Shield size={18} /> Admin Cleanup</h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          Scan and remove orphaned exports, thumbnails, and task temp directories left behind on disk.
        </p>
      </div>

      {summaryQuery.isPending ? (
        <p className="text-sm flex items-center gap-2" style={{ color: 'var(--color-text-secondary)' }}>
          <Loader2 size={14} className="animate-spin" /> Scanning storage...
        </p>
      ) : summary ? (
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>Orphan exports</p>
            <p className="text-xl font-semibold">{summary.orphan_exports}</p>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>Orphan thumbnails</p>
            <p className="text-xl font-semibold">{summary.orphan_thumbnails}</p>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>Orphan task dirs</p>
            <p className="text-xl font-semibold">{summary.orphan_task_dirs}</p>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>Total removable items</p>
            <p className="text-xl font-semibold">{summary.total_items}</p>
          </div>
        </div>
      ) : null}

      {activeError && (
        <div className="p-3 rounded-lg text-sm" style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', color: 'var(--color-danger)' }}>
          {activeError.message}
        </div>
      )}

      {lastResult && (
        <div className="p-3 rounded-lg space-y-1 text-sm" style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}>
          <p className="font-medium">Last cleanup result</p>
          <p style={{ color: 'var(--color-text-secondary)' }}>
            Removed {lastResult.removed_total} items
            {' '}({lastResult.removed_exports} exports, {lastResult.removed_thumbnails} thumbnails, {lastResult.removed_task_dirs} task dirs).
          </p>
          {lastResult.errors.length > 0 && (
            <p style={{ color: 'var(--color-danger)' }}>
              {lastResult.errors.length} item(s) could not be removed.
            </p>
          )}
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => { void summaryQuery.refetch() }}
          className="px-4 py-2 rounded-lg text-sm"
          style={{ border: '1px solid var(--color-border)' }}
        >
          Refresh
        </button>
        <button
          onClick={() => setShowConfirm(true)}
          disabled={!summary || summary.total_items === 0 || cleanupMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
          style={{ background: 'var(--color-danger)' }}
        >
          {cleanupMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
          Clean residual files
        </button>
      </div>

      {showConfirm && summary && (
        <div className="fixed inset-0 flex items-center justify-center z-50" style={{ background: 'rgba(0,0,0,0.5)' }}>
          <div className="w-full max-w-md p-6 rounded-2xl space-y-4" style={{ background: 'var(--color-bg)' }}>
            <div className="flex items-start gap-3">
              <AlertTriangle size={20} style={{ color: 'var(--color-danger)' }} />
              <div className="space-y-1">
                <h3 className="font-semibold">Clean residual files?</h3>
                <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                  This will remove orphaned files that are no longer referenced by the database.
                </p>
              </div>
            </div>
            <div className="text-sm space-y-1" style={{ color: 'var(--color-text-secondary)' }}>
              <p>Total removable items: <strong style={{ color: 'var(--color-text)' }}>{summary.total_items}</strong></p>
              <p>Exports: <strong style={{ color: 'var(--color-text)' }}>{summary.orphan_exports}</strong></p>
              <p>Thumbnails: <strong style={{ color: 'var(--color-text)' }}>{summary.orphan_thumbnails}</strong></p>
              <p>Task temp dirs: <strong style={{ color: 'var(--color-text)' }}>{summary.orphan_task_dirs}</strong></p>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 rounded-lg text-sm"
                style={{ border: '1px solid var(--color-border)' }}
              >
                Cancel
              </button>
              <button
                onClick={() => cleanupMutation.mutate()}
                disabled={cleanupMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white"
                style={{ background: 'var(--color-danger)' }}
              >
                {cleanupMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                Confirm cleanup
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
