import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Play, ExternalLink, Share2, Lock, Trash2, Plus, X, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import { formatDuration, formatTimeRange, generatePlaybackUrl, platformLabel, cn } from '../lib/utils'
import LangToggle from '../components/LangToggle'
import MindmapView from '../components/MindmapView'
import UsageDisplay from '../components/UsageDisplay'
import type { UsageInfo } from '../stores/analysisStore'
import { useAuthStore } from '../stores/authStore'
import { useLangPreference } from '../hooks/useLangPreference'
import { useT } from '../i18n'

interface SubtitleEntry {
  start: number
  duration: number
  text: string
}

interface TagInfo { id: number; name: string; color: string }
interface SegmentInfo {
  id: number; segment_index: number; title: string; title_en: string
  summary: string; summary_en: string; start_seconds: number; end_seconds: number
}
interface VideoDetail {
  id: number; url: string; platform: string; video_id: string
  title: string; thumbnail_url: string; upload_date: string
  duration_seconds: number
  summary: string; summary_en: string; usage_json: string
  is_public: boolean; segments: SegmentInfo[]
  tags: TagInfo[]; owner_name: string
}

function SegmentCard({ seg, videoId, platform, platformVideoId, lang: cardLang }: {
  seg: SegmentInfo; videoId: number; platform: string; platformVideoId: string; lang: 'zh' | 'en'
}) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const [subs, setSubs] = useState<SubtitleEntry[] | null>(null)
  const [loading, setLoading] = useState(false)

  const toggleExpand = async () => {
    if (expanded) { setExpanded(false); return }
    setExpanded(true)
    if (subs !== null) return
    setLoading(true)
    try {
      const data = await api.get<SubtitleEntry[]>(
        `/videos/${videoId}/subtitles?start=${seg.start_seconds}&end=${seg.end_seconds}`
      )
      setSubs(data)
    } catch { setSubs([]) }
    finally { setLoading(false) }
  }

  const fmtTs = (s: number) => {
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec.toString().padStart(2, '0')}`
  }

  return (
    <div className="rounded-xl overflow-hidden transition-shadow hover:shadow-sm"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
      <div className="p-4 flex items-start gap-4 group">
        <div className="shrink-0 text-center">
          <span className="text-xs font-bold block" style={{ color: 'var(--color-primary)' }}>#{seg.segment_index + 1}</span>
          <span className="text-[11px] font-mono block mt-1" style={{ color: 'var(--color-text-secondary)' }}>
            {formatTimeRange(seg.start_seconds, seg.end_seconds)}
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-medium text-sm mb-1">
            {cardLang === 'en' && seg.title_en ? seg.title_en : seg.title}
          </h3>
          <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            {cardLang === 'en' && seg.summary_en ? seg.summary_en : seg.summary}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={toggleExpand}
            className="p-2 rounded-lg opacity-50 hover:opacity-100 transition-opacity"
            style={{ color: 'var(--color-text-secondary)' }}
            title={expanded ? t('detail.hideSubtitles') : t('detail.showSubtitles')}
          >
            {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
          <a
            href={generatePlaybackUrl(platform, platformVideoId, seg.start_seconds)}
            target="_blank" rel="noopener noreferrer"
            className="p-2 rounded-lg opacity-50 group-hover:opacity-100 transition-opacity"
            style={{ color: 'var(--color-primary)' }}
            title={t('detail.playFromHere')}
          >
            <Play size={18} />
          </a>
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4">
          <div className="rounded-lg p-3 max-h-64 overflow-y-auto space-y-0.5"
            style={{ background: 'var(--color-bg-tertiary)' }}>
            {loading && (
              <div className="flex items-center gap-2 py-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                <Loader2 size={14} className="animate-spin" /> {t('detail.loadingSubtitles')}
              </div>
            )}
            {subs && subs.length === 0 && !loading && (
              <div className="text-xs py-2" style={{ color: 'var(--color-text-secondary)' }}>
                {t('detail.noSubtitles')}
              </div>
            )}
            {subs?.map((entry, i) => (
              <div key={i} className="flex gap-3 py-1 text-xs hover:opacity-80">
                <span className="shrink-0 font-mono tabular-nums" style={{ color: 'var(--color-primary)', minWidth: '40px' }}>
                  {fmtTs(entry.start)}
                </span>
                <span style={{ color: 'var(--color-text)' }}>{entry.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function VideoDetailPage() {
  const t = useT()
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [newTag, setNewTag] = useState('')
  const [showTagInput, setShowTagInput] = useState(false)
  const [activeView, setActiveView] = useState<'segments' | 'mindmap'>('segments')
  const isViewer = useAuthStore((s) => s.isViewer)()
  const { lang, setLang } = useLangPreference()

  const { data: video, isLoading, isError, error } = useQuery({
    queryKey: ['video', id],
    queryFn: () => api.get<VideoDetail>(`/videos/${id}`),
    enabled: !!id,
  })

  const addTagMutation = useMutation({
    mutationFn: (name: string) => api.post(`/tags/${id}/tags`, { name }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['video', id] }); setNewTag(''); setShowTagInput(false) },
  })

  const removeTagMutation = useMutation({
    mutationFn: (tagId: number) => api.delete(`/tags/${id}/tags/${tagId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['video', id] }),
  })

  const shareMutation = useMutation({
    mutationFn: (share: boolean) => api.post(`/videos/${id}/${share ? 'share' : 'unshare'}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['video', id] }),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/videos/${id}`),
    onSuccess: () => navigate('/library'),
  })

  if (isLoading) return <div className="flex justify-center py-20 opacity-50">{t('detail.loading')}</div>
  if (isError) return (
    <div className="flex flex-col items-center py-20 gap-4">
      <p className="text-sm" style={{ color: 'var(--color-danger)' }}>
        {error instanceof Error ? error.message : t('detail.notFound')}
      </p>
      <button onClick={() => navigate('/library')} className="text-sm opacity-60 hover:opacity-100">
        {t('detail.back')}
      </button>
    </div>
  )
  if (!video) return <div className="text-center py-20 opacity-50">{t('detail.notFound')}</div>

  return (
    <div className="space-y-6">
      {/* Header */}
      <button onClick={() => navigate(-1)} className="flex items-center gap-1.5 text-sm opacity-60 hover:opacity-100 transition-opacity">
        <ArrowLeft size={16} /> {t('detail.back')}
      </button>

      {/* Video Info */}
      <div className="flex gap-5">
        {video.thumbnail_url && (
          <img src={video.thumbnail_url} alt="" className="w-64 h-40 object-cover rounded-xl shrink-0" />
        )}
        <div className="flex-1 min-w-0 space-y-3">
          <h1 className="text-xl font-bold">{video.title}</h1>
          <div className="flex items-center gap-3 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            <span>{platformLabel(video.platform)}</span>
            <span>{formatDuration(video.duration_seconds)}</span>
            {video.upload_date && <span>{video.upload_date}</span>}
            <a href={video.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 hover:underline" style={{ color: 'var(--color-primary)' }}>
              <ExternalLink size={14} /> {t('detail.openOriginal')}
            </a>
          </div>

          {/* Tags */}
          <div className="flex gap-1.5 items-center flex-wrap">
            {video.tags.map((tag) => (
              <span key={tag.id} className="flex items-center gap-1 px-2.5 py-1 rounded-full text-xs" style={{ background: tag.color + '20', color: tag.color }}>
                {tag.name}
                {!isViewer && (
                  <button onClick={() => removeTagMutation.mutate(tag.id)} className="hover:opacity-70"><X size={12} /></button>
                )}
              </span>
            ))}
            {!isViewer && (
              showTagInput ? (
                <form onSubmit={(e) => { e.preventDefault(); newTag.trim() && addTagMutation.mutate(newTag.trim()) }} className="flex gap-1">
                  <input
                    type="text" value={newTag} onChange={(e) => setNewTag(e.target.value)}
                    className="px-2 py-1 rounded text-xs w-24 outline-none"
                    style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
                    placeholder={t('detail.tagPlaceholder')}
                    autoFocus
                  />
                  <button type="button" onClick={() => setShowTagInput(false)} className="text-xs opacity-50"><X size={14} /></button>
                </form>
              ) : (
                <button onClick={() => setShowTagInput(true)} className="flex items-center gap-0.5 px-2 py-1 rounded-full text-xs" style={{ border: '1px dashed var(--color-border)', color: 'var(--color-text-secondary)' }}>
                  <Plus size={12} /> {t('detail.addTag')}
                </button>
              )
            )}
          </div>

          {/* Actions — hidden for viewer */}
          {!isViewer && (
            <div className="flex gap-2">
              <button onClick={() => shareMutation.mutate(!video.is_public)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs"
                style={{ border: '1px solid var(--color-border)' }}>
                {video.is_public ? <><Lock size={13} /> {t('detail.makePrivate')}</> : <><Share2 size={13} /> {t('detail.shareToPublic')}</>}
              </button>
              <button onClick={() => { if (window.confirm(t('detail.confirmDeleteVideo'))) deleteMutation.mutate() }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--color-danger)', border: '1px solid var(--color-border)' }}>
                <Trash2 size={13} /> {t('common.delete')}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Summary */}
      <div className="p-5 rounded-xl relative" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <h2 className="font-semibold">{t('detail.summary')}</h2>
            <a
              href={generatePlaybackUrl(video.platform, video.video_id, 0)}
              target="_blank"
              rel="noopener noreferrer"
              className="p-1.5 rounded-lg hover:opacity-70 transition-opacity"
              style={{ color: 'var(--color-primary)' }}
              title={t('detail.playFromBeginning')}
            >
              <Play size={16} />
            </a>
          </div>
          {video.summary_en && <LangToggle lang={lang} onChange={setLang} />}
        </div>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
          {lang === 'en' && video.summary_en ? video.summary_en : video.summary}
        </p>
        {(() => {
          let usage: UsageInfo | undefined
          if (video.usage_json) {
            try { usage = JSON.parse(video.usage_json) } catch { /* ignore */ }
          }
          return usage ? (
            <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--color-border)' }}>
              <UsageDisplay usage={usage} />
            </div>
          ) : null
        })()}
      </div>

      {/* Segments / Mind map */}
      <div>
        <div className="flex gap-1 p-1 rounded-lg w-fit mb-4" style={{ background: 'var(--color-bg-tertiary)' }}>
          <button
            type="button"
            onClick={() => setActiveView('segments')}
            className={cn(
              'px-4 py-2 rounded-md text-sm font-medium transition-colors',
              activeView === 'segments' ? 'shadow-sm' : 'opacity-60 hover:opacity-100',
            )}
            style={activeView === 'segments' ? { background: 'var(--color-bg)', color: 'var(--color-primary)' } : {}}
          >
            {t('detail.segmentsCount', { count: video.segments.length })}
          </button>
          <button
            type="button"
            onClick={() => setActiveView('mindmap')}
            className={cn(
              'px-4 py-2 rounded-md text-sm font-medium transition-colors',
              activeView === 'mindmap' ? 'shadow-sm' : 'opacity-60 hover:opacity-100',
            )}
            style={activeView === 'mindmap' ? { background: 'var(--color-bg)', color: 'var(--color-primary)' } : {}}
          >
            {t('detail.mindMap')}
          </button>
        </div>

        {activeView === 'segments' ? (
          <div className="space-y-2">
            {video.segments.map((seg) => (
              <SegmentCard
                key={seg.id}
                seg={seg}
                videoId={video.id}
                platform={video.platform}
                platformVideoId={video.video_id}
                lang={lang}
              />
            ))}
          </div>
        ) : (
          <MindmapView videoId={video.id} platform={video.platform} videoVideoId={video.video_id} />
        )}
      </div>
    </div>
  )
}
