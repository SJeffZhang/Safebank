# ADR-011: L3 domains add internal BankState fields additively

Date: 2026-10-28
Status: accepted

## Context

The frozen contract snapshot `docs/contracts/v1.json` includes the internal `BankState`
model, and the gateway validates L3 snapshots against it. The remaining L3 domains (C3
standing instructions, C4 splits, C5 risk assessments, C6 products and funds) each need
their own data in `BankState` so that L2 preflight and L3 execution share the same pure
rules (AGENTS §2.6). C4 is the first to add one: `BankState.splits`.

## Decision

- A domain may add new optional fields to `BankState` with an empty default. It may not
  rename, remove or retype existing fields, and may not change any public read/write tool
  argument or result model (the 18 reads / 14 writes stay frozen).
- Workers do not edit the snapshot. The coordinator regenerates it with
  `make contracts-generate` when merging the domain, checks the diff is additive only
  (no removed lines apart from `v1.sha256`), and runs `make contracts-check` and
  `make test`, which covers the gateway's snapshot validation.
- When several domain branches add fields in parallel, each later merge regenerates the
  snapshot again on top of main.

## Consequences

The snapshot stays the single source of truth that both L2 and L3 validate against, while
domains can land independently. The version stays `v1` because existing consumers keep
working: every addition is optional with a default.
