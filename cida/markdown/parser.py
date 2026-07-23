import re
import yaml  # type: ignore

class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = []
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in [k for k, _ in mapping]:
                raise yaml.constructor.ConstructorError(
                    None, None, f"Duplicate key '{key}' found in YAML frontmatter", key_node.start_mark
                )
            value = self.construct_object(value_node, deep=deep)
            mapping.append((key, value))
        return super().construct_mapping(node, deep=deep)

class Block:
    def __init__(self, block_type, content, metadata=None):
        self.type = block_type
        self.content = content
        self.metadata = metadata or {}

    def __repr__(self):
        return f"Block(type={self.type}, content_len={len(self.content)})"

def find_frontmatter_end(lines):
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            yaml_str = "".join(lines[1:idx])
            try:
                yaml.load(yaml_str, Loader=UniqueKeyLoader)
                return idx
            except yaml.constructor.ConstructorError:
                return idx
            except Exception:
                continue
    return -1

def has_frontmatter_at_document_start(text):
    if text.startswith('\ufeff'):
        text = text[1:]
    return bool(re.match(r'^---[ \t]*[\r\n]', text)) or text == '---'

def parse_markdown(text):
    """
    Splits the markdown text into block objects deterministically.
    """
    lines = text.splitlines(keepends=True)
    blocks = []

    # 1. Frontmatter check
    has_frontmatter = has_frontmatter_at_document_start(text)

    line_idx = 0
    if has_frontmatter:
        end_idx = find_frontmatter_end(lines)
        if end_idx == -1:
            raise ValueError("YAML frontmatter not closed properly")
        fm_content = "".join(lines[0:end_idx+1])
        blocks.append(Block("frontmatter", fm_content))
        line_idx = end_idx + 1

    while line_idx < len(lines):
        line = lines[line_idx]
        stripped = line.strip()

        # 2. Check for fenced code block opening
        m = re.match(r'^([ \t]{0,3})(([`~])\3{2,})(.*)', line)
        if m:
            indent = m.group(1)
            fence_str = m.group(2)
            fence_char = m.group(3)
            fence_len = len(fence_str)
            info_string = m.group(4)

            code_block_lines = [line]
            closed = False
            line_idx += 1

            close_pattern = rf'^([ \t]{{0,3}})({re.escape(fence_char)}{{{fence_len},}})\s*$'
            while line_idx < len(lines):
                inner_line = lines[line_idx]
                code_block_lines.append(inner_line)
                if re.match(close_pattern, inner_line):
                    closed = True
                    line_idx += 1
                    break
                line_idx += 1

            if not closed:
                raise ValueError("Fenced code block not closed")

            blocks.append(Block("code_block", "".join(code_block_lines), {
                "lang": info_string.strip(),
                "fence_char": fence_char,
                "fence_len": fence_len,
                "indent": indent
            }))
            continue

        # 3. HTML Comment
        is_comment_start = stripped.startswith("<!--")
        if is_comment_start:
            if "-->" in stripped:
                blocks.append(Block("comment", line))
                line_idx += 1
                continue
            else:
                comment_lines = [line]
                line_idx += 1
                closed_comment = False
                while line_idx < len(lines):
                    comment_line = lines[line_idx]
                    comment_lines.append(comment_line)
                    if "-->" in comment_line:
                        closed_comment = True
                        break
                    line_idx += 1
                if not closed_comment:
                    raise ValueError("HTML comment not closed")
                blocks.append(Block("comment", "".join(comment_lines)))
                line_idx += 1
                continue

        # 4. Header
        is_header = stripped.startswith("#") and re.match(r'^#{1,6}\s+', stripped) is not None
        if is_header:
            blocks.append(Block("header", line))
            line_idx += 1
            continue

        # 5. Table
        is_table = stripped.startswith("|") or (stripped.endswith("|") and "|" in stripped)
        if is_table:
            table_lines = [line]
            line_idx += 1
            while line_idx < len(lines):
                next_line = lines[line_idx]
                next_stripped = next_line.strip()
                if next_stripped.startswith("|") or (next_stripped.endswith("|") and "|" in next_stripped):
                    table_lines.append(next_line)
                    line_idx += 1
                else:
                    break
            blocks.append(Block("table", "".join(table_lines)))
            continue

        # 6. List
        is_list = re.match(r'^(\s*)([-*+]|\d+\.)\s+', line) is not None
        if is_list:
            list_lines = [line]
            line_idx += 1
            while line_idx < len(lines):
                next_line = lines[line_idx]
                next_stripped = next_line.strip()
                if not next_stripped:
                    if line_idx + 1 < len(lines):
                        after_next = lines[line_idx + 1]
                        if re.match(r'^(\s*)([-*+]|\d+\.)\s+', after_next):
                            list_lines.append(next_line)
                            line_idx += 1
                            continue
                    break
                if re.match(r'^(\s*)([-*+]|\d+\.)\s+', next_line) or next_line.startswith(" ") or next_line.startswith("\t"):
                    list_lines.append(next_line)
                    line_idx += 1
                else:
                    break
            blocks.append(Block("list", "".join(list_lines)))
            continue

        # 7. Blockquote
        is_blockquote = stripped.startswith(">")
        if is_blockquote:
            bq_lines = [line]
            line_idx += 1
            while line_idx < len(lines):
                next_line = lines[line_idx]
                next_stripped = next_line.strip()
                if next_stripped.startswith(">"):
                    bq_lines.append(next_line)
                    line_idx += 1
                else:
                    break
            blocks.append(Block("blockquote", "".join(bq_lines)))
            continue

        # 8. HR
        is_hr = re.match(r'^[-*_]{3,}\s*$', stripped) is not None
        if is_hr:
            blocks.append(Block("hr", line))
            line_idx += 1
            continue

        # 9. Blank
        if not stripped:
            blocks.append(Block("blank", line))
            line_idx += 1
            continue

        # 10. Paragraph
        para_lines = [line]
        line_idx += 1
        while line_idx < len(lines):
            next_line = lines[line_idx]
            next_stripped = next_line.strip()
            if (not next_stripped or
                next_stripped.startswith("#") or
                re.match(r'^([ \t]{0,3})(([`~])\3{2,})(.*)', next_line) or
                next_stripped.startswith("<!--") or
                next_stripped.startswith("|") or
                re.match(r'^(\s*)([-*+]|\d+\.)\s+', next_line) or
                next_stripped.startswith(">") or
                re.match(r'^[-*_]{3,}\s*$', next_stripped)):
                break
            para_lines.append(next_line)
            line_idx += 1
        blocks.append(Block("paragraph", "".join(para_lines)))

    return blocks
