import { useEffect, useState } from 'react'
import { fetchMessages, deleteMessage, PublishedMessage } from './api'

function MessagesPage() {
  const [items, setItems] = useState<PublishedMessage[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const [marketplace, setMarketplace] = useState<string>('')
  const [onlyOzonShort, setOnlyOzonShort] = useState(false)
  const limit = 50

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const page = await fetchMessages(limit, offset, onlyOzonShort, marketplace || undefined)
      setItems(page.items)
      setTotal(page.total)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, marketplace, onlyOzonShort])

  const handleCopy = async (m: PublishedMessage) => {
    try {
      await navigator.clipboard.writeText(m.message_link)
      setCopiedId(m.id)
      setTimeout(() => {
      setCopiedId((cur) => (cur === m.id ? null : cur))
      }, 1500)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Удалить запись из дашборда? Сообщение в Telegram останется.')) return
    try {
      await deleteMessage(id, false)
      await load()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    }
  }

  const pages = Math.ceil(total / limit)
  const currentPage = Math.floor(offset / limit) + 1

  return (
    <div className="messages-page">
      <h2>📨 Опубликованные сообщения (Ozon, короткие ссылки)</h2>
      <div className="messages-hint">
        Всего: {total}. Кнопка «Копировать» кладёт ссылку в буфер обмена.
      </div>

      <div className="messages-filters">
        <select
          value={marketplace}
          onChange={(e) => { setMarketplace(e.target.value); setOffset(0) }}
        >
          <option value="">Все маркетплейсы</option>
          <option value="ozon">Ozon</option>
          <option value="wildberries">Wildberries</option>
          <option value="yandex">Яндекс</option>
          <option value="aliexpress">AliExpress</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={onlyOzonShort}
            onChange={(e) => { setOnlyOzonShort(e.target.checked); setOffset(0) }}
          />
          Только Ozon с короткой ссылкой (/t/...)
        </label>
      </div>

      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">Загрузка...</div>}

      {!loading && items.length === 0 && (
        <div className="empty">Сообщений пока нет.</div>
      )}

      {!loading && items.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>Дата</th>
                <th>Маркетплейс</th>
                <th>Товар</th>
                <th>Сообщение</th>
                <th>Исходная ссылка</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id}>
                  <td>{new Date(m.created_at).toLocaleString('ru-RU')}</td>
                  <td>
                    {m.marketplace && (
                      <span className={`badge badge-${m.marketplace}`}>
                        {m.marketplace}
                      </span>
                    )}
                  </td>
                  <td className="title-cell">{m.title || '—'}</td>
                  <td>
                    <button
                      className="copy-btn"
                      onClick={() => handleCopy(m)}
                      title={m.message_link}
                    >
                      {copiedId === m.id ? '✓ Скопировано' : '📋 Копировать'}
                    </button>
                  </td>
                  <td className="url-cell">
                    {m.source_url ? (
                      <a href={m.source_url} target="_blank" rel="noreferrer">
                        {m.source_url.length > 50
                          ? m.source_url.slice(0, 50) + '…'
                          : m.source_url}
                      </a>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>
                    <button
                      className="delete-btn"
                      onClick={() => handleDelete(m.id)}
                      title="Удалить запись из БД (пост в Telegram останется)"
                    >
                      🗑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {pages > 1 && (
            <div className="pagination">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
              >
                ← Назад
              </button>
              <span>
                Страница {currentPage} из {pages} ({total} записей)
              </span>
              <button
                disabled={offset + limit >= total}
                onClick={() => setOffset(offset + limit)}
              >
                Вперёд →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default MessagesPage