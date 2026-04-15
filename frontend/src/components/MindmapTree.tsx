import { useMemo } from 'react'
import type { MindmapChapter } from './MindmapView'
import { useT } from '../i18n'

interface MindmapTreeProps {
  chapters: MindmapChapter[]
  lang: 'zh' | 'en'
}

const BRANCH_COLORS = [
  '#3b82f6', '#a855f7', '#ec4899', '#f59e0b', '#10b981', '#6366f1',
]

const LEAF_HEIGHT = 28
const GROUP_GAP = 16
const PADDING_Y = 28
const MAX_VISIBLE_POINTS = 4

const COL = {
  rootCx: 60,
  rootRight: 120,
  chapterDot: 192,
  chapterText: 206,
  branchStart: 396,
  pointDot: 436,
  pointText: 450,
}

function estimatePixelWidth(text: string, fontSize: number = 13): number {
  let width = 0
  for (let i = 0; i < text.length; i++) {
    width += text.charCodeAt(i) > 0x7f ? fontSize : fontSize * 0.6
  }
  return width
}

function truncateByWidth(text: string, maxPx: number): string {
  let width = 0
  for (let i = 0; i < text.length; i++) {
    width += text.charCodeAt(i) > 0x7f ? 13 : 7.8
    if (width > maxPx) return text.slice(0, i) + '…'
  }
  return text
}

function sCurve(x1: number, y1: number, x2: number, y2: number): string {
  const midX = (x1 + x2) / 2
  return `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`
}

interface LayoutGroup {
  index: number
  chapter: MindmapChapter
  color: string
  centerY: number
  points: { label: string; y: number }[]
}

function computeLayout(chapters: MindmapChapter[], lang: 'zh' | 'en', moreLabel: (count: number) => string) {
  let currentY = PADDING_Y
  const groups: LayoutGroup[] = []

  for (let i = 0; i < chapters.length; i++) {
    const chapter = chapters[i]
    const color = BRANCH_COLORS[i % BRANCH_COLORS.length]
    const rawPoints = chapter.key_points ?? []
    const visiblePoints = rawPoints.slice(0, MAX_VISIBLE_POINTS)
    const overflow = rawPoints.length - visiblePoints.length

    const points: { label: string; y: number }[] = []
    for (const kp of visiblePoints) {
      const text = lang === 'en' && kp.text_en ? kp.text_en : kp.text
      points.push({ label: truncateByWidth(text, 320), y: currentY })
      currentY += LEAF_HEIGHT
    }
    if (overflow > 0) {
      points.push({ label: moreLabel(overflow), y: currentY })
      currentY += LEAF_HEIGHT
    }
    if (points.length === 0) {
      currentY += LEAF_HEIGHT
    }

    const centerY = points.length > 0
      ? (points[0].y + points[points.length - 1].y) / 2
      : currentY - LEAF_HEIGHT

    groups.push({ index: i, chapter, color, centerY, points })
    currentY += GROUP_GAP
  }

  const totalHeight = currentY - GROUP_GAP + PADDING_Y
  const rootY = groups.length > 0
    ? (groups[0].centerY + groups[groups.length - 1].centerY) / 2
    : totalHeight / 2

  return { groups, totalHeight, rootY }
}

export default function MindmapTree({ chapters, lang }: MindmapTreeProps) {
  const t = useT()
  const moreLabel = useMemo(() => (count: number) => t('mindmap.morePoints', { count }), [t])
  const { groups, totalHeight, rootY } = useMemo(
    () => computeLayout(chapters, lang, moreLabel),
    [chapters, lang, moreLabel],
  )

  const svgWidth = useMemo(() => {
    let maxRight = COL.pointText + 40
    for (const g of groups) {
      for (const p of g.points) {
        const w = estimatePixelWidth(p.label, 12)
        maxRight = Math.max(maxRight, COL.pointText + w + 24)
      }
    }
    return Math.max(maxRight, 700)
  }, [groups])

  if (chapters.length === 0) return null

  return (
    <div
      className="rounded-xl overflow-x-auto mb-4"
      style={{
        background: 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
        padding: '16px 12px',
      }}
    >
      <svg
        width={svgWidth}
        height={totalHeight}
        viewBox={`0 0 ${svgWidth} ${totalHeight}`}
        style={{ display: 'block', minWidth: svgWidth, fontFamily: 'inherit' }}
      >
        {/* root node */}
        <rect
          x={COL.rootCx - 48}
          y={rootY - 15}
          width={96}
          height={30}
          rx={15}
          fill="var(--color-primary)"
          opacity={0.92}
        />
        <text
          x={COL.rootCx}
          y={rootY + 5}
          textAnchor="middle"
          fill="#fff"
          fontSize={12}
          fontWeight={600}
          style={{ fontFamily: 'inherit' }}
        >
          {t('mindmap.rootLabel')}
        </text>

        {groups.map((g) => {
          const rawTitle = lang === 'en' && g.chapter.title_en
            ? g.chapter.title_en
            : g.chapter.title
          const displayTitle = truncateByWidth(rawTitle, 178)

          return (
            <g key={g.index}>
              {/* root → chapter curve */}
              <path
                d={sCurve(COL.rootRight, rootY, COL.chapterDot, g.centerY)}
                fill="none"
                stroke={g.color}
                strokeWidth={2}
                opacity={0.5}
              />

              {/* chapter dot */}
              <circle
                cx={COL.chapterDot}
                cy={g.centerY}
                r={4.5}
                fill={g.color}
              />

              {/* subtle trunk line behind chapter text */}
              {g.points.length > 0 && (
                <line
                  x1={COL.chapterDot + 5}
                  y1={g.centerY}
                  x2={COL.branchStart}
                  y2={g.centerY}
                  stroke={g.color}
                  strokeWidth={1.5}
                  opacity={0.1}
                />
              )}

              {/* chapter title */}
              <text
                x={COL.chapterText}
                y={g.centerY + 5}
                fill="var(--color-text)"
                fontSize={13}
                fontWeight={600}
                style={{ fontFamily: 'inherit' }}
              >
                {displayTitle}
              </text>

              {/* key point branches */}
              {g.points.map((pt, j) => (
                <g key={j}>
                  <path
                    d={sCurve(COL.branchStart, g.centerY, COL.pointDot, pt.y)}
                    fill="none"
                    stroke={g.color}
                    strokeWidth={1.5}
                    opacity={0.3}
                  />
                  <circle
                    cx={COL.pointDot}
                    cy={pt.y}
                    r={2.5}
                    fill={g.color}
                    opacity={0.65}
                  />
                  <text
                    x={COL.pointText}
                    y={pt.y + 4}
                    fill="var(--color-text-secondary)"
                    fontSize={12}
                    style={{ fontFamily: 'inherit' }}
                  >
                    {pt.label}
                  </text>
                </g>
              ))}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
