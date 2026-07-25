"""Lọc domain theo blacklist / whitelist."""


class DomainFilter:
    def __init__(self, blacklist=None, whitelist=None):
        self.blacklist = [b.lower().strip() for b in (blacklist or []) if b.strip()]
        self.whitelist = [w.lower().strip() for w in (whitelist or []) if w.strip()]

    def allowed(self, domain: str) -> bool:
        d = (domain or "").lower()
        if not d:
            return False
        # Nếu có whitelist -> chỉ giữ domain khớp whitelist
        if self.whitelist:
            return any(w in d for w in self.whitelist)
        return not any(b in d for b in self.blacklist)
