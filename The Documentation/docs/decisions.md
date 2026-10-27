# Design Decisions

Each entry records what was chosen, why, and what it costs.

## 1. Deterministic engine, AI only explains

**Decision.** Every figure is computed by Python. The language model receives results and writes prose.

**Why.** ALM numbers must be reproducible and auditable. A model can mis-add or invent a figure.

**Cost.** Explanations are only as good as the small local model.

## 2. Local model (Ollama + Phi-3)

**Decision.** Run the model on the bank's machine.

**Why.** Position data should not leave the premises.

**Cost.** Lower language quality than hosted models, and slow on weak hardware (hence the 800-second timeout).

## 3. Existing database, no DDL

**Decision.** The application never creates or alters tables.

**Why.** The schema belongs to the data team; the app should not be able to damage it.

**Cost.** The app depends on the exact schema; missing tables stop it with a clear message.

## 4. Flat SQL views with Python fallback

**Decision.** Joins and EGP conversion live in two views. If a view's row count differs from its source, an embedded `LEFT JOIN` query is used.

**Why.** Keeps Python simple and ensures no position is dropped by an inner join.

**Cost.** The join logic exists twice and must stay in sync.

## 5. Everything converted to EGP in SQL

**Decision.** FX and gold balances are valued with the latest rate in the view; the engine works in one currency.

**Why.** Consistent totals and simple formulas.

**Cost.** Currency exposure is represented only through a shock factor, not a currency-level model.

## 6. Event-based repricing with a parallel shock

**Decision.** A rate shock applies after each position's first repricing event, in proportion to the time remaining.

**Why.** It reflects contract mechanics, so a one-year fixed loan and a daily-repricing deposit react differently.

**Cost.** No curve shape and no pass-through. See [Limitations](limitations.md).

## 7. Rollover at maturity

**Decision.** Positions that mature in the window are assumed to renew and reprice at maturity; already matured positions are excluded.

**Why.** Keeps the balance sheet static over the horizon, which is the standard simplification for a gap-style analysis.

## 8. Treasury deposits are Funding

**Decision.** Deposits from the `Treasury` segment are classed as wholesale funding.

**Why.** They behave like market funding, not customer deposits, and should not be run off or counted in the customer deposit base.

## 9. Nine drivers that map one-to-one to database columns

**Decision.** `PARAM_SPECS` defines the drivers once; the UI, persistence, reports and parser all read it.

**Why.** Adding a driver is one entry plus the engine logic, and the saved scenario always matches what ran.

## 10. A driver-attribution table with an interaction row

**Decision.** Each driver is run alone and the residual is shown as *Interaction*.

**Why.** Management asks *which factor caused this*. The residual makes the table add up honestly instead of hiding combined effects.

## 11. Independent validation table

**Decision.** Headline KPIs are recomputed from the position detail and marked PASS or FAIL.

**Why.** Gives engineers a visible self-check.

**Cost.** It tests consistency of the summary, not correctness of the model.

## 12. Excel with live formulas

**Decision.** The Row-by-Row export contains formulas, not pasted values.

**Why.** Reviewers can trace each number in a tool they trust.

**Cost.** A second implementation of the maths. It can drift from the engine (see Limitations A).

## 13. Single transaction for persistence

**Decision.** Scenario, run and results are saved together.

**Why.** No half-saved runs.

## 14. Cache data for 120 seconds, engine without expiry

**Decision.** Tables are cached briefly; a **Reload data** button clears the cache.

**Why.** Streamlit reruns on every click; reading the database each time would be slow.

## 15. Documentation as code

**Decision.** These pages are Markdown in a repository, built with MkDocs Material and deployed to GitHub Pages.

**Why.** They are versioned with the project, reviewable, searchable and always one command from publishing.
