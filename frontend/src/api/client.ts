import axios, { type InternalAxiosRequestConfig } from 'axios'

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const apiClient = axios.create({
  baseURL: apiBaseUrl,
  // 古籍上传 → workflow 涉及 4 次 LLM 调用 + Neo4j 写入，
  // 单篇耗时 30s 左右；放到 5 分钟兜底超长正史文档。
  timeout: 300000,
})

// 会话在到期前 5 分钟续期，但只有近期有界面操作时才会持续延长。
const REFRESH_LEAD_MS = 5 * 60 * 1000
const ACTIVE_SESSION_WINDOW_MS = 10 * 60 * 1000
const REFRESH_RETRY_DELAY_MS = 60 * 1000
const REFRESH_ATTEMPT_COOLDOWN_MS = 10 * 1000

const tokenRefreshClient = axios.create({
  baseURL: apiBaseUrl,
  timeout: 30000,
})

interface TokenRefreshResponse {
  tokens: {
    access_token: string
    refresh_token: string
  }
}

type RefreshableRequestConfig = InternalAxiosRequestConfig & {
  _authRefreshAttempted?: boolean
}

let refreshTimer: number | null = null
let refreshPromise: Promise<string | null> | null = null
let lastActivityAt = 0
let lastRefreshAttemptAt = 0
let activityListenersInstalled = false
let authExpiredNotified = false

function getStoredToken(key: 'access_token' | 'refresh_token') {
  if (typeof window === 'undefined') {
    return null
  }
  return window.localStorage.getItem(key)
}

function persistTokenPair(tokens: TokenRefreshResponse['tokens']) {
  window.localStorage.setItem('access_token', tokens.access_token)
  window.localStorage.setItem('refresh_token', tokens.refresh_token)
}

function clearStoredTokens() {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.removeItem('access_token')
  window.localStorage.removeItem('refresh_token')
}

function getTokenExpiryMs(token: string) {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    const encodedPayload = token.split('.')[1]
    if (!encodedPayload) {
      return null
    }

    const base64Payload = encodedPayload.replace(/-/g, '+').replace(/_/g, '/')
    const paddedPayload = base64Payload.padEnd(Math.ceil(base64Payload.length / 4) * 4, '=')
    const payload = JSON.parse(window.atob(paddedPayload)) as { exp?: unknown }
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null
  } catch {
    return null
  }
}

function isRecentlyActive() {
  return lastActivityAt > 0 && Date.now() - lastActivityAt <= ACTIVE_SESSION_WINDOW_MS
}

function notifyAuthExpired() {
  if (authExpiredNotified || typeof window === 'undefined') {
    return
  }

  authExpiredNotified = true
  window.dispatchEvent(new Event('acgagent:auth-expired'))
}

function clearRefreshTimer() {
  if (refreshTimer !== null && typeof window !== 'undefined') {
    window.clearTimeout(refreshTimer)
    refreshTimer = null
  }
}

async function performTokenRefresh(refreshToken: string): Promise<string | null> {
  try {
    const { data } = await tokenRefreshClient.post<TokenRefreshResponse>('/auth/refresh', {
      refresh_token: refreshToken,
    })

    if (!data.tokens?.access_token || !data.tokens.refresh_token) {
      return null
    }

    // 另一个标签页可能已经轮换了 refresh token，避免覆盖更新后的会话。
    if (getStoredToken('refresh_token') !== refreshToken) {
      return getStoredToken('access_token')
    }

    persistTokenPair(data.tokens)
    authExpiredNotified = false
    return data.tokens.access_token
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      // 只有当前存储的 refresh token 仍是本次请求使用的 token 时，才判定会话真正失效。
      if (getStoredToken('refresh_token') === refreshToken) {
        clearStoredTokens()
        notifyAuthExpired()
      }
    }
    return null
  }
}

export function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) {
    return refreshPromise
  }

  const refreshToken = getStoredToken('refresh_token')
  if (!refreshToken) {
    return Promise.resolve(null)
  }

  lastRefreshAttemptAt = Date.now()
  refreshPromise = performTokenRefresh(refreshToken).finally(() => {
    refreshPromise = null
    scheduleTokenRefresh()
  })
  return refreshPromise
}

async function ensureFreshAccessToken() {
  const accessToken = getStoredToken('access_token')
  const refreshToken = getStoredToken('refresh_token')
  const expiresAt = accessToken ? getTokenExpiryMs(accessToken) : null

  if (!accessToken || !refreshToken || expiresAt === null) {
    return accessToken
  }

  if (expiresAt - Date.now() <= REFRESH_LEAD_MS) {
    // 业务请求已经代表用户正在操作，即使刚刚错过定时器也应先续期再发请求。
    return (await refreshAccessToken()) ?? getStoredToken('access_token')
  }

  return accessToken
}

