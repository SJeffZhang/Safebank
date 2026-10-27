# ADR-010: Signed `max_outflow_cents` in `AccountReserve`

## Status
Accepted (2026-10-27).

## Context
The PRD §7.7 reserve formula computes `max_outflow(acct) = available − max(0, max_k(−Σ_{i≤k} delta_i))`. When obligations exceed available funds (existing shortfall), this produces a negative value. The `packages/bankcore` pure-function layer already returns negative `max_outflow_cents` for such cases and explicitly documents that callers must not clamp to zero.

However, `packages/contracts` defined `AccountReserve.max_outflow_cents: Cents`, where `Cents = Annotated[Int64, Field(ge=0)]`. A negative value would fail Pydantic validation at the contract boundary, making the L3 read tool `upcoming_charges` (R11) unable to serialize the truthful projection.

The `projected_shortfall` (S2, days=7) path in the gateway consumes `max_outflow_cents` directly from the bankcore dataclass, bypassing the contracts model, so the gateway's `hint_max_outflow` and `SCHEDULED_SHORTFALL` finding logic was unaffected by the `Cents` constraint. R11 is the first public-facing contract that must serialize `AccountReserve` for arbitrary `days` values.

## Decision
1. Add `SignedCents` to `packages/contracts/src/safebank_contracts/base.py`: `SignedCents = Annotated[Int64, Field()]` — same `Int64` range (`[-2^63, 2^63-1]`) but without the `ge=0` lower bound.
2. Change only `AccountReserve.max_outflow_cents` from `Cents` to `SignedCents`. All other `*_cents` fields on `AccountReserve` (`reserve_cents`) and all other contract models retain their existing bounds.
3. No clamp-to-zero is applied anywhere in the read path; negative values are serialized as-is.

## Consequences
- **L1 / L2 consumers**: `max_outflow_cents` is now a signed integer. Display code must distinguish negative values as "existing shortfall" rather than showing a negative outflow limit. The `hint_max_outflow` in the gateway already handles this correctly (negative values are not used as hints).
- **No behavioral change**: The bankcore layer already produced negative values; this only affects serialization at the contract boundary.
- **Existing tests**: Bankcore tests (`test_reserve.py`) that assert `max_outflow_cents == -1500` etc. remain correct.

## Alternatives considered
1. **Clamp to 0 in the read path**: Would silently hide existing shortfalls from R11 consumers, making the data untruthful. Rejected.
2. **Add a separate `is_deficit: bool` field and clamp**: Adds complexity without benefit; the signed value conveys more information (magnitude of the shortfall).
3. **Use `int` with no validation**: Loses the `BeforeValidator(exact_int)` guard from `Int64`, which rejects float/bool/string coercion. Rejected.