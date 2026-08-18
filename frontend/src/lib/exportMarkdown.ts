import { formatDuration, formatTimeRange, platformLabel } from './utils'

export interface MdSubtitle { start: number; duration: number; text: string }
export interface MdSegment {
  segment_index: number
  title: string; title_en: string
  summary: string; summary_en: string
  start_seconds: number; end_seconds: number
}
export interface MdVideo {
  url: string; platform: string
  title: string; upload_date: string
  duration_seconds: number
  summary: string; summary_en: string
  essence: string
  segments: MdSegment[]
  tags: { name: string }[]
}

function pick(zh: string, en: string, lang: 'zh' | 'en') {
  return lang === 'en' && en ? en : zh
}

function ts(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

/** Build the full analysis document, one `##` section per part. */
export function buildMarkdown(
  video: MdVideo,
  subtitles: MdSubtitle[],
  lang: 'zh' | 'en',
): string {
  const en = lang === 'en'
  const lines: string[] = []

  lines.push(`# ${video.title}`, '')
  const meta = [
    `- ${en ? 'Platform' : '平台'}: ${platformLabel(video.platform)}`,
    `- ${en ? 'Duration' : '时长'}: ${formatDuration(video.duration_seconds)}`,
  ]
  if (video.upload_date) meta.push(`- ${en ? 'Published' : '发布日期'}: ${video.upload_date}`)
  if (video.tags.length) meta.push(`- ${en ? 'Tags' : '标签'}: ${video.tags.map((tg) => tg.name).join(', ')}`)
  meta.push(`- ${en ? 'Source' : '原始链接'}: ${video.url}`)
  lines.push(...meta, '')

  lines.push(`## ${en ? 'Summary' : '摘要'}`, '', pick(video.summary, video.summary_en, lang) || '-', '')

  if (video.essence) {
    lines.push(`## ${en ? 'Essence' : '精华总结'}`, '', video.essence, '')
  }

  const claimed = new Set<MdSubtitle>()
  const transcriptHeading = `### ${en ? 'Transcript' : '逐字稿'}`

  for (const seg of video.segments) {
    const title = pick(seg.title, seg.title_en, lang)
    lines.push(
      `## ${seg.segment_index + 1}. ${title}`,
      '',
      `\`${formatTimeRange(seg.start_seconds, seg.end_seconds)}\``,
      '',
    )
    const summary = pick(seg.summary, seg.summary_en, lang)
    if (summary) lines.push(summary, '')

    const segSubs = subtitles.filter(
      (s) => s.start >= seg.start_seconds - 0.5 && s.start < seg.end_seconds,
    )
    if (segSubs.length) {
      lines.push(transcriptHeading, '')
      for (const s of segSubs) {
        claimed.add(s)
        lines.push(`- \`${ts(s.start)}\` ${s.text}`)
      }
      lines.push('')
    }
  }

  // Subtitles outside every segment range (gaps, or tail past the last
  // segment) would otherwise vanish from the export — keep them visible.
  const leftover = subtitles.filter((s) => !claimed.has(s))
  if (leftover.length) {
    lines.push(`## ${en ? 'Other transcript' : '其他逐字稿'}`, '')
    lines.push(
      en
        ? '_Not covered by any segment range._'
        : '_未被任何片段区间覆盖的部分。_',
      '',
    )
    for (const s of leftover) lines.push(`- \`${ts(s.start)}\` ${s.text}`)
    lines.push('')
  }

  return lines.join('\n')
}

/** Strip characters that break filenames across OSes. */
export function safeFilename(title: string): string {
  const cleaned = title.replace(/[\\/:*?"<>|\n\r\t]/g, '_').trim().slice(0, 120)
  return `${cleaned || 'analysis'}.md`
}

/** Build a standalone essence-only markdown (no timestamps, no dialogue). */
export function buildEssenceMarkdown(title: string, essence: string): string {
  return `# ${title}\n\n${essence}\n`
}

export function downloadMarkdown(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
