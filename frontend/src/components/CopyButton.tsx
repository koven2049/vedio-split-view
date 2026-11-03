import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useT } from '../i18n';

/** Copy-to-clipboard button. Icon-only by default; pass `label` for a bordered text button. */
export default function CopyButton({ text, label }: { text: string; label?: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const icon = copied ? <Check size={14} /> : <Copy size={14} />;
  if (!label) {
    return (
      <button
        onClick={handleCopy}
        className="p-1.5 rounded-md hover:opacity-80 transition-opacity"
        style={{ color: copied ? 'var(--color-success)' : 'var(--color-text-secondary)' }}
        title={t('common.copy')}
      >
        {icon}
      </button>
    );
  }
  return (
    <button
      onClick={handleCopy}
      className="inline-flex items-center gap-1.5 px-3 rounded-lg text-xs font-medium shrink-0"
      style={{
        border: '1px solid var(--color-border)',
        color: copied ? 'var(--color-success)' : 'var(--color-text)',
      }}
    >
      {icon}
      {copied ? t('settings.copied') : label}
    </button>
  );
}
