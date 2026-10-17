import { formatDuration, formatTimeRange, platformLabel } from './utils';
import { createT } from '../i18n';

export interface MdSubtitle {
  start: number;
  duration: number;
  text: string;
}
export interface MdSegment {
  segment_index: number;
  title: string;
  title_en: string;
  summary: string;
  summary_en: string;
  start_seconds: number;
  end_seconds: number;
}
export interface MdVideo {
  url: string;
  platform: string;
  title: string;
  upload_date: string;
  duration_seconds: number;
  summary: string;
  summary_en: string;
  essence: string;
  segments: MdSegment[];
  tags: { name: string }[];
  /** New dialogue-era VideoOut fields (empty string when absent). */
  takeaways_json?: string;
  show_intro?: string;
  uploader?: string;
}

export interface DialogueSpeaker {
  id: string;
  name: string;
  role: string;
}
export interface DialogueBlock {
  index: number;
  speaker_id: string;
  speaker: string;
  start: number;
  text: string;
  new_turn: boolean;
}
export interface DialogueSection {
  index: number;
  title: string;
}
export interface DialogueOut {
  video_id: number;
  single_speaker: boolean;
  fallback: boolean;
  speakers: DialogueSpeaker[];
  blocks: DialogueBlock[];
  sections: DialogueSection[];
}
export interface Takeaway {
  title: string;
  body: string;
  highlight: string;
}

function pick(zh: string, en: string, lang: 'zh' | 'en') {
  return lang === 'en' && en ? en : zh;
}

function ts(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/** `mm:ss` timestamp used by the dialogue view/export (minutes zero-padded). */
export function formatDialogueTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

/** Parse the `takeaways_json` VideoOut column; malformed/empty input → []. */
export function parseTakeaways(json: string | undefined | null): Takeaway[] {
  if (!json) return [];
  try {
    const parsed = JSON.parse(json);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is Takeaway => !!item && typeof item === 'object')
      .map((item) => ({
        title: String(item.title ?? ''),
        body: String(item.body ?? ''),
        highlight: String(item.highlight ?? ''),
      }));
  } catch {
    return [];
  }
}

export interface BoldSegment {
  text: string;
  bold: boolean;
}

/** Split `plain **bold** plain` on paired `**` markers for <strong> rendering. */
export function parseBoldSegments(text: string): BoldSegment[] {
  const parts = text.split('**');
  const segments: BoldSegment[] = [];
  for (let i = 0; i < parts.length; i++) {
    if (!parts[i]) continue;
    // pairs (0,1), (2,3), … are bold; an unpaired trailing part is plain text
    segments.push({ text: parts[i], bold: i % 2 === 1 });
  }
  return segments;
}

/** Build the full analysis document, one `##` section per part. */
export function buildMarkdown(video: MdVideo, subtitles: MdSubtitle[], lang: 'zh' | 'en'): string {
  const en = lang === 'en';
  const lines: string[] = [];

  lines.push(`# ${video.title}`, '');
  const meta = [
    `- ${en ? 'Platform' : '平台'}: ${platformLabel(video.platform)}`,
    `- ${en ? 'Duration' : '时长'}: ${formatDuration(video.duration_seconds)}`,
  ];
  if (video.upload_date) meta.push(`- ${en ? 'Published' : '发布日期'}: ${video.upload_date}`);
  if (video.tags.length)
    meta.push(`- ${en ? 'Tags' : '标签'}: ${video.tags.map((tg) => tg.name).join(', ')}`);
  meta.push(`- ${en ? 'Source' : '原始链接'}: ${video.url}`);
  lines.push(...meta, '');

  lines.push(
    `## ${en ? 'Summary' : '摘要'}`,
    '',
    pick(video.summary, video.summary_en, lang) || '-',
    '',
  );

  if (video.essence) {
    lines.push(`## ${en ? 'Essence' : '精华总结'}`, '', video.essence, '');
  }

  const claimed = new Set<MdSubtitle>();
  const transcriptHeading = `### ${en ? 'Transcript' : '逐字稿'}`;

  for (const seg of video.segments) {
    const title = pick(seg.title, seg.title_en, lang);
    lines.push(
      `## ${seg.segment_index + 1}. ${title}`,
      '',
      `\`${formatTimeRange(seg.start_seconds, seg.end_seconds)}\``,
      '',
    );
    const summary = pick(seg.summary, seg.summary_en, lang);
    if (summary) lines.push(summary, '');

    const segSubs = subtitles.filter(
      (s) => s.start >= seg.start_seconds - 0.5 && s.start < seg.end_seconds,
    );
    if (segSubs.length) {
      lines.push(transcriptHeading, '');
      for (const s of segSubs) {
        claimed.add(s);
        lines.push(`- \`${ts(s.start)}\` ${s.text}`);
      }
      lines.push('');
    }
  }

  // Subtitles outside every segment range (gaps, or tail past the last
  // segment) would otherwise vanish from the export — keep them visible.
  const leftover = subtitles.filter((s) => !claimed.has(s));
  if (leftover.length) {
    lines.push(`## ${en ? 'Other transcript' : '其他逐字稿'}`, '');
    lines.push(en ? '_Not covered by any segment range._' : '_未被任何片段区间覆盖的部分。_', '');
    for (const s of leftover) lines.push(`- \`${ts(s.start)}\` ${s.text}`);
    lines.push('');
  }

  return lines.join('\n');
}

