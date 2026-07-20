import { create } from 'zustand'
import { api } from '../lib/api'
import { useAuthStore } from './authStore'

export type AnalysisPlatform = 'youtube' | 'bilibili' | 'xiaoyuzhou'

/**
 * Strongly-typed view of ProgressData.detail for the audio download stage.
 * Backend sends { ratio, downloaded_bytes, total_bytes } when relaying chunked
 * download progress (see spec 3.1/3.2). Other stages keep detail as a generic
 * record — only audio_download populates these byte fields today.
 */
export interface DownloadProgressDetail {
  ratio?: number
  downloaded_bytes?: number
  total_bytes?: number
}

export interface ProgressData {
  stage: string
  progress: number
  message: string
  detail?: Record<string, unknown>
}

/** Error codes surfaced from backend XiaoyuzhouError (spec 3.4). */
export type XiaoyuzhouErrorCode = 'cdn_expired' | 'paid_private' | 'page_changed' | 'not_episode'

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
  slotId: string
  taskId: number | null
  platform: string
  status?: string
  title?: string
  uploader?: string
  uploadDate?: string
  durationSeconds?: number
  thumbnailUrl?: string
  analyzing: boolean
  url: string
  progress: ProgressData | null
  stepLog: StepEntry[]
  result: AnalysisResult | null
  error: string
  /** Backend error_code (e.g. cdn_expired) when the failure was typed. */
  errorCode?: string
  completedVideoId: number | null
  pendingConfirm: ConfirmInfo | null
  reconnected: boolean
  _sseAbort: AbortController | null
}

const EMPTY_SLOT: Omit<SlotState, 'slotId' | 'platform'> = {
  taskId: null,
  analyzing: false,
  url: '',
  progress: null,
  stepLog: [],
  result: null,
  error: '',
  errorCode: undefined,
  completedVideoId: null,
  pendingConfirm: null,
  reconnected: false,
  _sseAbort: null,
}

interface AnalysisState {
  slots: Record<string, SlotState>
  _nextId: number
  _initialized: boolean

  getSlot: (slotId: string) => SlotState | null
  getActiveSlots: () => SlotState[]
  countAnalyzing: () => number
  startAnalysis: (platform: AnalysisPlatform | string, url: string) => Promise<string>
  retryTask: (platform: AnalysisPlatform | string, taskId: number, taskUrl?: string, taskStatus?: string) => Promise<string>
  cancelAnalysis: (slotId: string) => void
  confirmTask: (slotId: string) => void
  declineTask: (slotId: string) => void
  removeSlot: (slotId: string) => Promise<void>
  dismissSlot: (slotId: string) => void
  fetchResultDetails: (slotId: string, videoId: number) => void
  isAnyAnalyzing: () => boolean
  reconnectActiveTasks: () => Promise<void>
}

interface ActiveTask {
  task_id: number
  platform: string
  url: string
  finished: boolean
  last_progress: { event: string; data: string } | null
}

interface RecoverableTask {
  id: number
  url: string
  platform: string
  status: string
  video_title: string
  error_message: string
}

const BASE = '/api'

const DISMISSED_TASKS_KEY = 'vsplit_dismissed_tasks'

function getDismissedTaskIds(): Set<number> {
  try {
    const raw = localStorage.getItem(DISMISSED_TASKS_KEY)
    if (!raw) return new Set()
    return new Set(JSON.parse(raw) as number[])
  } catch {
    return new Set()
  }
}

function addDismissedTaskId(taskId: number): void {
  const ids = getDismissedTaskIds()
  ids.add(taskId)
  localStorage.setItem(DISMISSED_TASKS_KEY, JSON.stringify([...ids]))
}

function removeDismissedTaskId(taskId: number): void {
  const ids = getDismissedTaskIds()
  ids.delete(taskId)
  if (ids.size === 0) {
    localStorage.removeItem(DISMISSED_TASKS_KEY)
  } else {
    localStorage.setItem(DISMISSED_TASKS_KEY, JSON.stringify([...ids]))
  }
}

