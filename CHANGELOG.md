# Changelog

All notable changes to purreal are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-06-20

### Fixed
- **Speculative connection churn at idle.** The predictive pre-warming heuristic
  warmed pools toward `max_connections` even at perpetual idle: a stale demand
  burst pinned the EWMA forecast high forever (it was only updated on acquisition,
  never decayed over time), so housekeeping warmed connections, the idle/lifetime
  reapers closed them, and the next cycle re-warmed them — a warm/reap/warm loop
  that opened thousands of connections to serve a handful of real acquisitions.
  `DemandPredictor.predict_demand` now decays the EWMA term toward zero by the
  time elapsed since the last acquisition and rounds (rather than `ceil`s) the
  forecast, so a pool seeing ~0 acquisitions pre-warms ~0 connections.

### Added
- `prewarm: bool = True` and `adaptive: bool = True` on `PoolConfig` and
  `SurrealDBConnectionPool.__init__` (forwarded transparently through
  `SurrealDBPoolManager.create_pool(name, **kwargs)`). Set both to `False` for
  strictly demand-driven behavior like 0.1.x: grow only under acquire pressure,
  shrink idle connections back to `min_idle`, and never pre-warm or scale on
  latency. `pre_ping`, circuit breakers, leak detection, and the multiplexer are
  unaffected.

## [0.2.0]

### Added
- Adaptive pool with predictive pre-warming and self-tuning topology
  (`DemandPredictor`, `LatencyOracle`, `AdaptiveScaler`).
- Session multiplexer with transparent teleportation across physical connections.
- Per-connection circuit breakers, leak detection, graceful drain, bounded waiters.

## [0.1.x]

### Added
- Initial demand-driven async connection pool for SurrealDB: overflow connections,
  `pre_ping`, `max_lifetime`/`max_idle_time` recycling, and the
  `SurrealDBPoolManager` singleton.

[0.2.1]: https://github.com/dyleeeeeeee/purreal/releases/tag/v0.2.1
[0.2.0]: https://github.com/dyleeeeeeee/purreal/releases/tag/v0.2.0
