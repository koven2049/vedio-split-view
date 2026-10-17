import { describe, it, expect } from 'vitest';
import {
  buildMarkdown,
  buildDialogueMarkdown,
  pickExportMarkdown,
  safeFilename,
  formatDialogueTimestamp,
  parseBoldSegments,
  parseTakeaways,
  type DialogueOut,
  type MdVideo,
} from './exportMarkdown';

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

const dialogue: DialogueOut = {
  video_id: 1,
  single_speaker: false,
  fallback: false,
  speakers: [
    { id: 'A', name: '主持人杰森', role: '主持人' },
    { id: 'B', name: '嘉宾小卡', role: '' },
  ],
  blocks: [
    {
      index: 0,
      speaker_id: 'A',
      speaker: '主持人杰森',
      start: 12.4,
      text: '欢迎收听',
      new_turn: true,
    },
    {
      index: 1,
      speaker_id: 'A',
      speaker: '主持人杰森',
      start: 45.1,
      text: '今天我们聊聊',
      new_turn: false,
    },
    {
      index: 2,
      speaker_id: 'B',
      speaker: '嘉宾小卡',
      start: 205.6,
      text: '大家好',
      new_turn: true,
    },
  ],
  sections: [{ index: 2, title: 'CoreWeave 债务遭抛售' }],
};

const dialogueVideo: MdVideo = {
  ...video,
  essence: '精华一句话',
  uploader: '杰森的播客',
  takeaways_json: JSON.stringify([{ title: '要点一', body: '说明一', highlight: '核心判断一' }]),
  show_intro: '本期我们聊聊 AI 泡沫。',
};

describe('buildDialogueMarkdown', () => {
  it('renders the WeChat-article section order with rich takeaway bullets', () => {
    const md = buildDialogueMarkdown(dialogueVideo, dialogue, 'zh');
    const h2 = md.split('\n').filter((l) => l.startsWith('## '));
    expect(h2).toEqual(['## 内容提要', '## 节目简介', '## 出场人物', '## 目录', '## 对话实录']);
    expect(md).toContain('> 整理自 杰森的播客（小宇宙）· 发布于 2026-07-21 · 时长 2:10');
    expect(md).toContain('> 原始链接：https://example.com/v/1');
    expect(md).toContain('- **要点一：** 说明一 **核心判断一**');
    expect(md).toContain('本期我们聊聊 AI 泡沫。');
    expect(md).toContain('- **主持人杰森**：主持人');
    // role omitted when the speaker has none
    expect(md).toContain('- **嘉宾小卡**\n');
    expect(md).toContain('- `0:00 - 1:00` 第一段');
  });

  it('timestamps and bolds only new turns; continuations are plain paragraphs', () => {
    const md = buildDialogueMarkdown(dialogueVideo, dialogue, 'zh');
    expect(md).toContain('`[00:12]` **主持人杰森：** 欢迎收听');
    // index 1 is a same-speaker continuation: bare line, no timestamp or name
    expect(md.split('\n').find((l) => l.includes('今天我们聊聊'))).toBe('今天我们聊聊');
  });

  it('inserts section headings before their anchor block', () => {
    const md = buildDialogueMarkdown(dialogueVideo, dialogue, 'zh');
    expect(
      md.split('### CoreWeave 债务遭抛售\n')[1].startsWith('\n`[03:25]` **嘉宾小卡：** 大家好'),
    ).toBe(true);
  });

  it('drops speaker names and the speakers section for single-speaker content', () => {
    const single: DialogueOut = { ...dialogue, single_speaker: true };
    const md = buildDialogueMarkdown(dialogueVideo, single, 'zh');
    expect(md).not.toContain('## 出场人物');
    expect(md).not.toContain('**主持人杰森：**');
    // every block still carries a timestamp, continuation included in the turn flow
    expect(md).toContain('`[00:12]` 欢迎收听');
    expect(md).toContain('`[03:25]` 大家好');
  });

  it('degrades 内容提要 to the essence paragraph when takeaways are empty', () => {
    const md = buildDialogueMarkdown({ ...dialogueVideo, takeaways_json: '' }, dialogue, 'zh');
    expect(md).toContain('## 内容提要\n\n精华一句话\n\n');
    expect(md).not.toContain('**要点一：**');
  });

  it('omits the takeaways section entirely when both takeaways and essence are empty', () => {
    const md = buildDialogueMarkdown(
      { ...dialogueVideo, takeaways_json: '', essence: '' },
      dialogue,
      'zh',
    );
    expect(md).not.toContain('## 内容提要');
  });

  it('omits empty sections (show_intro) and drops ### without sections', () => {
    const md = buildDialogueMarkdown(
      { ...dialogueVideo, show_intro: '' },
      { ...dialogue, sections: [] },
      'zh',
    );
    expect(md).not.toContain('## 节目简介');
    expect(md).not.toContain('### ');
  });

  it('uses English headings for lang=en while keeping Chinese content', () => {
    const md = buildDialogueMarkdown(dialogueVideo, dialogue, 'en');
    const h2 = md.split('\n').filter((l) => l.startsWith('## '));
    expect(h2).toEqual([
      '## Key Takeaways',
      '## Show Intro',
      '## Speakers',
      '## Contents',
      '## Dialogue Transcript',
    ]);
    expect(md).toContain(
      '> Compiled from 杰森的播客 (小宇宙) · published 2026-07-21 · duration 2:10',
    );
    // takeaway text stays Chinese (no en variant in the contract)
    expect(md).toContain('**要点一：** 说明一 **核心判断一**');
  });

  it('ends with the auto-compiled footer', () => {
    const md = buildDialogueMarkdown(dialogueVideo, dialogue, 'zh');
    expect(md).toMatch(/\n---\n\n_由 VideoSplit 自动整理 · 生成于 \d{4}-\d{2}-\d{2}_\n$/);
  });
});

