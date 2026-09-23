import { useEffect, useState } from 'react'
import {
  fetchResults, fetchStats, deleteResult, enqueueUrl,
  Product, Stats, Filters,
} from './api'

const MARKETPLACES = [
  { id: '', label: 'Все' },
  { id: 'ozon', label: 'Ozon' },
  { id: 'wildberries', label: 'Wildberries' },
  { id: 'aliexpress', label: 'AliExpress' },
  { id: 'yandex', label: 'Yandex' },
]

function App() {
  const [results, setResults] = useState<Product[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [filters, setFilters] = useState<Filters>({
    marketplace: '',
    search: '',
    page: 1,
    per_page: 20,
  })

  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(0)

  // Форма отправки URL
  const [newUrl, setNewUrl] = useState('')
  const [enqueueStatus, setEnqueueStatus] = useState<string | null>(null)

  // Модалка
  const [selected, setSelected] = useState<Product | null>(null)

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const [page, s] = await Promise.all([
        fetchResults(filters),
        fetchStats(),
      ])
      setResults(page.items)
      setTotal(page.total)
      setPages(page.pages)
      setStats(s)
    } catch (e: any) {
      setError(e.message || 'Ошибка загрузки')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters])

  // SSE автообновление
  useEffect(() => {
    const es = new EventSource('/api/v1/results/stream')
    es.addEventListener('product', (e: MessageEvent) => {
      try {
        const product = JSON.parse(e.data)
        setResults((prev) => [product, ...prev])
        setTotal((t) => t + 1)
      } catch {}
    })
    return () => es.close()
  }, [])

  const handleEnqueue = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newUrl.trim()) return
    setEnqueueStatus('Отправка...')
    try {
      await enqueueUrl(newUrl.trim())
      setEnqueueStatus('✅ URL добавлен в очередь. Обновите через 10 секунд.')
      setNewUrl('')
      setTimeout(() => {
        setEnqueueStatus(null)
        loadData()
      }, 10000)
    } catch (e: any) {
      setEnqueueStatus(`❌ Ошибка: ${e.message}`)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Удалить?')) return
    try {
      await deleteResult(id)
      loadData()
    } catch (e: any) {
      alert(`Ошибка удаления: ${e.message}`)
    }
  }

  const updateFilter = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch, page: patch.page ?? 1 }))
  }

  return (
    <div className="app">
      <header className="header">
        <h1>🛒 Marketplace Parser Dashboard</h1>
        {stats && (
          <div className="stats">
            <div className="stat">
              <span className="stat-value">{stats.total}</span>
              <span className="stat-label">Всего товаров</span>
            </div>
            {Object.entries(stats.by_marketplace).map(([mp, count]) => (
              <div key={mp} className="stat">
                <span className="stat-value">{count}</span>
                <span className="stat-label">{mp}</span>
              </div>
            ))}
          </div>
        )}
      </header>

      <section className="enqueue-form">
        <form onSubmit={handleEnqueue}>
          <input
            type="text"
            placeholder="Вставьте URL товара с Ozon/WB/AliExpress/Yandex..."
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
          />
          <button type="submit">Отправить на парсинг</button>
        </form>
        {enqueueStatus && <div className="enqueue-status">{enqueueStatus}</div>}
      </section>

      <section className="filters">
        <div className="marketplace-tabs">
          {MARKETPLACES.map((m) => (
            <button
              key={m.id}
              className={filters.marketplace === m.id ? 'active' : ''}
              onClick={() => updateFilter({ marketplace: m.id })}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div className="filter-inputs">
          <input
            type="text"
            placeholder="Поиск по названию..."
            value={filters.search || ''}
            onChange={(e) => updateFilter({ search: e.target.value })}
          />
          <input
            type="number"
            placeholder="Цена от"
            value={filters.min_price ?? ''}
            onChange={(e) =>
              updateFilter({ min_price: e.target.value ? Number(e.target.value) : undefined })
            }
          />
          <input
            type="number"
            placeholder="Цена до"
            value={filters.max_price ?? ''}
            onChange={(e) =>
              updateFilter({ max_price: e.target.value ? Number(e.target.value) : undefined })
            }
          />
          <button onClick={loadData}>🔄 Обновить</button>
        </div>
      </section>

      {error && <div className="error">{error}</div>}

      <section className="results">
        {loading && <div className="loading">Загрузка...</div>}

        {!loading && results.length === 0 && (
          <div className="empty">Нет результатов. Отправьте URL на парсинг.</div>
        )}

        {!loading && results.length > 0 && (
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Маркетплейс</th>
                <th>Название</th>
                <th>Цена</th>
                <th>Было</th>
                <th>Рейтинг</th>
                <th>Продавец</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((p) => (
                <tr key={p.id} onClick={() => setSelected(p)}>
                  <td>
                    {p.image_url && <img src={p.image_url} alt="" width="40" />}
                  </td>
                  <td>
                    <span className={`badge badge-${p.marketplace}`}>{p.marketplace}</span>
                  </td>
                  <td className="title-cell">{p.title}</td>
                  <td className="price-cell">
                    {p.price_rub != null ? `${p.price_rub.toLocaleString('ru-RU')}₽` : '—'}
                  </td>
                  <td className="old-price-cell">
                    {p.price_original_rub != null
                      ? `${p.price_original_rub.toLocaleString('ru-RU')}₽`
                      : '—'}
                  </td>
                  <td>{p.rating != null ? `⭐ ${p.rating}` : '—'}</td>
                  <td className="seller-cell">{p.seller || '—'}</td>
                  <td>
                    <button
                      className="delete-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(p.id)
                      }}
                    >
                      🗑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {pages > 1 && (
          <div className="pagination">
            <button
              disabled={filters.page === 1}
              onClick={() => updateFilter({ page: (filters.page || 1) - 1 })}
            >
              ← Назад
            </button>
            <span>
              Страница {filters.page} из {pages} ({total} записей)
            </span>
            <button
              disabled={filters.page === pages}
              onClick={() => updateFilter({ page: (filters.page || 1) + 1 })}
            >
              Вперёд →
            </button>
          </div>
        )}
      </section>

      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="close-btn" onClick={() => setSelected(null)}>
              ✕
            </button>
            <div className="modal-content">
              <div className="modal-image">
                {selected.image_url && <img src={selected.image_url} alt="" />}
              </div>
              <div className="modal-info">
                <h2>{selected.title}</h2>
                <p className="modal-price">
                  {selected.price_rub != null
                    ? `${selected.price_rub.toLocaleString('ru-RU')}₽`
                    : 'Цена не указана'}
                  {selected.price_original_rub != null && (
                    <span className="modal-old-price">
                      {selected.price_original_rub.toLocaleString('ru-RU')}₽
                    </span>
                  )}
                </p>
                <p>
                  <b>Маркетплейс:</b> {selected.marketplace}
                </p>
                <p>
                  <b>Продавец:</b> {selected.seller || '—'}
                </p>
                <p>
                  <b>Рейтинг:</b> {selected.rating ?? '—'} ({selected.rating_count ?? 0})
                </p>
                <p>
                  <b>В наличии:</b> {selected.in_stock ? '✅' : '❌'}
                </p>
                <p>
                  <a href={selected.url} target="_blank" rel="noreferrer">
                    Открыть на маркетплейсе →
                  </a>
                </p>

                <div className="post-preview">
                  <b>Готовый пост:</b>
                  <pre>{selected.post_text}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App