import { groupTurns } from '../lib/dialogueTurns';
import { type DialogueOut, formatDialogueTimestamp } from '../lib/exportMarkdown';

const SPEAKER_COLORS = 6;

function Timestamp({
  start,
  playbackUrl,
  playTitle,
}: {
  start: number;
  playbackUrl?: string | null;
  playTitle?: string;
}) {
  const label = formatDialogueTimestamp(start);
  const cls = 'font-mono text-xs tabular-nums text-[var(--color-text-secondary)]';
  if (!playbackUrl) return <span className={cls}>{label}</span>;
  return (
    <button
      type="button"
      onClick={() => window.open(playbackUrl, '_blank', 'noopener noreferrer')}
      className={`${cls} rounded px-1 -mx-1 hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-primary)]`}
      title={playTitle}
    >
      {label}
    </button>
  );
}

/**
 * Reading layout for a dialogue transcript: each speaking turn is one unit
 * (speaker line, then its paragraphs inside a speaker-coloured rule) with
 * generous paragraph spacing, so long turns never collapse into a wall of text.
 */
export default function DialogueTranscript({
  dialogue,
  playbackUrlFor,
  playTitle,
}: {
  dialogue: DialogueOut;
  playbackUrlFor?: (start: number) => string | null;
  playTitle?: string;
}) {
  const colorSlot = new Map(
    dialogue.speakers.map((sp, i) => [sp.id, `var(--speaker-${(i % SPEAKER_COLORS) + 1})`]),
  );
  const turns = groupTurns(dialogue);
  // Turns get a clear break; single-speaker paragraphs only a paragraph gap.
  const gap = dialogue.single_speaker ? 'mt-[0.9em]' : 'mt-7';

  return (
    <div className="max-w-[40rem] text-[15px] leading-[1.85]">
      {turns.map((turn, ti) => {
        const color = colorSlot.get(turn.speakerId) ?? 'var(--color-text-secondary)';
        return (
          <div key={turn.key} className={ti === 0 ? '' : turn.section ? 'mt-12' : gap}>
            {turn.section !== undefined && (
              <h3 className="mb-5 text-lg font-semibold leading-snug">{turn.section}</h3>
            )}
            {dialogue.single_speaker ? (
              <div className="grid grid-cols-[3.25rem_1fr] gap-x-3">
                <div className="pt-[0.2em]">
                  <Timestamp
                    start={turn.start}
                    playbackUrl={playbackUrlFor?.(turn.start)}
                    playTitle={playTitle}
                  />
                </div>
                <div>
                  {turn.paragraphs.map((p) => (
                    <p key={p.index} className="mb-[0.9em] last:mb-0">
                      {p.text}
                    </p>
                  ))}
                </div>
              </div>
            ) : (
              <>
                <div className="mb-2 flex items-baseline gap-2.5">
                  <span className="font-semibold" style={{ color }}>
                    {turn.speaker}
                  </span>
                  <Timestamp
                    start={turn.start}
                    playbackUrl={playbackUrlFor?.(turn.start)}
                    playTitle={playTitle}
                  />
                </div>
                <div
                  className="border-l-2 pl-4"
                  style={{ borderColor: `color-mix(in srgb, ${color} 35%, transparent)` }}
                >
                  {turn.paragraphs.map((p) => (
                    <p key={p.index} className="mb-[0.9em] last:mb-0">
                      {p.text}
                    </p>
                  ))}
                </div>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
