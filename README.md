# inspectflow

Edge-to-cloud visual inspection pipeline: a CPU-capped edge worker classifies steel-surface
defects with ONNX Runtime, store-and-forwards results through RabbitMQ, and a Node.js/TypeScript
service persists them to PostgreSQL and pushes them live to a React dashboard.

Full write-up with measured results: see below (being filled in from the CI run).
