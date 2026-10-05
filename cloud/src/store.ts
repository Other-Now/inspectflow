import pg from "pg";
import type { InspectionEvent } from "./event.js";

export interface Store {
  /** Returns true if the row was new, false if this id was already stored. */
  insert(ev: InspectionEvent): Promise<boolean>;
}

export const SCHEMA = `
CREATE TABLE IF NOT EXISTS inspections (
  id           uuid PRIMARY KEY,
  line_id      text        NOT NULL,
  run_id       text        NOT NULL,
  seq          integer     NOT NULL,
  captured_at  timestamptz NOT NULL,
  stored_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  label        text        NOT NULL,
  confidence   real        NOT NULL,
  needs_review boolean     NOT NULL,
  infer_ms     real,
  model        text,
  thumb        bytea
);
CREATE INDEX IF NOT EXISTS inspections_line_time ON inspections (line_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS inspections_run ON inspections (run_id);
`;

export interface ListFilter {
  line?: string;
  label?: string;
  review?: boolean;
  limit?: number;
}

export class PgStore implements Store {
  constructor(readonly pool: pg.Pool) {}

  async migrate(): Promise<void> {
    await this.pool.query(SCHEMA);
  }

  async insert(ev: InspectionEvent): Promise<boolean> {
    // Single statement = single implicit transaction. The PK turns a broker
    // redelivery (or an edge replay) into a no-op instead of a duplicate row.
    const r = await this.pool.query(
      `INSERT INTO inspections (id, line_id, run_id, seq, captured_at, label, confidence, needs_review, infer_ms, model, thumb)
       VALUES ($1, $2, $3, $4, to_timestamp($5 / 1000.0), $6, $7, $8, $9, $10, $11)
       ON CONFLICT (id) DO NOTHING
       RETURNING id`,
      [ev.id, ev.line_id, ev.run_id, ev.seq, ev.captured_at, ev.label, ev.confidence, ev.needs_review,
       ev.infer_ms, ev.model, ev.thumb_jpeg_b64 ? Buffer.from(ev.thumb_jpeg_b64, "base64") : null],
    );
    return (r.rowCount ?? 0) > 0;
  }

  async list(f: ListFilter) {
    const where: string[] = [];
    const args: unknown[] = [];
    if (f.line) { args.push(f.line); where.push(`line_id = $${args.length}`); }
    if (f.label) { args.push(f.label); where.push(`label = $${args.length}`); }
    if (f.review !== undefined) { args.push(f.review); where.push(`needs_review = $${args.length}`); }
    args.push(Math.min(Math.max(f.limit ?? 50, 1), 500));
    const r = await this.pool.query(
      `SELECT id, line_id, run_id, seq, captured_at, stored_at, label, confidence, needs_review, infer_ms, model
         FROM inspections ${where.length ? "WHERE " + where.join(" AND ") : ""}
        ORDER BY captured_at DESC LIMIT $${args.length}`,
      args,
    );
    return r.rows;
  }

  async thumb(id: string): Promise<Buffer | null> {
    const r = await this.pool.query(`SELECT thumb FROM inspections WHERE id = $1`, [id]);
    return r.rows[0]?.thumb ?? null;
  }

  async lines() {
    const r = await this.pool.query(
      `SELECT line_id, count(*)::int AS total, avg(needs_review::int)::float AS review_rate,
              max(captured_at) AS last_seen
         FROM inspections GROUP BY line_id ORDER BY line_id`,
    );
    return r.rows;
  }

  /** Defect-type mix, review rate and a per-minute series for one line. */
  async lineStats(line: string, minutes: number) {
    const m = Math.min(Math.max(minutes, 1), 24 * 60);
    const [mix, series] = await Promise.all([
      this.pool.query(
        `SELECT label, count(*)::int AS n, avg(needs_review::int)::float AS review_rate
           FROM inspections
          WHERE line_id = $1 AND captured_at > now() - make_interval(mins => $2)
          GROUP BY label ORDER BY label`,
        [line, m],
      ),
      this.pool.query(
        `SELECT date_trunc('minute', captured_at) AS minute, count(*)::int AS total,
                sum(needs_review::int)::int AS review
           FROM inspections
          WHERE line_id = $1 AND captured_at > now() - make_interval(mins => $2)
          GROUP BY 1 ORDER BY 1`,
        [line, m],
      ),
    ]);
    const total = mix.rows.reduce((s, r) => s + r.n, 0);
    const review = mix.rows.reduce((s, r) => s + r.n * r.review_rate, 0);
    return {
      line_id: line, minutes: m, total,
      review_rate: total ? review / total : 0,
      by_label: Object.fromEntries(mix.rows.map((r) => [r.label, r.n])),
      series: series.rows,
    };
  }

  /** Used by the outage test: did every seq of a run arrive exactly once? */
  async runAudit(run: string) {
    const r = await this.pool.query(
      `SELECT count(*)::int AS rows, count(DISTINCT seq)::int AS distinct_seq,
              min(seq) AS min_seq, max(seq) AS max_seq
         FROM inspections WHERE run_id = $1`,
      [run],
    );
    return r.rows[0];
  }

  /** Frame-capture -> committed-row latency, computed in the database. */
  async runLatency(run: string) {
    const r = await this.pool.query(
      `SELECT percentile_cont(ARRAY[0.5, 0.95, 0.99]) WITHIN GROUP
                (ORDER BY extract(epoch FROM stored_at - captured_at) * 1000) AS p
         FROM inspections WHERE run_id = $1`,
      [run],
    );
    const p = r.rows[0]?.p ?? [null, null, null];
    return { p50_ms: p[0], p95_ms: p[1], p99_ms: p[2] };
  }
}
