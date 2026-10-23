"""Entity ownership is checked against authoritative L3 state, never LLM text."""

from safebank_contracts.state import BankState

READ_HINTS = {
    "account_id": "get_accounts",
    "from_account_id": "get_accounts",
    "to_account_id": "get_accounts",
    "card_id": "get_cards",
    "payee_id": "list_payees",
    "participant_payee_ids": "list_payees",
    "instruction_id": "list_scheduled",
    "product_id": "get_product",
}


def entity_ids(args: dict):
    for field in READ_HINTS:
        value = args.get(field)
        if value is not None:
            for entity_id in value if isinstance(value, (tuple, list)) else (value,):
                if isinstance(entity_id, str):
                    yield field, entity_id


def owned(state: BankState, field: str, entity_id: str, customer_id: str) -> bool:
    if field in ("account_id", "from_account_id", "to_account_id"):
        item = state.accounts.get(entity_id)
        return item is not None and item.customer_id == customer_id
    if field == "card_id":
        item = state.cards.get(entity_id)
        return item is not None and owned(state, "account_id", item.account_id, customer_id)
    if field in ("payee_id", "participant_payee_ids"):
        item = state.payees.get(entity_id)
        return item is not None and item.customer_id == customer_id
    if field == "instruction_id":
        for item in (*state.instructions, *state.subscriptions):
            key = getattr(item, "id", getattr(item, "instruction_id", None))
            account = getattr(item, "from_account_id", getattr(item, "account_id", None))
            if key == entity_id and owned(state, "account_id", account, customer_id):
                return True
        return False
    if field == "product_id":
        return any(item.id == entity_id for item in state.products)
    return False
