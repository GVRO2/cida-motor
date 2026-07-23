# Diagnóstico da Arquitetura Python Atual - CIDA Motor

Este documento mapeia o estado da arquitetura Python do projeto CIDA Motor antes da refatoração em Clean Architecture.

---

## 1. Inventário de Módulos

### Módulo: `token_optimizer.py` (Root)
* **Responsabilidades:**
  * Ponto de entrada CLI e orquestração do pipeline de minificação/otimização.
  * Coleta de arquivos na pasta de origem (filtra md, txt, binários).
  * Execução dos transformadores em sequência.
  * Validação semântica e decisão final de minificação/substituição baseada em economia líquida.
  * Geração do relatório de benchmark.
* **Funções Públicas:** `optimize_markdown_dictionary_file_scope`, `minificar_codigo_para_ia`, `detect_profile`, `remove_html_comments`, `trim_trailing_whitespace`, `normalize_newlines`, `table_whitespace`, `list_compaction`, `build_corpus_dictionary`, `is_binary_file`, `verify_destination_sidecars`, `main`.
* **Dependências Internas:** `markdown.protected_regions`, `markdown.phrase_dictionary`, `markdown.semantic_validator`, `markdown.report`, `markdown.sidecar`.
* **Dependências Externas:** `os`, `sys`, `argparse`, `time`, `json`, `re`, `shutil`, `hashlib`.
* **Acesso ao Filesystem/Ambiente:** Alto (leitura de arquivos, escrita de minificados, cópia de binários, escrita de sidecars, deleção de arquivos temporários, etc.).
* **Regras de Negócio Misturadas:** Decisões de métricas (bruto vs líquido), políticas de aceitação/rejeição de otimização, e orquestração de CLI.
* **Tratamento de Erros:** Captura genérica de `Exception`. Chama `sys.exit()` com códigos 1, 3, 5.
* **Cobertura por Testes:** Alta via `test_token_optimizer.py`.

### Módulo: `token_counter.py` (Root)
* **Responsabilidades:**
  * Wrapper CLI fino para contar tokens a partir do `stdin` usando tiktoken.
* **Funções Públicas:** Nenhuma (executável como script).
* **Dependências Internas:** `markdown.phrase_dictionary`.
* **Dependências Externas:** `sys`, `os`.
* **Acesso ao Filesystem/Ambiente:** Modifica `sys.path`.
* **Tratamento de Erros:** `sys.exit(2)` em caso de falha de inicialização do tiktoken.

### Módulo: `translate.py` (Root)
* **Responsabilidades:**
  * Mapeia aliases minificados para as strings originais usando sidecars.
* **Funções Públicas:** `reject_duplicate_keys`, `translate`.
* **Dependências Internas:** Nenhuma.
* **Dependências Externas:** `os`, `sys`, `json`.
* **Acesso ao Filesystem/Ambiente:** Lê diretório `tknd/`.
* **Tratamento de Erros:** `sys.exit(5)` em caso de erros de leitura de sidecars.

### Módulo: `markdown/block_parser.py`
* **Responsabilidades:**
  * Parsing de blocos Markdown determinístico.
* **Classes/Funções Públicas:** `UniqueKeyLoader`, `Block`, `find_frontmatter_end`, `has_frontmatter_at_document_start`, `parse_markdown`.
* **Dependências Internas:** Nenhuma.
* **Dependências Externas:** `re`, `yaml`.
* **Efeitos Colaterais:** Nenhum (puro).

### Módulo: `markdown/phrase_dictionary.py`
* **Responsabilidades:**
  * Interface com tiktoken.
  * Geração de aliases lexicográficos determinísticos.
  * Construção e aplicação de dicionários locais/corpus.
* **Classes/Funções Públicas:** `TokenizerError`, `verify_tokenizer_cache`, `get_encoder`, `count_tokens`, `generate_alias_candidates`, `find_candidate_words`, `build_file_dictionary`, `apply_dictionary`.
* **Dependências Internas:** Nenhuma.
* **Dependências Externas:** `re`, `string`, `sys`, `os`, `hashlib`, `tiktoken`.
* **Acesso ao Filesystem/Ambiente:** Acessa `os.environ["TIKTOKEN_CACHE_DIR"]` e valida tamanho/hash do arquivo de cache.
* **Efeitos Colaterais:** `sys.exit(2)` em falha do tiktoken.

### Módulo: `markdown/protected_regions.py`
* **Responsabilidades:**
  * Proteção e restauração de blocos de código e regex patterns normativos antes de minificar.
