import { describe, it, expect } from 'vitest';
import { buildMarkdown, safeFilename, type MdVideo } from './exportMarkdown';

const video: MdVideo = {
  url: 'https://example.com/v/1',
  platform: 'xiaoyuzhou',
  title: 'Vol.226 宏观漫谈',
  upload_date: '2026-07-21',
  duration_seconds: 130,
  summary: '中文摘要',
  summary_en: 'English summary',
  tags: [{ name: '小宇宙' }],
  segments: [
    {
      segment_index: 0,
      title: '第一段',
      title_en: 'Part one',
      summary: '第一段摘要',
      summary_en: 'First summary',
      start_seconds: 0,
      end_seconds: 60,
    },
    {
      segment_index: 1,
      title: '第二段',
      title_en: 'Part two',
      summary: '第二段摘要',
      summary_en: '',
      start_seconds: 60,
      end_seconds: 130,
    },
  ],
};

const subs = [
  { start: 0, duration: 3, text: '开场白' },
  { start: 65, duration: 3, text: '第二段第一句' },
];

describe('buildMarkdown', () => {
  it('uses ## for each part: summary plus one per segment', () => {
    const md = buildMarkdown(video, subs, 'zh');
    const h2 = md.split('\n').filter((l) => l.startsWith('## '));
    expect(h2).toEqual(['## 摘要', '## 1. 第一段', '## 2. 第二段']);
  });

  it('routes each subtitle into the segment covering its timestamp', () => {
    const md = buildMarkdown(video, subs, 'zh');
    const firstPart = md.split('## 2. ')[0];
    expect(firstPart).toContain('开场白');
    expect(firstPart).not.toContain('第二段第一句');
    expect(md).toContain('`1:05` 第二段第一句');
  });

  it('prefers English text when lang=en and falls back when it is missing', () => {
    const md = buildMarkdown(video, subs, 'en');
    expect(md).toContain('English summary');
    expect(md).toContain('## 1. Part one');
    // segment 2 has no English summary — keep the Chinese one rather than blank
    expect(md).toContain('第二段摘要');
  });

  it('omits the transcript heading for segments without subtitles', () => {
    const md = buildMarkdown({ ...video, segments: [video.segments[0]] }, [], 'zh');
    expect(md).not.toContain('### 逐字稿');
  });

  it('keeps subtitles that fall outside every segment range', () => {
    // tail past the last segment (ends 130s) + a gap between two segments
    const gapped = {
      ...video,
      segments: [
        { ...video.segments[0], end_seconds: 30 },
        { ...video.segments[1], start_seconds: 90 },
      ],
    };
    const md = buildMarkdown(
      gapped,
      [
        { start: 10, duration: 3, text: '片段内' },
        { start: 50, duration: 3, text: '空隙里' },
        { start: 200, duration: 3, text: '结尾之后' },
      ],
      'zh',
    );
    expect(md).toContain('## 其他逐字稿');
    expect(md).toContain('空隙里');
    expect(md).toContain('结尾之后');
    // in-range text stays in its own segment, not duplicated into the leftovers
    expect(md.split('## 其他逐字稿')[1]).not.toContain('片段内');
  });

  it('adds no leftover section when every subtitle lands in a segment', () => {
    const md = buildMarkdown(video, subs, 'zh');
    expect(md).not.toContain('## 其他逐字稿');
  });
});

describe('safeFilename', () => {
  it('replaces path separators and other illegal characters', () => {
    expect(safeFilename('a/b:c*d?"<>|e')).toBe('a_b_c_d_____e.md');
  });

  it('falls back to a default when the title is empty', () => {
    expect(safeFilename('   ')).toBe('analysis.md');
  });
});
