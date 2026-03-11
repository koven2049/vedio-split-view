import { create } from 'zustand'
import { createSSE, api } from '../lib/api'

export interface ProgressData {
  stage: string
  progress: number
  message: string
  detail?: Record<string, unknown>
}

export interface StepEntry {
  stage: string
  message: string
  detail?: Record<string, unknown>
  timestamp: number
}

export interface Segment {
  index: number
  title: string
  title_en: string
  summary: string
  summary_en: string
  start_seconds: number
  end_seconds: number
}

export interface UsageInfo {
  asr_duration_seconds: number
  asr_model: string
  llm_prompt_tokens: number
  llm_completion_tokens: number
  llm_total_tokens: number
  llm_model: string
}

export interface AnalysisResult {
  video_id?: number
  title: string
  summary: string
  summary_en: string
  platform: string
  videoId: string
  upload_date: string
  duration_seconds: number
  segments: Segment[]
  thumbnail_url: string
  usage?: UsageInfo
}

export interface ConfirmInfo {
  taskId: number
  title: string
  durationSeconds: number
  message: string
}

export interface SlotState {
  analyzing: boolean
  url: string
  progress: ProgressData | null
  stepLog: StepEntry[]
  result: AnalysisResult | null
  error: string
  completedVideoId: number | null
  pendingConfirm: ConfirmInfo | null
  _sseCancel: (() => void) | null
}

const EMPTY_SLOT: SlotState = {
  analyzing: false,
  url: '',
  progress: null,
  stepLog: [],
  result: null,
  error: '',
  completedVideoId: null,
  pendingConfirm: null,
  _sseCancel: null,
}

interface AnalysisState {
  slots: Record<string, SlotState>

  getSlot: (platform: string) => SlotState
  startAnalysis: (platform: string, url: string) => void
  startTaskRetry: (platform: string, taskId: number, taskUrl?: string) => void
  cancelAnalysis: (platform: string) => void
  confirmTask: (platform: string) => void
  declineTask: (platform: string) => void
  reset: (platform: string) => void
  setResult: (platform: string, result: AnalysisResult) => void
  fetchResultDetails: (platform: string, videoId: number) => void
  isAnyAnalyzing: () => boolean
}

function updateSlot(
  set: (fn: (s: AnalysisState) => Partial<AnalysisState>) => void,
  platform: string,
  updater: (slot: SlotState) => Partial<SlotState>,
) {
  set((s) => {
    const prev = s.slots[platform] ?? { ...EMPTY_SLOT }
    return { slots: { ...s.slots, [platform]: { ...prev, ...updater(prev) } } }
  })
}

function processEvent(
  set: (fn: (s: AnalysisState) => Partial<AnalysisState>) => void,
  platform: string,
  _event: string,
  data: unknown,
) {
  const d = data as ProgressData

  if (_event === 'error' || d.stage === 'error') {
    updateSlot(set, platform, (slot) => ({
      error: d.message,
      analyzing: false,
      stepLog: [...slot.stepLog, { stage: 'error', message: d.message, detail: d.detail, timestamp: Date.now() }],
    }))
    return
  }

  if (_event === 'cancelled' || d.stage === 'cancelled') {
    updateSlot(set, platform, () => ({ analyzing: false, progress: null, pendingConfirm: null }))
    return
  }

  if (_event === 'confirm_required' || d.stage === 'confirm_required') {
    updateSlot(set, platform, (slot) => ({
      progress: d,
      pendingConfirm: {
        taskId: d.detail?.task_id as number,
        title: (d.detail?.title as string) ?? '',
        durationSeconds: (d.detail?.duration_seconds as number) ?? 0,
        message: d.message,
      },
      stepLog: [...slot.stepLog, { stage: d.stage, message: d.message, detail: d.detail, timestamp: Date.now() }],
    }))
    return
  }

  updateSlot(set, platform, (slot) => {
    const last = slot.stepLog[slot.stepLog.length - 1]
    let newLog = slot.stepLog

    if (!(last && last.stage === d.stage && last.message === d.message)) {
      const subStep = d.detail?.sub_step as string | undefined
      if (
        subStep === 'chunk_asr_polling' &&
        last?.detail?.sub_step === 'chunk_asr_polling' &&
        last?.detail?.chunk_index === d.detail?.chunk_index
      ) {
        newLog = [
          ...slot.stepLog.slice(0, -1),
          { stage: d.stage, message: d.message, detail: d.detail, timestamp: Date.now() },
        ]
      } else {
        newLog = [
          ...slot.stepLog,
          { stage: d.stage, message: d.message, detail: d.detail, timestamp: Date.now() },
        ]
      }
    }

    const updates: Partial<SlotState> = { progress: d, stepLog: newLog }

    if (d.stage === 'complete' && d.detail) {
      updates.analyzing = false
      updates.completedVideoId = d.detail.video_id as number
      updates.result = {
        video_id: d.detail.video_id as number,
        title: '', summary: '', summary_en: '', platform: '', videoId: '', upload_date: '',
        duration_seconds: 0, segments: [], thumbnail_url: '',
        usage: d.detail.usage as UsageInfo | undefined,
      }
    }

    return updates
  })
}

