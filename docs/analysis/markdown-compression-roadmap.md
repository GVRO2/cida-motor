# Roadmap tecnico

## Fase 1 - medicoes e quick wins

Objetivo: garantir metricas corretas e ganhos liquidos. Mudancas: aliases token-aware, limiar adaptativo, cache por SHA. Testes: break-even, roundtrip, dictionary efficiency. Rollback: estrategia sem dicionario.

Aceite adicional: todo relatorio deve separar arquivos reais e sinteticos e deve impedir que o resultado agregado de 10.38% seja apresentado como ganho produtivo geral.

## Fase 2 - melhorias estruturais

Objetivo: reduzir overhead e custo. Mudancas: dicionario hibrido, sidecar compacto, parsing unico, tokenizacao em lote. Testes: determinismo e benchmark corpus. Rollback: feature flags por estrategia.

## Fase 3 - experimentos

Objetivo: validar frases e perfis adaptativos. Mudancas: n-grams simulados, paralelismo deterministico. Testes: ablation e adversariais. Rollback: manter apenas simulacao ate provar ganho.

Aceite adicional: frases so podem sair do modo experimental com aplicacao real, sidecar real, eliminacao de dupla contagem por n-grams sobrepostos e round trip validado.
