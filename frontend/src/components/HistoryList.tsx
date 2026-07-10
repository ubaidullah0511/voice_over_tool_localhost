import { downloadUrl, mediaUrl, type HistoryEntry } from '../api'
import { downloadName, formatDuration, timeAgo } from '../format'
import ClipPlayer from './ClipPlayer'
import { TrashIcon, WandIcon } from './Icons'

interface Props {
  history: HistoryEntry[]
  onDelete: (id: string) => void
  onRequeue: (entry: HistoryEntry) => void
}

function truncate(text: string, max = 80): string {
  return text.length > max ? `${text.slice(0, max)}...` : text
}

export default function HistoryList({ history, onDelete, onRequeue }: Props) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Generations</h2>
        {history.length > 0 && <span className="count-badge mono">{history.length}</span>}
      </div>

      {history.length === 0 ? (
        <p className="empty-hint">No submissions yet -- generated clips will show up here.</p>
      ) : (
        <ul className="history-list">
          {history.map((entry) => (
            <li key={entry.id} className="history-row">
              <div className="history-row-top">
                <div className="history-row-title">
                  <strong>{entry.preset_name}</strong>
                  <span className="badge-pill">{entry.style}</span>
                  <span className="badge-pill">{entry.stability}</span>
                </div>
                <span className="list-meta mono">{timeAgo(entry.created_at)}</span>
              </div>
              <p className="history-text">{truncate(entry.text)}</p>
              <p className="list-meta mono">
                estimated {formatDuration(entry.estimated_s)} -- actual{' '}
                {formatDuration(entry.generation_s)}
              </p>
              <div className="list-actions">
                <ClipPlayer
                  src={mediaUrl(entry.audio_url)}
                  durationS={entry.duration_s}
                  entryKey={entry.id}
                  label={`${entry.preset_name} clip`}
                />
                <a
                  href={downloadUrl(entry.audio_url, downloadName(entry.preset_name, entry.created_at))}
                  download
                  className="download-link"
                >
                  Download
                </a>
                <button
                  type="button"
                  className="icon-btn"
                  aria-label="Re-queue with edits"
                  onClick={() => onRequeue(entry)}
                >
                  <WandIcon size={14} />
                </button>
                <button
                  type="button"
                  className="icon-btn icon-btn-danger"
                  aria-label="Delete history entry"
                  onClick={() => onDelete(entry.id)}
                >
                  <TrashIcon size={14} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
