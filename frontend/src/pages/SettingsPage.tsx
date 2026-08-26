import { useState, useEffect, type ButtonHTMLAttributes } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  User,
  Link2,
  Loader2,
  CheckCircle,
  RefreshCw,
  X,
  Key,
  Copy,
  Check,
  Plus,
  Trash2,
  Shield,
  AlertTriangle,
  Cookie,
  SlidersHorizontal,
  Save,
  BarChart3,
  type LucideIcon,
} from 'lucide-react';
import { ApiError, api } from '../lib/api';
import { useAuthStore } from '../stores/authStore';
import { useT } from '../i18n';

interface BiliStatus {
  connected: boolean;
  bilibili_username: string;
  expired: boolean;
}
interface QRData {
  qr_key: string;
  qr_url: string;
  qr_image_base64: string;
}
interface TaskItem {
  id: number;
  url: string;
  status: string;
  video_title: string;
  error_message: string;
}
interface ApiTokenItem {
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
interface AdminCleanupSummary {
  orphan_exports: number;
  orphan_thumbnails: number;
  orphan_task_dirs: number;
  total_items: number;
}
interface AdminCleanupResult extends AdminCleanupSummary {
  removed_exports: number;
  removed_thumbnails: number;
  removed_task_dirs: number;
  removed_total: number;
  errors: string[];
}
interface YoutubeCookiesStatus {
  configured: boolean;
  file_exists: boolean;
  earliest_expiry: string | null;
  earliest_expiry_ts: number | null;
  expired: boolean;
  cookie_count: number;
  domain_summary: string;
  usability_checked: boolean;
  usable: boolean | null;
  usability_message: string;
  checked_at: string | null;
}

const TOKEN_ENDPOINT = '/settings/tokens';
const LEGACY_TOKEN_ENDPOINT = '/settings/api-keys';
const TOKEN_HEADER_EXAMPLE = 'X-API-Key: <your-token>';

async function withTokenEndpoint<T>(request: (path: string) => Promise<T>): Promise<T> {
  try {
    return await request(TOKEN_ENDPOINT);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return request(LEGACY_TOKEN_ENDPOINT);
    }
    throw error;
  }
}

