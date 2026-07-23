# Oportunidades de melhoria

| ID | Melhoria | Problema resolvido | Ganho esperado | Risco | Complexidade | Prioridade |
| --- | --- | --- | --- | --- | --- | --- |
| MC-01 | Aliases token-aware | Substituir ordenacao freq*chars por ganho liquido por token | ate 50586 tokens no corpus misto; apenas 8 tokens comprovados nos arquivos reais medidos | LOW | M | P1 |
| MC-02 | Limiar adaptativo | Ignorar dicionario em micro/pequenos sem break-even | evita inflacao; ganho indireto medido por zero-overhead | LOW | S | P1 |
| MC-03 | Sidecar compacto | JSON minificado ou arrays versionados | reduz overhead de sidecar observado | MEDIUM | M | P2 |
| MC-04 | Dicionario hibrido | Termos globais + locais com sidecars separados | 50915 (10.45%) | MEDIUM | L | P2 |
| MC-05 | N-grams/frases | Simular e aceitar apenas frases com ganho liquido, sem sobreposicao e com sidecar/round trip reais | 280520 tokens simulados; ganho produtivo nao comprovado | MEDIUM | L | P3 |
| MC-06 | Parsing unico | Compartilhar AST/regioes protegidas entre transformacoes | 1.267s parsing medido | LOW | M | P1 |
| MC-07 | Tokenizacao em lote/cache SHA | Evitar contagens repetidas | 17206 chamadas; 31.913s | LOW | M | P1 |
| MC-08 | Compressao dentro de codigo | Processar fences por perfil separado | nao recomendado sem contrato novo | HIGH | L | P4 |

Observacao: os percentuais agregados nao devem ser usados como ganho produtivo dos arquivos reais. A medicao real separada foi 8/4504 tokens (0.1776%); a medicao sintetica foi 50578/482953 tokens (10.4727%).
