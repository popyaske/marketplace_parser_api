const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export interface Product {
  id: number
  url: string
  marketplace: string
  sku?: string
  title?: string
  price_rub?: number
  price_with_plus?: number
  price_original_rub?: number
  seller?: string
  brand?: string
  rating?: number
  rating_count?: number
  in_stock?: boolean
  image_url?: string
  tier_used?: string
  post_text?: string
  success: boolean
  error?: string
  created_at: string
}

export interface ProductsPage {
  items: Product[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface Stats {
  total: number
  by_marketplace: Record<string, number>
  last_24h: number
}

export interface Filters {
  marketplace?: string
  search?: string
  min_price?: number
  max_price?: number
  page?: number
  per_page?: number
}


class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, init)
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try { detail = (await r.json()).detail ?? detail } catch {}
    throw new ApiError(r.status, detail)
  }
  return r.json() as Promise<T>
}

export async function fetchResults(filters: Filters): Promise<ProductsPage> {
  const params = new URLSearchParams()
  if (filters.marketplace) params.set('marketplace', filters.marketplace)
  if (filters.search) params.set('search', filters.search)
  if (filters.min_price != null) params.set('min_price', String(filters.min_price))
  if (filters.max_price != null) params.set('max_price', String(filters.max_price))
  params.set('page', String(filters.page || 1))
  params.set('per_page', String(filters.per_page || 20))
  return request<ProductsPage>(`/api/v1/results?${params}`)
}

export async function fetchStats(): Promise<Stats> {
  const r = await fetch(`${API_BASE}/api/v1/results/stats`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

export async function deleteResult(id: number): Promise<void> {
  const r = await fetch(`${API_BASE}/api/v1/results/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
}

export async function enqueueUrl(url: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/v1/queue/enqueue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
}

export interface PublishedMessage {
  id: number
  product_id?: number
  chat_id: string
  message_id: number
  message_link: string
  source_url?: string
  marketplace?: string
  title?: string
  created_at: string
}

export interface MessagesPage {
  items: PublishedMessage[]
  total: number
  limit: number
  offset: number
}

export async function fetchMessages(
  limit = 50,
  offset = 0,
  onlyOzonShort = true,
  marketplace?: string,
): Promise<MessagesPage> {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  params.set('only_ozon_short', String(onlyOzonShort))
  if (marketplace) params.set('marketplace', marketplace)

  return request<MessagesPage>(`/api/v1/messages?${params}`)
}

export async function deleteMessage(
  id: number,
  deleteFromTelegram = true,
): Promise<void> {
  const r = await fetch(
    `/api/v1/messages/${id}?delete_from_telegram=${deleteFromTelegram}`,
    { method: 'DELETE' },
  )
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
}

export async function publishReferral(url: string): Promise<{ success: boolean; message: string }> {
  const r = await fetch('/api/v1/referral/publish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }))
    throw new Error(err.detail || `HTTP ${r.status}`)
  }
  return r.json()
}