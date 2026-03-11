import type { UsageInfo } from '../stores/analysisStore'

interface UsageDisplayProps {
  usage: UsageInfo
  className?: string
}

export default function UsageDisplay({ usage, className }: UsageDisplayProps) {
  const items: Array<{ label: string; value: string }> = []

  if (usage.asr_model && usage.asr_duration_seconds > 0) {
    items.push({
      label: `ASR (${usage.asr_model})`,
      value: `${Math.round(usage.asr_duration_seconds)}s`,
    })
  }

  if (usage.llm_model && usage.llm_total_tokens > 0) {
    items.push({
      label: `LLM (${usage.llm_model})`,
      value: `${usage.llm_total_tokens.toLocaleString()} tokens`,
    })
  }

  if (items.length === 0) return null

  return (
    <div
      className={`flex flex-wrap gap-x-4 gap-y-1 text-xs ${className ?? ''}`}
      style={{ color: 'var(--color-text-secondary)' }}
    >
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <span className="opacity-60">{item.label}:</span>
          <span className="font-medium tabular-nums">{item.value}</span>
        </span>
      ))}
    </div>
  )
}
