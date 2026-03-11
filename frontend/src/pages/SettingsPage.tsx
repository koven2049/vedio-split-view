import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { User, Link2, Loader2, CheckCircle, RefreshCw, X } from 'lucide-react'
import { api } from '../lib/api'
import { useAuthStore } from '../stores/authStore'

interface BiliStatus { connected: boolean; bilibili_username: string; expired: boolean }
interface QRData { qr_key: string; qr_url: string; qr_image_base64: string }
interface TaskItem { id: number; url: string; status: string; video_title: string; error_message: string }

export default function SettingsPage() {
  const { username } = useAuthStore()
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
      </section>

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
    </div>
  )
}
