import { useMutation } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useAuthStore } from '../stores/authStore';

interface AuthResponse {
  access_token: string;
  role: string;
  username: string;
  lang_preference: string;
}

export function useLogin() {
  const setAuth = useAuthStore((s) => s.setAuth);
  return useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      api.post<AuthResponse>('/auth/login', body),
    onSuccess: (data) => setAuth(data.access_token, data.username, data.role, data.lang_preference),
  });
}
