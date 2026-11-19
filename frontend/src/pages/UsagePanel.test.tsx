import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nContext, createT, type Locale } from '../i18n';
import { UsagePanel } from './LibraryPage';

function renderPanel(locale: Locale, data: Parameters<typeof UsagePanel>[0]['data']) {
  const value = { locale, t: createT(locale), setLocale: () => {} };
  return render(
    <I18nContext.Provider value={value}>
      <UsagePanel data={data} />
    </I18nContext.Provider>,
  );
}

const data = {
  asr: [
    { model: 'fun-asr', total_seconds: 131936, requests: 40, cost: 29.026 },
    { model: 'qwen-audio-3.1-asr-flash-filetrans', total_seconds: 98, requests: 1, cost: 0.004 },
  ],
  llm: [
    {
      model: 'glm-5.3-flash',
      prompt_tokens: 1900000,
      completion_tokens: 377166,
      total_tokens: 2277166,
      requests: 300,
      cost: null,
    },
  ],
  total_cost: 29.03,
};

describe('UsagePanel', () => {
  it('shows hours, compact tokens, per-model cost and the estimated total', () => {
    renderPanel('zh', data);
    expect(screen.getByText('36.6 小时')).toBeTruthy();
    expect(screen.getByText('2 分钟')).toBeTruthy();
    expect(screen.getByText('227.7万 Token')).toBeTruthy();
    expect(screen.getByText('¥29.03')).toBeTruthy();
    expect(screen.getByText('<¥0.01')).toBeTruthy();
    expect(screen.getByText('≈ ¥29.03')).toBeTruthy();
    // group label appears once per group, not per row
    expect(screen.getAllByText('ASR')).toHaveLength(1);
  });

  it('omits the total when no model has a price', () => {
    renderPanel('en', { asr: [], llm: data.llm, total_cost: null });
    expect(screen.getByText('2.3M tokens')).toBeTruthy();
    expect(screen.queryByText(/≈/)).toBeNull();
  });
});
