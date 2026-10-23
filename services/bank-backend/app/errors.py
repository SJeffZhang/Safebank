from safebank_contracts.codes import Code


class BankError(Exception):
    def __init__(self, code: Code, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code.value)
