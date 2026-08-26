import { cn } from '../lib/utils';

interface LangToggleProps {
  lang: 'zh' | 'en';
  onChange: (lang: 'zh' | 'en') => void;
  className?: string;
}

export default function LangToggle({ lang, onChange, className }: LangToggleProps) {
  return (
    <div
      className={cn(
        'inline-flex rounded-md overflow-hidden text-xs font-medium select-none',
        className,
      )}
      style={{ border: '1px solid var(--color-border)' }}
    >
      <button
        onClick={() => onChange('zh')}
        className="px-2.5 py-1 transition-colors"
        style={{
          background: lang === 'zh' ? 'var(--color-primary)' : 'var(--color-bg)',
          color: lang === 'zh' ? '#fff' : 'var(--color-text-secondary)',
        }}
      >
        中
      </button>
      <button
        onClick={() => onChange('en')}
        className="px-2.5 py-1 transition-colors"
        style={{
          background: lang === 'en' ? 'var(--color-primary)' : 'var(--color-bg)',
          color: lang === 'en' ? '#fff' : 'var(--color-text-secondary)',
          borderLeft: '1px solid var(--color-border)',
        }}
      >
        EN
      </button>
    </div>
  );
}
