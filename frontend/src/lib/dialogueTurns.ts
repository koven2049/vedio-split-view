import type { DialogueBlock, DialogueOut } from './exportMarkdown';

export interface Turn {
  key: number;
  section?: string;
  speaker: string;
  speakerId: string;
  start: number;
  paragraphs: DialogueBlock[];
}

/**
 * Group display paragraphs into speaking turns. A section title that lands
 * mid-turn starts a fresh group so the speaker header is repeated under it.
 */
export function groupTurns(dialogue: DialogueOut): Turn[] {
  const sectionByIndex = new Map(dialogue.sections.map((s) => [s.index, s.title]));
  const turns: Turn[] = [];
  for (const block of dialogue.blocks) {
    const section = sectionByIndex.get(block.index);
    const last = turns[turns.length - 1];
    // Single-speaker content has no turns to group: one paragraph per unit
    // so every paragraph keeps its own timestamp in the gutter.
    if (last && !block.new_turn && section === undefined && !dialogue.single_speaker) {
      last.paragraphs.push(block);
    } else {
      turns.push({
        key: block.index,
        section,
        speaker: block.speaker,
        speakerId: block.speaker_id,
        start: block.start,
        paragraphs: [block],
      });
    }
  }
  return turns;
}