function progressFromTaskStatus(task: RecoverableTask): ProgressData | null {
  switch (task.status) {
    case 'downloading':
      return { stage: 'audio_download', progress: 15, message: task.video_title || 'Download in progress before restart.' }
    case 'downloaded':
      return { stage: 'audio_download', progress: 55, message: 'Audio downloaded. Ready to resume transcription.' }
    case 'transcribing':
    case 'failed_transcribe':
      return { stage: 'transcription', progress: 56, message: task.error_message || 'Transcription needs attention.' }
    case 'analyzing':
    case 'failed_analyze':
      return { stage: 'analysis', progress: 87, message: task.error_message || 'Analysis needs attention.' }
    case 'failed_download':
      return { stage: 'audio_download', progress: 15, message: task.error_message || 'Download did not complete.' }
    default:
      return null
  }
}

function stepLogFromRecoverableTask(task: RecoverableTask): StepEntry[] {
  const message = task.error_message || task.video_title || task.url
  const detail: Record<string, unknown> = { recovered: true, task_status: task.status }
  if (task.video_title) detail.video_title = task.video_title
  return [{ stage: 'error', message, detail, timestamp: Date.now() }]
}

function connectSSE(
  taskId: number,
  slotId: string,
  set: (fn: (s: AnalysisState) => Partial<AnalysisState>) => void,
  get: () => AnalysisState,
): AbortController {
  const ctrl = new AbortController()
  const token = useAuthStore.getState().token

  fetch(`${BASE}/videos/tasks/${taskId}/stream`, {
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    signal: ctrl.signal,
  }).then(async (res) => {
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      updateSlot(set, slotId, () => ({
        error: err.detail || `Error ${res.status}`,
        analyzing: false,
      }))
      return
    }
    const reader = res.body?.getReader()
    if (!reader) return
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          try {
            const data = JSON.parse(line.slice(5).trim())
            processEvent(set, get, slotId, currentEvent || data.stage, data)
          } catch { /* skip malformed */ }
        }
      }
    }
    const slot = get().slots[slotId]
    if (slot?.analyzing) {
      updateSlot(set, slotId, () => ({ analyzing: false }))
    }
  }).catch((err) => {
    if (err.name !== 'AbortError') {
      updateSlot(set, slotId, () => ({
        error: err.message,
        analyzing: false,
      }))
    }
  })

  return ctrl
}

function updateSlot(
  set: (fn: (s: AnalysisState) => Partial<AnalysisState>) => void,
  slotId: string,
  updater: (slot: SlotState) => Partial<SlotState>,
) {
  set((s) => {
    const prev = s.slots[slotId]
    if (!prev) return {}
    return { slots: { ...s.slots, [slotId]: { ...prev, ...updater(prev) } } }
  })
}

