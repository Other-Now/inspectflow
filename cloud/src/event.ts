export interface InspectionEvent {
  id: string;
  line_id: string;
  run_id: string;
  seq: number;
  captured_at: number; // epoch ms, edge clock
  label: string;
  confidence: number;
  needs_review: boolean;
  infer_ms: number;
  model: string;
  thumb_jpeg_b64?: string;
}

export const LABELS = ["crazing", "inclusion", "patches", "pitted_surface", "rolled-in_scale", "scratches"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class InvalidEvent extends Error {}

/** Parse and validate one message body. Throws InvalidEvent for poison messages. */
export function parseEvent(body: Buffer | string): InspectionEvent {
  let raw: any;
  try {
    raw = JSON.parse(body.toString());
  } catch {
    throw new InvalidEvent("body is not JSON");
  }
  if (!raw || typeof raw !== "object") throw new InvalidEvent("body is not an object");
  if (typeof raw.id !== "string" || !UUID.test(raw.id)) throw new InvalidEvent("bad id");
  if (typeof raw.line_id !== "string" || raw.line_id.length === 0 || raw.line_id.length > 64)
    throw new InvalidEvent("bad line_id");
  if (!Number.isInteger(raw.seq) || raw.seq < 0) throw new InvalidEvent("bad seq");
  if (typeof raw.captured_at !== "number" || !Number.isFinite(raw.captured_at)) throw new InvalidEvent("bad captured_at");
  if (!LABELS.includes(raw.label)) throw new InvalidEvent("unknown label");
  if (typeof raw.confidence !== "number" || raw.confidence < 0 || raw.confidence > 1)
    throw new InvalidEvent("bad confidence");
  return {
    id: raw.id.toLowerCase(),
    line_id: raw.line_id,
    run_id: typeof raw.run_id === "string" ? raw.run_id : "",
    seq: raw.seq,
    captured_at: raw.captured_at,
    label: raw.label,
    confidence: raw.confidence,
    needs_review: Boolean(raw.needs_review),
    infer_ms: typeof raw.infer_ms === "number" ? raw.infer_ms : 0,
    model: typeof raw.model === "string" ? raw.model : "",
    thumb_jpeg_b64: typeof raw.thumb_jpeg_b64 === "string" ? raw.thumb_jpeg_b64 : undefined,
  };
}
