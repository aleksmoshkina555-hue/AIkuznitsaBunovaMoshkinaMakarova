import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Icon } from '../components/Icon'
import { Modal } from '../components/Modal'

interface Person {
  id: number
  fio: string
  position: string
  email: string
  phone: string
}
interface Department {
  name: string
  employees: Person[]
}
type Card = { id?: number; department: string; fio: string; position: string; email: string; phone: string }

export function EmployeesPage() {
  const [departments, setDepartments] = useState<Department[]>([])
  const [editable, setEditable] = useState(false)
  const [filter, setFilter] = useState('Все подразделения')
  const [needle, setNeedle] = useState('')
  const [selected, setSelected] = useState<Card | null>(null)
  const [card, setCard] = useState<Card | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const data = await api.get<{ departments: Department[]; editable: boolean }>('employees')
    setDepartments(data.departments)
    setEditable(data.editable)
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const save = async () => {
    if (!card) return
    setError('')
    try {
      if (card.id) await api.put(`employees/${card.id}`, card)
      else await api.post('employees', card)
      setCard(null)
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const remove = async () => {
    if (!selected?.id) return window.alert('Выберите сотрудника в списке.')
    if (!window.confirm(`Удалить из справочника: ${selected.fio} (${selected.department})?`)) return
    await api.del(`employees/${selected.id}`)
    setSelected(null)
    load()
  }

  const text = needle.trim().toLowerCase()
  const shown = departments
    .filter((d) => filter === 'Все подразделения' || d.name === filter)
    .map((d) => ({
      ...d,
      employees: d.name.toLowerCase().includes(text)
        ? d.employees
        : d.employees.filter((e) => `${e.fio} ${e.position} ${e.email} ${e.phone}`.toLowerCase().includes(text)),
    }))
    .filter((d) => d.employees.length)

  return (
    <div className="page">
      <h1>Сотрудники</h1>
      <p className="lead">
        Справочник по подразделениям: ФИО, должность, e-mail, телефон. Контакты ответственного из справочника показываются в ответе
        и добавляются в письмо.
      </p>
      <div className="row">
        <label className="inline">
          Подразделение:
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option>Все подразделения</option>
            {departments.map((d) => (
              <option key={d.name}>{d.name}</option>
            ))}
          </select>
        </label>
        <label className="inline">
          Поиск:
          <input value={needle} onChange={(e) => setNeedle(e.target.value)} placeholder="ФИО, должность, e-mail, телефон" />
        </label>
      </div>
      {editable && (
        <div className="toolbar">
          <button onClick={() => setCard({ department: '', fio: '', position: '', email: '', phone: '' })}>
            <Icon name="plus" size={18} /> Добавить
          </button>
          <button onClick={() => (selected ? setCard(selected) : window.alert('Выберите сотрудника в списке.'))}>
            <Icon name="edit" size={18} /> Изменить
          </button>
          <button onClick={remove}>
            <Icon name="trash" size={18} /> Удалить
          </button>
        </div>
      )}
      <div className="table-wrap">
        <table className="list">
          <thead>
            <tr>
              <th>Подразделение / ФИО</th>
              <th>Должность</th>
              <th>E-mail</th>
              <th>Телефон</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((d) => [
              <tr key={d.name} className="group-row">
                <td colSpan={4}>{d.name}</td>
              </tr>,
              ...d.employees.map((e) => (
                <tr
                  key={e.id}
                  className={selected?.id === e.id ? 'selected' : ''}
                  onClick={() => setSelected({ ...e, department: d.name })}
                  onDoubleClick={() => editable && setCard({ ...e, department: d.name })}
                >
                  <td className="indent">{e.fio}</td>
                  <td>{e.position}</td>
                  <td>{e.email && <a href={`mailto:${e.email}`}>{e.email}</a>}</td>
                  <td className="nowrap">{e.phone}</td>
                </tr>
              )),
            ])}
          </tbody>
        </table>
        {!shown.length && <p className="empty">Никого не найдено — измените поиск или подразделение.</p>}
      </div>
      <p className="hint">{editable ? 'Двойной щелчок — изменить карточку сотрудника.' : 'Справочник изменяет администратор.'}</p>
      {card && (
        <Modal title={card.id ? `Сотрудник: ${card.fio}` : 'Новый сотрудник'} onClose={() => setCard(null)}>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault()
              save()
            }}
          >
            <label>
              Подразделение
              <input list="departments" value={card.department} onChange={(e) => setCard({ ...card, department: e.target.value })} autoFocus />
              <datalist id="departments">
                {departments.map((d) => (
                  <option key={d.name} value={d.name} />
                ))}
              </datalist>
            </label>
            {(
              [
                ['fio', 'ФИО'],
                ['position', 'Должность'],
                ['email', 'E-mail'],
                ['phone', 'Телефон'],
              ] as [keyof Card, string][]
            ).map(([key, label]) => (
              <label key={key}>
                {label}
                <input value={String(card[key] ?? '')} onChange={(e) => setCard({ ...card, [key]: e.target.value })} />
              </label>
            ))}
            {error && <p className="error-text">{error}</p>}
            <div className="dialog-buttons">
              <button type="submit" className="primary">Сохранить</button>
              <button type="button" onClick={() => setCard(null)}>Отмена</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  )
}
