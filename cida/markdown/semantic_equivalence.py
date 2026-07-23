import re
import yaml  # type: ignore
from cida.markdown.parser import parse_markdown, has_frontmatter_at_document_start
from cida.domain.policies import classify_comment
from cida.markdown.protected_regions import ProtectedRegionsManager

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

def parse_yaml_frontmatter_safe(content):
    """
    Parses frontmatter content using safe PyYAML and UniqueKeyLoader to reject duplicates.
    """
    if content.startswith('\ufeff'):
        content = content[1:]
    lines = content.strip().splitlines()
    if not lines or lines[0].strip() != '---':
        raise ValueError("YAML frontmatter must start with '---'")
    if len(lines) < 2 or lines[-1].strip() != '---':
        raise ValueError("YAML frontmatter must end with '---'")

    yaml_str = "\n".join(lines[1:-1])
    if not yaml_str.strip():
        return {}

    try:
        data = yaml.load(yaml_str, Loader=UniqueKeyLoader)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Frontmatter must be a key-value dictionary")
        return {str(k): v for k, v in data.items()}
    except Exception as e:
        raise ValueError(f"YAML parsing error: {e}")

def parse_yaml_frontmatter(content):
    return parse_yaml_frontmatter_safe(content)

def extract_all_protected_elements(text):
    """
    Extracts all occurrences of protected elements in their exact order of appearance.
    """
    patterns = [
        # Inline code
        r'`[^`\n]+`',
        # Link/Image destinations
        r'(?<=\]\()(?:\([^)]*\)|[^)])+(?=\))',
        # URLs
        r'https?://[^\s)\]]+',
        # Placeholders
        r'\{\{[\w.-]+\}\}',
        r'\{[\w.-]+\}',
        r'\$\{[\w_]+\}',
        # XML/HTML tags
        r'<[^>]+>',
        # BMAD critical terms
        r'\bstepsCompleted\b',
        r'\bworkflowType\b',
        r'\binputDocuments\b',
        r'\bnextStepFile\b',
        r'\boutputFile\b',
        r'\bbmad-create-architecture\b',
        r'\bbmad-dev-story\b',
        r'\bsteps-c/?\b',
        r'\bsteps-e/?\b',
        r'\bsteps-v/?\b',
        r'\b_bmad/?\b',
        r'\b_bmad-output/?\b',
        # Paths
        r'\b[\w.-]+/[\w.-]+(?:/[\w.-]+)*\b/?',
        r'\b[a-zA-Z]:\\[\w.-\\]*\b',
        r'\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\(\)',
        # Normative values
        r'\b(?i:must|never|deve|não|somente|obrigatório)\b',
    ]
    combined = re.compile('|'.join(patterns), re.MULTILINE)
    return combined.findall(text)

def extract_inline_elements(text):
    """
    Extracts inline code blocks, links, placeholders, and XML/HTML tags.
    """
    inline_codes = re.findall(r'`([^`\n]+)`', text)
    links = re.findall(r'\[([^\]]*)\]\(((?:\([^)]*\)|[^)])+)\)', text)
    placeholders = re.findall(r'(\{\{[\w.-]+\}\}|\{[\w.-]+\}|\$\{[\w_]+\})', text)
    tags = re.findall(r'(<[^>]+>)', text)
    return {
        "inline_codes": inline_codes,
        "links": links,
        "placeholders": placeholders,
        "tags": tags,
    }

def split_table_row(row_text):
    parts = []
    current = []
    in_code = False
    i = 0
    while i < len(row_text):
        char = row_text[i]
        if char == '`':
            in_code = not in_code
            current.append(char)
        elif char == '|' and not in_code:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    parts.append("".join(current))
    return parts

def clean_comments(text):
    def repl(m):
        comment = m.group(0)
        if classify_comment(comment) in ["decorative", "generated"]:
            return ""
        return comment
    return re.sub(r'<!--([\s\S]*?)-->', repl, text)

def normalize_spaces(text):
    return re.sub(r'\s+', ' ', text.strip())

