import { create } from 'zustand';
import { createSSE } from '../lib/api';
import type { MindmapData } from '../components/MindmapView';

/** Placeholder when the server emits an error without a message; map to i18n in the UI. */
export const MINDMAP_GENERIC_STREAM_ERROR = '__mindmap_generic_stream_error__';

const sseHandles = new Map<number, { cancel: () => void }>();

type ProgressLabelKey = '' | 'mindmap.analyzingTheme' | 'mindmap.extractingQuotes';

export interface MindmapGenerationState {
  generating: boolean;
  progressLabel: ProgressLabelKey;
  streamError: string;
  data: MindmapData | null;
  videoId: number | null;
}

function defaultGen(videoId: number): MindmapGenerationState {
  return {
    generating: false,
    progressLabel: '',
    streamError: '',
    data: null,
    videoId,
  };
}

interface MindmapStore {
  generations: Record<number, MindmapGenerationState>;
  startGeneration: (videoId: number, refresh: boolean) => void;
  /** Per-video generation slice; `null` if none yet for this id. */
  getState: (videoId: number) => MindmapGenerationState | null;
  clearError: (videoId: number) => void;
}

function patchGeneration(
  generations: Record<number, MindmapGenerationState>,
  videoId: number,
  patch: Partial<MindmapGenerationState>,
): Record<number, MindmapGenerationState> {
  const prev = generations[videoId] ?? defaultGen(videoId);
  return {
    ...generations,
    [videoId]: { ...prev, ...patch, videoId },
  };
}

export const useMindmapStore = create<MindmapStore>((set, get) => ({
  generations: {},

  getState: (videoId) => get().generations[videoId] ?? null,

  clearError: (videoId) => {
    set((s) => {
      const cur = s.generations[videoId];
      if (!cur) return s;
      return { generations: patchGeneration(s.generations, videoId, { streamError: '' }) };
    });
  },

  startGeneration: (videoId, refresh) => {
    sseHandles.get(videoId)?.cancel();
    sseHandles.delete(videoId);

    set((s) => {
      const prevGen = s.generations[videoId];
      return {
        generations: patchGeneration(s.generations, videoId, {
          generating: true,
          progressLabel: 'mindmap.analyzingTheme',
          streamError: '',
          data: prevGen?.data ?? null,
          videoId,
        }),
      };
    });

    const path = `/videos/${videoId}/mindmap${refresh ? '?refresh=true' : ''}`;
    const sse = createSSE(path, {}, (evt, raw) => {
      const d = raw as {
        stage?: string;
        message?: string;
        progress?: number;
        data?: MindmapData;
      };

      if (evt === 'error' || d.stage === 'error') {
        sseHandles.delete(videoId);
        const raw = typeof d.message === 'string' ? d.message.trim() : '';
        const streamError = raw || MINDMAP_GENERIC_STREAM_ERROR;
        set((s) => ({
          generations: patchGeneration(s.generations, videoId, {
            generating: false,
            progressLabel: '',
            streamError,
            videoId,
          }),
        }));
        return;
      }

      if (d.stage === 'generating') {
        set((s) => ({
          generations: patchGeneration(s.generations, videoId, {
            progressLabel: 'mindmap.analyzingTheme',
            videoId,
          }),
        }));
        return;
      }

      if (d.stage === 'stage1_done') {
        set((s) => ({
          generations: patchGeneration(s.generations, videoId, {
            progressLabel: 'mindmap.extractingQuotes',
            videoId,
          }),
        }));
        return;
      }

      if (d.stage === 'complete') {
        sseHandles.delete(videoId);
        if (d.data) {
          set((s) => ({
            generations: patchGeneration(s.generations, videoId, {
              data: d.data ?? null,
              generating: false,
              progressLabel: '',
              streamError: '',
              videoId,
            }),
          }));
        } else {
          set((s) => ({
            generations: patchGeneration(s.generations, videoId, {
              generating: false,
              progressLabel: '',
              streamError: MINDMAP_GENERIC_STREAM_ERROR,
              videoId,
            }),
          }));
        }
        return;
      }

      if (evt === 'done') {
        sseHandles.delete(videoId);
        const cur = get().generations[videoId];
        if (cur?.generating) {
          set((s) => ({
            generations: patchGeneration(s.generations, videoId, {
              generating: false,
              progressLabel: '',
              videoId,
            }),
          }));
        }
      }
    });

    sseHandles.set(videoId, sse);
  },
}));