* **Classes/Funções Públicas:** `ProtectedRegionsManager`.
* **Dependências Internas:** `markdown.block_parser`.
* **Dependências Externas:** `re`.
* **Efeitos Colaterais:** Nenhum.

### Módulo: `markdown/report.py`
* **Responsabilidades:**
  * Acúmulo de métricas e geração de relatórios (Markdown e JSON).
* **Classes/Funções Públicas:** `ReportGenerator`.
* **Dependências Internas:** Nenhuma.
* **Dependências Externas:** `json`, `os`, `re`.
* **Acesso ao Filesystem/Ambiente:** Cria diretórios e grava relatórios físicos. Valida caminhos para evitar leak de caminhos absolutos.

### Módulo: `markdown/sidecar.py`
* **Responsabilidades:**
  * Validação de schema, integridade SHA-256 e gravação/leitura de sidecars `.cidatkn`.
* **Classes/Funções Públicas:** `SidecarValidationError`, `calculate_sha256`, `create_sidecar_data`, `validate_sidecar_schema`, `validate_sidecar`, `reject_duplicate_keys`, `read_sidecar`, `write_sidecar`.
* **Dependências Internas:** Nenhuma.
* **Dependências Externas:** `json`, `hashlib`, `os`, `re`.
* **Acesso ao Filesystem/Ambiente:** Gravação e leitura física de arquivos JSON.

### Módulo: `markdown/semantic_validator.py`
* **Responsabilidades:**
  * Validador semântico estrito. Garante invariância estrutural (mesma quantidade de headers, blocos de código, tabelas, links, etc).
* **Classes/Funções Públicas:** `UniqueKeyLoader`, `parse_yaml_frontmatter_safe`, `parse_yaml_frontmatter`, `extract_all_protected_elements`, `extract_inline_elements`, `split_table_row`, `classify_comment`, `clean_comments`, `normalize_spaces`, `validate_semantics`.
* **Dependências Internas:** `markdown.block_parser`, `markdown.sidecar`.
* **Dependências Externas:** `re`, `yaml`, `json`.
* **Efeitos Colaterais:** Nenhum.

---

## 2. Grafo de Dependências Conceitual

```
token_optimizer.py (CLI/Orchestrator)
 ├── markdown.protected_regions
 │    └── markdown.block_parser
 ├── markdown.phrase_dictionary
 ├── markdown.semantic_validator
 │    ├── markdown.block_parser
 │    └── markdown.sidecar
 ├── markdown.report
 └── markdown.sidecar
```

---

## 3. Violações de Arquitetura Identificadas

1. **God Module:** `token_optimizer.py` acumula CLI, parsing de argumentos, IO físico (criação de diretórios, cópia de binários, exclusão de lixo temporário), transformação de dados, validações e decisões de políticas de ganho.
2. **sys.exit() no Core e Módulos de Domínio:** `phrase_dictionary.py` (exit 2) e `translate.py` (exit 5) chamam `sys.exit` diretamente, violando o princípio de que efeitos de borda ficam na CLI.
3. **IO Físico Acoplado no Domínio/Aplicação:**
   * `sidecar.py` possui funções `read_sidecar` e `write_sidecar` que dependem de IO físico (`open`, `os.path.exists`, `os.makedirs`).
   * `report.py` grava arquivos físicos (`save_reports`).
   * `phrase_dictionary.py` lê cache físico no disco e acessa variável de ambiente `TIKTOKEN_CACHE_DIR`.
4. **Acoplamento Temporal e Estado Global:** `token_counter.py` altera dinamicamente `sys.path`.
5. **Falta de Abstrações para Infraestrutura (Ports & Adapters):** O tiktoken, o sistema de arquivos e o gerador de hash SHA-256 são importados e usados diretamente no domínio, impedindo testes isolados via injeção de dependência.

---

## 4. Riscos de Regressão

* **Byte-perfect Round Trip:** Qualquer mudança nos transformadores ou na lógica de substituição de aliases pode corromper a compatibilidade de re-tradução de sidecars.
* **Determinismo de Relatórios/Manifestos:** As ordenações lexicográficas de chaves no dicionário do corpus e nos sidecars devem ser rigidamente mantidas para manter hashes estáveis entre Windows e Linux.
* **Integração Go → Python:** O executável Go chama scripts individuais no raiz (`token_optimizer.py`, `token_counter.py`, `translate.py`) e espera argumentos e saídas com códigos de saída exatos (`0` a `6`).
