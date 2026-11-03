import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, BookOpen, Check, Copy, Key, Loader2, Plus, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { formatRelativeTime, parseServerTime } from '../lib/utils';
import { useLocale } from '../i18n';

export interface ApiTokenItem {
  id: number;
  name: string;
  key_prefix: string;
  is_active: boolean;
  last_used_at: string | null;
  created_at: string;
}
interface ApiTokenCreated extends ApiTokenItem {
  full_key: string;
}

const TOKEN_ENDPOINT = '/settings/tokens';
const HEADER_NAME = 'X-API-Key';

function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = async (id: string, text: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied((c) => (c === id ? null : c)), 2000);
  };
  return { copied, copy };
}

function CodeLine({
  text,
  copyId,
  copied,
  onCopy,
  label,
}: {
  text: string;
  copyId: string;
  copied: string | null;
  onCopy: (id: string, text: string) => void;
  label: string;
}) {
  const done = copied === copyId;
  return (
    <div className="flex items-stretch gap-2">
      <code
        className="text-xs flex-1 font-mono px-3 py-2 rounded-lg break-all"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
      >
        {text}
      </code>
      <button
        onClick={() => onCopy(copyId, text)}
        className="inline-flex items-center gap-1.5 px-3 rounded-lg text-xs font-medium shrink-0"
        style={{
          border: '1px solid var(--color-border)',
          color: done ? 'var(--color-success)' : 'var(--color-text)',
        }}
      >
        {done ? <Check size={14} /> : <Copy size={14} />}
        {label}
      </button>
    </div>
  );
}

