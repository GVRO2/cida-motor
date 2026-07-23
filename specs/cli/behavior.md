# Especificação: CLI (Motor de Minificação)

## Objetivo
Interface de linha de comando para invocar o motor de minificação e monitoramento.

## Comportamento Esperado
- **Entrada:** Receber o caminho do diretório original como argumento obrigatório.
- **Saída:** Gerar a pasta minificada (opcional ou padrão sufixado com `_mimificado`).
- **Modo Watcher:** Suportar a flag `--watcher` (ou `-watch`) para monitorar mudanças no diretório original e re-executar a minificação.
- **Feedback:** Deve imprimir o progresso da análise e os resultados do benchmark (tokens originais vs minificados, economia).
- **Modo Dry Run:** `--dry-run` não realiza nenhuma gravação em disco.
- **Relatórios:** `--report` suporta `text`, `json`, `both`.

## Códigos de Saída (Exit Codes)
- `0` = sucesso
- `1` = erro de uso/argumentos (inclusive flag desconhecida)
- `2` = erro de tokenizer ou dependência obrigatória
- `3` = falha de validação semântica
- `4` = origem ou caminho inválido
- `5` = sidecar inválido ou incompatível
- `6` = erro interno ou falha de determinismo
