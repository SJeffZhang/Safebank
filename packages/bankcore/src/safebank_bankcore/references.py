"""Pure, deterministic reference identity shared by prepare and execute."""

from safebank_contracts.state import BankState
from safebank_contracts.tools import EntityRef

from .ids import derive, digest


def request_digest(steps) -> str:
    return digest([s.model_dump(mode="json", exclude={"arg_sources"}) for s in steps])


def resolved_steps(state: BankState, steps, customer_id: str, plan_id: str) -> list[dict]:
    refs: dict[str, str] = {}
    result = []
    known = {
        p.target_fingerprint: p.id for p in state.payees.values() if p.customer_id == customer_id
    }
    for step in steps:
        if step.tool == "add_payee":
            entry = state.directory["number:" + step.args.account_no]
            existing = known.get(entry.target_fingerprint)
            refs[step.step_id] = existing or derive(plan_id, step.step_id, "pye")
            known[entry.target_fingerprint] = refs[step.step_id]
        data = step.model_dump(mode="json", exclude={"arg_sources"})
        if isinstance(getattr(step.args, "payee_id", None), EntityRef):
            data["args"]["payee_id"] = refs[step.args.payee_id.ref]
        result.append(data)
    return result
