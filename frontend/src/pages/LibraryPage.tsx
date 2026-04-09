import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, Trash2, Share2, Lock, Clock, Play, X, Loader2, FileAudio, AlertCircle, TriangleAlert, CheckCircle2, BarChart3 } from 'lucide-react'
import { api } from '../lib/api'
import { formatDuration, platformLabel, timeAgo, cn } from '../lib/utils'
import { useAnalysisStore } from '../stores/analysisStore'
import { useAuthStore } from '../stores/authStore'

interface AsrUsageSummary { model: string; total_seconds: number }
interface LlmUsageSummary { model: string; prompt_tokens: number; completion_tokens: number; total_tokens: number }
interface UsageSummary { asr: AsrUsageSummary[]; llm: LlmUsageSummary[] }

interface TagInfo { id: number; name: string; color: string }
interface VideoItem {
  id: number; url: string; platform: string; title: string
  thumbnail_url: string; duration_seconds: number; is_public: boolean
  created_at: string; tags: TagInfo[]; owner_name: string
}
interface TaskItem {
  id: number; url: string; platform: string; status: string
  video_title: string; error_message: string; created_at: string; updated_at: string
}

type LibraryItem =
  | { kind: 'video'; data: VideoItem }
  | { kind: 'task'; data: TaskItem }

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  analyzed:          { label: 'Analyzed',           color: 'var(--color-success)',  bg: 'rgba(34,197,94,0.1)' },
  downloaded:        { label: 'Downloaded',         color: 'var(--color-primary)',  bg: 'rgba(59,130,246,0.1)' },
  downloading:       { label: 'Downloading...',     color: 'var(--color-warning)',  bg: 'rgba(245,158,11,0.1)' },
  transcribing:      { label: 'Transcribing...',    color: 'var(--color-warning)',  bg: 'rgba(245,158,11,0.1)' },
  analyzing:         { label: 'Analyzing...',       color: 'var(--color-warning)',  bg: 'rgba(245,158,11,0.1)' },
  failed_transcribe: { label: 'Transcription Failed', color: 'var(--color-danger)', bg: 'rgba(239,68,68,0.1)' },
  failed_analyze:    { label: 'Analysis Failed',    color: 'var(--color-danger)',   bg: 'rgba(239,68,68,0.1)' },
}

function canAnalyze(status: string): boolean {
  return ['downloaded', 'failed_transcribe', 'failed_analyze'].includes(status)
}

