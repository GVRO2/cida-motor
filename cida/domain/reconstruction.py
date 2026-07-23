import re
from cida.domain.errors import SidecarValidationError

from cida.domain.sidecar import validate_sidecar_schema

def reconstruct_content(compressed_text: str, sidecar_data: dict) -> str:
    """
    Reconstructs original text from compressed text and sidecar data.
    Validates schema, checks for alias collisions, and substitutes tokens.
    """
    if not isinstance(sidecar_data, dict):
        raise SidecarValidationError("Sidecar data must be a dictionary")

    validate_sidecar_schema(sidecar_data)

    entries = sidecar_data.get("entries", {})

    # Strip header line if present
    text = compressed_text
    header_pattern = re.compile(r'^>\s*🤖\s*AI RAG DICT:.*?\n\n', re.DOTALL)
    text = header_pattern.sub('', text)

    if not entries:
        return text

    # Sort aliases by length descending to prevent substring collisions
    sorted_entries = sorted(entries.items(), key=lambda item: len(item[0]), reverse=True)

    for alias, original_word in sorted_entries:
        escaped_alias = re.escape(alias)
        left_b = r'\b' if re.match(r'^\w', alias) else r'(?<!\w)'
        right_b = r'\b' if re.search(r'\w$', alias) else r'(?!\w)'
        pattern = re.compile(f'{left_b}{escaped_alias}{right_b}')
        text = pattern.sub(original_word, text)

    return text
