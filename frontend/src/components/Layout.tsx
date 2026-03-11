import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Library, Settings, Shield, LogOut, Video } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import { cn } from '../lib/utils'

const navItems = [
  { path: '/', label: 'Analyze', icon: Video, roles: ['user'] },
  { path: '/library', label: 'Library', icon: Library, roles: ['user', 'admin', 'viewer'] },
  { path: '/settings', label: 'Settings', icon: Settings, roles: ['user'] },
  { path: '/admin', label: 'Users', icon: Shield, roles: ['admin'] },
]

export default function Layout() {
  const { username, role, logout } = useAuthStore()
  const location = useLocation()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const visibleNav = navItems.filter((item) => item.roles.includes(role || ''))

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
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="p-3 border-t" style={{ borderColor: 'var(--color-border)' }}>
          <div className="flex items-center justify-between px-3 py-2">
            <span className="text-sm truncate" style={{ color: 'var(--color-text-secondary)' }}>
              {username} ({role})
            </span>
            <button onClick={handleLogout} className="p-1.5 rounded hover:opacity-70 transition-opacity" title="Logout">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <div className="max-w-6xl mx-auto p-6">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
