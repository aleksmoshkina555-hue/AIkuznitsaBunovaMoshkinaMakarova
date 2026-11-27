// Запросы к Django API. Вход — сессия Django (cookie), защита от подделки запросов — заголовок X-CSRFToken.

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const isForm = body instanceof FormData
  const response = await fetch(`/api/${url}`, {
    method,
    credentials: 'same-origin',
    headers: {
      ...(method !== 'GET' ? { 'X-CSRFToken': csrfToken() } : {}),
      ...(body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  })
  if (!response.ok) {
    let message = `Ошибка ${response.status}`
    try {
      const data = await response.json()
      message = data.error || data.detail || message
    } catch {
      /* ответ не JSON */
    }
    if (response.status === 403 && message.includes('Authentication')) message = 'Нужно войти в систему'
    throw new ApiError(message, response.status)
  }
  const type = response.headers.get('Content-Type') || ''
  return (type.includes('application/json') ? response.json() : response.blob()) as Promise<T>
}

export const api = {
  get: <T,>(url: string) => request<T>('GET', url),
  post: <T,>(url: string, body?: unknown) => request<T>('POST', url, body ?? {}),
  put: <T,>(url: string, body: unknown) => request<T>('PUT', url, body),
  patch: <T,>(url: string, body: unknown) => request<T>('PATCH', url, body),
  del: <T,>(url: string) => request<T>('DELETE', url),
}

/** Скачать файл (архив переноса, документ базы) — через ссылку, чтобы браузер сохранил его как обычно. */
export async function download(url: string, method: 'GET' | 'POST' = 'GET'): Promise<string> {
  const response = await fetch(`/api/${url}`, {
    method,
    credentials: 'same-origin',
    headers: method === 'POST' ? { 'X-CSRFToken': csrfToken() } : {},
  })
  if (!response.ok) throw new ApiError(`Ошибка ${response.status}`, response.status)
  const disposition = response.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename\*=utf-8''([^;]+)|filename="?([^";]+)"?/i)
  const name = match ? decodeURIComponent(match[1] || match[2]) : 'file'
  const blob = await response.blob()
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = name
  link.click()
  URL.revokeObjectURL(link.href)
  return name
}
