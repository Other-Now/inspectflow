import client from "prom-client";

export const registry = new client.Registry();
client.collectDefaultMetrics({ register: registry });

export const consumed = new client.Counter({
  name: "cloud_deliveries_total",
  help: "broker deliveries by outcome",
  labelNames: ["outcome"] as const,
  registers: [registry],
});

export const insertSeconds = new client.Histogram({
  name: "cloud_db_insert_seconds",
  help: "time to commit one inspection row",
  buckets: [0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.5],
  registers: [registry],
});

export const edgeToDb = new client.Histogram({
  name: "cloud_edge_to_db_seconds",
  help: "frame capture (edge clock) to committed row",
  buckets: [0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 5, 30, 120],
  registers: [registry],
});

export const wsClients = new client.Gauge({
  name: "cloud_ws_clients",
  help: "connected dashboard websockets",
  registers: [registry],
});

export const brokerUp = new client.Gauge({
  name: "cloud_broker_connected",
  help: "1 when the AMQP consumer is attached",
  registers: [registry],
});
