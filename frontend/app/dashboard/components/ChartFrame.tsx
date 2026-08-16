'use client';

import { useId, useState } from 'react';

/**
 * The frame every chart wraps in.
 *
 * Exists so that three things are structural rather than remembered:
 *
 *   1. An accessible name. A chart with no name is invisible to a screen
 *      reader and unlabelled in print.
 *   2. A TABLE VIEW. Colour and position are not available to every reader,
 *      and a table is also how someone checks a number they intend to lend
 *      against. Every chart here carries one.
 *   3. A real empty state. "No data" and "we do not produce this" are
 *      different sentences, and the caller has to supply which.
 */

export interface TableColumn<T> {
  header: string;
  /** Right-align numerics; they get tabular figures automatically. */
  numeric?: boolean;
  cell: (row: T) => React.ReactNode;
}

export function ChartFrame<T>({
  title,
  subtitle,
  note,
  legend,
  empty,
  tableRows,
  tableColumns,
  children,
}: {
  title: string;
  subtitle?: string;
  /** Provenance, caveat, or refusal shown beneath the plot. */
  note?: React.ReactNode;
  legend?: React.ReactNode;
  /** When set, the chart is not rendered and this is shown instead. */
  empty?: React.ReactNode;
  tableRows?: T[];
  tableColumns?: TableColumn<T>[];
  children?: React.ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  const id = useId();
  const canTable = !!tableRows?.length && !!tableColumns?.length;

  return (
    <section
      className="bg-card rounded-xl border border-rule p-4 sm:p-5"
      aria-labelledby={`${id}-title`}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h3 id={`${id}-title`} className="text-sm font-semibold text-ink">
            {title}
          </h3>
          {subtitle && (
            <p className="text-[12px] text-ink-muted mt-0.5 leading-snug max-w-xl">
              {subtitle}
            </p>
          )}
        </div>
        {canTable && !empty && (
          <button
            type="button"
            onClick={() => setShowTable((v) => !v)}
            aria-pressed={showTable}
            className="text-[11px] font-semibold text-ink-2 border border-rule rounded-md px-2 py-1 hover:bg-paper transition-colors shrink-0"
          >
            {showTable ? 'Chart' : 'Table'}
          </button>
        )}
      </div>

      {empty ? (
        <div className="mt-3 rounded-lg border border-dashed border-rule bg-paper/40 px-3.5 py-6 text-[12px] text-ink-muted leading-relaxed">
          {empty}
        </div>
      ) : showTable && canTable ? (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-rule">
                {tableColumns!.map((c) => (
                  <th
                    key={c.header}
                    scope="col"
                    className={`py-1.5 px-2 font-semibold text-ink-muted uppercase tracking-wide text-[10px] ${
                      c.numeric ? 'text-right' : 'text-left'
                    }`}
                  >
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {tableRows!.map((row, i) => (
                <tr key={i}>
                  {tableColumns!.map((c) => (
                    <td
                      key={c.header}
                      className={`py-1.5 px-2 text-ink ${
                        c.numeric ? 'text-right font-mono tabular-nums' : ''
                      }`}
                    >
                      {c.cell(row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="mt-3">{children}</div>
      )}

      {legend && !empty && <div className="mt-3">{legend}</div>}
      {note && (
        <p className="text-[11px] text-ink-muted mt-3 leading-relaxed max-w-2xl">{note}</p>
      )}
    </section>
  );
}

/**
 * A legend entry. Always a swatch AND a word — a bare colour chip is never
 * sufficient identity, and several of our marks fail contrast on cream.
 */
export function LegendItem({
  color,
  label,
  dash,
}: {
  color: string;
  label: string;
  /** Renders a dashed rule instead of a block, for provenance encoding. */
  dash?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-2">
      {dash ? (
        <svg width="16" height="8" aria-hidden className="shrink-0">
          <line
            x1="0"
            y1="4"
            x2="16"
            y2="4"
            stroke={color}
            strokeWidth="2"
            strokeDasharray={dash}
          />
        </svg>
      ) : (
        <span
          className="w-2.5 h-2.5 rounded-sm shrink-0"
          style={{ background: color }}
          aria-hidden
        />
      )}
      {label}
    </span>
  );
}

export function Legend({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap gap-x-3.5 gap-y-1.5">{children}</div>;
}
