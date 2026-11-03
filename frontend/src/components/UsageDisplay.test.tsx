import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import UsageDisplay from './UsageDisplay';

describe('UsageDisplay', () => {
  it('renders partial usage_json instead of crashing the detail page', () => {
    // Older / imported records may carry only the totals.
    render(<UsageDisplay usage={{ llm_model: 'glm', llm_total_tokens: 1200 }} />);
    expect(screen.getByText('0↑ 0↓ (1,200)')).toBeInTheDocument();
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
