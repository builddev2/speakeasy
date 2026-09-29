import type { SearchResult } from '../mock/meetings';
import styles from './SearchResults.module.css';

interface SearchResultsProps {
  results: SearchResult[];
  onSelect: (result: SearchResult) => void;
}

export function SearchResults({ results, onSelect }: SearchResultsProps) {
  if (results.length === 0) {
    return (
      <div className={styles.empty}>No results.</div>
    );
  }

  return (
    <div className={styles.list} role="listbox" aria-label="Search results">
      {results.map((result, i) => (
        <button
          key={`${result.meetingId}-${i}`}
          role="option"
          aria-selected={false}
          className={styles.row}
          onClick={() => onSelect(result)}
        >
          <div className={styles.rowTop}>
            <span className={styles.title}>{result.title}</span>
            <span className={styles.dayLabel}>
              {result.dayLabel} · {result.time}
            </span>
          </div>
          {result.speaker && (
            <div className={styles.speakerLine}>
              {result.speaker}
              {result.alsoSpeakers.length > 0 && (
                <span className={styles.also}> · also: {result.alsoSpeakers.join(', ')}</span>
              )}
            </div>
          )}
          <div className={styles.snippet}>
            {result.parts.map((part, j) =>
              part.hit ? (
                <mark key={j} className={styles.mark}>
                  {part.text}
                </mark>
              ) : (
                <span key={j}>{part.text}</span>
              ),
            )}
          </div>
        </button>
      ))}
    </div>
  );
}