export default function LibraryPage() {
  const isViewer = useAuthStore((s) => s.isViewer)()
  const [activeTab, setActiveTab] = useState<'mine' | 'public'>(isViewer ? 'public' : 'mine')
  const [search, setSearch] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<{ type: 'video' | 'task'; id: number; title: string } | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const store = useAnalysisStore()
  const storeAnalyzing = store.isAnyAnalyzing()

  const videosQuery = useQuery({
    queryKey: ['videos', activeTab, search, tagFilter],
    queryFn: () => {
      const path = activeTab === 'public' ? '/videos/public' : '/videos'
      const params = new URLSearchParams()
      if (search) params.set('q', search)
      if (tagFilter) params.set('tag', tagFilter)
      const qs = params.toString()
      return api.get<VideoItem[]>(`${path}${qs ? `?${qs}` : ''}`)
    },
  })

  const tasksQuery = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<TaskItem[]>('/videos/tasks'),
    enabled: !isViewer,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/videos/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      setConfirmDelete(null)
    },
  })

  const shareMutation = useMutation({
    mutationFn: ({ id, share }: { id: number; share: boolean }) =>
      api.post(`/videos/${id}/${share ? 'share' : 'unshare'}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['videos'] }),
  })

  const discardTaskMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/videos/tasks/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      setConfirmDelete(null)
    },
  })

  const handleAnalyzeTask = (task: TaskItem) => {
    const platform = task.platform || 'youtube'
    void store.retryTask(platform, task.id, task.url)
    navigate('/')
  }

  const handleViewProgress = () => {
    navigate('/')
  }

  const tagsQuery = useQuery({
    queryKey: ['tags'],
    queryFn: () => api.get<TagInfo[]>('/tags'),
  })

  const usageQuery = useQuery({
    queryKey: ['usage-summary'],
    queryFn: () => api.get<UsageSummary>('/videos/usage-summary'),
    enabled: !isViewer,
  })

  const mergedItems: LibraryItem[] = []
  if (activeTab === 'mine') {
    const tasks = (tasksQuery.data || []).filter(
      (t) => !['completed', 'cancelled', 'failed_download'].includes(t.status)
    )
    for (const t of tasks) {
      mergedItems.push({ kind: 'task', data: t })
    }
  }
  for (const v of (videosQuery.data || [])) {
    mergedItems.push({ kind: 'video', data: v })
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Library</h1>

      {/* Tabs + Search */}
      <div className="flex items-center justify-between gap-4">
        {!isViewer ? (
          <div className="flex gap-1 p-1 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            {(['mine', 'public'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={cn('px-4 py-2 rounded-md text-sm font-medium transition-colors', activeTab === tab ? 'shadow-sm' : 'opacity-60 hover:opacity-100')}
                style={activeTab === tab ? { background: 'var(--color-bg)', color: 'var(--color-primary)' } : {}}
              >
                {tab === 'mine' ? 'My Videos' : 'Public'}
              </button>
            ))}
          </div>
        ) : (
          <h2 className="text-sm font-medium" style={{ color: 'var(--color-text-secondary)' }}>Public Videos</h2>
        )}

        <div className="flex gap-2 items-center">
          {tagFilter && (
            <button onClick={() => setTagFilter('')} className="flex items-center gap-1 px-2 py-1 rounded-md text-xs" style={{ background: 'var(--color-primary)', color: 'white' }}>
              {tagFilter} <X size={12} />
            </button>
          )}
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search..."
              className="pl-9 pr-3 py-2 rounded-lg text-sm w-48 outline-none"
              style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            />
          </div>
        </div>
      </div>

      {/* Usage Summary */}
      {activeTab === 'mine' && usageQuery.data && (usageQuery.data.asr.length > 0 || usageQuery.data.llm.length > 0) && (
        <div
          className="flex items-start gap-3 px-4 py-3 rounded-xl text-xs"
          style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
        >
          <BarChart3 size={14} className="mt-0.5 shrink-0 opacity-50" />
          <div className="flex flex-wrap gap-x-5 gap-y-1" style={{ color: 'var(--color-text-secondary)' }}>
            <span className="font-medium" style={{ color: 'var(--color-text)' }}>Total usage:</span>
            {usageQuery.data.asr.map((a) => (
              <span key={`asr-${a.model}`} className="inline-flex items-center gap-1">
                <span className="opacity-60">ASR ({a.model}):</span>
                <span className="font-medium tabular-nums">{Math.round(a.total_seconds).toLocaleString()}s</span>
              </span>
            ))}
            {usageQuery.data.llm.map((l) => (
              <span key={`llm-${l.model}`} className="inline-flex items-center gap-1">
                <span className="opacity-60">LLM ({l.model}):</span>
                <span className="font-medium tabular-nums">{l.total_tokens.toLocaleString()} tokens</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Tags filter bar */}
      {(tagsQuery.data?.length ?? 0) > 0 && (
        <div className="flex gap-2 flex-wrap">
          {tagsQuery.data?.map((tag) => (
            <button
              key={tag.id}
              onClick={() => setTagFilter(tagFilter === tag.name ? '' : tag.name)}
              className={cn('px-2.5 py-1 rounded-full text-xs transition-colors', tagFilter === tag.name ? 'text-white' : '')}
              style={{
                background: tagFilter === tag.name ? tag.color : 'var(--color-bg-tertiary)',
                color: tagFilter === tag.name ? 'white' : 'var(--color-text-secondary)',
              }}
            >
              {tag.name}
            </button>
          ))}
        </div>
      )}

      {/* Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {mergedItems.map((item) =>
          item.kind === 'task' ? (
            <TaskCard
              key={`task-${item.data.id}`}
              task={item.data}
              isGlobalAnalyzing={storeAnalyzing}
              onAnalyze={() => handleAnalyzeTask(item.data)}
              onViewProgress={handleViewProgress}
              onDelete={() => setConfirmDelete({ type: 'task', id: item.data.id, title: item.data.video_title || item.data.url })}
            />
          ) : (
            <VideoCard
              key={`video-${item.data.id}`}
              video={item.data}
              isMine={activeTab === 'mine'}
              isViewer={isViewer}
              onShare={(share) => shareMutation.mutate({ id: item.data.id, share })}
              onDelete={() => setConfirmDelete({ type: 'video', id: item.data.id, title: item.data.title })}
            />
          )
        )}
      </div>

      {mergedItems.length === 0 && (
        <p className="text-center py-12 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          No videos found.
        </p>
      )}

      {/* Confirm Delete Modal */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setConfirmDelete(null)}>
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
          <div
            className="relative w-full max-w-sm mx-4 rounded-2xl shadow-2xl p-6 space-y-4"
            style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-full" style={{ background: 'rgba(239, 68, 68, 0.1)' }}>
                <TriangleAlert size={20} style={{ color: 'var(--color-danger)' }} />
              </div>
              <h3 className="text-base font-semibold">Confirm Delete</h3>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
              Delete <strong>{confirmDelete.title}</strong>?
              {confirmDelete.type === 'task' && ' All associated audio files will also be removed.'}
              {confirmDelete.type === 'video' && ' This cannot be undone.'}
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-80"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (confirmDelete.type === 'video') deleteMutation.mutate(confirmDelete.id)
                  else discardTaskMutation.mutate(confirmDelete.id)
                }}
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


function TaskCard({ task, isGlobalAnalyzing, onAnalyze, onViewProgress, onDelete }: {
  task: TaskItem
  isGlobalAnalyzing: boolean
  onAnalyze: () => void
  onViewProgress: () => void
  onDelete: () => void
}) {
  const cfg = STATUS_CONFIG[task.status] ?? { label: task.status, color: 'var(--color-text-secondary)', bg: 'var(--color-bg-tertiary)' }
  const inProgress = ['downloading', 'transcribing', 'analyzing'].includes(task.status)

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      {/* Clickable top area — navigates to progress view when in-progress */}
      <div
        className={cn('w-full h-36 flex flex-col items-center justify-center gap-3 relative', inProgress && 'cursor-pointer hover:opacity-80 transition-opacity')}
        style={{ background: 'var(--color-bg-tertiary)' }}
        onClick={inProgress ? onViewProgress : undefined}
      >
        <FileAudio size={32} className="opacity-30" />
        <span
          className="px-2.5 py-1 rounded-full text-[11px] font-medium"
          style={{ background: cfg.bg, color: cfg.color }}
        >
          {inProgress && <Loader2 size={11} className="inline animate-spin mr-1 -mt-0.5" />}
          {cfg.label}
        </span>
        {inProgress && (
          <span className="text-[10px]" style={{ color: 'var(--color-primary)' }}>
            Click to view progress
          </span>
        )}
      </div>

      <div className="p-3 space-y-2">
        <h3 className="font-medium text-sm leading-snug line-clamp-2">
          {task.video_title || task.url}
        </h3>
        <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          <span>{platformLabel(task.platform)}</span>
          <span>{timeAgo(task.created_at)}</span>
        </div>
        {task.error_message && (
          <div className="flex items-start gap-1.5 text-xs" style={{ color: 'var(--color-danger)' }}>
            <AlertCircle size={12} className="mt-0.5 shrink-0" />
            <span className="line-clamp-2">{task.error_message}</span>
          </div>
        )}
        <div className="flex gap-2 pt-1" style={{ borderTop: '1px solid var(--color-border)' }}>
          {canAnalyze(task.status) && (
            <button
              onClick={onAnalyze}
              disabled={isGlobalAnalyzing}
              className="flex items-center gap-1 text-xs px-2 py-1 rounded font-medium disabled:opacity-40"
              style={{ color: 'var(--color-primary)' }}
            >
              {isGlobalAnalyzing
                ? <><Loader2 size={12} className="animate-spin" /> Busy</>
                : <><Play size={12} /> {task.status === 'downloaded' ? 'Analyze' : 'Retry'}</>
              }
            </button>
          )}
          {inProgress && (
            <button
              onClick={onViewProgress}
              className="flex items-center gap-1 text-xs px-2 py-1 rounded font-medium"
              style={{ color: 'var(--color-primary)' }}
            >
              <Play size={12} /> View Progress
            </button>
          )}
          <button
            onClick={onDelete}
            disabled={isGlobalAnalyzing && inProgress}
            className="flex items-center gap-1 text-xs px-2 py-1 rounded ml-auto disabled:opacity-40"
            style={{ color: 'var(--color-danger)' }}
          >
            <Trash2 size={12} /> Delete
          </button>
        </div>
      </div>
    </div>
  )
}


function VideoCard({ video, isMine, isViewer: viewerMode, onShare, onDelete }: {
  video: VideoItem
  isMine: boolean
  isViewer: boolean
  onShare: (share: boolean) => void
  onDelete: () => void
}) {
  const cfg = STATUS_CONFIG.analyzed

  return (
    <div
      className="rounded-xl overflow-hidden hover:shadow-md transition-shadow"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <Link to={`/video/${video.id}`} className="relative block">
        {video.thumbnail_url ? (
          <img src={video.thumbnail_url} alt="" className="w-full h-36 object-cover" />
        ) : (
          <div className="w-full h-36 flex items-center justify-center" style={{ background: 'var(--color-bg-tertiary)' }}>
            <span className="text-3xl opacity-30">{platformLabel(video.platform)[0]}</span>
          </div>
        )}
        <span
          className="absolute top-2 right-2 flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium backdrop-blur-sm"
          style={{ background: cfg.bg, color: cfg.color, border: '1px solid rgba(34,197,94,0.2)' }}
        >
          <CheckCircle2 size={11} /> {cfg.label}
        </span>
      </Link>
      <div className="p-3 space-y-2">
        <Link to={`/video/${video.id}`}>
          <h3 className="font-medium text-sm leading-snug line-clamp-2 hover:underline">{video.title}</h3>
        </Link>
        <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          <span>{platformLabel(video.platform)}</span>
          <span className="flex items-center gap-0.5"><Clock size={11} /> {formatDuration(video.duration_seconds)}</span>
          <span>{timeAgo(video.created_at)}</span>
        </div>
        {video.tags.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {video.tags.map((tag) => (
              <span key={tag.id} className="px-2 py-0.5 rounded-full text-[10px]" style={{ background: tag.color + '20', color: tag.color }}>{tag.name}</span>
            ))}
          </div>
        )}
        {!viewerMode && (
          <div className="flex gap-2 pt-1" style={{ borderTop: '1px solid var(--color-border)' }}>
            {isMine && (
              <button
                onClick={() => onShare(!video.is_public)}
                className="flex items-center gap-1 text-xs px-2 py-1 rounded"
                style={{ color: video.is_public ? 'var(--color-success)' : 'var(--color-text-secondary)' }}
              >
                {video.is_public ? <><Share2 size={12} /> Public</> : <><Lock size={12} /> Private</>}
              </button>
            )}
            {!isMine && video.owner_name && (
              <span className="text-xs px-2 py-1" style={{ color: 'var(--color-text-secondary)' }}>by {video.owner_name}</span>
            )}
            {isMine && (
              <button
                onClick={onDelete}
                className="flex items-center gap-1 text-xs px-2 py-1 rounded ml-auto"
                style={{ color: 'var(--color-danger)' }}
              >
                <Trash2 size={12} /> Delete
              </button>
            )}
          </div>
        )}
        {viewerMode && video.owner_name && (
          <div className="pt-1" style={{ borderTop: '1px solid var(--color-border)' }}>
            <span className="text-xs px-2 py-1" style={{ color: 'var(--color-text-secondary)' }}>by {video.owner_name}</span>
          </div>
        )}
      </div>
    </div>
  )
}
