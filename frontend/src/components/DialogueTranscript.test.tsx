import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import DialogueTranscript from './DialogueTranscript';
import { groupTurns } from '../lib/dialogueTurns';
import type { DialogueBlock, DialogueOut } from '../lib/exportMarkdown';

function block(index: number, speaker_id: string, new_turn: boolean, text: string): DialogueBlock {
  return {
    index,
    speaker_id,
    speaker: speaker_id === 'A' ? '主持人' : '嘉宾',
    start: index * 10,
    text,
    new_turn,
  };
}

const multi: DialogueOut = {
  video_id: 1,
  single_speaker: false,
  fallback: false,
  speakers: [
    { id: 'A', name: '主持人', role: '' },
    { id: 'B', name: '嘉宾', role: '' },
  ],
  blocks: [
    block(0, 'A', true, '问题？'),
    block(1, 'B', true, '第一段。'),
    block(2, 'B', false, '第二段。'),
    block(3, 'B', false, '第三段。'),
    block(4, 'B', false, '新话题第一段。'),
  ],
  sections: [{ index: 4, title: '新话题' }],
};

describe('DialogueTranscript', () => {
  it('groups continuation paragraphs under one speaker turn', () => {
    const turns = groupTurns(multi);
    expect(turns.map((t) => t.paragraphs.map((p) => p.index))).toEqual([[0], [1, 2, 3], [4]]);
    // A section starting mid-turn opens a new group (speaker header repeats).
    expect(turns[2].section).toBe('新话题');
    expect(turns[2].speaker).toBe('嘉宾');
  });

  it('renders every paragraph as its own <p> and the speaker once per turn', () => {
    const { container } = render(<DialogueTranscript dialogue={multi} />);
    expect(container.querySelectorAll('p')).toHaveLength(5);
    // 嘉宾 header: once for the turn, once again under the new section.
    expect(screen.getAllByText('嘉宾')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: '新话题' })).toBeInTheDocument();
  });

  it('single-speaker content gets no names and a timestamp per paragraph', () => {
    const single: DialogueOut = {
      ...multi,
      single_speaker: true,
      speakers: [{ id: 'A', name: '讲述者', role: '' }],
      blocks: [
        block(0, 'A', true, '一。'),
        block(1, 'A', false, '二。'),
        block(2, 'A', false, '三。'),
      ],
      sections: [],
    };
    render(<DialogueTranscript dialogue={single} />);
    expect(screen.queryByText('主持人')).toBeNull();
    expect(screen.getAllByText(/^\d{2}:\d{2}$/)).toHaveLength(3);
  });

  it('timestamps become play buttons when a playback url exists', () => {
    render(
      <DialogueTranscript dialogue={multi} playbackUrlFor={() => 'https://x'} playTitle="play" />,
    );
    expect(screen.getAllByRole('button', { name: /\d{2}:\d{2}/ })).toHaveLength(3);
  });
});
