import { useEffect, useState } from 'react'
import { api } from '../api'
import { DocModal } from '../components/DocView'
import type { Doc } from '../types'

export function AttentionPage() {
  const [items, setItems] = useState<{ kind: string; text: string; doc_id: string }[]>([])
  const [opened, setOpened] = useState<Doc | null>(null)
  useEffect(() => {
    api.get<{ items: typeof items }>('attention').then((d) => setItems(d.items))
  }, [])
  return (
    <div className="page">
      <h1>Требует внимания</h1>
      <p className="lead">Противоречия между документами и сроки пересмотра. Владельцы уведомлены. Двойной щелчок — открыть документ.</p>
      <div className="table-wrap">
        <table className="list">
          <thead>
            <tr>
              <th>Тип</th>
              <th>Что требует внимания</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, n) => (
              <tr
                key={n}
                className={item.kind === 'Противоречие' ? 'overdue' : 'escalated'}
                onDoubleClick={() => setOpened({ id: item.doc_id, title: '', source_type: '', date: '' })}
              >
                <td className="nowrap">{item.kind}</td>
                <td className="wrap">{item.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="empty">Противоречий и просроченных пересмотров нет.</p>}
      </div>
      {opened && <DocModal doc={opened} onClose={() => setOpened(null)} />}
    </div>
  )
}