export default function ApiTokensSection() {
  const { t, locale } = useLocale();
  const queryClient = useQueryClient();
  const [newName, setNewName] = useState('');
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const { copied, copy } = useCopy();

  const keysQuery = useQuery({
    queryKey: ['api-tokens'],
    queryFn: () => api.get<ApiTokenItem[]>(TOKEN_ENDPOINT),
  });

  const createMutation = useMutation({
    mutationFn: (name: string) => api.post<ApiTokenCreated>(TOKEN_ENDPOINT, { name }),
    onSuccess: (data) => {
      setCreated(data);
      setNewName('');
      queryClient.invalidateQueries({ queryKey: ['api-tokens'] });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (id: number) => api.delete(`${TOKEN_ENDPOINT}/${id}`),
    onSuccess: (_data, id) => {
      setConfirmId(null);
      if (created?.id === id) setCreated(null);
      queryClient.invalidateQueries({ queryKey: ['api-tokens'] });
    },
  });

  const submit = () => {
    const name = newName.trim();
    if (name && !createMutation.isPending) createMutation.mutate(name);
  };

  const keys = keysQuery.data || [];
  const error =
    (keysQuery.error as Error | null) ||
    (createMutation.error as Error | null) ||
    (revokeMutation.error as Error | null);
  const when = (iso: string) => formatRelativeTime(parseServerTime(iso), locale);
  const tryCurl = (key: string) =>
    `curl -H "${HEADER_NAME}: ${key}" ${window.location.origin}/api/videos/ids`;

  return (
    <section
      className="p-5 rounded-xl space-y-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold flex items-center gap-2">
          <Key size={18} /> {t('settings.apiTokens')}
          {keys.length > 0 && (
            <span
              className="text-xs font-normal px-2 py-0.5 rounded-full"
              style={{
                background: 'var(--color-bg-tertiary)',
                color: 'var(--color-text-secondary)',
              }}
            >
              {keys.length}
            </span>
          )}
        </h2>
        <Link
          to="/api-docs"
          className="inline-flex items-center gap-1 text-xs hover:underline"
          style={{ color: 'var(--color-primary)' }}
        >
          <BookOpen size={14} /> {t('settings.viewApiDocs')}
        </Link>
      </div>
      <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
        {t('settings.apiTokensDesc', { header: HEADER_NAME })}
      </p>

      {error && (
        <div
          className="p-3 rounded-lg text-sm"
          style={{
            background: 'rgba(239, 68, 68, 0.08)',
            border: '1px solid rgba(239, 68, 68, 0.25)',
            color: 'var(--color-danger)',
          }}
        >
          {error.message}
        </div>
      )}

      {/* One-time reveal */}
      {created && (
        <div
          role="status"
          className="p-4 rounded-lg space-y-3"
          style={{
            background: 'var(--color-bg-tertiary)',
            border: '1px solid var(--color-success)',
          }}
        >
          <p className="text-sm font-medium" style={{ color: 'var(--color-success)' }}>
            {t('settings.tokenCreatedTitle', { name: created.name })}
          </p>
          <p className="text-xs flex items-start gap-1.5" style={{ color: 'var(--color-warning)' }}>
            <AlertTriangle size={14} className="shrink-0 mt-px" />
            {t('settings.tokenOnceWarning')}
          </p>
          <CodeLine
            text={created.full_key}
            copyId="key"
            copied={copied}
            onCopy={copy}
            label={copied === 'key' ? t('settings.copied') : t('settings.copyToken')}
          />
          <div className="space-y-1.5">
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              {t('settings.tryIt')}
            </p>
            <CodeLine
              text={tryCurl(created.full_key)}
              copyId="curl"
              copied={copied}
              onCopy={copy}
              label={copied === 'curl' ? t('settings.copied') : t('common.copy')}
            />
          </div>
          <button
            onClick={() => setCreated(null)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-white"
            style={{ background: 'var(--color-primary)' }}
          >
            {t('settings.savedClose')}
          </button>
        </div>
      )}

      {/* Create */}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          maxLength={128}
          aria-label={t('settings.tokenNamePlaceholder')}
          placeholder={t('settings.tokenNamePlaceholder')}
          className="flex-1 text-sm px-3 py-2 rounded-lg"
          style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
        />
        <button
          type="submit"
          disabled={!newName.trim() || createMutation.isPending}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
          style={{ background: 'var(--color-primary)' }}
        >
          {createMutation.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Plus size={14} />
          )}
          {t('settings.issueToken')}
        </button>
      </form>

      {/* List */}
      {keysQuery.isPending ? (
        <p
          className="text-sm flex items-center gap-2"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          <Loader2 size={14} className="animate-spin" /> {t('settings.loadingTokens')}
        </p>
      ) : keys.length === 0 ? (
        <p
          className="text-sm p-4 rounded-lg text-center"
          style={{ color: 'var(--color-text-secondary)', border: '1px dashed var(--color-border)' }}
        >
          {t('settings.noTokens')}
        </p>
      ) : (
        <ul className="space-y-2">
          {keys.map((k) => (
            <li
              key={k.id}
              className="py-2.5 px-3 rounded-lg space-y-2"
              style={{ background: 'var(--color-bg-tertiary)' }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium flex items-center gap-2">
                    <span className="truncate">{k.name}</span>
                    <code
                      className="text-xs font-mono font-normal"
                      style={{ color: 'var(--color-text-secondary)' }}
                    >
                      {k.key_prefix}
                    </code>
                  </p>
                  <p className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
                    {t('settings.createdAt')} {when(k.created_at)} · {t('settings.lastUsed')}{' '}
                    {k.last_used_at ? when(k.last_used_at) : t('settings.neverUsed')}
                  </p>
                </div>
                {confirmId !== k.id && (
                  <button
                    onClick={() => setConfirmId(k.id)}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs shrink-0 hover:opacity-80"
                    style={{
                      color: 'var(--color-danger)',
                      border: '1px solid rgba(239, 68, 68, 0.25)',
                    }}
                  >
                    <Trash2 size={13} /> {t('settings.revoke')}
                  </button>
                )}
              </div>
              {confirmId === k.id && (
                <div
                  className="flex flex-wrap items-center justify-between gap-2 p-2 rounded-lg text-xs"
                  style={{
                    background: 'rgba(239, 68, 68, 0.08)',
                    border: '1px solid rgba(239, 68, 68, 0.25)',
                  }}
                >
                  <span style={{ color: 'var(--color-danger)' }}>
                    {t('settings.revokeConfirm', { name: k.name })}
                  </span>
                  <span className="flex gap-2">
                    <button
                      onClick={() => setConfirmId(null)}
                      className="px-2.5 py-1 rounded-md"
                      style={{ border: '1px solid var(--color-border)' }}
                    >
                      {t('common.cancel')}
                    </button>
                    <button
                      onClick={() => revokeMutation.mutate(k.id)}
                      disabled={revokeMutation.isPending}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-white disabled:opacity-60"
                      style={{ background: 'var(--color-danger)' }}
                    >
                      {revokeMutation.isPending && <Loader2 size={12} className="animate-spin" />}
                      {t('settings.revokeConfirmBtn')}
                    </button>
                  </span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
