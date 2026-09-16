const API_BASE = ''

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

export async function fetchResults(filters: Filters): Promise<ProductsPage> {
  const params = new URLSearchParams()
  if (filters.marketplace) params.set('marketplace', filters.marketplace)
  if (filters.search) params.set('search', filters.search)
  if (filters.min_price != null) params.set('min_price', String(filters.min_price))
  if (filters.max_price != null) params.set('max_price', String(filters.max_price))
  params.set('page', String(filters.page || 1))
  params.set('per_page', String(filters.per_page || 20))

  const r = await fetch(`${API_BASE}/api/v1/results?${params}`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
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