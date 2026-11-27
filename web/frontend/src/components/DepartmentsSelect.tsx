import { useApp } from '../app/context'

/** «Подразделение» на «Главной»: по умолчанию — подразделение того, кто вошёл; можно убрать, заменить или добавить
 *  ещё. Ответ подбирается по документам «для всех» и для любого из выбранных; ничего не выбрано — по всем документам. */
export function DepartmentsSelect({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  const { meta } = useApp()
  const all = meta?.departments || []
  if (!all.length) return null
  const rest = all.filter((d) => !value.includes(d))
  return (
    <div className="source-row">
      <b>Подразделение:</b>
      {value.map((d) => (
        <span key={d} className="dept-chip">
          {d}
          <button type="button" title={`Убрать «${d}»`} aria-label={`Убрать ${d}`} onClick={() => onChange(value.filter((x) => x !== d))}>
            ×
          </button>
        </span>
      ))}
      {!!rest.length && (
        <select
          value=""
          aria-label="Добавить подразделение"
          onChange={(e) => {
            if (e.target.value) onChange([...value, e.target.value])
          }}
        >
          <option value="">{value.length ? '＋ добавить подразделение' : 'выбрать подразделение'}</option>
          {rest.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      )}
      <span className="hint">
        {value.length ? 'ответ — по документам для всех и для выбранных подразделений' : 'не выбрано — ответ по всем документам'}
      </span>
    </div>
  )
}