export default function SettingsPage() {
  const t = useT();
  const { username, role } = useAuthStore();
  const queryClient = useQueryClient();

  const biliQuery = useQuery({
    queryKey: ['bilibili-status'],
    queryFn: () => api.get<BiliStatus>('/bilibili/status'),
  });

  const youtubeCookiesQuery = useQuery({
    queryKey: ['youtube-cookies-status'],
    queryFn: () => api.get<YoutubeCookiesStatus>('/youtube/cookies-status'),
    staleTime: 300_000,
    refetchInterval: 3_600_000,
    retry: false,
  });

  const tasksQuery = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<TaskItem[]>('/videos/tasks'),
  });

  const [showQR, setShowQR] = useState(false);
  const [qrData, setQrData] = useState<QRData | null>(null);
  const [polling, setPolling] = useState(false);

  const generateQR = useMutation({
    mutationFn: () => api.post<QRData>('/bilibili/qr/generate'),
    onSuccess: (data) => {
      setQrData(data);
      setShowQR(true);
      setPolling(true);
    },
  });

  useEffect(() => {
    if (!polling || !qrData) return;
    const interval = setInterval(async () => {
      try {
        const result = await api.get<{ status: string }>(`/bilibili/qr/poll/${qrData.qr_key}`);
        if (result.status === 'confirmed') {
          setPolling(false);
          setShowQR(false);
          queryClient.invalidateQueries({ queryKey: ['bilibili-status'] });
        } else if (result.status === 'expired') {
          setPolling(false);
        }
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [polling, qrData, queryClient]);

  const disconnectMutation = useMutation({
    mutationFn: () => api.delete('/bilibili/disconnect'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['bilibili-status'] }),
  });

  const discardTaskMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/videos/tasks/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tasks'] }),
  });

  const biliStatus = biliQuery.data;
  const tasks = tasksQuery.data?.filter((t) => t.status.startsWith('failed')) || [];

  return (
    <div className="space-y-8 max-w-4xl">
      <h1 className="text-2xl font-bold">{t('settings.title')}</h1>

      {/* Profile */}
      <section
        className="p-5 rounded-xl space-y-3"
        style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
      >
        <h2 className="font-semibold flex items-center gap-2">
          <User size={18} /> {t('settings.profile')}
        </h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          {t('settings.username')}: <strong>{username}</strong>
        </p>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          {t('settings.role')}: <strong>{role}</strong>
        </p>
      </section>

      <YoutubeCookiesSection
        status={youtubeCookiesQuery.data}
        isLoading={youtubeCookiesQuery.isPending}
        isRefreshing={youtubeCookiesQuery.isFetching}
        error={youtubeCookiesQuery.error as Error | null}
        onRefresh={() => {
          void youtubeCookiesQuery.refetch();
        }}
      />

      {role === 'admin' && <AdminCleanupSection />}

      {role !== 'admin' ? null : (
        <>
          {/* Bilibili Connection */}
          <section
            className="p-5 rounded-xl space-y-4"
            style={{
              background: 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
            }}
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-1 flex-1 min-w-0">
                <h2 className="font-semibold flex items-center gap-2">
                  <Link2 size={18} /> {t('settings.bilibiliAccount')}
                </h2>
                <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                  {t('settings.bilibiliDesc')}
                </p>
              </div>
              {biliStatus?.connected ? (
                <SectionActionButton
                  type="button"
                  onClick={() => disconnectMutation.mutate()}
                  disabled={disconnectMutation.isPending}
                  icon={disconnectMutation.isPending ? Loader2 : Link2}
                  spinning={disconnectMutation.isPending}
                  label={
                    disconnectMutation.isPending
                      ? t('settings.disconnecting')
                      : t('settings.disconnect')
                  }
                  variant="outline"
                  danger
                />
              ) : (
                <SectionActionButton
                  type="button"
                  onClick={() => generateQR.mutate()}
                  disabled={generateQR.isPending}
                  icon={generateQR.isPending ? Loader2 : Link2}
                  spinning={generateQR.isPending}
                  label={
                    generateQR.isPending ? t('settings.connecting') : t('settings.connectBilibili')
                  }
                  variant="primary"
                />
              )}
            </div>

            {biliStatus?.connected ? (
              <div
                className="flex items-center gap-2 p-3 rounded-lg text-sm"
                style={{ background: 'var(--color-bg-tertiary)' }}
              >
                <CheckCircle size={16} style={{ color: 'var(--color-success)' }} />
                <span className="font-medium">
                  {biliStatus.bilibili_username
                    ? t('settings.connectedWithUser', { username: biliStatus.bilibili_username })
                    : t('settings.connected')}
                </span>
              </div>
            ) : (
              <div
                className="p-3 rounded-lg text-sm"
                style={{
                  background: 'var(--color-bg-tertiary)',
                  color: 'var(--color-text-secondary)',
                }}
              >
                {t('settings.notConnected')}
              </div>
            )}

            {/* QR Code Dialog */}
            {showQR && qrData && (
              <div
                className="fixed inset-0 flex items-center justify-center z-50"
                style={{ background: 'rgba(0,0,0,0.5)' }}
              >
                <div
                  className="p-6 rounded-2xl w-80 text-center space-y-4 relative"
                  style={{ background: 'var(--color-bg)' }}
                >
                  <button
                    onClick={() => {
                      setShowQR(false);
                      setPolling(false);
                    }}
                    className="absolute top-3 right-3 opacity-50 hover:opacity-100"
                  >
                    <X size={18} />
                  </button>
                  <h3 className="font-semibold">{t('settings.scanWithApp')}</h3>
                  <img
                    src={`data:image/png;base64,${qrData.qr_image_base64}`}
                    alt={t('settings.qrCodeAlt')}
                    className="w-48 h-48 mx-auto"
                  />
                  {polling ? (
                    <p
                      className="text-sm flex items-center justify-center gap-2"
                      style={{ color: 'var(--color-text-secondary)' }}
                    >
                      <Loader2 size={14} className="animate-spin" /> {t('settings.waitingForScan')}
                    </p>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-sm" style={{ color: 'var(--color-danger)' }}>
                        {t('settings.qrExpired')}
                      </p>
                      <button
                        onClick={() => generateQR.mutate()}
                        className="text-sm underline"
                        style={{ color: 'var(--color-primary)' }}
                      >
                        {t('settings.generateNewQR')}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </section>

          {/* Analysis Limits */}
          <AnalysisLimitsSection />

          {/* Cumulative Usage */}
          <CumulativeUsageSection />

          {/* API Tokens */}
          <ApiTokensSection />

          {/* Pending Tasks */}
          <section
            className="p-5 rounded-xl space-y-3"
            style={{
              background: 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
            }}
          >
            <h2 className="font-semibold flex items-center gap-2">
              <RefreshCw size={18} /> {t('settings.unfinishedTasks')}
            </h2>
            {tasks.length === 0 ? (
              <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                {t('settings.noUnfinishedTasks')}
              </p>
            ) : (
              <div className="space-y-2">
                {tasks.map((task) => (
                  <div
                    key={task.id}
                    className="flex items-center justify-between py-2 px-3 rounded-lg"
                    style={{ background: 'var(--color-bg-tertiary)' }}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm truncate">{task.video_title || task.url}</p>
                      <p className="text-xs" style={{ color: 'var(--color-danger)' }}>
                        {task.status}: {task.error_message}
                      </p>
                    </div>
                    <button
                      onClick={() => discardTaskMutation.mutate(task.id)}
                      className="text-xs px-2 py-1 ml-2 shrink-0"
                      style={{ color: 'var(--color-danger)' }}
                    >
                      {t('common.delete')}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function SectionActionButton({
  icon: Icon,
  label,
  variant = 'primary',
  danger = false,
  spinning = false,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon;
  label: string;
  variant?: 'primary' | 'outline';
  danger?: boolean;
  spinning?: boolean;
}) {
  const textColor =
    variant === 'primary' ? '#fff' : danger ? 'var(--color-danger)' : 'var(--color-text)';
  const background = variant === 'primary' ? 'var(--color-primary)' : 'transparent';
  const borderColor = danger ? 'rgba(239, 68, 68, 0.25)' : 'var(--color-border)';

  return (
    <button
      className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-60 shrink-0"
      style={{
        background,
        color: textColor,
        border: variant === 'outline' ? `1px solid ${borderColor}` : '1px solid transparent',
      }}
      {...props}
    >
      <Icon size={14} className={spinning ? 'animate-spin' : ''} />
      {label}
    </button>
  );
}

function YoutubeCookiesSection({
  status,
  isLoading,
  isRefreshing,
  error,
  onRefresh,
}: {
  status?: YoutubeCookiesStatus;
  isLoading: boolean;
  isRefreshing: boolean;
  error: Error | null;
  onRefresh: () => void;
}) {
  const t = useT();
  // 渲染期不允许直接调 Date.now()（非纯函数）；挂载时取一次即可
  const [nowSeconds] = useState(() => Math.floor(Date.now() / 1000));
  const expiryText = status?.earliest_expiry
    ? new Date(status.earliest_expiry).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    : '';
  const checkedText = status?.checked_at
    ? new Date(status.checked_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    : '';
  const daysLeft = status?.earliest_expiry_ts
    ? Math.floor((status.earliest_expiry_ts - nowSeconds) / 86400)
    : null;

  let tone: string = 'var(--color-text-secondary)';
  let message = t('settings.youtubeCookiesUnavailable');
  if (!status?.configured) {
    tone = 'var(--color-danger)';
    message = t('settings.youtubeCookiesNotConfiguredDetail');
  } else if (!status.file_exists) {
    tone = 'var(--color-danger)';
    message = t('settings.youtubeCookiesFileMissingDetail');
  } else if (status.expired) {
    tone = 'var(--color-danger)';
    message = expiryText
      ? t('settings.youtubeCookiesExpiredWithDateDetail', { date: expiryText })
      : t('settings.youtubeCookiesExpiredShort');
  } else if (status.usability_checked && status.usable === false) {
    tone = 'var(--color-danger)';
    message = status.usability_message || t('settings.cookiesConfiguredButUnavailable');
  } else if (status.usability_checked && status.usable === true) {
    tone = 'var(--color-success)';
    message = status.usability_message || t('settings.cookiesUsableDefaultMsg');
  } else if (status.usability_checked && status.usable === null) {
    tone = 'var(--color-warning, #f59e0b)';
    message = status.usability_message || t('settings.cookiesProbeInconclusiveMsg');
  } else if (status?.file_exists) {
    tone = 'var(--color-text-secondary)';
    message = t('settings.cookiesFileConfiguredMsg');
  }

  return (
    <section
      className="p-5 rounded-xl space-y-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1 flex-1 min-w-0">
          <h2 className="font-semibold flex items-center gap-2">
            <Cookie size={18} /> {t('settings.youtubeCookies')}
          </h2>
          <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            {t('settings.youtubeCookiesDesc')}
          </p>
        </div>
        <SectionActionButton
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          icon={isRefreshing ? Loader2 : RefreshCw}
          spinning={isRefreshing}
          label={isRefreshing ? t('settings.testing') : t('settings.connectionTest')}
          variant="primary"
        />
      </div>

      {isLoading ? (
        <p
          className="text-sm flex items-center gap-2"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          <Loader2 size={14} className="animate-spin" /> {t('settings.checkingCookies')}
        </p>
      ) : error ? (
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
      ) : status ? (
        <>
          <div
            className="p-3 rounded-lg text-sm"
            style={{ background: 'var(--color-bg-tertiary)', color: tone }}
          >
            {message}
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.configured')}</p>
              <p className="font-semibold">
                {status.configured ? t('settings.yes') : t('settings.no')}
              </p>
            </div>
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.cookieFile')}</p>
              <p className="font-semibold">
                {status.file_exists ? t('settings.found') : t('settings.missing')}
              </p>
            </div>
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.expiry')}</p>
              <p className="font-semibold">{expiryText || t('settings.unknown')}</p>
              {daysLeft !== null && !status.expired && (
                <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {t('settings.remaining', { days: daysLeft })}
                </p>
              )}
            </div>
            <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.usabilityProbe')}</p>
              <p className="font-semibold">
                {status.usability_checked
                  ? status.usable === true
                    ? t('settings.usable')
                    : status.usable === false
                      ? t('settings.rejected')
                      : t('settings.inconclusive')
                  : t('settings.skipped')}
              </p>
            </div>
          </div>
          <div className="text-xs space-y-1" style={{ color: 'var(--color-text-secondary)' }}>
            <p>
              {status.domain_summary ||
                t('settings.cookiesLoadedSummary', { count: status.cookie_count })}
            </p>
            {checkedText && (
              <p>
                {t('settings.lastChecked')}: {checkedText}
              </p>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}

function ApiTokensSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const [newName, setNewName] = useState('');
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [headerCopied, setHeaderCopied] = useState(false);

  const keysQuery = useQuery({
    queryKey: ['api-tokens'],
    queryFn: () => withTokenEndpoint((path) => api.get<ApiTokenItem[]>(path)),
  });

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      withTokenEndpoint((path) => api.post<ApiTokenCreated>(path, { name })),
    onSuccess: (data) => {
      setCreatedKey(data.full_key);
      setNewName('');
      queryClient.invalidateQueries({ queryKey: ['api-tokens'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => withTokenEndpoint((path) => api.delete(`${path}/${id}`)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['api-tokens'] }),
  });

  const handleCopyKey = async () => {
    if (!createdKey) return;
    await navigator.clipboard.writeText(createdKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCopyHeader = async () => {
    await navigator.clipboard.writeText(TOKEN_HEADER_EXAMPLE);
    setHeaderCopied(true);
    setTimeout(() => setHeaderCopied(false), 2000);
  };

  const keys = keysQuery.data || [];
  const tokenError =
    (keysQuery.error as Error | null) ||
    (createMutation.error as Error | null) ||
    (deleteMutation.error as Error | null);

  return (
    <section
      className="p-5 rounded-xl space-y-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <h2 className="font-semibold flex items-center gap-2">
        <Key size={18} /> {t('settings.apiTokens')}
      </h2>
      <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
        {t('settings.apiTokensDesc')}
      </p>

      <div className="p-3 rounded-lg space-y-2" style={{ background: 'var(--color-bg-tertiary)' }}>
        <p
          className="text-xs font-medium uppercase tracking-wide"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          {t('settings.requestHeader')}
        </p>
        <div className="flex items-center gap-2">
          <code
            className="text-xs flex-1 font-mono px-2 py-1.5 rounded"
            style={{ background: 'var(--color-bg)' }}
          >
            {TOKEN_HEADER_EXAMPLE}
          </code>
          <button
            onClick={handleCopyHeader}
            className="p-1.5 rounded-md"
            style={{ color: headerCopied ? 'var(--color-success)' : 'var(--color-text-secondary)' }}
          >
            {headerCopied ? <Check size={16} /> : <Copy size={16} />}
          </button>
        </div>
      </div>

      {tokenError && (
        <div
          className="p-3 rounded-lg text-sm"
          style={{
            background: 'rgba(239, 68, 68, 0.08)',
            border: '1px solid rgba(239, 68, 68, 0.25)',
            color: 'var(--color-danger)',
          }}
        >
          {tokenError.message}
        </div>
      )}

      {/* Created key banner */}
      {createdKey && (
        <div
          className="p-3 rounded-lg space-y-2"
          style={{
            background: 'var(--color-bg-tertiary)',
            border: '1px solid var(--color-success)',
          }}
        >
          <p className="text-sm font-medium" style={{ color: 'var(--color-success)' }}>
            {t('settings.tokenCreated')}
          </p>
          <div className="flex items-center gap-2">
            <code
              className="text-xs flex-1 font-mono px-2 py-1.5 rounded"
              style={{ background: 'var(--color-bg)' }}
            >
              {createdKey}
            </code>
            <button
              onClick={handleCopyKey}
              className="p-1.5 rounded-md"
              style={{ color: copied ? 'var(--color-success)' : 'var(--color-text-secondary)' }}
            >
              {copied ? <Check size={16} /> : <Copy size={16} />}
            </button>
          </div>
          <button
            onClick={() => setCreatedKey(null)}
            className="text-xs underline"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {t('common.dismiss')}
          </button>
        </div>
      )}

      {/* Create form */}
      <div className="flex gap-2">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t('settings.tokenNamePlaceholder')}
          className="flex-1 text-sm px-3 py-2 rounded-lg"
          style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && newName.trim()) createMutation.mutate(newName.trim());
          }}
        />
        <button
          onClick={() => newName.trim() && createMutation.mutate(newName.trim())}
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
      </div>

      {/* Keys list */}
      {keysQuery.isPending ? (
        <p
          className="text-sm flex items-center gap-2"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          <Loader2 size={14} className="animate-spin" /> {t('settings.loadingTokens')}
        </p>
      ) : keys.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          {t('settings.noTokens')}
        </p>
      ) : (
        <div className="space-y-2">
          {keys.map((k) => (
            <div
              key={k.id}
              className="flex items-center justify-between py-2 px-3 rounded-lg"
              style={{ background: 'var(--color-bg-tertiary)' }}
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">{k.name}</p>
                <p className="text-xs font-mono" style={{ color: 'var(--color-text-secondary)' }}>
                  {k.key_prefix}
                  {k.last_used_at && (
                    <span className="ml-2">
                      · {t('settings.lastUsed')}: {new Date(k.last_used_at).toLocaleDateString()}
                    </span>
                  )}
                </p>
              </div>
              <button
                onClick={() => deleteMutation.mutate(k.id)}
                disabled={deleteMutation.isPending}
                className="p-1.5 rounded hover:opacity-70"
                style={{ color: 'var(--color-danger)' }}
                title={t('settings.deleteToken')}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

interface PreferencesData {
  max_duration_seconds: number;
  confirm_threshold_seconds: number;
  max_concurrent_analyses: number;
  defaults: {
    max_duration_seconds: number;
    confirm_threshold_seconds: number;
    max_concurrent_analyses: number;
  };
}

function formatMinutes(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h${m > 0 ? `${m}m` : ''}` : `${m}m`;
}

function secondsToMinutes(seconds: number) {
  return Math.floor(seconds / 60);
}

function AnalysisLimitsSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: prefs, isPending } = useQuery({
    queryKey: ['user-preferences'],
    queryFn: () => api.get<PreferencesData>('/auth/preferences'),
  });

  const [maxDuration, setMaxDuration] = useState('');
  const [confirmThreshold, setConfirmThreshold] = useState('');
  const [maxConcurrent, setMaxConcurrent] = useState('');
  const [saved, setSaved] = useState(false);

  // prefs 加载/变化时在渲染期同步表单初值（adjust-during-render），
  // 避免在 effect 里同步 setState 造成级联渲染
  const [prevPrefs, setPrevPrefs] = useState(prefs);
  if (prefs !== prevPrefs) {
    setPrevPrefs(prefs);
    if (prefs) {
      setMaxDuration(String(secondsToMinutes(prefs.max_duration_seconds)));
      setConfirmThreshold(String(secondsToMinutes(prefs.confirm_threshold_seconds)));
      setMaxConcurrent(String(prefs.max_concurrent_analyses));
    }
  }

  const mutation = useMutation({
    mutationFn: (body: {
      max_duration_seconds: number;
      confirm_threshold_seconds: number;
      max_concurrent_analyses: number;
    }) => api.put<PreferencesData>('/auth/preferences', body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-preferences'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const handleSave = () => {
    const dur = (parseInt(maxDuration) || 0) * 60;
    const thresh = (parseInt(confirmThreshold) || 0) * 60;
    const conc = parseInt(maxConcurrent) || 0;
    mutation.mutate({
      max_duration_seconds: dur,
      confirm_threshold_seconds: thresh,
      max_concurrent_analyses: conc,
    });
  };

  if (isPending) return null;

  const defs = prefs?.defaults;

  return (
    <section
      className="p-5 rounded-xl space-y-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h2 className="font-semibold flex items-center gap-2">
            <SlidersHorizontal size={18} /> {t('settings.videoLimits')}
          </h2>
          <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            {t('settings.videoLimitsDesc')}
          </p>
        </div>
        <SectionActionButton
          type="button"
          onClick={handleSave}
          disabled={mutation.isPending}
          icon={saved ? Check : mutation.isPending ? Loader2 : Save}
          spinning={mutation.isPending}
          label={saved ? t('settings.saved') : t('settings.save')}
          variant="primary"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-1">
          <label className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
            {t('settings.maxDuration')}
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={720}
              step={1}
              value={maxDuration}
              onChange={(e) => setMaxDuration(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            />
            <span
              className="text-xs whitespace-nowrap"
              style={{ color: 'var(--color-text-secondary)' }}
            >
              {t('settings.unitMinutes')}
            </span>
          </div>
          {defs && (
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              {t('settings.defaultValue', {
                duration: formatMinutes(defs.max_duration_seconds),
                seconds: defs.max_duration_seconds,
              })}
            </p>
          )}
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
            {t('settings.confirmThreshold')}
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={720}
              step={1}
              value={confirmThreshold}
              onChange={(e) => setConfirmThreshold(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            />
            <span
              className="text-xs whitespace-nowrap"
              style={{ color: 'var(--color-text-secondary)' }}
            >
              {t('settings.unitMinutes')}
            </span>
          </div>
          {defs && (
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              {t('settings.defaultValue', {
                duration: formatMinutes(defs.confirm_threshold_seconds),
                seconds: defs.confirm_threshold_seconds,
              })}
            </p>
          )}
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
            {t('settings.maxConcurrent')}
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={10}
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
            />
            <span
              className="text-xs whitespace-nowrap"
              style={{ color: 'var(--color-text-secondary)' }}
            >
              {t('settings.unitSlots')}
            </span>
          </div>
          {defs && (
            <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              {t('settings.default')}: {defs.max_concurrent_analyses}
            </p>
          )}
        </div>
      </div>

      {mutation.isError && (
        <p className="text-xs" style={{ color: 'var(--color-danger)' }}>
          {t('settings.saveFailed')}
        </p>
      )}
    </section>
  );
}

interface UsageStats {
  asr?: Record<string, { total_seconds: number; requests: number }>;
  llm?: Record<
    string,
    { prompt_tokens: number; completion_tokens: number; total_tokens: number; requests: number }
  >;
}

function CumulativeUsageSection() {
  const t = useT();
  const { data: stats, isPending } = useQuery({
    queryKey: ['usage-stats'],
    queryFn: () => api.get<UsageStats>('/auth/usage-stats'),
  });

  const asrModels = stats?.asr ? Object.entries(stats.asr) : [];
  const llmModels = stats?.llm ? Object.entries(stats.llm) : [];
  const hasData = asrModels.length > 0 || llmModels.length > 0;

  return (
    <section
      className="p-5 rounded-xl space-y-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <div className="space-y-1">
        <h2 className="font-semibold flex items-center gap-2">
          <BarChart3 size={18} /> {t('settings.cumulativeUsage')}
        </h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          {t('settings.cumulativeUsageDesc')}
        </p>
      </div>

      {isPending ? (
        <p
          className="text-sm flex items-center gap-2"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          <Loader2 size={14} className="animate-spin" /> {t('settings.loadingUsage')}
        </p>
      ) : !hasData ? (
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          {t('settings.noUsageData')}
        </p>
      ) : (
        <div className="space-y-4">
          {llmModels.length > 0 && (
            <div className="space-y-2">
              <h3
                className="text-xs font-medium uppercase tracking-wide"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('settings.llmModels')}
              </h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {llmModels.map(([model, data]) => (
                  <div
                    key={model}
                    className="p-3 rounded-lg"
                    style={{ background: 'var(--color-bg-tertiary)' }}
                  >
                    <p className="text-sm font-medium mb-2">{model}</p>
                    <div
                      className="grid grid-cols-2 gap-2 text-xs"
                      style={{ color: 'var(--color-text-secondary)' }}
                    >
                      <div>
                        <p className="opacity-60">{t('settings.prompt')}</p>
                        <p
                          className="font-medium tabular-nums"
                          style={{ color: 'var(--color-text)' }}
                        >
                          {data.prompt_tokens.toLocaleString()}
                        </p>
                      </div>
                      <div>
                        <p className="opacity-60">{t('settings.completion')}</p>
                        <p
                          className="font-medium tabular-nums"
                          style={{ color: 'var(--color-text)' }}
                        >
                          {data.completion_tokens.toLocaleString()}
                        </p>
                      </div>
                      <div>
                        <p className="opacity-60">{t('settings.totalTokens')}</p>
                        <p
                          className="font-medium tabular-nums"
                          style={{ color: 'var(--color-primary)' }}
                        >
                          {data.total_tokens.toLocaleString()}
                        </p>
                      </div>
                      <div>
                        <p className="opacity-60">{t('settings.requests')}</p>
                        <p
                          className="font-medium tabular-nums"
                          style={{ color: 'var(--color-text)' }}
                        >
                          {data.requests}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {asrModels.length > 0 && (
            <div className="space-y-2">
              <h3
                className="text-xs font-medium uppercase tracking-wide"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                {t('settings.asrModels')}
              </h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {asrModels.map(([model, data]) => (
                  <div
                    key={model}
                    className="p-3 rounded-lg"
                    style={{ background: 'var(--color-bg-tertiary)' }}
                  >
                    <p className="text-sm font-medium mb-2">{model}</p>
                    <div
                      className="grid grid-cols-2 gap-2 text-xs"
                      style={{ color: 'var(--color-text-secondary)' }}
                    >
                      <div>
                        <p className="opacity-60">{t('settings.totalDuration')}</p>
                        <p
                          className="font-medium tabular-nums"
                          style={{ color: 'var(--color-text)' }}
                        >
                          {Math.round(data.total_seconds).toLocaleString()}s
                        </p>
                      </div>
                      <div>
                        <p className="opacity-60">{t('settings.requests')}</p>
                        <p
                          className="font-medium tabular-nums"
                          style={{ color: 'var(--color-text)' }}
                        >
                          {data.requests}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function AdminCleanupSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const [showConfirm, setShowConfirm] = useState(false);
  const [lastResult, setLastResult] = useState<AdminCleanupResult | null>(null);

  const summaryQuery = useQuery({
    queryKey: ['admin-cleanup-summary'],
    queryFn: () => api.get<AdminCleanupSummary>('/admin/cleanup/summary'),
  });

  const cleanupMutation = useMutation({
    mutationFn: () => api.post<AdminCleanupResult>('/admin/cleanup/run'),
    onSuccess: (data) => {
      setLastResult(data);
      setShowConfirm(false);
      queryClient.invalidateQueries({ queryKey: ['admin-cleanup-summary'] });
    },
  });

  const summary = summaryQuery.data;
  const activeError =
    (summaryQuery.error as Error | null) || (cleanupMutation.error as Error | null);

  return (
    <section
      className="p-5 rounded-xl space-y-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <div className="space-y-1">
        <h2 className="font-semibold flex items-center gap-2">
          <Shield size={18} /> {t('settings.adminCleanup')}
        </h2>
        <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          {t('settings.adminCleanupDesc')}
        </p>
      </div>

      {summaryQuery.isPending ? (
        <p
          className="text-sm flex items-center gap-2"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          <Loader2 size={14} className="animate-spin" /> {t('settings.scanningStorage')}
        </p>
      ) : summary ? (
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.orphanExports')}</p>
            <p className="text-xl font-semibold">{summary.orphan_exports}</p>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.orphanThumbnails')}</p>
            <p className="text-xl font-semibold">{summary.orphan_thumbnails}</p>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.orphanTaskDirs')}</p>
            <p className="text-xl font-semibold">{summary.orphan_task_dirs}</p>
          </div>
          <div className="p-3 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>{t('settings.totalRemovable')}</p>
            <p className="text-xl font-semibold">{summary.total_items}</p>
          </div>
        </div>
      ) : null}

      {activeError && (
        <div
          className="p-3 rounded-lg text-sm"
          style={{
            background: 'rgba(239, 68, 68, 0.08)',
            border: '1px solid rgba(239, 68, 68, 0.25)',
            color: 'var(--color-danger)',
          }}
        >
          {activeError.message}
        </div>
      )}

      {lastResult && (
        <div
          className="p-3 rounded-lg space-y-1 text-sm"
          style={{
            background: 'var(--color-bg-tertiary)',
            border: '1px solid var(--color-border)',
          }}
        >
          <p className="font-medium">{t('settings.lastCleanupResult')}</p>
          <p style={{ color: 'var(--color-text-secondary)' }}>
            {t('settings.cleanupRemovedDetail', {
              total: lastResult.removed_total,
              exports: lastResult.removed_exports,
              thumbnails: lastResult.removed_thumbnails,
              tasks: lastResult.removed_task_dirs,
            })}
          </p>
          {lastResult.errors.length > 0 && (
            <p style={{ color: 'var(--color-danger)' }}>
              {t('settings.cleanupErrors', { count: lastResult.errors.length })}
            </p>
          )}
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => {
            void summaryQuery.refetch();
          }}
          className="px-4 py-2 rounded-lg text-sm"
          style={{ border: '1px solid var(--color-border)' }}
        >
          {t('settings.refresh')}
        </button>
        <button
          onClick={() => setShowConfirm(true)}
          disabled={!summary || summary.total_items === 0 || cleanupMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
          style={{ background: 'var(--color-danger)' }}
        >
          {cleanupMutation.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Trash2 size={14} />
          )}
          {t('settings.cleanResidual')}
        </button>
      </div>

      {showConfirm && summary && (
        <div
          className="fixed inset-0 flex items-center justify-center z-50"
          style={{ background: 'rgba(0,0,0,0.5)' }}
        >
          <div
            className="w-full max-w-md p-6 rounded-2xl space-y-4"
            style={{ background: 'var(--color-bg)' }}
          >
            <div className="flex items-start gap-3">
              <AlertTriangle size={20} style={{ color: 'var(--color-danger)' }} />
              <div className="space-y-1">
                <h3 className="font-semibold">{t('settings.cleanConfirm')}</h3>
                <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                  {t('settings.cleanConfirmDesc')}
                </p>
              </div>
            </div>
            <div className="text-sm space-y-1" style={{ color: 'var(--color-text-secondary)' }}>
              <p>{t('settings.modalTotalRemovable', { count: summary.total_items })}</p>
              <p>{t('settings.modalExports', { count: summary.orphan_exports })}</p>
              <p>{t('settings.modalThumbnails', { count: summary.orphan_thumbnails })}</p>
              <p>{t('settings.modalTaskDirs', { count: summary.orphan_task_dirs })}</p>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 rounded-lg text-sm"
                style={{ border: '1px solid var(--color-border)' }}
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={() => cleanupMutation.mutate()}
                disabled={cleanupMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white"
                style={{ background: 'var(--color-danger)' }}
              >
                {cleanupMutation.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Trash2 size={14} />
                )}
                {t('settings.confirmCleanup')}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
