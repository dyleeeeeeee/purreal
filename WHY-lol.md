# WHY-lol: What Is Purreal?

---

## Part 1: Like You're a Database Admin Who's Been Up Since 5 AM

Look. You've got a SurrealDB instance. You've got a Python app. You connect them with a WebSocket. Life is good.

Then you get 50 concurrent users and everything explodes with:

```
websockets.exceptions.ConcurrencyError: cannot call recv while another coro is calling recv
```

**That's the problem.** One WebSocket connection = one query at a time. Two coroutines try to use the same connection simultaneously and the whole thing barfs.

The boring solution: connection pool. Keep N connections open, hand them out one at a time, get them back when you're done. Every database driver since 1998 has done this.

**Purreal does that.** But here's why it's different from the connection pool you could write in an afternoon:

### What happens when a connection dies?
Normal pool: your query fails. You get an exception. You cry. You retry manually.
Purreal: the session **teleports** to a different connection. Your query retries automatically. You never even know it happened.

### What happens at 3 AM when your batch job spins up 200 workers?
Normal pool: all 200 workers slam the pool simultaneously. Half of them timeout waiting. Connection storm. Pages go off. You wake up.
Purreal: it **saw this coming yesterday** at 3 AM. It pre-warmed 15 connections at 2:59:55 AM. Your batch job gets connections instantly. You sleep through the night.

### What happens when one connection is slow?
Normal pool: you don't notice until the P99 dashboard turns red. Then you spend 45 minutes figuring out which connection is the problem.
Purreal: it tracks latency per-connection. If one connection's median latency exceeds 3 standard deviations above the pool mean, it's **automatically rotated out**. No alert. No human. Fixed before you knew it was broken.

### What's a "virtual session"?
Think of it like this: you have 3 physical WebSocket connections to SurrealDB. But your app has 200 different things that need their own namespace, database, and auth context. Instead of opening 200 connections (which would melt your server), Purreal gives you 200 **virtual sessions** that transparently share those 3 physical connections. It handles all the `USE namespace/database` and `signin` switching automatically, and it's smart about it — sessions with the same state get routed to the same physical connection to avoid switching overhead.

### The short version:
- Connection pooling that doesn't deadlock ✓
- Automatic retry when connections die ✓
- Predicts and pre-warms before traffic spikes ✓
- Self-tunes pool size based on actual latency ✓
- 200 sessions on 3 connections ✓
- You `pip install purreal` and it just works ✓

---

## Part 2: Like You're Linus Torvalds Reviewing the Architecture

Right. Let me tell you what's actually going on here because the marketing fluff above doesn't explain why the code is structured the way it is.

### The fundamental constraint

SurrealDB's WebSocket protocol is **not multiplexed at the protocol level.** One `recv` at a time per socket. Period. This isn't something you can engineer around at the application layer with clever buffering — the transport is inherently serialized. Every connection pool for every database has dealt with this since Berkeley sockets were invented. The correct solution has been known for 30 years: exclusive leasing with a well-defined acquire/release lifecycle.

The question isn't "how do you pool connections." The question is "how do you pool connections without writing garbage that falls apart under real load."

### Why most pools are garbage

They use a mutex around a list. They scan the list linearly on every acquire. They don't validate connections before handing them out — so you get a dead socket from a connection that timed out server-side 5 minutes ago. They don't implement backpressure — so when the pool is exhausted, waiters pile up unbounded until the process OOMs. They don't track who's holding what — so leaked connections are invisible until the pool starves.

Purreal uses:
- **`asyncio.Semaphore`** for admission control (O(1), fair, no scanning)
- **`asyncio.LifoQueue`** for idle connections (LIFO = recently-used connections are warm in TCP buffers and server-side caches)
- **Bounded waiter count** — if the queue depth exceeds the limit, reject immediately instead of contributing to a cascading failure
- **Leak detection** — background task checks every 30 seconds, logs the full stack trace of who checked out a connection and hasn't returned it. You find the bug in your code in 30 seconds instead of 3 hours.

This isn't clever. This is just doing what HikariCP, PgBouncer, and asyncpg already figured out, except in Python, for SurrealDB. The clever part is what comes next.

### The adaptive layer

Static pool sizes are a cop-out. "Just set min=5 max=20" is an admission that you don't know what load looks like and you're making the operator guess. The operator always guesses wrong because load isn't static.

Purreal measures. It records acquisition latency into a ring buffer. Every 5 seconds the `AdaptiveScaler` looks at the p95 and makes a decision:
- p95 > target → pool is too small → create one connection
- p95 < target/2 AND utilization < 30% → pool is too large → evict one idle connection
- Rate-limited to one decision per cycle so it can't oscillate

This is a trivial proportional controller. It's not "AI" or "ML." It's a feedback loop. The same thing your thermostat does. But apparently nobody thought to put one in a connection pool before.

### The predictive layer

The adaptive layer is reactive — it responds to latency that already happened. The predictive layer is proactive — it responds to patterns that repeat.

A ring buffer of 17,280 slots (one per 5 seconds of a 24-hour day) counts acquisitions. If your batch job runs at 03:00 every night, after one night the ring buffer has a spike at bucket 2160. Next night at 02:59:55, the housekeeping loop sees predicted demand exceeds available idle connections and pre-creates them.

Overlaid with an EWMA of instantaneous demand rate for burst detection that doesn't align with daily patterns.

Total cost: one float multiplication per acquisition. No numpy. No tensorflow. No external dependencies. Just arithmetic that a 486 could do.

### The multiplexer

Here's where it gets interesting. You have N physical connections (small, expensive, limited by server resources). You have M logical sessions (large, cheap, limited only by RAM for state tracking). M >> N.

Each `VirtualSession` is a state machine that knows its namespace, database, auth credentials, and LET variables. When it needs to execute a query, it doesn't hold a connection — it routes a request to a `PhysicalSlot` via the `SessionRouter`.

The router scores each slot:
- Same (ns, db, creds, vars) → 1000 points (zero switch cost)
- Same (ns, db) → 500 points (only need re-auth)
- Lowest queue depth breaks ties

The slot's drain loop processes requests serially (because the underlying WebSocket is serial). Before each query, it compares current state to desired state and applies the minimum diff: only `USE` if namespace/database changed, only `signin` if credentials changed, only `LET` if variables changed.

This is transaction-level multiplexing. ProxySQL does this for MySQL. PgBouncer does it for PostgreSQL. Nobody was doing it for SurrealDB. Now someone is.

### Session teleportation

This is the part I'm actually proud of.

When a physical connection dies — and they will die, because networks are garbage and servers restart — the slot's drain loop catches the exception. It does NOT propagate it to the caller. Instead:

1. Increment retry count on the failed request
2. Check the broken connection back into the pool (which destroys it)
3. Check out a fresh connection from the pool
4. Re-enqueue the failed request
5. The drain loop picks it up, re-applies state, re-executes the query

The caller's `await session.query(...)` resolves with the result. They have no idea a connection died and was replaced under them. As far as they're concerned, SurrealDB was available 100% of the time.

And for connections approaching max-lifetime (which is jittered by ±2.5% to prevent thundering herds), the multiplexer does this **proactively** — it migrates sessions to a fresh connection BEFORE the old one expires. Zero failed requests during rotation.

### What this isn't

This isn't a query router. It doesn't parse SurrealQL. It doesn't do read/write splitting. It doesn't manage schema migrations. It doesn't have a control plane or a dashboard or a config file in YAML.

It's a connection pooler and session multiplexer. It does one thing. It does it correctly. It handles the failure modes. It doesn't lose your data. Ship it.

```
pip install purreal
```
