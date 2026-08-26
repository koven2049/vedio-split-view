import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useT } from '../i18n';

interface LLMLogEntry {
  id: number;
  task_id: number | null;
  provider: string;
  model: string;
  purpose: string;
  status: string;
  error_message: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  duration_ms: number;
  created_at: string;
}

const PROVIDER_LABEL: Record<string, string> = {
  primary: '主 LLM',
  backup: '备用 LLM',
};

const STATUS_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  ok: { bg: '#22c55e20', color: '#22c55e', label: '成功' },
  error: { bg: '#ef444420', color: '#ef4444', label: '失败' },
  fallback: { bg: '#f59e0b20', color: '#f59e0b', label: '回退' },
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${mm}-${dd} ${hh}:${mi}:${ss}`;
}

export default function LLMLogsPage() {
  const t = useT();
  const {
    data: logs,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ['llm-logs'],
    queryFn: () => api.get<LLMLogEntry[]>('/videos/llm-logs'),
    refetchInterval: 10000,
  });

  if (isLoading)
    return <div className="flex justify-center py-20 opacity-50">{t('common.loading')}</div>;
  if (isError)
    return <div className="flex justify-center py-20 opacity-50">{t('common.error')}</div>;

  const entries = logs || [];

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">{t('nav.llmLogs')}</h1>

      <div
        className="rounded-xl overflow-hidden"
        style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.time')}
              </th>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.provider')}
              </th>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.model')}
              </th>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.purpose')}
              </th>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.status')}
              </th>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.tokens')}
              </th>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.duration')}
              </th>
              <th
                className="px-4 py-3 text-left font-medium"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('logs.error')}
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center opacity-50">
                  {t('logs.empty')}
                </td>
              </tr>
            )}
            {entries.map((log) => {
              const st = STATUS_STYLE[log.status] || STATUS_STYLE.ok;
              return (
                <tr key={log.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                  <td className="px-4 py-2.5 font-mono text-xs">{formatTime(log.created_at)}</td>
                  <td className="px-4 py-2.5">
                    <span className="text-xs">{PROVIDER_LABEL[log.provider] || log.provider}</span>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">{log.model}</td>
                  <td className="px-4 py-2.5 text-xs">{log.purpose}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className="px-2 py-0.5 rounded-full text-[11px] font-medium"
                      style={{ background: st.bg, color: st.color }}
                    >
                      {st.label}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">
                    {log.prompt_tokens}+{log.completion_tokens}={log.total_tokens}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">
                    {(log.duration_ms / 1000).toFixed(1)}s
                  </td>
                  <td
                    className="px-4 py-2.5 text-xs max-w-[200px] truncate"
                    title={log.error_message}
                  >
                    {log.error_message || '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