describe('pickExportMarkdown', () => {
  it('falls back to the legacy format when there is no dialogue', () => {
    const md = pickExportMarkdown(video, undefined, subs, 'zh');
    expect(md).toContain('## 摘要');
    expect(md).not.toContain('## 对话实录');
  });

  it('falls back on a dialogue without blocks', () => {
    const empty: DialogueOut = { ...dialogue, blocks: [] };
    expect(pickExportMarkdown(video, empty, subs, 'zh')).toContain('## 摘要');
  });

  it('uses the dialogue format when blocks exist', () => {
    expect(pickExportMarkdown(video, dialogue, subs, 'zh')).toContain('## 对话实录');
  });
});

describe('formatDialogueTimestamp', () => {
  it('formats seconds as zero-padded mm:ss', () => {
    expect(formatDialogueTimestamp(12.4)).toBe('00:12');
    expect(formatDialogueTimestamp(205.6)).toBe('03:25');
    expect(formatDialogueTimestamp(3671)).toBe('61:11');
  });
});

describe('parseBoldSegments', () => {
  it('pairs ** markers into bold segments', () => {
    expect(parseBoldSegments('a **b** c')).toEqual([
      { text: 'a ', bold: false },
      { text: 'b', bold: true },
      { text: ' c', bold: false },
    ]);
  });

  it('treats an unpaired trailing marker as bold text start', () => {
    expect(parseBoldSegments('a **b')).toEqual([
      { text: 'a ', bold: false },
      { text: 'b', bold: true },
    ]);
  });

  it('handles empty parts around markers', () => {
    expect(parseBoldSegments('**x**')).toEqual([{ text: 'x', bold: true }]);
    expect(parseBoldSegments('plain')).toEqual([{ text: 'plain', bold: false }]);
  });
});

describe('parseTakeaways', () => {
  it('parses the takeaways_json array', () => {
    expect(parseTakeaways(JSON.stringify([{ title: 't', body: 'b', highlight: 'h' }]))).toEqual([
      { title: 't', body: 'b', highlight: 'h' },
    ]);
  });

  it('returns [] for empty or malformed input', () => {
    expect(parseTakeaways('')).toEqual([]);
    expect(parseTakeaways(undefined)).toEqual([]);
    expect(parseTakeaways('not json')).toEqual([]);
    expect(parseTakeaways('{"title":"oap"}')).toEqual([]);
  });
});
