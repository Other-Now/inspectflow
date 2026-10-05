import express from "express";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { WebSocketServer, WebSocket } from "ws";
import type { InspectionEvent } from "./event.js";
import { LABELS } from "./event.js";
import type { PgStore } from "./store.js";
import { registry, wsClients } from "./metrics.js";

type ReadStore = Pick<PgStore, "list" | "thumb" | "lines" | "lineStats" | "runAudit" | "runLatency">;

const UUID = /^[0-9a-f-]{36}$/i;

export function createApp(store: ReadStore, dashboardDir?: string) {
  const app = express();

  app.get("/healthz", (_req, res) => { res.json({ ok: true }); });

  app.get("/metrics", async (_req, res) => {
    res.set("Content-Type", registry.contentType);
    res.send(await registry.metrics());
  });

  app.get("/api/inspections", async (req, res, next) => {
    try {
      const label = req.query.label as string | undefined;
      if (label && !LABELS.includes(label)) return res.status(400).json({ error: "unknown label" });
      const review = req.query.review === undefined ? undefined : req.query.review === "true";
      const limit = req.query.limit ? Number(req.query.limit) : undefined;
      if (limit !== undefined && !Number.isFinite(limit)) return res.status(400).json({ error: "bad limit" });
      res.json(await store.list({ line: req.query.line as string | undefined, label, review, limit }));
    } catch (e) { next(e); }
  });

  app.get("/api/inspections/:id/thumb", async (req, res, next) => {
    try {
      if (!UUID.test(req.params.id)) return res.status(400).end();
      const t = await store.thumb(req.params.id);
      if (!t) return res.status(404).end();
      res.set("Content-Type", "image/jpeg").set("Cache-Control", "max-age=86400").send(t);
    } catch (e) { next(e); }
  });

  app.get("/api/lines", async (_req, res, next) => {
    try { res.json(await store.lines()); } catch (e) { next(e); }
  });

  app.get("/api/lines/:line/stats", async (req, res, next) => {
    try {
      const minutes = req.query.minutes ? Number(req.query.minutes) : 60;
      if (!Number.isFinite(minutes)) return res.status(400).json({ error: "bad minutes" });
      res.json(await store.lineStats(req.params.line, minutes));
    } catch (e) { next(e); }
  });

  app.get("/api/runs/:run/audit", async (req, res, next) => {
    try {
      res.json({ ...(await store.runAudit(req.params.run)), latency: await store.runLatency(req.params.run) });
    } catch (e) { next(e); }
  });

  if (dashboardDir && fs.existsSync(dashboardDir)) {
    app.use(express.static(dashboardDir));
    app.get(/^\/(?!api|ws|metrics).*/, (_req, res) => res.sendFile(path.join(dashboardDir, "index.html")));
  }

  app.use((err: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    console.error(err);
    res.status(500).json({ error: "internal" });
  });
  return app;
}

/** Live push: every newly stored inspection goes to every connected dashboard. */
export function attachWs(server: http.Server) {
  const wss = new WebSocketServer({ server, path: "/ws" });
  wss.on("connection", (ws) => {
    wsClients.inc();
    ws.on("close", () => wsClients.dec());
  });
  return (ev: InspectionEvent) => {
    const { thumb_jpeg_b64: _t, ...rest } = ev;
    const msg = JSON.stringify({ type: "inspection", ...rest, pushed_at: Date.now() });
    for (const c of wss.clients) {
      // Slow client protection: skip rather than buffer unboundedly.
      if (c.readyState === WebSocket.OPEN && c.bufferedAmount < 1 << 20) c.send(msg);
    }
  };
}
