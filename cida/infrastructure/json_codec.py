import json
from cida.domain.errors import SidecarValidationError

from typing import Any

class JsonCodec:
    """Concrete JSON parser supporting duplicate key rejection."""

    def reject_duplicate_keys(self, pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SidecarValidationError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def decode(self, content: str) -> dict:
        try:
            return json.loads(content, object_pairs_hook=self.reject_duplicate_keys)
        except Exception as e:
            if not isinstance(e, SidecarValidationError):
                raise SidecarValidationError(f"Failed to parse JSON: {e}") from e
            raise

    def encode(self, data: Any, indent: int = 4) -> str:
        return json.dumps(data, indent=indent, ensure_ascii=False)

    def canonical_encode(self, data: Any) -> str:
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
