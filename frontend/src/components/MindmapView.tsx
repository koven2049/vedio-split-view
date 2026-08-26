import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Brain, Loader2, Play, Quote, RefreshCw, Sparkles } from 'lucide-react';
import LangToggle from './LangToggle';
import MindmapTree from './MindmapTree';
import UsageDisplay from './UsageDisplay';
import { api } from '../lib/api';
import { generatePlaybackUrl } from '../lib/utils';
import { useLangPreference } from '../hooks/useLangPreference';
import type { UsageInfo } from '../stores/analysisStore';
import { useAuthStore } from '../stores/authStore';
import { MINDMAP_GENERIC_STREAM_ERROR, useMindmapStore } from '../stores/mindmapStore';
import { useT } from '../i18n';
import type { TranslationKey } from '../i18n';

const CHAPTER_BORDER_COLORS = ['#3b82f6', '#a855f7', '#ec4899', '#f59e0b', '#10b981', '#6366f1'];

export interface MindmapViewProps {
  videoId: number;
  platform: string;
  videoVideoId: string;
}

export interface MindmapKeyPoint {
  text: string;
  text_en: string;
}

export interface MindmapQuote {
  text: string;
  text_en: string;
  time_ref: string;
}

export interface MindmapChapter {
  title: string;
  title_en: string;
  summary: string;
  summary_en: string;
  key_points: MindmapKeyPoint[];
  quotes?: MindmapQuote[];
}

export interface MindmapUsageShape {
  model?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  llm_model?: string;
  llm_prompt_tokens?: number;
  llm_completion_tokens?: number;
  llm_total_tokens?: number;
}

export interface MindmapData {
  status?: string;
  chapters?: MindmapChapter[];
  usage?: MindmapUsageShape;
  generated_at?: string;
  quotes_note?: string;
}

function parseTimeRefToSeconds(ref: string): number {
  const trimmed = ref.trim();
  if (!trimmed) return 0;
  const parts = trimmed.split(':').map((p) => parseInt(p.replace(/\D/g, ''), 10));
  if (parts.some((n) => Number.isNaN(n))) return 0;
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 1) return parts[0];
  return 0;
}

function mindmapUsageToUsageInfo(u: MindmapUsageShape | undefined): UsageInfo | null {
  if (!u) return null;
  const model = u.llm_model ?? u.model;
  const pt = u.llm_prompt_tokens ?? u.prompt_tokens;
  const ct = u.llm_completion_tokens ?? u.completion_tokens;
  const tt = u.llm_total_tokens ?? u.total_tokens;
  if (!model || !tt || tt <= 0) return null;
  return {
    asr_duration_seconds: 0,
    asr_model: '',
    llm_prompt_tokens: pt ?? 0,
    llm_completion_tokens: ct ?? 0,
    llm_total_tokens: tt,
    llm_model: model,
  };
}

function countMindmapStats(chapters: MindmapChapter[]) {
  let keyPoints = 0;
  let quotes = 0;
  for (const ch of chapters) {
    keyPoints += ch.key_points?.length ?? 0;
    quotes += ch.quotes?.length ?? 0;
  }
  return { chapters: chapters.length, keyPoints, quotes };
}

