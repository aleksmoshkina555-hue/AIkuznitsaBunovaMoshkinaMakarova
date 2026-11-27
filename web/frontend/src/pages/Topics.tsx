import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Modal } from '../components/Modal'

interface Variant {
  department: string
  answer: string
  expert: string
  basis: string
  points: string[]
}

interface Topic {
  id: string
  name: string
  stage: string
  expert: string
  keywords: string[]
  exclude: string
  examples: string[]
  documents: string[]
  ready: { kind: 'none' | 'single' | 'by_department' | 'amount'; answer?: string; basis?: string; points?: string[]; variants?: Variant[] }
}

interface DocShort {
  id: string
  title: string
  type: string
}

interface Form {
  id: string | null
  name: string
  expert: string
  keywords: string
  exclude: string
  examples: string
  documents: string[]
  answer: string
  variants: Variant[]
  ready: Topic['ready']
}

const READY_LABEL: Record<string, string> = {
  none: 'нет — вопросы уходят эксперту',
  single: 'есть',
  by_department: 'есть, по подразделениям',
  amount: 'есть, по сумме договора',
}

function toForm(t: Topic | null): Form {
  return {
    id: t?.id ?? null,
    name: t?.name ?? '',
    expert: t?.expert ?? '',
    keywords: (t?.keywords ?? []).join('\n'),
    exclude: t?.exclude ?? '',
    examples: (t?.examples ?? []).join('\n'),
    documents: t?.documents ?? [],
    answer: t?.ready.answer ?? '',
    variants: (t?.ready.variants ?? []).map((v) => ({ ...v })),
    ready: t?.ready ?? { kind: 'none' },
  }
}

const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

