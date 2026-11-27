import { useApp } from '../app/context'

/** «Искать в: Приказы / Регламенты / Письма / Чаты» — отметки общие для всех экранов и запоминаются. */
export function SourceRow() {
  const { meta, sources, setSources } = useApp()
  const groups = meta?.source_groups || []
  const toggle = (group: string) =>
    setSources(sources.includes(group) ? sources.filter((g) => g !== group) : [...sources, group])
  const note =
    sources.length === 0 ? 'Отметьте хотя бы один источник' : sources.length < groups.length ? 'Поиск только в отмеченных источниках' : ''
  return (
    <div className="source-row">
      <b>Искать в:</b>
      {groups.map((group) => (
        <label key={group} className="check">
          <input type="checkbox" checked={sources.includes(group)} onChange={() => toggle(group)} /> {group}
        </label>
      ))}
      <button type="button" className="link" onClick={() => setSources(groups)}>
        все источники
      </button>
      {note && <span className="warn-text">{note}</span>}
    </div>
  )
}
