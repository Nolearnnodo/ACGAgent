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

export default apiClient