/** «Темы и ответственные» (администратор): тема вопроса → кто отвечает, как узнать тему, готовый ответ. */
export function TopicsPage() {
  const [topics, setTopics] = useState<Topic[]>([])
  const [docs, setDocs] = useState<DocShort[]>([])
  const [people, setPeople] = useState<string[]>([])
  const [form, setForm] = useState<Form | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    const data = await api.get<{ topics: Topic[]; documents: DocShort[] }>('admin/topics')
    setTopics(data.topics)
    setDocs(data.documents)
  }, [])

  useEffect(() => {
    load()
    api
      .get<{ departments: { employees: { fio: string; email: string }[] }[] }>('employees')
      .then((d) => setPeople(d.departments.flatMap((dep) => dep.employees.map((e) => (e.email ? `${e.fio} <${e.email}>` : e.fio)))))
      .catch(() => setPeople([]))
  }, [load])

  const save = async () => {
    if (!form) return
    setError('')
    const body: Record<string, unknown> = {
      name: form.name,
      expert: form.expert,
      keywords: lines(form.keywords),
      exclude: form.exclude,
      examples: lines(form.examples),
      documents: form.documents,
    }
    if (form.ready.kind === 'single') body.answer = form.answer
    if (form.ready.kind === 'by_department') body.variants = form.variants.map((v) => ({ department: v.department, answer: v.answer, expert: v.expert }))
    try {
      if (form.id) await api.put(`admin/topics/${encodeURIComponent(form.id)}`, body)
      else await api.post('admin/topics', body)
      setMessage(`Тема «${form.name}» сохранена`)
      setForm(null)
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const remove = async () => {
    if (!form?.id) return
    const ready = form.ready.kind !== 'none' ? '\n\nУ темы есть готовый ответ — он тоже будет удалён, на такие вопросы будет отвечать модель или специалист.' : ''
    if (!window.confirm(`Удалить тему «${form.name}»?${ready}`)) return
    await api.del(`admin/topics/${encodeURIComponent(form.id)}`)
    setMessage(`Тема «${form.name}» удалена`)
    setForm(null)
    load()
  }

  const title = (id: string) => docs.find((d) => d.id === id)?.title || id

  return (
    <div className="page">
      <div className="row spread">
        <h1>Темы и ответственные</h1>
        <button className="primary" onClick={() => setForm(toForm(null))}>
          ＋ Новая тема
        </button>
      </div>
      <p className="lead">
        Тема вопроса определяет, кто отвечает, если система не нашла ответ: вопрос уходит эксперту темы (если тема не
        определилась — владельцу самого подходящего документа, иначе дежурному методологу). Тему система узнаёт по словам,
        по примерам вопросов и по документам темы. У частых вопросов есть <b>готовый ответ</b> со ссылкой на пункт
        регламента — на них система отвечает сама, мгновенно и без модели.
      </p>
      {message && <p className="ok-text">{message}</p>}
      <div className="table-wrap">
        <table className="list">
          <thead>
            <tr>
              <th>Тема</th>
              <th>Ответственный эксперт</th>
              <th>Готовый ответ</th>
              <th>Примеров вопросов</th>
              <th>Документы</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {topics.map((t) => (
              <tr key={t.id} onDoubleClick={() => setForm(toForm(t))}>
                <td>{t.name}</td>
                <td>
                  {t.expert || <span className="hint">не указан — дежурный методолог</span>}
                  {t.ready.variants?.some((v) => v.expert) && (
                    <div className="hint">
                      {t.ready.variants.filter((v) => v.expert).map((v) => `${v.department}: ${v.expert.split('<')[0].trim()}`).join('; ')}
                    </div>
                  )}
                </td>
                <td>{READY_LABEL[t.ready.kind]}</td>
                <td>{t.examples.length}</td>
                <td>{t.documents.map(title).join('; ') || '—'}</td>
                <td className="row-actions">
                  <button className="small" onClick={() => setForm(toForm(t))}>
                    Изменить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        Специалист, получивший вопрос не по своей теме, нажимает «Передать другому» и может указать тему — вопрос
        становится её примером, и похожие вопросы дальше сразу уходят нужному эксперту.
      </p>

      <datalist id="topic-people">
        {people.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>

      {form && (
        <Modal title={form.id ? `Тема «${form.name}»` : 'Новая тема'} onClose={() => setForm(null)} wide>
          <div className="form-grid">
            <label>
              Название темы
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label>
              Ответственный эксперт — кому уходят вопросы этой темы без ответа
              <input
                list="topic-people"
                value={form.expert}
                onChange={(e) => setForm({ ...form, expert: e.target.value })}
                placeholder="ФИО <e-mail> — начните вводить"
              />
            </label>
            <label>
              Слова темы — каждая строка: начала слов через запятую; в вопросе должно быть хотя бы одно слово из каждой строки
              <textarea rows={3} value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} placeholder={'смет\nсрок, созда, завод'} />
            </label>
            <label>
              Слова-исключения — если они есть в вопросе, тема не подходит
              <input value={form.exclude} onChange={(e) => setForm({ ...form, exclude: e.target.value })} placeholder="закры, архив" />
            </label>
            <label>
              Примеры вопросов — по одному в строке (по ним тема узнаётся и при других словах)
              <textarea rows={4} value={form.examples} onChange={(e) => setForm({ ...form, examples: e.target.value })} />
            </label>
            <div>
              <b>Документы темы</b> — вопрос, к которому ближе всего такой документ, относится к теме
              <div className="checks">
                {docs.map((d) => (
                  <label key={d.id} className="check">
                    <input
                      type="checkbox"
                      checked={form.documents.includes(d.id)}
                      onChange={(e) =>
                        setForm({ ...form, documents: e.target.checked ? [...form.documents, d.id] : form.documents.filter((x) => x !== d.id) })
                      }
                    />
                    {d.type} «{d.title}»
                  </label>
                ))}
              </div>
            </div>
            {form.ready.kind === 'single' && (
              <label>
                Готовый ответ ({title(form.ready.basis || '')}
                {form.ready.points?.length ? `, п. ${form.ready.points.join(', ')}` : ''}) — сроки, суммы и ФИО проверяются по документу
                <textarea rows={3} value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })} />
              </label>
            )}
            {form.ready.kind === 'by_department' &&
              form.variants.map((v, n) => (
                <fieldset key={v.department} className="variant">
                  <legend>
                    Готовый ответ для «{v.department}» ({title(v.basis)}
                    {v.points.length ? `, п. ${v.points.join(', ')}` : ''})
                  </legend>
                  <textarea
                    rows={3}
                    value={v.answer}
                    onChange={(e) => setForm({ ...form, variants: form.variants.map((x, i) => (i === n ? { ...x, answer: e.target.value } : x)) })}
                  />
                  <label>
                    Эксперт в этом подразделении
                    <input
                      list="topic-people"
                      value={v.expert}
                      onChange={(e) => setForm({ ...form, variants: form.variants.map((x, i) => (i === n ? { ...x, expert: e.target.value } : x)) })}
                      placeholder="если пусто — эксперт темы"
                    />
                  </label>
                </fieldset>
              ))}
            {form.ready.kind === 'amount' && (
              <p className="hint">
                Готовый ответ зависит от суммы договора ({form.variants.length} варианта). Пороги сумм меняет методолог в файле
                process_map.json.
              </p>
            )}
            {form.ready.kind === 'none' && form.id && (
              <p className="hint">Готового ответа нет: на вопросы темы отвечает модель по документам, а если ответа нет — эксперт.</p>
            )}
          </div>
          {error && <p className="error-text">{error}</p>}
          <div className="dialog-buttons">
            {form.id && (
              <button className="danger left" onClick={remove}>
                Удалить тему
              </button>
            )}
            <button onClick={() => setForm(null)}>Отмена</button>
            <button className="primary" onClick={save} disabled={!form.name.trim()}>
              Сохранить
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}
