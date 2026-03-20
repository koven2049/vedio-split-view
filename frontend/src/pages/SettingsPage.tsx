import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { User, Link2, Loader2, CheckCircle, RefreshCw, X, Key, Copy, Check, Plus, Trash2, Shield, AlertTriangle } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import { useAuthStore } from '../stores/authStore'

interface BiliStatus { connected: boolean; bilibili_username: string; expired: boolean }
interface QRData { qr_key: string; qr_url: string; qr_image_base64: string }
interface TaskItem { id: number; url: string; status: string; video_title: string; error_message: string }
interface ApiTokenItem { id: number; name: string; key_prefix: string; is_active: boolean; last_used_at: string | null; created_at: string }
interface ApiTokenCreated extends ApiTokenItem { full_key: string }
interface AdminCleanupSummary { orphan_exports: number; orphan_thumbnails: number; orphan_task_dirs: number; total_items: number }
interface AdminCleanupResult extends AdminCleanupSummary { removed_exports: number; removed_thumbnails: number; removed_task_dirs: number; removed_total: number; errors: string[] }

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
    <div className="space-y-8 max-w-2xl">
      <h1 className="text-2xl font-bold">Settings</h1>

      {/* Profile */}
      <section className="p-5 rounded-xl space-y-3" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <h2 className="font-semibold flex items-center gap-2"><User size={18} /> Profile</h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>Username: <strong>{username}</strong></p>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>Role: <strong>{role}</strong></p>
      </section>

      {role === 'admin' && <AdminCleanupSection />}

      {role !== 'user' ? null : (
        <>
      {/* Bilibili Connection */}
      <section className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <h2 className="font-semibold flex items-center gap-2"><Link2 size={18} /> Bilibili Account</h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          Connect your Bilibili account to directly fetch subtitles (faster analysis, no audio download needed).
        </p>

        {biliStatus?.connected ? (
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CheckCircle size={16} style={{ color: 'var(--color-success)' }} />
              <span className="text-sm font-medium">Connected{biliStatus.bilibili_username ? ` — @${biliStatus.bilibili_username}` : ''}</span>
            </div>
            <button onClick={() => disconnectMutation.mutate()} className="text-xs px-3 py-1.5 rounded-lg"
              style={{ border: '1px solid var(--color-border)', color: 'var(--color-danger)' }}>
              Disconnect
            </button>
          </div>
        ) : (
          <div>
            <button
              onClick={() => generateQR.mutate()}
              disabled={generateQR.isPending}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white"
              style={{ background: 'var(--color-primary)' }}
            >
              {generateQR.isPending ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
              Connect Bilibili
            </button>
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
          onClick={() => queryClient.invalidateQueries({ queryKey: ['admin-cleanup-summary'] })}
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
