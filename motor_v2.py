import tiktoken
import os
import re
import sys
from collections import Counter

def get_b16_id(n):
    prefixChars = "ABCDEF"
    allChars = "0123456789ABCDEF"
    if n < len(prefixChars):
        return prefixChars[n]
    n -= len(prefixChars)
    if n < 6*16:
        return prefixChars[n//16] + allChars[n%16]
    n -= 6 * 16
    return prefixChars[(n//(16*16))%len(prefixChars)] + allChars[(n//16)%16] + allChars[n%16]

def main():
    if len(sys.argv) < 2:
        print("Uso: python motor_v2.py <caminho_da_pasta>")
        return
    pasta_orig = sys.argv[1]
    processar_e_comparar(pasta_orig, pasta_orig + "_mimificado", ".java", ".tknc")

if __name__ == "__main__":
    main()

def estimar_tokens(texto):
    if not texto:
        return 0
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(texto))

def calcular_ter(tokens, caracteres):
    return tokens / caracteres if caracteres > 0 else 0

def eh_arquivo_de_teste(root, file, pasta_orig):
    caminho_relativo = os.path.relpath(root, pasta_orig).lower()
    partes = caminho_relativo.split(os.sep)
    nome_arquivo = file.lower()
    if "test" in partes or "tests" in partes or "teste" in partes or "testes" in partes:
        return True
    if "test" in nome_arquivo or "teste" in nome_arquivo:
        return True
    return False

def construir_dicionario_rag(pasta_orig, pasta_comp, ext_orig, limite_palavras=300):
    contador = Counter()
    for root, dirs, files in os.walk(pasta_orig):
        for file in files:
            if eh_arquivo_de_teste(root, file, pasta_orig):
                continue
            if not ext_orig or file.endswith(ext_orig):
                caminho = os.path.join(root, file)
                try:
                    with open(caminho, 'r', encoding='utf-8', errors='ignore') as f:
                        palavras = re.findall(r'\b[a-zA-Z_]{6,}\b', f.read())
                        contador.update(palavras)
                except OSError:
                    pass

    palavras_impacto = sorted(contador.items(), key=lambda x: x[1] * len(x[0]), reverse=True)

    dicionario = {}
    os.makedirs(os.path.join(pasta_comp, "tknd"), exist_ok=True)

    # Agrupar em arquivos de 500
    for i in range(0, len(palavras_impacto), 500):
        end = min(i + 500, len(palavras_impacto))
        start_id = get_b16_id(i)

        with open(os.path.join(pasta_comp, "tknd", f"{start_id}.cidatkn"), 'w', encoding='utf-8') as fd:
            for j in range(i, end):
                palavra, freq = palavras_impacto[j]
                if freq >= 3:
                    id_token = get_b16_id(j)
                    dicionario[palavra] = id_token
                    fd.write(f"{id_token}={palavra}\n")
    return dicionario

def minificar_codigo_para_ia(codigo_fonte, dicionario=None):
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

def processar_e_comparar(pasta_orig, pasta_comp, ext_orig, ext_comp):
    print("⏳ Analisando frequências e construindo dicionário...")
    dicionario_rag = construir_dicionario_rag(pasta_orig, pasta_comp, ext_orig)

    total_tokens_orig = 0
    total_tokens_comp = 0
    total_arquivos = 0

    for root, dirs, files in os.walk(pasta_orig):
        for file in files:
            if eh_arquivo_de_teste(root, file, pasta_orig):
                continue
            if not ext_orig or file.endswith(ext_orig):
                caminho_orig = os.path.join(root, file)
                caminho_relativo = os.path.relpath(root, pasta_orig)
                arquivo_alvo = f"{file}{ext_comp}"
                diretorio_alvo = os.path.normpath(os.path.join(pasta_comp, caminho_relativo))
                os.makedirs(diretorio_alvo, exist_ok=True)
                caminho_comp_arquivo = os.path.join(diretorio_alvo, arquivo_alvo)

                try:
                    with open(caminho_orig, 'r', encoding='utf-8', errors='ignore') as f1:
                        conteudo_orig = f1.read()
                    conteudo_comp = minificar_codigo_para_ia(conteudo_orig, dicionario_rag)
                    with open(caminho_comp_arquivo, 'w', encoding='utf-8', errors='ignore') as f2:
                        f2.write(conteudo_comp)
                    total_arquivos += 1
                    total_tokens_orig += estimar_tokens(conteudo_orig)
                    total_tokens_comp += estimar_tokens(conteudo_comp)
                except Exception as e:
                    print(f"Erro: {e}")
    print("✓ Processamento concluído.")
