import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/authStore'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import AnalyzePage from './pages/AnalyzePage'
import LibraryPage from './pages/LibraryPage'
import VideoDetailPage from './pages/VideoDetailPage'
import SettingsPage from './pages/SettingsPage'
import ApiDocsPage from './pages/ApiDocsPage'
import AdminPage from './pages/AdminPage'
import LLMLogsPage from './pages/LLMLogsPage'

function ProtectedRoute({ children, adminOnly = false }: { children: React.ReactNode; adminOnly?: boolean }) {
  const { token, role } = useAuthStore()
  if (!token) return <Navigate to="/login" replace />
  if (adminOnly && role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

function HomeRedirect() {
  const { role } = useAuthStore()
  if (role === 'admin') return <Navigate to="/analyze" replace />
  return <Navigate to="/library" replace />
}

export default function App() {
  const { token } = useAuthStore()

  return (
    <Routes>
      <Route path="/login" element={token ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="/" element={<HomeRedirect />} />
        <Route path="/analyze" element={<ProtectedRoute adminOnly><AnalyzePage /></ProtectedRoute>} />
        <Route path="/library" element={<ProtectedRoute><LibraryPage /></ProtectedRoute>} />
        <Route path="/video/:id" element={<ProtectedRoute><VideoDetailPage /></ProtectedRoute>} />
        <Route path="/api-docs" element={<ProtectedRoute adminOnly><ApiDocsPage /></ProtectedRoute>} />
        <Route path="/llm-logs" element={<ProtectedRoute adminOnly><LLMLogsPage /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute adminOnly><SettingsPage /></ProtectedRoute>} />
        <Route path="/admin" element={<ProtectedRoute adminOnly><AdminPage /></ProtectedRoute>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
