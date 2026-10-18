import { describe, expect, it } from 'vitest';
import { splitParagraphs } from './utils';

describe('splitParagraphs', () => {
  it('splits prose on blank lines and trims parts', () => {
    expect(splitParagraphs('第一段。\n\n第二段。\n \n第三段。')).toEqual([
      '第一段。',
      '第二段。',
      '第三段。',
    ]);
  });

  it('falls back to a single paragraph when there are no blank lines', () => {
    expect(splitParagraphs('一整块没有分段的文字。')).toEqual(['一整块没有分段的文字。']);
  });

  it('drops empty-only parts and returns nothing for empty input', () => {
    expect(splitParagraphs('\n\n\n')).toEqual([]);
    expect(splitParagraphs('')).toEqual([]);
  });
});
