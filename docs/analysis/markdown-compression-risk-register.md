# Registro de riscos

| ID | Risco | Severidade | Mitigacao |
| --- | --- | --- | --- |
| R-01 | Transformacoes estruturais nao sao reversiveis byte a byte sem sidecar de patches | HIGH | Adicionar contrato explicito ou sidecar de reversao |
| R-02 | Parser falha em fences/frontmatter invalidos e pode rejeitar ganho seguro | MEDIUM | Tratar parse error como zona protegida total |
| R-03 | Sidecar JSON com entries objeto nao detecta chaves duplicadas no parse JSON padrao | MEDIUM | Decoder com object_pairs_hook para duplicatas |
| R-04 | Alias curto pode ter tokenizacao ruim e colisao contextual | MEDIUM | Gerador token-aware + validacao contra regioes protegidas |
| R-05 | Regex em entradas grandes pode custar caro | LOW | Benchmarks adversariais e limites de tamanho |
| R-06 | Ubuntu nao foi executado nesta rodada | INFORMATIONAL | CI matrix Windows/Ubuntu com hash normalizado |
| R-07 | Resultado agregado mascara ganho quase nulo nos arquivos reais | HIGH | Relatar sempre real e sintetico separadamente |
| R-08 | Simulacao de frases usa n-grams sobrepostos sem aplicacao, sidecar ou round trip reais | HIGH | Manter como experimento ate haver implementacao reversivel e validada |