function processEvent(
  set: (fn: (s: AnalysisState) => Partial<AnalysisState>) => void,
  _get: () => AnalysisState,
  slotId: string,
  _event: string,
  data: unknown,
) {
  const d = data as ProgressData

  if (_event === 'error' || d.stage === 'error') {
    const errorCode = typeof d.detail?.error_code === 'string' ? (d.detail.error_code as string) : undefined
    updateSlot(set, slotId, (slot) => ({
      error: d.message,
      errorCode,
      analyzing: false,
      stepLog: [...slot.stepLog, { stage: 'error', message: d.message, detail: d.detail, timestamp: Date.now() }],
    }))
    return
  }

  if (_event === 'cancelled' || d.stage === 'cancelled') {
    updateSlot(set, slotId, () => ({ analyzing: false, progress: null, pendingConfirm: null }))
    return
  }

  if (_event === 'confirm_required' || d.stage === 'confirm_required') {
    updateSlot(set, slotId, (slot) => ({
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

  updateSlot(set, slotId, (slot) => {
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

    if (d.stage === 'metadata' && d.detail?.title) {
      updates.title = d.detail.title as string
      if (d.detail.uploader) updates.uploader = d.detail.uploader as string
      if (d.detail.upload_date) updates.uploadDate = d.detail.upload_date as string
      if (d.detail.duration_seconds) updates.durationSeconds = d.detail.duration_seconds as number
      if (d.detail.thumbnail_url) updates.thumbnailUrl = d.detail.thumbnail_url as string
    }

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
  _nextId: 1,
  _initialized: false,

  getSlot: (slotId: string): SlotState | null => {
    return get().slots[slotId] ?? null
  },

  getActiveSlots: (): SlotState[] => {
    return Object.values(get().slots)
      .filter((s) => s.analyzing || s.result || s.error || s.progress)
  },

  countAnalyzing: (): number => {
    return Object.values(get().slots).filter((s) => s.analyzing).length
  },

  startAnalysis: async (platform: AnalysisPlatform | string, url: string): Promise<string> => {
    const id = get()._nextId
    const slotId = `slot-${id}`

    set((s) => ({
      _nextId: s._nextId + 1,
      slots: {
        ...s.slots,
        [slotId]: {
          ...EMPTY_SLOT,
          slotId,
          platform,
          status: undefined,
          title: undefined,
          analyzing: true,
          url,
          progress: { stage: 'metadata', progress: 0, message: 'Starting...' },
        },
      },
    }))

    try {
      const resp = await api.post<{ task_id: number; platform: string }>('/videos/analyze', { url })
      updateSlot(set, slotId, () => ({ taskId: resp.task_id }))
      const ctrl = connectSSE(resp.task_id, slotId, set, get)
      updateSlot(set, slotId, () => ({ _sseAbort: ctrl }))
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to start analysis'
      updateSlot(set, slotId, () => ({ error: msg, analyzing: false, progress: null }))
    }

    return slotId
  },

  retryTask: async (platform: AnalysisPlatform | string, taskId: number, taskUrl?: string, taskStatus?: string): Promise<string> => {
    const id = get()._nextId
    const slotId = `slot-${id}`

    const initialProgress: ProgressData = taskStatus === 'failed_download'
      ? { stage: 'audio_download', progress: 15, message: 'Resuming download...' }
      : taskStatus === 'failed_analyze'
        ? { stage: 'analysis', progress: 87, message: 'Resuming analysis...' }
        : { stage: 'transcription', progress: 56, message: 'Resuming...' }

    set((s) => ({
      _nextId: s._nextId + 1,
      slots: {
        ...s.slots,
        [slotId]: {
          ...EMPTY_SLOT,
          slotId,
          platform,
          status: undefined,
          title: undefined,
          analyzing: true,
          url: taskUrl ?? '',
          taskId,
          progress: initialProgress,
        },
      },
    }))

    try {
      const resp = await api.post<{ task_id: number }>(`/videos/tasks/${taskId}/retry`)
      updateSlot(set, slotId, () => ({ taskId: resp.task_id }))
      const ctrl = connectSSE(resp.task_id, slotId, set, get)
      updateSlot(set, slotId, () => ({ _sseAbort: ctrl }))
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to retry'
      updateSlot(set, slotId, () => ({ error: msg, analyzing: false, progress: null }))
    }

    return slotId
  },

  cancelAnalysis: (slotId: string) => {
    const slot = get().slots[slotId]
    if (!slot) return
    slot._sseAbort?.abort()
    if (slot.taskId) {
      api.delete(`/videos/tasks/${slot.taskId}/cancel`).catch(() => {})
    }
    updateSlot(set, slotId, () => ({ analyzing: false, progress: null, pendingConfirm: null, _sseAbort: null }))
  },

  confirmTask: (slotId: string) => {
    const slot = get().slots[slotId]
    if (!slot?.pendingConfirm) return
    api.post(`/videos/tasks/${slot.pendingConfirm.taskId}/confirm`).catch(() => {})
    updateSlot(set, slotId, () => ({ pendingConfirm: null }))
  },

  declineTask: (slotId: string) => {
    const slot = get().slots[slotId]
    if (!slot?.pendingConfirm) return
    const taskId = slot.pendingConfirm.taskId
    slot._sseAbort?.abort()
    api.delete(`/videos/tasks/${taskId}/cancel`).catch(() => {})
    updateSlot(set, slotId, () => ({
      analyzing: false, progress: null, pendingConfirm: null, _sseAbort: null,
    }))
  },

  removeSlot: async (slotId: string) => {
    const slot = get().slots[slotId]
    slot?._sseAbort?.abort()
    const taskId = slot?.taskId
    set((s) => {
      const { [slotId]: _, ...rest } = s.slots
      return { slots: rest }
    })
    if (taskId) {
      addDismissedTaskId(taskId)
      try {
        await api.delete(`/videos/tasks/${taskId}`)
        removeDismissedTaskId(taskId)
      } catch { /* kept in dismissed list for next reconnect retry */ }
    }
  },

  dismissSlot: (slotId: string) => {
    const slot = get().slots[slotId]
    slot?._sseAbort?.abort()
    if (slot?.taskId) {
      addDismissedTaskId(slot.taskId)
    }
    set((s) => {
      const { [slotId]: _, ...rest } = s.slots
      return { slots: rest }
    })
  },

  fetchResultDetails: (slotId: string, videoId: number) => {
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
      updateSlot(set, slotId, () => ({ result }))
    })
  },

  isAnyAnalyzing: () => {
    return Object.values(get().slots).some((s) => s.analyzing)
  },

  reconnectActiveTasks: async () => {
    if (get()._initialized) return
    set(() => ({ _initialized: true }))

    try {
      const dismissed = getDismissedTaskIds()
      const [activeTasks, recoverableTasks] = await Promise.all([
        api.get<ActiveTask[]>('/videos/tasks/active').catch(() => []),
        api.get<RecoverableTask[]>('/videos/tasks/recoverable').catch(() => []),
      ])

      for (const task of activeTasks) {
        if (dismissed.has(task.task_id)) continue
        const existingSlot = Object.values(get().slots).find(
          (s) => s.taskId === task.task_id
        )
        if (existingSlot) continue

        const id = get()._nextId
        const slotId = `slot-${id}`

        set((s) => ({
          _nextId: s._nextId + 1,
          slots: {
            ...s.slots,
            [slotId]: {
              ...EMPTY_SLOT,
              slotId,
              platform: task.platform,
              status: undefined,
              title: undefined,
              analyzing: !task.finished,
              url: task.url,
              taskId: task.task_id,
              reconnected: true,
              progress: { stage: 'metadata', progress: 0, message: 'Reconnecting...' },
            },
          },
        }))

        const ctrl = connectSSE(task.task_id, slotId, set, get)
        updateSlot(set, slotId, () => ({ _sseAbort: ctrl }))
      }

      for (const id of dismissed) {
        api.delete(`/videos/tasks/${id}`).then(() => removeDismissedTaskId(id)).catch(() => {})
      }

      const MAX_RECOVERABLE_DISPLAY = 3
      let recoverableCount = 0
      for (const task of recoverableTasks) {
        if (recoverableCount >= MAX_RECOVERABLE_DISPLAY) break
        if (dismissed.has(task.id)) continue
        const existingSlot = Object.values(get().slots).find(
          (s) => s.taskId === task.id
        )
        if (existingSlot) continue
        recoverableCount++

        const id = get()._nextId
        const slotId = `slot-${id}`
        const recoveredProgress = progressFromTaskStatus(task)
        const recoveredError = task.status.startsWith('failed') ? (task.error_message || 'Task needs attention.') : ''
        const recoveredLog = recoveredError ? stepLogFromRecoverableTask(task) : []

        set((s) => ({
          _nextId: s._nextId + 1,
          slots: {
            ...s.slots,
            [slotId]: {
              ...EMPTY_SLOT,
              slotId,
              platform: task.platform,
              status: task.status,
              title: task.video_title,
              analyzing: false,
              url: task.url,
              taskId: task.id,
              reconnected: true,
              progress: recoveredProgress,
              error: recoveredError,
              stepLog: recoveredLog,
            },
          },
        }))
      }
    } catch {
      // silently fail — user will see no active tasks
    }
  },
}))