export const useAnalysisStore = create<AnalysisState>((set, get) => ({
  slots: {},

  getSlot: (platform: string): SlotState => {
    return get().slots[platform] ?? { ...EMPTY_SLOT }
  },

  startAnalysis: (platform: string, url: string) => {
    const prev = get().slots[platform]
    prev?._sseCancel?.()
    updateSlot(set, platform, () => ({
      analyzing: true,
      url,
      progress: { stage: 'metadata', progress: 0, message: 'Starting...' },
      stepLog: [],
      result: null,
      error: '',
      completedVideoId: null,
      pendingConfirm: null,
      _sseCancel: null,
    }))
    const sse = createSSE('/videos/analyze', { url }, (ev, data) => processEvent(set, platform, ev, data))
    updateSlot(set, platform, () => ({ _sseCancel: sse.cancel }))
  },

  startTaskRetry: (platform: string, taskId: number, taskUrl?: string) => {
    const prev = get().slots[platform]
    prev?._sseCancel?.()
    updateSlot(set, platform, (slot) => ({
      analyzing: true,
      url: taskUrl ?? slot.url,
      progress: { stage: 'transcription', progress: 56, message: 'Starting analysis...' },
      stepLog: [],
      result: null,
      error: '',
      completedVideoId: null,
      pendingConfirm: null,
      _sseCancel: null,
    }))
    const sse = createSSE(`/videos/tasks/${taskId}/retry`, {}, (ev, data) => processEvent(set, platform, ev, data))
    updateSlot(set, platform, () => ({ _sseCancel: sse.cancel }))
  },

  cancelAnalysis: (platform: string) => {
    const prev = get().slots[platform]
    prev?._sseCancel?.()
    updateSlot(set, platform, () => ({ analyzing: false, progress: null, pendingConfirm: null, _sseCancel: null }))
  },

  confirmTask: (platform: string) => {
    const slot = get().slots[platform]
    if (!slot?.pendingConfirm) return
    api.post(`/videos/tasks/${slot.pendingConfirm.taskId}/confirm`).catch(() => {})
    updateSlot(set, platform, () => ({ pendingConfirm: null }))
  },

  declineTask: (platform: string) => {
    const slot = get().slots[platform]
    if (!slot?.pendingConfirm) return
    const taskId = slot.pendingConfirm.taskId
    slot._sseCancel?.()
    api.delete(`/videos/tasks/${taskId}/cancel`).catch(() => {})
    updateSlot(set, platform, () => ({
      analyzing: false, progress: null, pendingConfirm: null, _sseCancel: null,
    }))
  },

  reset: (platform: string) => {
    const prev = get().slots[platform]
    prev?._sseCancel?.()
    updateSlot(set, platform, () => ({ ...EMPTY_SLOT }))
  },

  setResult: (platform: string, result: AnalysisResult) => {
    updateSlot(set, platform, () => ({ result }))
  },

  fetchResultDetails: (platform: string, videoId: number) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    api.get<any>(`/videos/${videoId}`).then((v) => {
      let usage: UsageInfo | undefined
      if (v.usage_json) {
        try { usage = JSON.parse(v.usage_json) } catch { /* ignore */ }
      }
      const result: AnalysisResult = {
        ...v,
        video_id: v.id as number,
        videoId: v.video_id as string,
        usage,
      }
      updateSlot(set, platform, () => ({ result }))
    })
  },

  isAnyAnalyzing: () => {
    return Object.values(get().slots).some((s) => s.analyzing)
  },
}))