/**
 * Build the WeChat-article style dialogue document:
 * takeaways → show intro → speakers → TOC → dialogue transcript.
 * Section headings reuse the i18n keys so UI and export stay in sync.
 */
export function buildDialogueMarkdown(
  video: MdVideo,
  dialogue: DialogueOut,
  lang: 'zh' | 'en',
): string {
  const t = createT(lang);
  const lines: string[] = [];

  lines.push(`# ${video.title}`, '');

  const platform = platformLabel(video.platform);
  const duration = formatDuration(video.duration_seconds);
  const source = video.uploader
    ? t('export.compiledWithName', { name: video.uploader, platform })
    : platform;
  const metaParts = [t('export.compiledFrom', { source })];
  if (video.upload_date) metaParts.push(t('export.publishedOn', { date: video.upload_date }));
  metaParts.push(t('export.durationIs', { duration }));
  lines.push(`> ${metaParts.join(' ')}`);
  lines.push(`> ${t('export.sourceLink', { url: video.url })}`, '');

  // 内容提要 — falls back to a single essence paragraph when takeaways are empty.
  const takeaways = parseTakeaways(video.takeaways_json);
  if (takeaways.length) {
    lines.push(`## ${t('detail.takeaways')}`, '');
    for (const tk of takeaways) {
      const bullet = [tk.title ? `**${tk.title}：**` : '', tk.body];
      if (tk.highlight) bullet.push(`**${tk.highlight}**`);
      lines.push(`- ${bullet.filter(Boolean).join(' ')}`);
    }
    lines.push('');
  } else if (video.essence) {
    lines.push(`## ${t('detail.takeaways')}`, '', video.essence, '');
  }

  // 节目简介 — omitted entirely when empty.
  if (video.show_intro) {
    lines.push(`## ${t('export.showIntro')}`, '', video.show_intro, '');
  }

  // 出场人物 — omitted for single-speaker content or when no speaker carries a name.
  const namedSpeakers = dialogue.single_speaker ? [] : dialogue.speakers.filter((s) => s.name);
  if (namedSpeakers.length) {
    lines.push(`## ${t('export.speakers')}`, '');
    for (const sp of namedSpeakers) {
      lines.push(sp.role ? `- **${sp.name}**：${sp.role}` : `- **${sp.name}**`);
    }
    lines.push('');
  }

  // 目录 — time-ranged segments from the analysis.
  if (video.segments.length) {
    lines.push(`## ${t('export.toc')}`, '');
    for (const seg of video.segments) {
      lines.push(
        `- \`${formatTimeRange(seg.start_seconds, seg.end_seconds)}\` ${pick(seg.title, seg.title_en, lang)}`,
      );
    }
    lines.push('');
  }

  // 对话实录 — section headings inserted before their anchor block.
  lines.push(`## ${t('export.dialogue')}`, '');
  const sectionByIndex = new Map<number, string>();
  for (const sec of dialogue.sections) sectionByIndex.set(sec.index, sec.title);

  for (const block of dialogue.blocks) {
    const sectionTitle = sectionByIndex.get(block.index);
    if (sectionTitle) {
      if (lines.length && lines[lines.length - 1] !== '') lines.push('');
      lines.push(`### ${sectionTitle}`, '');
    }

    const stamp = `\`[${formatDialogueTimestamp(block.start)}]\``;
    if (block.new_turn && !dialogue.single_speaker) {
      lines.push(`${stamp} **${block.speaker}：** ${block.text}`, '');
    } else {
      // new turns on single-speaker content keep the timestamp, drop the name;
      // continuation blocks of the same turn are plain paragraphs.
      lines.push(block.new_turn ? `${stamp} ${block.text}` : block.text, '');
    }
  }

  lines.push('---', '', `_${t('export.footer', { date: todayISO() })}_`, '');

  return lines.join('\n');
}

function todayISO(): string {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${mm}-${dd}`;
}

/** Dialogue format when a dialogue exists, else the legacy per-line export. */
export function pickExportMarkdown(
  video: MdVideo,
  dialogue: DialogueOut | undefined | null,
  subtitles: MdSubtitle[],
  lang: 'zh' | 'en',
): string {
  return dialogue?.blocks?.length
    ? buildDialogueMarkdown(video, dialogue, lang)
    : buildMarkdown(video, subtitles, lang);
}

/** Strip characters that break filenames across OSes. */
export function safeFilename(title: string): string {
  const cleaned = title
    .replace(/[\\/:*?"<>|\n\r\t]/g, '_')
    .trim()
    .slice(0, 120);
  return `${cleaned || 'analysis'}.md`;
}

/** Build a standalone essence-only markdown (no timestamps, no dialogue). */
export function buildEssenceMarkdown(title: string, essence: string): string {
  return `# ${title}\n\n${essence}\n`;
}

export function downloadMarkdown(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
