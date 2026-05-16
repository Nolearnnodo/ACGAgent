import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  fetchCurrentUser,
  login,
  logout,
  register,
  type AuthPayload,
  type UserProfile,
} from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserProfile | null>(null)
  const loading = ref(false)

  const isAuthenticated = computed(() => Boolean(user.value && localStorage.getItem('access_token')))

  function persistTokens(accessToken: string, refreshToken: string) {
    localStorage.setItem('access_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
  }

  async function loginAction(payload: AuthPayload) {
    loading.value = true
    try {
      const response = await login(payload)
      persistTokens(response.tokens.access_token, response.tokens.refresh_token)
      user.value = response.user
    } finally {
      loading.value = false
    }
  }

  async function registerAction(payload: AuthPayload) {
    loading.value = true
    try {
      const response = await register(payload)
      persistTokens(response.tokens.access_token, response.tokens.refresh_token)
      user.value = response.user
    } finally {
      loading.value = false
    }
  }

  async function restoreSession() {
    const token = localStorage.getItem('access_token')
    if (!token) {
      return
    }
    try {
      user.value = await fetchCurrentUser()
    } catch {
      clearSession()
    }
  }

  async function logoutAction() {
    const refreshToken = localStorage.getItem('refresh_token')
    if (refreshToken) {
      try {
        await logout(refreshToken)
      } catch {
        // 即使后端登出失败，也要清理前端状态，避免死锁。
      }
    }
    clearSession()
  }

  function clearSession() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    user.value = null
  }

  return {
    user,
    loading,
    isAuthenticated,
    loginAction,
    registerAction,
    restoreSession,
    logoutAction,
  }
})
