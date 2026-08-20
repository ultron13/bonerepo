# plimsoll web

The v0.1 interface: sign in, watch runs, and read what a run measured.

```
src/app/page.tsx            Sign-in and the run list
src/app/runs/[runId]/       One run: live windows, results, SLA, errors
src/lib/api.ts              The one place a request is shaped and a token attached
src/lib/types.ts            The shapes this interface reads
src/lib/useLiveRun.ts       The run's WebSocket
```

`make dev` builds and serves it on <http://localhost:3000>. It talks to the API
directly from the browser rather than through a Next.js proxy: one origin to
reason about, one place the token lives, and no server-side copy of a credential
that belongs to the person holding the tab. That makes CORS load-bearing, which
is why `PLIMSOLL_CORS_ORIGINS` lists origins rather than opening them.

`NEXT_PUBLIC_API_URL` is baked at image build time, because Next.js inlines
`NEXT_PUBLIC_*` into the client bundle.

## What is not here yet

Creating projects, repositories, and tests — those are API-only for now, and the
README quickstart shows the `curl` for them. This covers the half of the journey
a user repeats: starting from an existing test, watching the run, reading the
result.
