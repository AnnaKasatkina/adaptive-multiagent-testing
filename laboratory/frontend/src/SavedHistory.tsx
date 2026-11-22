import {
  Download,
  Upload,
  RotateCcw,
  Trash2,
  GitCompareArrows,
} from "lucide-react";
import { statuses, type Run, type SavedComparison } from "./types";

type Props = {
  runs: Run[];
  comparisons: SavedComparison[];
  selected: string | null;
  compare: string[];
  onOpen: (run: Run) => void;
  onCompare: (ids: string[]) => void;
  onOpenComparison: (value: SavedComparison) => void;
  onRemoveRun: (id: string) => void;
  onRemoveComparison: (id: string) => void;
  onImport: () => void;
  onReplay: () => void;
  onShowComparison: () => void;
};

export function SavedHistory(p: Props) {
  const saved = p.runs.filter((r) => r.saved);
  return (
    <div className="saved-history">
      <div className="library-toolbar">
        <button className="button small" onClick={p.onImport}>
          <Upload size={14} />
          Импорт
        </button>
        <button
          className="button small"
          disabled={!p.selected}
          onClick={p.onReplay}
        >
          <RotateCcw size={14} />
          Повторить
        </button>
        {p.selected && (
          <a className="button small" href={`/api/runs/${p.selected}/export`}>
            <Download size={14} />
            Экспорт
          </a>
        )}
      </div>
      {!!p.comparisons.length && (
        <section>
          <h3>Сравнения</h3>
          <div className="saved-comparison-list">
            {p.comparisons.map((c) => (
              <article className="saved-comparison-card" key={c.id}>
                <button
                  className="saved-comparison-open"
                  onClick={() => p.onOpenComparison(c)}
                >
                  <GitCompareArrows size={17} />
                  <span>
                    <strong>{c.name}</strong>
                    <small>
                      {c.metric === "Q" ? "Разброс Q" : "Ошибка среднего"}
                    </small>
                  </span>
                </button>
                <button
                  className="icon-button"
                  aria-label={`Удалить сравнение ${c.name}`}
                  onClick={() => p.onRemoveComparison(c.id)}
                >
                  <Trash2 size={15} />
                </button>
              </article>
            ))}
          </div>
        </section>
      )}
      <section>
        <h3>Эксперименты</h3>
        {saved.length ? (
          <div className="saved-run-list">
            {saved.map((r) => (
              <article
                key={r.id}
                data-run-id={r.id}
                className={`run-card ${r.id === p.selected ? "selected" : ""}`}
              >
                <div className="saved-run-heading">
                  <button className="run-main" onClick={() => p.onOpen(r)}>
                    <div>
                      <strong>{r.name}</strong>
                      <span>
                        {r.configuration.algorithm?.startsWith("homework")
                          ? "Локальный"
                          : "RAPS"}{" "}
                        · {r.runtime === "spade" ? "SPADE" : "Ядро"} · seed{" "}
                        {r.seed}
                      </span>
                    </div>
                  </button>
                  <button
                    className="icon-button"
                    aria-label={`Убрать эксперимент ${r.name}`}
                    onClick={() => p.onRemoveRun(r.id)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
                <div className="run-bottom">
                  <span className={`status ${r.status}`}>
                    {statuses[r.status] || r.status} ·{" "}
                    {r.configuration.schema_version === 2
                      ? `${(r.summary.time_ms ?? 0) / 1000} с · ${r.tick} событий`
                      : `${r.tick}/${r.configuration.horizon} тактов`}
                  </span>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      aria-label={`Сравнить ${r.id.slice(0, 5)}`}
                      checked={p.compare.includes(r.id)}
                      onChange={(e) =>
                        p.onCompare(
                          e.target.checked
                            ? [...p.compare.slice(-1), r.id]
                            : p.compare.filter((id) => id !== r.id),
                        )
                      }
                    />
                    Сравнить
                  </label>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="library-empty">Нет сохранённых экспериментов</div>
        )}
      </section>
      <div className="library-footer">
        <button
          className="button primary"
          disabled={p.compare.length !== 2}
          onClick={p.onShowComparison}
        >
          Показать сравнение
        </button>
      </div>
    </div>
  );
}
