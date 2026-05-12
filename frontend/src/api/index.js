// Axios 封装 + 所有后端 API 调用方法
import axios from 'axios'

// 开发环境走 Vite 代理（/api -> localhost:8888），生产环境直连
export const apiBase = import.meta.env.VITE_API_BASE || ''

const http = axios.create({
  baseURL: apiBase + '/api/v1',
  timeout: 120_000,
})

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message
    return Promise.reject(new Error(msg))
  }
)

/* ═══════════ 健康检查 ═══════════ */
export const healthCheck = () => http.get('/health')

/* ═══════════ 链接检测 ═══════════ */
export const checkSingleUrl = (data) => http.post('/check/url', data)

export const getSingleUrlResult = (taskId) =>
  http.get(`/check/url/result/${taskId}`)

export const checkBatchUrls = (data) => http.post('/check/batch', data)

export const uploadFileCheck = (formData) =>
  http.post('/check/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300_000,
  })

export const checkMysqlSource = (data) => http.post('/check/mysql', data)

/* ═══════════ 任务管理 ═══════════ */
export const getTaskProgress = (taskId, logOffset = 0) =>
  http.get(`/task/${taskId}`, { params: { log_offset: logOffset } })

export const cancelTask = (taskId) => http.post(`/task/${taskId}/cancel`)

export const downloadTaskResult = (taskId, format = 'excel') =>
  axios.get(`${apiBase}/api/v1/task/${taskId}/result`, {
    params: { format },
    responseType: 'blob',
  })

export const getTaskJsonResult = (taskId) =>
  http.get(`/task/${taskId}/result/json`)

export const getTaskComments = (taskId) =>
  http.get(`/task/${taskId}/comments`)

export const downloadTaskComments = (taskId, format = 'json') =>
  axios.get(`${apiBase}/api/v1/task/${taskId}/comments/download`, {
    params: { format },
    responseType: 'blob',
  })

/* ═══════════ Cookie 管理 ═══════════ */
export const listCookies = (platform = '') =>
  http.get('/cookies', { params: platform ? { platform } : {} })

export const addCookie = (data) => http.post('/cookies/add', data)

export const removeCookie = (data) => http.post('/cookies/remove', data)

export const reloadCookies = () => http.post('/cookies/reload')

/* ═══════════ 扫码登录 ═══════════ */
export const startScan = (platform = 'all', note = '', scanMode = 'force_new') =>
  http.post(`/cookies/scan/start?platform=${platform}&note=${encodeURIComponent(note)}&scan_mode=${scanMode}`)

export const getScanQrcode = (sessionId) =>
  http.get(`/cookies/scan/qrcode/${sessionId}`)

export const getScanStatus = (sessionId) =>
  http.get(`/cookies/scan/status/${sessionId}`)

export const cancelScan = (sessionId) =>
  http.post(`/cookies/scan/cancel/${sessionId}`)

/* ═══════════ 批量操作 ═══════════ */
export const batchRemoveCookies = (items) =>
  http.post('/cookies/remove/batch', { items })

export const deleteTask = (taskId) =>
  http.post(`/task/${taskId}/delete`)

export const batchDeleteTasks = (taskIds) =>
  http.post('/tasks/delete/batch', { task_ids: taskIds })

/* ═══════════ 回调配置 ═══════════ */
export const getCallbackConfig = () => http.get('/callback/config')

export const updateCallbackConfig = (data) => http.post('/callback/config', data)

export default http