def validate_semantics(original_text, minified_text, dictionary=None):
    """
    Validates that the minified Markdown is semantically and structurally equivalent to the original.
    """
    try:
        orig_has_fm = has_frontmatter_at_document_start(original_text)
        mini_has_fm = has_frontmatter_at_document_start(minified_text)
        if orig_has_fm != mini_has_fm:
            return False, f"Frontmatter presence mismatch: original {orig_has_fm} vs minified {mini_has_fm}"

        original_clean = clean_comments(original_text)
        minified_clean = clean_comments(minified_text)

        decompiled_text = minified_clean
        if decompiled_text.startswith("> 🤖 AI RAG DICT: "):
            parts = decompiled_text.split("\n\n", 1)
            if len(parts) > 1:
                decompiled_text = parts[1]

        if dictionary:
            pm = ProtectedRegionsManager()
            protected_text = pm.protect(decompiled_text)
            sorted_dict = sorted(dictionary.items(), key=lambda x: len(x[0]), reverse=True)
            for original_word, alias in sorted_dict:
                pattern = re.compile(rf'\b{re.escape(alias)}\b')
                protected_text = pattern.sub(original_word, protected_text)
            decompiled_text = pm.restore(protected_text)

        orig_blocks = parse_markdown(original_clean)
        decomp_blocks = parse_markdown(decompiled_text)

        # 0. Check YAML frontmatter syntax/duplicates first
        for blocks in [orig_blocks, decomp_blocks]:
            fm_blocks = [b for b in blocks if b.type == "frontmatter"]
            for b in fm_blocks:
                try:
                    parse_yaml_frontmatter_safe(b.content)
                except Exception as e:
                    return False, f"YAML frontmatter error: {e}"

        # 1. Compare inline elements first for explicit test cases
        orig_inline = extract_inline_elements(original_clean)
        decomp_inline = extract_inline_elements(decompiled_text)

        for key in ["inline_codes", "links", "placeholders", "tags"]:
            orig_items = orig_inline[key]
            decomp_items = decomp_inline[key]
            if len(orig_items) != len(decomp_items):
                return False, f"Inline element '{key}' count mismatch: original {len(orig_items)} vs minified {len(decomp_items)}"
            for i, (oi, di) in enumerate(zip(orig_items, decomp_items)):
                if oi != di:
                    return False, f"Inline element '{key}' mismatch at index {i}: '{oi}' vs '{di}'"

        # 2. Check all protected elements (fences, URLs, paths, normative values)
        orig_all_prot = extract_all_protected_elements(original_clean)
        decomp_all_prot = extract_all_protected_elements(decompiled_text)
        if len(orig_all_prot) != len(decomp_all_prot):
            return False, f"Protected elements count mismatch: original {len(orig_all_prot)} vs minified {len(decomp_all_prot)}"
        for i, (oi, di) in enumerate(zip(orig_all_prot, decomp_all_prot)):
            if oi != di:
                return False, f"Protected element mismatch at index {i}: '{oi}' vs '{di}'"

        # 3. Parse blocks
        orig_filtered = [b for b in orig_blocks if b.type != "blank" and b.type != "comment"]
        decomp_filtered = [b for b in decomp_blocks if b.type != "blank" and b.type != "comment"]

        orig_filtered += [b for b in orig_blocks if b.type == "comment" and classify_comment(b.content) in ["operational", "unknown"]]
        decomp_filtered += [b for b in decomp_blocks if b.type == "comment" and classify_comment(b.content) in ["operational", "unknown"]]

        orig_filtered = sorted(orig_filtered, key=lambda b: original_clean.find(b.content))
        decomp_filtered = sorted(decomp_filtered, key=lambda b: decompiled_text.find(b.content))

        if len(orig_filtered) != len(decomp_filtered):
            return False, f"Block structure mismatch: original count {len(orig_filtered)} vs minified count {len(decomp_filtered)}"

        for idx, (ob, db) in enumerate(zip(orig_filtered, decomp_filtered)):
            if ob.type != db.type:
                return False, f"Block type mismatch at index {idx}: original '{ob.type}' vs minified '{db.type}'"

            if ob.type == "frontmatter":
                try:
                    orig_fm = parse_yaml_frontmatter_safe(ob.content)
                    decomp_fm = parse_yaml_frontmatter_safe(db.content)
                except Exception as e:
                    return False, f"YAML frontmatter error at index {idx}: {e}"
                if set(orig_fm.keys()) != set(decomp_fm.keys()):
                    return False, f"Frontmatter keys mismatch: original {orig_fm.keys()} vs minified {decomp_fm.keys()}"
                for k, v in orig_fm.items():
                    if v != decomp_fm[k]:
                        return False, f"Frontmatter value mismatch for key '{k}': '{v}' vs '{decomp_fm[k]}'"

            elif ob.type == "header":
                if normalize_spaces(ob.content) != normalize_spaces(db.content):
                    return False, f"Header content mismatch at index {idx}: '{ob.content.strip()}' vs '{db.content.strip()}'"

            elif ob.type == "code_block":
                if ob.metadata.get("lang") != db.metadata.get("lang"):
                    return False, f"Code block language mismatch at index {idx}: '{ob.metadata.get('lang')}' vs '{db.metadata.get('lang')}'"
                if ob.content.strip() != db.content.strip():
                    return False, f"Code block content mismatch at index {idx}"

            elif ob.type == "table":
                orig_lines = [line.strip() for line in ob.content.splitlines() if line.strip()]
                decomp_lines = [line.strip() for line in db.content.splitlines() if line.strip()]
                if len(orig_lines) != len(decomp_lines):
                    return False, f"Table line count mismatch at index {idx}"
                for row_idx, (ol, dl) in enumerate(zip(orig_lines, decomp_lines)):
                    if row_idx == 1:
                        o_sep = [c.strip() for c in split_table_row(ol) if c.strip()]
                        d_sep = [c.strip() for c in split_table_row(dl) if c.strip()]
                        if o_sep != d_sep:
                            return False, f"Table columns alignment mismatch at index {idx}"
                    else:
                        o_cells = [c.strip() for c in split_table_row(ol) if c.strip()]
                        d_cells = [c.strip() for c in split_table_row(dl) if c.strip()]
                        if o_cells != d_cells:
                            return False, f"Table cells content mismatch at index {idx}, row {row_idx}"

            elif ob.type == "list":
                orig_lines = [line for line in ob.content.splitlines() if line.strip()]
                decomp_lines = [line for line in db.content.splitlines() if line.strip()]
                if len(orig_lines) != len(decomp_lines):
                    return False, f"List items count mismatch at index {idx}"
                for item_idx, (ol, dl) in enumerate(zip(orig_lines, decomp_lines)):
                    o_depth = len(ol) - len(ol.lstrip())
                    d_depth = len(dl) - len(dl.lstrip())
                    if o_depth != d_depth:
                        return False, f"List item indentation depth mismatch at index {idx}, item {item_idx}"
                    o_m = re.match(r'^\s*([-*+]|\d+\.)\s+', ol)
                    d_m = re.match(r'^\s*([-*+]|\d+\.)\s+', dl)
                    if not o_m or not d_m or o_m.group(1) != d_m.group(1):
                        return False, f"List item marker mismatch at index {idx}, item {item_idx}"
                    o_txt = ol.lstrip()[len(o_m.group(0).lstrip()):].strip()
                    d_txt = dl.lstrip()[len(d_m.group(0).lstrip()):].strip()
                    if normalize_spaces(o_txt) != normalize_spaces(d_txt):
                        return False, f"List item content mismatch at index {idx}, item {item_idx}"

            elif ob.type == "blockquote":
                o_txt = re.sub(r'^>\s*', '', ob.content.strip())
                d_txt = re.sub(r'^>\s*', '', db.content.strip())
                if normalize_spaces(o_txt) != normalize_spaces(d_txt):
                    return False, f"Blockquote mismatch at index {idx}"

            elif ob.type == "comment":
                if ob.content.strip() != db.content.strip():
                    return False, f"Operational comment content mismatch at index {idx}"

            elif ob.type == "paragraph":
                if normalize_spaces(ob.content) != normalize_spaces(db.content):
                    return False, f"Paragraph mismatch at index {idx}"

        return True, "Success"

    except Exception as e:
        return False, f"Semantic validator internal error: {e}"
