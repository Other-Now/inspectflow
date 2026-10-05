### Edge inference (batch=1, preprocess + model, 2 CPUs)

250.1 fps · p50 3.965 ms · p95 4.031 ms · p99 4.83 ms (sched_setaffinity(2))

### Pipeline runs

| scenario | frames | stored | lost | dup rows | redeliveries absorbed | max spool | edge fps | frame→DB p50/p95 ms | frame→dashboard p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| steady | 1500 | 1500 | 0 | 0 | 0 | 8 | 25.0 | 4.8 / 94.6 | 4.9 / 113.9 |
| broker | 3000 | 3000 | 0 | 0 | 1 | 1723 | 25.01 | 13554.6 / 63860.6 | — / — |
| cloud | 3000 | 3000 | 0 | 0 | 1 | 5 | 25.01 | 1552.5 / 55101.6 | — / — |
| broker-direct | 3000 | 1290 | 1710 | 0 | 0 | 0 | 25.01 | 4.2 / 17.9 | — / — |
