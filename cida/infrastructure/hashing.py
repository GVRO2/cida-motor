import hashlib

class HashService:
    """Concrete HashService using standard hashlib."""
    def sha256(self, content: bytes) -> str:
        if isinstance(content, str):
            content = content.encode('utf-8')
        return hashlib.sha256(content).hexdigest()

    def sha1(self, content: bytes) -> str:
        if isinstance(content, str):
            content = content.encode('utf-8')
        return hashlib.sha1(content).hexdigest()
