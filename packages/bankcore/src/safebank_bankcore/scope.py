"""Pure selection of affected customer IDs from an immutable ownership directory."""

from safebank_contracts.state import DirectoryEntry, StateScope
from safebank_contracts.tools import EntityRef


def scope_for(customer_id: str, steps, directory: dict[str, DirectoryEntry]) -> StateScope:
    customers = {customer_id}
    for step in steps:
        args = step.args
        keys = []
        if step.tool in ("transfer", "schedule_transfer"):
            keys += ["account:" + args.from_account_id]
            if args.to_account_id:
                keys += ["account:" + args.to_account_id]
            if isinstance(args.payee_id, str):
                keys += ["payee:" + args.payee_id]
            elif isinstance(args.payee_id, EntityRef):
                creator = next(s for s in steps if s.step_id == args.payee_id.ref)
                keys += ["number:" + creator.args.account_no]
        elif step.tool == "add_payee":
            keys += ["number:" + args.account_no]
        for key in keys:
            entry = directory.get(key)
            if entry and entry.customer_id:
                customers.add(entry.customer_id)
    return StateScope(customer_ids=tuple(sorted(customers)), directory=directory)
