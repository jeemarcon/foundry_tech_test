# Data Quality Log

Registro de problemas de qualidade de dados identificados durante o desenvolvimento do pipeline, causa raiz e solução aplicada.

## DQ-001 — Traduções de descrição faltantes para alguns livros

**Identificado em:** 2026-08-27, execução de `make translate-descriptions`

**Sintoma:** Nem todos os livros tiveram suas descrições traduzidas para EN/ES/FR —
alguns registros ficaram com campos de tradução nulos no `description_translations.json`,
sem nenhuma sinalização de que houve falha.

**Causa raiz:** O script `05_translate_descriptions.py` dispara 3 chamadas ao LLM por
livro (uma por idioma) em sequência, sem pausa entre elas. Com 10 livros, isso
ultrapassa o limite de 15 requisições/minuto do tier gratuito da Gemini API (erro 429).
O script não trata esse erro — descarta a tentativa e segue em frente.

**Evidência observada:**
```
LLM error: Error code: 429 - You exceeded your current quota [...]
limit: 15, model: gemini-3.5-flash-lite, quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier
Please retry in 49.484914876s.
```

**Pilares afetados:** Data Quality (dado incompleto sem sinalização) e Scalability
(rate limiting/backpressure ausente, apesar de não ser pilar formal desta entrega).

**Solução — Data Quality** (`feature/data-quality`): sinalização explícita de
status/reason por registro em `03_describe.py`, e status/reason por idioma +
`translation_complete` agregado em `05_translate_descriptions.py`, em vez de
falha silenciosa. *(concluído)*

**Solução — Scalability** (`extra/scalability-rate-limiting`): pausa/throttling entre
chamadas ao LLM para respeitar o limite de requisições por minuto. *(a construir)*