### Pipeline runs

| scenario | frames | stored | lost | dup rows | redeliveries absorbed | max spool | edge fps | frame→DB p50/p95 ms | frame→dashboard p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| steady | 1500 | 1500 | 0 | 0 | 0 | 149 | 25.01 | 138.6 / 5433.7 | 139.7 / 5462.3 |
| broker | 3000 | 3000 | 0 | 0 | 0 | 2528 | 21.92 | 77776.9 / 107299.0 | — / — |
| cloud | 3000 | 3000 | 0 | 0 | 0 | 187 | 23.92 | 15677.4 / 66748.0 | — / — |
| broker-direct | 3000 | 766 | 2234 | 0 | 32 | 0 | 25.0 | 47.2 / 98311.7 | — / — |