function scheduleTokenRefresh() {
  if (typeof window === 'undefined') {
    return
  }

  clearRefreshTimer()

  const accessToken = getStoredToken('access_token')
  const refreshToken = getStoredToken('refresh_token')
  const expiresAt = accessToken ? getTokenExpiryMs(accessToken) : null
  if (!accessToken || !refreshToken || expiresAt === null) {
    return
  }

  const remainingMs = expiresAt - Date.now()
  if (remainingMs > REFRESH_LEAD_MS) {
    refreshTimer = window.setTimeout(scheduleTokenRefresh, remainingMs - REFRESH_LEAD_MS)
    return
  }

  if (Date.now() - lastRefreshAttemptAt < REFRESH_ATTEMPT_COOLDOWN_MS) {
    refreshTimer = window.setTimeout(scheduleTokenRefresh, REFRESH_RETRY_DELAY_MS)
    return
  }

  if (isRecentlyActive()) {
    void refreshAccessToken()
    return
  }

  // 用户暂时离开页面时不续期；回来后的下一次操作会立即触发刷新。
  refreshTimer = window.setTimeout(scheduleTokenRefresh, REFRESH_RETRY_DELAY_MS)
}

function handleUserActivity() {
  lastActivityAt = Date.now()
  scheduleTokenRefresh()
}

function handleVisibilityChange() {
  if (document.visibilityState === 'visible') {
    handleUserActivity()
  }
}

function installActivityListeners() {
  if (activityListenersInstalled || typeof window === 'undefined') {
    return
  }

  for (const eventName of ['pointerdown', 'keydown', 'input', 'touchstart'] as const) {
    window.addEventListener(eventName, handleUserActivity, { passive: true })
  }
  document.addEventListener('visibilitychange', handleVisibilityChange)
  activityListenersInstalled = true
}

function removeActivityListeners() {
  if (!activityListenersInstalled || typeof window === 'undefined') {
    return
  }

  for (const eventName of ['pointerdown', 'keydown', 'input', 'touchstart'] as const) {
    window.removeEventListener(eventName, handleUserActivity)
  }
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  activityListenersInstalled = false
}

export function startTokenRefresh() {
  if (typeof window === 'undefined') {
    return
  }

  installActivityListeners()
  lastActivityAt = Date.now()
  authExpiredNotified = false
  scheduleTokenRefresh()
}

export function stopTokenRefresh() {
  clearRefreshTimer()
  removeActivityListeners()
  lastActivityAt = 0
  lastRefreshAttemptAt = 0
}

function shouldSkipAuthRefresh(config: RefreshableRequestConfig) {
  const path = (config.url ?? '').split('?')[0]
  return [
    '/auth/login',
    '/auth/register',
    '/auth/refresh',
    '/auth/logout',
    '/auth/password-reset/code',
    '/auth/password-reset',
  ].some((endpoint) => path.endsWith(endpoint))
}

// 统一注入 access token，并把每次请求视为一次活跃操作。
// 临近过期时先完成续期，再发送业务请求，避免正常提交先收到 401。
apiClient.interceptors.request.use(async (config) => {
  handleUserActivity()
  const token = shouldSkipAuthRefresh(config as RefreshableRequestConfig)
    ? getStoredToken('access_token')
    : await ensureFreshAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 保存操作恰好遇到 access token 过期时，刷新后自动重试一次原请求。
apiClient.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (!axios.isAxiosError(error)) {
      return Promise.reject(error)
    }

    const config = error.config as RefreshableRequestConfig | undefined
    if (
      error.response?.status !== 401 ||
      !config ||
      config._authRefreshAttempted ||
      shouldSkipAuthRefresh(config) ||
      !getStoredToken('refresh_token')
    ) {
      return Promise.reject(error)
    }

    config._authRefreshAttempted = true
    const accessToken = await refreshAccessToken()
    if (!accessToken) {
      return Promise.reject(error)
    }

    config.headers.Authorization = `Bearer ${accessToken}`
    return apiClient(config)
  },
)

interface ApiValidationItem {
  loc?: unknown[]
  msg?: string
  type?: string
  path?: string
  message?: string
}

function isValidationItem(value: unknown): value is ApiValidationItem {
  return Boolean(value && typeof value === 'object')
}

function formatValidationItem(item: ApiValidationItem) {
  if (item.message) {
    return item.path ? `${item.path}：${item.message}` : item.message
  }
  const field = Array.isArray(item.loc) ? String(item.loc[item.loc.length - 1] ?? '') : ''
  const message = item.msg ?? '输入内容不符合要求。'

  if (field === 'email') {
    return '请输入合法邮箱。'
  }

  if (
    (field === 'password' || field === 'new_password') &&
    (item.type?.includes('too_short') || message.includes('at least 6'))
  ) {
    return '密码至少需要 6 位。'
  }

  if ((field === 'password' || field === 'new_password') && item.type?.includes('too_long')) {
    return '密码不能超过 128 位。'
  }

  if (field === 'code') {
    return '请输入 6 位数字验证码。'
  }

  return message
}

export function getApiErrorMessage(error: unknown, fallback = '请求失败，请稍后重试。') {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error.message : fallback
  }

  const detail = error.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }

  if (Array.isArray(detail)) {
    const messages = detail.filter(isValidationItem).map(formatValidationItem)
    if (messages.length) {
      return Array.from(new Set(messages)).join(' ')
    }
  }

  return error.message || fallback
}

export default apiClient