export default function MindmapView({ videoId, platform, videoVideoId }: MindmapViewProps) {
  const { lang, setLang } = useLangPreference();
  const isViewer = useAuthStore((s) => s.isViewer)();
  const t = useT();
  const gen = useMindmapStore((s) => s.generations[videoId]);
  const startGeneration = useMindmapStore((s) => s.startGeneration);

  const [serverData, setServerData] = useState<MindmapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState('');
  const [regenConfirmOpen, setRegenConfirmOpen] = useState(false);

  const loadMindmap = useCallback(async () => {
    setLoading(true);
    setFetchError('');
    const cached = useMindmapStore.getState().getState(videoId);
    if (cached?.data?.chapters && cached.data.chapters.length > 0) {
      setLoading(false);
      return;
    }
    try {
      const res = await api.get<MindmapData>(`/videos/${videoId}/mindmap`);
      setServerData(res);
    } catch (e: unknown) {
      setFetchError(e instanceof Error ? e.message : t('mindmap.loadFailed'));
      setServerData(null);
    } finally {
      setLoading(false);
    }
  }, [videoId, t]);

  // videoId 变化时在渲染期同步重置（React 推荐的 adjust-state-during-render 模式），
  // 避免在 effect 里同步 setState 造成级联渲染
  const [prevVideoId, setPrevVideoId] = useState(videoId);
  if (prevVideoId !== videoId) {
    setPrevVideoId(videoId);
    setServerData(null);
    setFetchError('');
  }

  useEffect(() => {
    // 延迟到微任务：loadMindmap 开头会同步 setLoading/setFetchError，
    // 直接在 effect 体内同步调用会触发级联渲染
    void Promise.resolve().then(loadMindmap);
  }, [loadMindmap]);

  const data = gen?.data ?? serverData;
  const generating = gen?.generating ?? false;
  const progressLabelKey = gen?.progressLabel ?? '';
  const progressLabel = progressLabelKey ? t(progressLabelKey as TranslationKey) : '';
  const streamErrorRaw = gen?.streamError ?? '';
  const streamError =
    streamErrorRaw === MINDMAP_GENERIC_STREAM_ERROR
      ? t('mindmap.generationFailed')
      : streamErrorRaw;

  const chapters = data?.chapters ?? [];
  const hasChapters = chapters.length > 0;
  const notGenerated = !loading && !fetchError && data?.status === 'not_generated';

  const stats = hasChapters ? countMindmapStats(chapters) : null;
  const usageForDisplay = mindmapUsageToUsageInfo(data?.usage);

  if (loading) {
    return (
      <div
        className="flex justify-center py-16 gap-2 items-center text-sm"
        style={{ color: 'var(--color-text-secondary)' }}
      >
        <Loader2 size={18} className="animate-spin" />
        {t('mindmap.loading')}
      </div>
    );
  }

  if (fetchError) {
    return (
      <div
        className="p-4 rounded-xl flex items-start gap-3"
        style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-danger)' }}
      >
        <AlertCircle
          size={18}
          style={{ color: 'var(--color-danger)' }}
          className="mt-0.5 shrink-0"
        />
        <p className="text-sm">{fetchError}</p>
      </div>
    );
  }

  if (notGenerated && !hasChapters) {
    return (
      <div
        className="rounded-xl p-10 flex flex-col items-center text-center max-w-md mx-auto"
        style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
      >
        <div className="flex items-center gap-2 mb-4" style={{ color: 'var(--color-primary)' }}>
          <Brain size={28} />
          <Sparkles size={24} />
        </div>
        <h3 className="text-lg font-semibold mb-2" style={{ color: 'var(--color-text)' }}>
          {t('mindmap.title')}
        </h3>
        <p
          className="text-sm mb-6 leading-relaxed"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          {t('mindmap.description')}
        </p>
        {!isViewer && (
          <button
            type="button"
            disabled={generating}
            onClick={() => startGeneration(videoId, false)}
            className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--color-primary)' }}
          >
            {generating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {t('mindmap.generate')}
          </button>
        )}
        {generating && (
          <p
            className="text-sm mt-4 flex items-center gap-2"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            <Loader2 size={14} className="animate-spin" />
            {progressLabel}
          </p>
        )}
        {streamErrorRaw && (
          <p
            className="text-sm mt-4 flex items-center gap-2"
            style={{ color: 'var(--color-danger)' }}
          >
            <AlertCircle size={14} />
            {streamError}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {stats && (
          <div
            className="text-xs font-medium tabular-nums"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {t('mindmap.statsSummary', {
              chapters: stats.chapters,
              keyPoints: stats.keyPoints,
              quotes: stats.quotes,
            })}
          </div>
        )}
        <div className="flex items-center gap-2 ml-auto">
          {hasChapters && chapters.some((c) => c.title_en || c.summary_en) && (
            <LangToggle lang={lang} onChange={setLang} />
          )}
          {!isViewer && hasChapters && (
            <button
              type="button"
              title={t('mindmap.regenerate')}
              disabled={generating}
              onClick={() => setRegenConfirmOpen(true)}
              className="p-2 rounded-lg transition-opacity hover:opacity-80 disabled:opacity-40"
              style={{
                border: '1px solid var(--color-border)',
                color: 'var(--color-text-secondary)',
              }}
            >
              <RefreshCw size={16} className={generating ? 'animate-spin' : ''} />
            </button>
          )}
        </div>
      </div>

      {streamErrorRaw && (
        <div
          className="p-3 rounded-lg text-sm flex items-center gap-2"
          style={{ background: 'rgba(239,68,68,0.08)', color: 'var(--color-danger)' }}
        >
          <AlertCircle size={16} />
          {streamError}
        </div>
      )}

      {generating && hasChapters && (
        <div
          className="flex items-center gap-2 text-sm px-3 py-2 rounded-lg"
          style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}
        >
          <Loader2 size={16} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
          {progressLabel}
        </div>
      )}

      {data?.quotes_note && (
        <p className="text-xs px-1" style={{ color: 'var(--color-text-secondary)' }}>
          {data.quotes_note}
        </p>
      )}

      {hasChapters && <MindmapTree chapters={chapters} lang={lang} />}

      <div className="space-y-3">
        {chapters.map((ch, i) => {
          const border = CHAPTER_BORDER_COLORS[i % CHAPTER_BORDER_COLORS.length];
          const title = lang === 'en' && ch.title_en ? ch.title_en : ch.title;
          const summary = lang === 'en' && ch.summary_en ? ch.summary_en : ch.summary;
          const points = ch.key_points ?? [];
          const quotes = ch.quotes ?? [];

          return (
            <div
              key={i}
              className="rounded-xl p-5 pl-4"
              style={{
                background: 'var(--color-bg-secondary)',
                border: '1px solid var(--color-border)',
                borderLeftWidth: '4px',
                borderLeftColor: border,
              }}
            >
              <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--color-text)' }}>
                <span className="opacity-60 mr-2">#{i + 1}</span>
                {title}
              </h3>
              <p
                className="text-sm mb-4 leading-relaxed"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {summary}
              </p>
              {points.length > 0 && (
                <ul className="space-y-1.5 mb-4">
                  {points.map((kp, j) => (
                    <li
                      key={j}
                      className="text-sm leading-relaxed"
                      style={{ color: 'var(--color-text)' }}
                    >
                      <span className="mr-1.5 opacity-50">•</span>
                      {lang === 'en' && kp.text_en ? kp.text_en : kp.text}
                    </li>
                  ))}
                </ul>
              )}
              {quotes.length > 0 && (
                <div
                  className="space-y-3 pt-2"
                  style={{ borderTop: '1px solid var(--color-border)' }}
                >
                  <div
                    className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider"
                    style={{ color: 'var(--color-text-secondary)' }}
                  >
                    <Quote size={12} />
                    {t('mindmap.quotes')}
                  </div>
                  {quotes.map((q, k) => {
                    const qtext = lang === 'en' && q.text_en ? q.text_en : q.text;
                    const sec = parseTimeRefToSeconds(q.time_ref);
                    const href = generatePlaybackUrl(platform, videoVideoId, sec);
                    return (
                      <blockquote
                        key={k}
                        className="text-sm italic pl-3 border-l-2 opacity-95"
                        style={{ borderColor: 'var(--color-border)', color: 'var(--color-text)' }}
                      >
                        <span className="not-italic opacity-40 mr-1">&ldquo;</span>
                        {qtext}
                        <span className="not-italic opacity-40 ml-1">&rdquo;</span>
                        {q.time_ref && (
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="not-italic inline-flex items-center gap-1 ml-2 text-xs font-medium hover:underline"
                            style={{ color: 'var(--color-primary)' }}
                          >
                            <Play size={12} />
                            {q.time_ref}
                          </a>
                        )}
                      </blockquote>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {(data?.generated_at || usageForDisplay) && (
        <div className="pt-4 mt-2 space-y-2" style={{ borderTop: '1px solid var(--color-border)' }}>
          {data?.generated_at && (
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              {t('detail.generated')} {new Date(data.generated_at).toLocaleString()}
            </p>
          )}
          {usageForDisplay && <UsageDisplay usage={usageForDisplay} />}
        </div>
      )}

      {regenConfirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <button
            type="button"
            className="absolute inset-0 bg-black/40 backdrop-blur-sm cursor-default"
            aria-label={t('common.dismiss')}
            onClick={() => setRegenConfirmOpen(false)}
          />
          <div
            className="relative w-full max-w-sm mx-4 rounded-2xl shadow-2xl p-6 space-y-4"
            style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <h3 className="text-base font-semibold">{t('mindmap.regenerateConfirm')}</h3>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
              {t('mindmap.regenerateWarning')}
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setRegenConfirmOpen(false)}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-opacity hover:opacity-80"
                style={{
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border)',
                }}
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={() => {
                  setRegenConfirmOpen(false);
                  startGeneration(videoId, true);
                }}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90"
                style={{ background: 'var(--color-primary)' }}
              >
                {t('analyze.continue')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
