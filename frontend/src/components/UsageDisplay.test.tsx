import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import UsageDisplay from './UsageDisplay';
import { parseUsage } from '../stores/analysisStore';

describe('parseUsage + UsageDisplay', () => {
  it('fills missing fields so partial usage_json cannot crash the detail page', () => {
    // Older / imported records may carry only the totals.
    const usage = parseUsage('{"llm_model":"glm","llm_total_tokens":1200}');
    expect(usage?.llm_prompt_tokens).toBe(0);
    render(<UsageDisplay usage={usage!} />);
    expect(screen.getByText('0↑ 0↓ (1,200)')).toBeInTheDocument();
  });

  it('returns undefined for empty or corrupt json', () => {
    expect(parseUsage('')).toBeUndefined();
    expect(parseUsage('{not json')).toBeUndefined();
  });

  it('renders full usage', () => {
    render(
      <UsageDisplay
        usage={{
          asr_model: 'asr',
          asr_duration_seconds: 61.4,
          llm_model: 'glm',
          llm_prompt_tokens: 48273,
          llm_completion_tokens: 5702,
          llm_total_tokens: 53975,
        }}
      />,
    );
    expect(screen.getByText('61s')).toBeInTheDocument();
    expect(screen.getByText('48,273↑ 5,702↓ (53,975)')).toBeInTheDocument();
  });
});
