class DomainError(Exception):
    """Public, deliberately sanitized error; never include upstream bodies/secrets."""

    def __init__(self, status: int, code: str, message: str, detail=None):
        super().__init__(message)
        self.status, self.code, self.message, self.detail = status, code, message, detail
