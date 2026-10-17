import { describe, it, expect } from 'vitest';
import { stagesForPlatform } from './analyzeHelpers';

describe('stagesForPlatform', () => {
  it('places the dialogue stage after analysis and before complete', () => {
    const stages = stagesForPlatform('youtube');
    expect(stages.indexOf('dialogue')).toBe(stages.indexOf('analysis') + 1);
    expect(stages.indexOf('dialogue')).toBeLessThan(stages.indexOf('complete'));
  });

  it('hides subtitle_check for xiaoyuzhou (spec 3.8)', () => {
    const stages = stagesForPlatform('xiaoyuzhou');
    expect(stages).not.toContain('subtitle_check');
    // Other stages are preserved.
    expect(stages).toContain('metadata');
    expect(stages).toContain('audio_download');
    expect(stages).toContain('transcription');
  });

  it('keeps subtitle_check for youtube', () => {
    const stages = stagesForPlatform('youtube');
    expect(stages).toContain('subtitle_check');
  });

  it('keeps subtitle_check for bilibili', () => {
    const stages = stagesForPlatform('bilibili');
    expect(stages).toContain('subtitle_check');
  });

  it('returns the full STAGES list for unknown platforms', () => {
    const stages = stagesForPlatform('unknown');
    expect(stages).toContain('subtitle_check');
  });
});
