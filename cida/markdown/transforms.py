import re

def remove_html_comments(text: str) -> str:
    comments = re.findall(r'<!--(.*?)-->', text, flags=re.DOTALL)
    result = text
    for c in comments:
        if any(w in c for w in ["stepsCompleted", "workflowType", "inputDocuments", "nextStepFile", "outputFile"]):
            continue
        result = result.replace(f"<!--{c}-->", "")
    return result

def trim_trailing_whitespace(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())

def normalize_newlines(text: str) -> str:
    return re.sub(r'\n{3,}', '\n\n', text)

def table_whitespace(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.strip().startswith('|') and line.strip().endswith('|'):
            parts = line.split('|')
            new_parts = [p.strip() for p in parts]
            lines.append('|'.join(new_parts))
        else:
            lines.append(line)
    return '\n'.join(lines)

def list_compaction(text: str) -> str:
    from cida.markdown.parser import parse_markdown
    blocks = parse_markdown(text)
    new_text = []
    for b in blocks:
        if b.type == "list":
            lines = [line for line in b.content.splitlines(keepends=True) if line.strip()]
            new_text.append("".join(lines))
        else:
            new_text.append(b.content)
    return "".join(new_text)

def minificar_codigo_para_ia(codigo_fonte: str, dicionario=None) -> str:
    codigo = re.sub(r'/\*.*?\*/', '', codigo_fonte, flags=re.DOTALL)
    codigo = re.sub(r'//.*', '', codigo)
    codigo = re.sub(r'package\s+[\w\.]+;', '', codigo)
    codigo = re.sub(r'import\s+(?:static\s+)?[\w\.\*]+;', '', codigo)
    codigo = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', codigo)
    codigo = re.sub(r'\b(System\.out\.\w+|console\.\w+|logger\.\w+|print|Log\.\w+|trace|debug|error|assert)\s*\([^)]*\);?', '', codigo)
    codigo = re.sub(r'(["\']).{15,}?\1', '""', codigo)
    codigo = re.sub(r'\b(public|private|protected|export|final|volatile|strictfp)\s+', '', codigo)
    codigo = re.sub(r'\b(this|self)\.', '', codigo)
    codigo = re.sub(r'\s+', ' ', codigo)
    codigo = re.sub(r'\s*([+\-*/%&|<>!^~?:;,{}()\[\]=]+)\s*', r'\1', codigo)
    if dicionario:
        for palavra, id_token in sorted(dicionario.items(), key=lambda x: len(x[0]), reverse=True):
            codigo = re.sub(rf'\b{re.escape(palavra)}\b', id_token, codigo)
    return codigo.strip()
