import axios from 'axios'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1',
  // 古籍上传 → workflow 涉及 4 次 LLM 调用 + Neo4j 写入，
  // 单篇耗时 30s 左右；放到 5 分钟兜底超长正史文档。
  timeout: 300000,
})

// 统一注入 access token，便于后续扩展刷新逻辑。
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

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
