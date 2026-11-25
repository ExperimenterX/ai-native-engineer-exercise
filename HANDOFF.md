# Engineering Handoff

Bhavani Shankar | DetailPage Engineering Exercise

## Ready for the team to rely on Monday?

Yes, provided upstream data is synchronized and validated before Monday. The requested server capabilities are implemented and tested.

The remaining dependency is upstream data synchronization. The newest recall is dated 2026-11-05, despite successful load statuses through 2026-11-19. The loader resides in another repository. `server_status` now exposes `data_may_be_stale` and associated warnings, enabling agents to communicate potential data incompleteness.

Unit totals are approximate because the database retains only the first number from CPSC's free-text unit descriptions. Multi-product recalls may be undercounted. `sum_units` separates clean records from those requiring review. In 2024, 14 of 282 recalls were flagged, with none changing the top result under the implemented interpretation.

## What I built and checked

- Added unit aggregation by year or retailer, retailer filtering, and freshness checks.
- Fixed three existing bugs: fiscal-year filtering (2025 returned 341 instead of 411), missing note retrieval, and silent pagination truncation.
- Added guardrails: read-only database access for read tools, a 1,000-character note limit, control-character sanitization, and literal matching of `%` and `_` in searches.
- Exercised all five supplied questions through the actual MCP server and cross-checked results against raw SQL on a database copy.
- All 48 automated tests passed.

## What I did not check

- Upstream loader behavior and reconciliation against live CPSC data.
- Retailer matching beyond Amazon and Walmart samples.
- Concurrent writers, access control, and note persistence across nightly database refreshes. Note authorship is self-declared.

## Questions for Dana and my assumptions

- Calendar or fiscal year? Assumed calendar year by recall date.
- US or worldwide units? Assumed US units, with overlapping recalls flagged rather than subtracted.
- Does "sold at X" include online marketplaces? Assumed yes, including online-only sales.
- Is stale data acceptable for the marketplace team, and who owns the upstream loader?

## Where I stopped and what's next

I stopped at 45 minutes with the requested capabilities implemented. With more time, I would investigate the upstream loader, introduce freshness alerts, reconcile counts against live CPSC data, improve unit parsing, expand retailer validation, implement note access control, and add CI.

**AI tools used:** Claude Code (Sonnet 5.5) for implementation, refactoring, and test development, followed by independent code review and verification.