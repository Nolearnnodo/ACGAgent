import apiClient from './client'

export interface AuthPayload {
  email: string
  password: string
}

export interface UserProfile {
  id: number
  email: string
  role: string
}

export interface AuthResponse {
  user: UserProfile
  tokens: {
    access_token: string
    refresh_token: string
    token_type: string
  }
}

export async function register(payload: AuthPayload) {
  const { data } = await apiClient.post<AuthResponse>('/auth/register', payload)
  return data
}

export async function login(payload: AuthPayload) {
  const { data } = await apiClient.post<AuthResponse>('/auth/login', payload)
  return data
}

export async function fetchCurrentUser() {
  const { data } = await apiClient.get<UserProfile>('/auth/me')
  return data
}

export async function updateProfile(payload: { email?: string; password?: string }) {
  const { data } = await apiClient.put<UserProfile>('/auth/me', payload)
  return data
}

export async function logout(refreshToken: string) {
  const { data } = await apiClient.post<{ message: string }>('/auth/logout', {
    refresh_token: refreshToken,
  })
  return data
}
