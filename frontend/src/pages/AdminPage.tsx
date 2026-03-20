import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Shield, UserCheck, Trash2, UserPlus, Loader2, Clock, AlertTriangle, X } from 'lucide-react'
import { api } from '../lib/api'

interface AdminUser {
  id: number; username: string; role: string
  is_active: boolean; created_at: string; video_count: number
}
interface DeletePreview {
  user_id: number
  username: string
  library_videos: number
  public_videos: number
  private_videos: number
  task_count: number
  api_token_count: number
  export_files: number
  thumbnail_files: number
  temp_dirs: number
  total_items: number
}

export default function AdminPage() {
  const queryClient = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newRole, setNewRole] = useState<'user' | 'viewer'>('user')
  const [formError, setFormError] = useState('')
  const [deletePreview, setDeletePreview] = useState<DeletePreview | null>(null)
  const [deleteError, setDeleteError] = useState('')

  const usersQuery = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get<AdminUser[]>('/admin/users'),
  })

  const toggleMutation = useMutation({
    mutationFn: (userId: number) => api.put(`/admin/users/${userId}/toggle`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin-users'] }),
  })

  const deleteMutation = useMutation({
    mutationFn: (userId: number) => api.delete(`/admin/users/${userId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      setDeletePreview(null)
      setDeleteError('')
    },
    onError: (e: Error) => setDeleteError(e.message),
  })

  const previewMutation = useMutation({
    mutationFn: (userId: number) => api.get<DeletePreview>(`/admin/users/${userId}/delete-preview`),
    onSuccess: (data) => {
      setDeletePreview(data)
      setDeleteError('')
    },
    onError: (e: Error) => setDeleteError(e.message),
  })

  const createMutation = useMutation({
    mutationFn: (data: { username: string; password: string; role: string }) => api.post('/admin/users', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      setShowForm(false)
      setNewUsername('')
      setNewPassword('')
      setNewRole('user')
      setFormError('')
    },
    onError: (e: Error) => setFormError(e.message),
  })

  const handleCreate = () => {
    setFormError('')
    if (newUsername.length < 2) { setFormError('Username must be at least 2 characters'); return }
    if (newPassword.length < 4) { setFormError('Password must be at least 4 characters'); return }
    createMutation.mutate({ username: newUsername, password: newPassword, role: newRole })
  }

  const handleDeleteClick = (userId: number) => {
    setDeleteError('')
    previewMutation.mutate(userId)
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Shield size={24} style={{ color: 'var(--color-primary)' }} /> User Management
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--color-text-secondary)' }}>
            Manage registered users. Admin accounts cannot analyze videos.
          </p>
        </div>
        <button
          onClick={() => { setShowForm(!showForm); setFormError('') }}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium text-white transition-colors"
          style={{ background: 'var(--color-primary)' }}
        >
          <UserPlus size={16} /> Add User
        </button>
      </div>

      {/* Create User Form */}
      {showForm && (
        <div className="p-5 rounded-xl space-y-4" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
          <h3 className="text-sm font-semibold">Create New User</h3>
          <div className="flex gap-3">
            <input
              type="text"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              placeholder="Username"
              className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            />
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Password"
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            />
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as 'user' | 'viewer')}
              className="px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            >
              <option value="user">User</option>
              <option value="viewer">Viewer</option>
            </select>
            <button
              onClick={handleCreate}
              disabled={createMutation.isPending}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
              style={{ background: 'var(--color-primary)' }}
            >
              {createMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : 'Create'}
            </button>
            <button
              onClick={() => { setShowForm(false); setFormError('') }}
              className="px-4 py-2 rounded-lg text-sm"
              style={{ border: '1px solid var(--color-border)' }}
            >
              Cancel
            </button>
          </div>
          {formError && (
            <p className="text-xs" style={{ color: 'var(--color-danger)' }}>{formError}</p>
          )}
        </div>
      )}

      {deleteError && !deletePreview && !previewMutation.isPending && (
        <div className="p-3 rounded-lg text-sm" style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', color: 'var(--color-danger)' }}>
          {deleteError}
        </div>
      )}

      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--color-border)' }}>
        <table className="w-full text-sm">
          <thead>
            <tr style={{ background: 'var(--color-bg-secondary)' }}>
              <th className="text-left px-4 py-3 font-medium">Username</th>
              <th className="text-left px-4 py-3 font-medium">Role</th>
              <th className="text-left px-4 py-3 font-medium">Videos</th>
              <th className="text-left px-4 py-3 font-medium">Created</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-right px-4 py-3 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {usersQuery.data?.map((user) => (
              <tr key={user.id} style={{ borderTop: '1px solid var(--color-border)' }}>
                <td className="px-4 py-3 font-medium">{user.username}</td>
                <td className="px-4 py-3">
                  <span className="px-2 py-0.5 rounded-full text-[11px] font-medium"
                    style={{
                      background: user.role === 'viewer' ? 'rgba(107,114,128,0.12)' : 'rgba(59,130,246,0.12)',
                      color: user.role === 'viewer' ? '#6b7280' : 'var(--color-primary)',
                    }}
                  >{user.role}</span>
                </td>
                <td className="px-4 py-3" style={{ color: 'var(--color-text-secondary)' }}>{user.video_count}</td>
                <td className="px-4 py-3" style={{ color: 'var(--color-text-secondary)' }}>
                  {new Date(user.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3">
                  {user.is_active ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                      style={{ background: 'var(--color-success)' + '20', color: 'var(--color-success)' }}>
                      <UserCheck size={12} /> Active
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                      style={{ background: 'var(--color-warning, #f59e0b)' + '20', color: 'var(--color-warning, #f59e0b)' }}>
                      <Clock size={12} /> Pending
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex gap-1 justify-end">
                    {user.is_active ? (
                      <button
                        onClick={() => toggleMutation.mutate(user.id)}
                        className="px-2.5 py-1 rounded text-xs"
                        style={{ border: '1px solid var(--color-border)' }}
                      >
                        Disable
                      </button>
                    ) : (
                      <button
                        onClick={() => toggleMutation.mutate(user.id)}
                        className="px-2.5 py-1 rounded text-xs font-medium text-white"
                        style={{ background: 'var(--color-success, #10b981)' }}
                      >
                        Approve
                      </button>
                    )}
                    <button
                      onClick={() => handleDeleteClick(user.id)}
                      className="px-2.5 py-1 rounded text-xs"
                      style={{ color: 'var(--color-danger)', border: '1px solid var(--color-border)' }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {usersQuery.data?.length === 0 && (
              <tr>
                <td colSpan={6} className="text-center py-8" style={{ color: 'var(--color-text-secondary)' }}>
                  No registered users yet. Click "Add User" to create one.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {(previewMutation.isPending || deletePreview) && (
        <div className="fixed inset-0 flex items-center justify-center z-50" style={{ background: 'rgba(0,0,0,0.5)' }}>
          <div className="w-full max-w-md p-6 rounded-2xl space-y-4 relative" style={{ background: 'var(--color-bg)' }}>
            {!deleteMutation.isPending && (
              <button
                onClick={() => { setDeletePreview(null); setDeleteError('') }}
                className="absolute top-3 right-3 opacity-50 hover:opacity-100"
              >
                <X size={18} />
              </button>
            )}

            {previewMutation.isPending ? (
              <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                <Loader2 size={16} className="animate-spin" /> Loading delete impact...
              </div>
            ) : deletePreview ? (
              <>
                <div className="flex items-start gap-3">
                  <AlertTriangle size={20} style={{ color: 'var(--color-danger)' }} />
                  <div className="space-y-1">
                    <h3 className="font-semibold">Delete user "{deletePreview.username}"?</h3>
                    <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                      This will remove the account and permanently delete its related files.
                    </p>
                  </div>
                </div>

                <div className="p-3 rounded-lg text-sm space-y-1" style={{ background: 'var(--color-bg-tertiary)' }}>
                  <p>Library: <strong>{deletePreview.library_videos}</strong></p>
                  <p>Public: <strong>{deletePreview.public_videos}</strong></p>
                  <p>Private: <strong>{deletePreview.private_videos}</strong></p>
                  <p>Tasks: <strong>{deletePreview.task_count}</strong></p>
                  <p>API tokens: <strong>{deletePreview.api_token_count}</strong></p>
                </div>

                <div className="text-sm space-y-1" style={{ color: 'var(--color-text-secondary)' }}>
                  <p>Files to delete: <strong style={{ color: 'var(--color-text)' }}>{deletePreview.total_items}</strong></p>
                  <p>Export files: <strong style={{ color: 'var(--color-text)' }}>{deletePreview.export_files}</strong></p>
                  <p>Thumbnails: <strong style={{ color: 'var(--color-text)' }}>{deletePreview.thumbnail_files}</strong></p>
                  <p>Task temp dirs: <strong style={{ color: 'var(--color-text)' }}>{deletePreview.temp_dirs}</strong></p>
                </div>

                {deleteError && (
                  <div className="p-3 rounded-lg text-sm" style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', color: 'var(--color-danger)' }}>
                    {deleteError}
                  </div>
                )}

                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => { setDeletePreview(null); setDeleteError('') }}
                    disabled={deleteMutation.isPending}
                    className="px-4 py-2 rounded-lg text-sm"
                    style={{ border: '1px solid var(--color-border)' }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => deleteMutation.mutate(deletePreview.user_id)}
                    disabled={deleteMutation.isPending}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white"
                    style={{ background: 'var(--color-danger)' }}
                  >
                    {deleteMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    Delete user and files
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  )
}
