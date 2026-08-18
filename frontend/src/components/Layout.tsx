import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { BookOpen, LayoutDashboard, Library, ScrollText, Settings, Shield, LogOut, Video } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import { cn } from '../lib/utils'
import { useLocale, type TranslationKey } from '../i18n'
import LangToggle from './LangToggle'

const NAV_ITEMS: {
  path: string
  label: TranslationKey
  icon: typeof Video
  roles: ('admin' | 'viewer')[]
}[] = [
  { path: '/analyze', label: 'nav.analyze', icon: Video, roles: ['admin'] },
  { path: '/library', label: 'nav.library', icon: Library, roles: ['admin', 'viewer'] },
  { path: '/llm-logs', label: 'nav.llmLogs', icon: ScrollText, roles: ['admin'] },
  { path: '/api-docs', label: 'nav.apiDocs', icon: BookOpen, roles: ['admin'] },
  { path: '/settings', label: 'nav.settings', icon: Settings, roles: ['admin'] },
  { path: '/admin', label: 'nav.users', icon: Shield, roles: ['admin'] },
]

export default function Layout() {
  const { username, role, logout } = useAuthStore()
  const { locale, setLocale, t } = useLocale()
  const location = useLocation()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const visibleNav = NAV_ITEMS.filter((item) => role != null && (item.roles as string[]).includes(role))

  return (
    <div className="flex h-screen" style={{ background: 'var(--color-bg)' }}>
      <aside
        className="w-56 flex flex-col border-r shrink-0"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}
      >
        <div className="p-5 flex items-center gap-2">
          <LayoutDashboard size={22} style={{ color: 'var(--color-primary)' }} />
          <span className="font-semibold text-lg">VideoSplit</span>
        </div>

        <nav className="flex-1 px-3 space-y-1">
          {visibleNav.map((item) => {
            const active = location.pathname === item.path
            return (
              <Link
                key={item.path}
                to={item.path}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors',
                  active ? 'font-medium' : 'opacity-70 hover:opacity-100',
                )}
                style={active ? { background: 'var(--color-bg-tertiary)', color: 'var(--color-primary)' } : {}}
              >
                <item.icon size={18} />
                {t(item.label)}
              </Link>
            )
          })}
        </nav>

        <div className="p-3 border-t" style={{ borderColor: 'var(--color-border)' }}>
          <div className="flex items-center justify-between px-3 py-2">
            <span className="text-sm truncate" style={{ color: 'var(--color-text-secondary)' }}>
              {username} ({role})
            </span>
            <button onClick={handleLogout} className="p-1.5 rounded hover:opacity-70 transition-opacity" title={t('nav.logout')}>
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <div className="flex justify-end px-6 pt-4">
          <LangToggle lang={locale} onChange={setLocale} />
        </div>
        <div className="max-w-6xl mx-auto px-6 pb-6 pt-2">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
