import re
from dataclasses import dataclass
from typing import Any

from cida.domain.errors import SemanticValidationError, UnsupportedFrontmatterSyntaxError


@dataclass
class ParseState:
    key_count: int = 0


class FrontmatterCodec:
    """Restricted stdlib-only frontmatter parser for CIDA metadata."""

    max_depth = 8
    max_keys = 1024
    max_bytes = 1024 * 1024

    def decode(self, content: str) -> dict:
        try:
            state = ParseState()
            if content.strip() == "---":
                return {}
            lines = self._prepare_lines(content)
            if not any(line.strip() for line in lines):
                return {}
            value, index = self._parse_mapping(lines, 0, 0, state)
            if index != len(lines):
                raise UnsupportedFrontmatterSyntaxError(f"Invalid indentation at line {index + 1}")
            return value
        except SemanticValidationError:
            raise
        except Exception as exc:
            raise SemanticValidationError(f"Frontmatter parsing error: {exc}") from exc

    def parse_frontmatter_safe(self, content: str) -> dict:
        if content.startswith("\ufeff"):
            content = content[1:]
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.strip().splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("YAML frontmatter must start with '---'")
        if len(lines) < 2 or lines[-1].strip() != "---":
            raise ValueError("YAML frontmatter must end with '---'")
        body = "\n".join(lines[1:-1])
        if not body.strip():
            return {}
        return self.decode(body)

    def parse_yaml_frontmatter_safe(self, content: str) -> dict:
        return self.parse_frontmatter_safe(content)

    def _prepare_lines(self, content: str) -> list[str]:
        if content.startswith("\ufeff"):
            content = content[1:]
        if len(content.encode("utf-8")) > self.max_bytes:
            raise UnsupportedFrontmatterSyntaxError("Frontmatter exceeds maximum supported size")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        prepared: list[str] = []
        for line_no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            if "\t" in line[: len(line) - len(line.lstrip())]:
                raise UnsupportedFrontmatterSyntaxError(f"Tabs are not supported for indentation at line {line_no}")
            stripped = self._strip_comment(line.rstrip())
            if not stripped.strip():
                continue
            text = stripped.lstrip()
            if text in ("---", "..."):
                raise UnsupportedFrontmatterSyntaxError("Multiple YAML documents are not supported")
            if text.startswith(("-", "?")) and not (text == "-" or text.startswith("- ")):
                raise UnsupportedFrontmatterSyntaxError(f"Unsupported YAML syntax at line {line_no}")
            prepared.append(stripped.rstrip())
        return prepared

    def _parse_mapping(self, lines: list[str], index: int, indent: int, state: ParseState) -> tuple[dict, int]:
        self._check_depth(indent)
        result: dict[str, Any] = {}
        while index < len(lines):
            line = lines[index]
            current_indent = self._indent_of(line)
            if current_indent < indent:
                break
            if current_indent > indent:
                raise UnsupportedFrontmatterSyntaxError(f"Invalid indentation at line {index + 1}")

            text = line[current_indent:]
            if text.startswith("- "):
                if indent == 0:
                    raise SemanticValidationError("Frontmatter must be a key-value dictionary")
                break

            key, raw_value = self._split_key_value(text, index + 1)
            if key == "<<":
                raise UnsupportedFrontmatterSyntaxError("Merge keys are not supported")
            if key in result:
                raise UnsupportedFrontmatterSyntaxError(f"Duplicate key '{key}' found in YAML frontmatter")
            self._record_key(state)

            if raw_value == "":
                next_index = index + 1
                if next_index >= len(lines) or self._indent_of(lines[next_index]) <= current_indent:
                    result[key] = None
                    index += 1
                    continue
                next_text = lines[next_index].lstrip()
                value: Any
                if next_text == "-" or next_text.startswith("- "):
                    value, index = self._parse_list(lines, next_index, self._indent_of(lines[next_index]), state)
                else:
                    value, index = self._parse_mapping(lines, next_index, self._indent_of(lines[next_index]), state)
                result[key] = value
                continue

            result[key] = self._parse_scalar_or_inline(raw_value, index + 1)
            index += 1
        return result, index

    def _parse_list(self, lines: list[str], index: int, indent: int, state: ParseState) -> tuple[list, int]:
        self._check_depth(indent)
        result: list[Any] = []
        while index < len(lines):
            line = lines[index]
            current_indent = self._indent_of(line)
            if current_indent < indent:
                break
            if current_indent > indent:
                raise UnsupportedFrontmatterSyntaxError(f"Invalid list indentation at line {index + 1}")
            text = line[current_indent:]
            if text != "-" and not text.startswith("- "):
                break
            raw_value = "" if text == "-" else text[2:].strip()
            if raw_value == "":
                next_index = index + 1
                if next_index >= len(lines) or self._indent_of(lines[next_index]) <= current_indent:
                    result.append(None)
                    index += 1
                    continue
                next_text = lines[next_index].lstrip()
                value: Any
                if next_text == "-" or next_text.startswith("- "):
                    value, index = self._parse_list(lines, next_index, self._indent_of(lines[next_index]), state)
                else:
                    value, index = self._parse_mapping(lines, next_index, self._indent_of(lines[next_index]), state)
                result.append(value)
                continue
            if ":" in raw_value and not raw_value.startswith(("'", '"', "[", "{")):
                key, value_text = self._split_key_value(raw_value, index + 1)
                self._record_key(state)
                item: dict[str, Any] = {key: self._parse_scalar_or_inline(value_text, index + 1) if value_text else None}
                next_index = index + 1
                if next_index < len(lines) and self._indent_of(lines[next_index]) > current_indent:
                    continuation, index = self._parse_mapping(lines, next_index, self._indent_of(lines[next_index]), state)
                    for nested_key, nested_value in continuation.items():
                        if nested_key in item:
                            raise UnsupportedFrontmatterSyntaxError(
                                f"Duplicate key '{nested_key}' found in YAML frontmatter"
                            )
                        item[nested_key] = nested_value
                    result.append(item)
                    continue
                result.append(item)
            else:
                result.append(self._parse_scalar_or_inline(raw_value, index + 1))
            index += 1
        return result, index

    def _parse_scalar_or_inline(self, value: str, line_no: int) -> Any:
        value = value.strip()
        self._reject_unsupported_tokens(value, line_no)
        if value in ("|", ">") or value.startswith(("|", ">")):
            raise UnsupportedFrontmatterSyntaxError("Block scalars are not supported")
        if value.startswith("["):
            if not value.endswith("]"):
                raise UnsupportedFrontmatterSyntaxError(f"Unclosed inline list at line {line_no}")
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [self._parse_scalar_or_inline(part, line_no) for part in self._split_inline_list(inner, line_no)]
        if value.startswith("{"):
            raise UnsupportedFrontmatterSyntaxError("Inline maps are not supported")
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            return self._unquote(value, line_no)
        lowered = value.lower()
        if lowered in ("true", "false"):
            return lowered == "true"
        if lowered in ("null", "~"):
            return None
        if re.fullmatch(r"[+-]?\d+", value):
            return int(value)
        if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)", value):
            return float(value)
        if value == "" or value.startswith(("-", "?", ":")) or ": " in value:
            raise UnsupportedFrontmatterSyntaxError(f"Ambiguous scalar at line {line_no}")
        return value

    def _split_key_value(self, text: str, line_no: int) -> tuple[str, str]:
        in_quote: str | None = None
        for index, char in enumerate(text):
            if char in ("'", '"'):
                in_quote = None if in_quote == char else char if in_quote is None else in_quote
            if char == ":" and in_quote is None:
                key = text[:index].strip()
                value = text[index + 1 :].strip()
                if not key:
                    raise UnsupportedFrontmatterSyntaxError(f"Empty frontmatter key at line {line_no}")
                self._reject_unsupported_tokens(key, line_no)
                return str(self._parse_key(key, line_no)), value
        raise UnsupportedFrontmatterSyntaxError(f"Expected key/value pair at line {line_no}")

    def _parse_key(self, key: str, line_no: int) -> Any:
        if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
            return self._unquote(key, line_no)
        if " " in key:
            raise UnsupportedFrontmatterSyntaxError(f"Ambiguous key at line {line_no}")
        if re.fullmatch(r"[+-]?\d+", key):
            return int(key)
        return key

    def _split_inline_list(self, text: str, line_no: int) -> list[str]:
        parts: list[str] = []
        current: list[str] = []
        in_quote: str | None = None
        for char in text:
            if char in ("'", '"'):
                in_quote = None if in_quote == char else char if in_quote is None else in_quote
            if char == "," and in_quote is None:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
        if in_quote is not None:
            raise UnsupportedFrontmatterSyntaxError(f"Unclosed string in inline list at line {line_no}")
        parts.append("".join(current).strip())
        return parts

    def _unquote(self, value: str, line_no: int) -> str:
        quote = value[0]
        inner = value[1:-1]
        if quote == "'":
            return inner.replace("''", "'")
        return self._decode_double_quoted(inner, line_no)

    def _decode_double_quoted(self, value: str, line_no: int) -> str:
        escapes = {
            '"': '"',
            "\\": "\\",
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "b": "\b",
            "f": "\f",
        }
        decoded: list[str] = []
        index = 0
        while index < len(value):
            char = value[index]
            if char != "\\":
                decoded.append(char)
                index += 1
                continue

            if index + 1 >= len(value):
                raise UnsupportedFrontmatterSyntaxError(f"Trailing backslash in quoted string at line {line_no}")
            escape = value[index + 1]
            if escape in escapes:
                decoded.append(escapes[escape])
                index += 2
                continue
            if escape == "u":
                codepoint, index = self._decode_unicode_escape(value, index, 4, line_no)
                if 0xD800 <= codepoint <= 0xDBFF:
                    if not value.startswith("\\u", index):
                        raise UnsupportedFrontmatterSyntaxError(f"Invalid surrogate pair at line {line_no}")
                    low, index = self._decode_unicode_escape(value, index, 4, line_no)
                    if not 0xDC00 <= low <= 0xDFFF:
                        raise UnsupportedFrontmatterSyntaxError(f"Invalid surrogate pair at line {line_no}")
                    codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)
                elif 0xDC00 <= codepoint <= 0xDFFF:
                    raise UnsupportedFrontmatterSyntaxError(f"Invalid surrogate pair at line {line_no}")
                decoded.append(chr(codepoint))
                continue
            if escape == "U":
                codepoint, index = self._decode_unicode_escape(value, index, 8, line_no)
                if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
                    raise UnsupportedFrontmatterSyntaxError(f"Invalid Unicode escape at line {line_no}")
                decoded.append(chr(codepoint))
                continue
            raise UnsupportedFrontmatterSyntaxError(f"Unsupported escape \\{escape} at line {line_no}")
        return "".join(decoded)

    def _decode_unicode_escape(self, value: str, slash_index: int, digits: int, line_no: int) -> tuple[int, int]:
        escape_type = value[slash_index + 1]
        start = slash_index + 2
        end = start + digits
        if end > len(value):
            raise UnsupportedFrontmatterSyntaxError(f"Incomplete Unicode escape at line {line_no}")
        hex_value = value[start:end]
        if not re.fullmatch(r"[0-9A-Fa-f]+", hex_value):
            raise UnsupportedFrontmatterSyntaxError(f"Invalid Unicode escape at line {line_no}")
        expected = "u" if digits == 4 else "U"
        if escape_type != expected:
            raise UnsupportedFrontmatterSyntaxError(f"Invalid Unicode escape at line {line_no}")
        return int(hex_value, 16), end

    def _strip_comment(self, line: str) -> str:
        in_quote: str | None = None
        escaped = False
        for index, char in enumerate(line):
            if char == "\\" and in_quote == '"' and not escaped:
                escaped = True
                continue
            if char in ("'", '"') and not escaped:
                in_quote = None if in_quote == char else char if in_quote is None else in_quote
            if char == "#" and in_quote is None and (index == 0 or line[index - 1].isspace()):
                return line[:index]
            escaped = False
        return line

    def _reject_unsupported_tokens(self, value: str, line_no: int) -> None:
        if value.startswith("!") or re.search(r"(^|\s)![^\s]+", value):
            raise UnsupportedFrontmatterSyntaxError(f"Custom tags are not supported at line {line_no}")
        if re.search(r"(^|\s)&[A-Za-z0-9_-]+", value):
            raise UnsupportedFrontmatterSyntaxError(f"Anchors are not supported at line {line_no}")
        if re.search(r"(^|\s)\*[A-Za-z0-9_-]+", value):
            raise UnsupportedFrontmatterSyntaxError(f"Aliases are not supported at line {line_no}")

    def _indent_of(self, line: str) -> int:
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2 != 0:
            raise UnsupportedFrontmatterSyntaxError("Indentation must use multiples of two spaces")
        return indent

    def _check_depth(self, indent: int) -> None:
        if indent // 2 > self.max_depth:
            raise UnsupportedFrontmatterSyntaxError("Frontmatter exceeds maximum nesting depth")

    def _record_key(self, state: ParseState) -> None:
        state.key_count += 1
        if state.key_count > self.max_keys:
            raise UnsupportedFrontmatterSyntaxError("Frontmatter exceeds maximum key count")


def parse_frontmatter_safe(content: str) -> dict:
    return FrontmatterCodec().parse_frontmatter_safe(content)


def parse_yaml_frontmatter_safe(content: str) -> dict:
    return parse_frontmatter_safe(content)


def parse_yaml_frontmatter(content: str) -> dict:
    return parse_frontmatter_safe(content)


class UniqueKeyLoader:
    """Compatibility symbol; CIDA no longer uses external YAML loaders."""


YamlCodec = FrontmatterCodec
