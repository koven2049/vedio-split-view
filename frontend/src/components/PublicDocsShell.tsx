import { Link, Outlet } from 'react-router-dom';
import { LayoutDashboard, LogIn } from 'lucide-react';
import LangToggle from './LangToggle';
import { useLocale } from '../i18n';

/** Minimal chrome for pages readable without login (the API docs). */
export default function PublicDocsShell() {
  const { locale, setLocale, t } = useLocale();
  return (
    <div className="min-h-screen" style={{ background: 'var(--color-bg)' }}>
      <header
        className="flex items-center justify-between px-6 py-4 border-b"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
      >
        <div className="flex items-center gap-2">
          <LayoutDashboard size={22} style={{ color: 'var(--color-primary)' }} />
          <span className="font-semibold text-lg">VideoSplit</span>
        </div>
        <div className="flex items-center gap-3">
          <LangToggle lang={locale} onChange={setLocale} />
          <Link
            to="/login"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white"
            style={{ background: 'var(--color-primary)' }}
          >
            <LogIn size={14} /> {t('login.submit')}
          </Link>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
