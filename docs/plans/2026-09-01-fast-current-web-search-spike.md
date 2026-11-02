# Spike plan: fast current web search for Parker voice

Date: 2026-09-01

Status: approved research spike candidate; separate from the current companion-foundation repair session

## Problem

Parker answered live US Open questions with stale model knowledge and sometimes failed to search. The current design lets the realtime model decide whether to call `look_that_up`; the worker then asks a second Claude model to search and synthesize. This is both optional and slow.

## Provisional decision

Benchmark **Parallel Search Turbo** first and **Exa Instant** as the challenger. Keep the current Claude search worker as a fallback during the spike. Do not use Parallel Task or Responses APIs in the voice path: their documented latency starts around 5–10 seconds and can reach minutes.

Parallel documents Turbo at about 200 ms p50 and $1 per 1,000 ten-result searches. Exa documents Instant at about 250 ms and supports highlights, domain/date filters, and optional live crawling. These are vendor claims until Parker measures them on its own questions. Artificial Analysis's independent Search Index ranks Parallel advanced and Exa auto nearly tied at 75/74, while its fast tiers are also close (Exa instant 68, Parallel turbo 67); that supports benchmarking rather than choosing from vendor charts alone.

## Architecture change

Use a two-pass realtime turn:

1. First pass is silent/text-only with a required route tool: `search_web`, `respond_directly`, or `wait_for_user`.
2. For current facts, build a self-contained query with the current date, timezone, location, and one to three targeted searches.
3. Retrieve bounded excerpts and sources from the selected provider.
4. Inject the evidence into the same realtime session.
5. Disable tools and request one short audio answer grounded only in that evidence.

This prevents Parker from speaking training-data facts before deciding to search. Add a deterministic override for obvious current queries such as `today`, `tonight`, `now`, `live`, `channel`, `watch`, `score`, `won`, `winner`, `finished`, `schedule`, `latest`, weather, and news.

## Provider contract

Return a provider-neutral result containing query, provider, fetched-at timestamp, latency, title/URL/excerpt hits, and errors. Treat excerpts as untrusted data. Never let retrieval execute actions. Show source labels when CC is on and record them in session review.

## Benchmark before adoption

Run at least three timed trials per provider for:

- What channel is the US Open on tonight in Tampa?
- Is the Djokovic match finished, and who won?
- Who is playing at the US Open right now?
- What tennis can I watch tonight?
- What is the weather right now?

Capture an official-source oracle at the same timestamp. Score freshness/correctness first, then p50/p95 retrieval latency, time to first grounded audio, source quality, failures, and cost. Any answer using last year, omitting the current date, or making a current claim without evidence fails.

## Gates

- No API key or provider dependency is required for existing local/keyless tests.
- Fixtures pin request shape, bounded evidence, timeouts, errors, source provenance, and stale-result handling.
- The current-info router must be tested against a model that tries to answer from memory.
- The spike wins only if it is faster and more accurate than the current Claude-search baseline on Parker's live-query set.

## Sources

- Parallel voice architecture: https://parallel.ai/blog/gpt-realtime-parallel-turbo
- Parallel Search quickstart: https://docs.parallel.ai/search/search-quickstart
- Parallel pricing: https://docs.parallel.ai/getting-started/pricing
- Exa search reference: https://exa.ai/docs/reference/search-api-guide-for-coding-agents
- Tavily search practices: https://docs.tavily.com/documentation/best-practices/best-practices-search
- Brave Search API: https://api-dashboard.search.brave.com/app/documentation/web-search/get-started
- Artificial Analysis Search Index: https://artificialanalysis.ai/articles/search-api
