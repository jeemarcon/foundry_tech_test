# Data Quality Log

Registro de problemas de qualidade de dados identificados durante o desenvolvimento do pipeline, causa raiz e solução aplicada.

## DQ-001 — Traduções de descrição faltantes para alguns livros

**Identificado em:** 2026-08-27, execução de `make translate-descriptions`

**Sintoma:** Nem todos os livros tiveram suas descrições traduzidas para EN/ES/FR — alguns registros ficaram com campos de tradução nulos no `description_translations.json`, sem nenhuma sinalização de que houve falha.

**Causa raiz:** O script `05_translate_descriptions.py` dispara 3 chamadas ao LLM por livro (uma por idioma) em sequência, sem pausa entre elas. Com 10 livros, isso ultrapassa o limite de 15 requisições/minuto do tier gratuito da Gemini API (erro 429). O script não trata esse erro — descarta a tentativa e segue em frente.

**Evidência observada:**
```
LLM error: Error code: 429 - You exceeded your current quota [...]
limit: 15, model: gemini-3.5-flash-lite, quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier
Please retry in 49.484914876s.
```

**Pilares afetados:** Data Quality (dado incompleto sem sinalização) e Scalability (rate limiting/backpressure ausente).

**Solução — Data Quality** (`feature/data-quality`): sinalização explícita de status/reason por registro em `03_describe.py`, e status/reason por idioma + `translation_complete` agregado em `05_translate_descriptions.py`, em vez de falha silenciosa. *(concluído)*

**Solução — Scalability** (`extra/scalability-rate-limiting`): pausa/throttling entre chamadas ao LLM para respeitar o limite de requisições por minuto. *(a construir)*

## DQ-002 — Ranking de "mais acessados" é afetado pela própria raspagem (01_download.py)

**Identificado em:** 2026-08-27, executando `01_download.py` duas vezes seguidas, sem apagar o `catalog.json` gerado pela primeira execução.

**Sintoma:** Nenhum efeito visível ainda — os mesmos 10 livros voltaram, na mesma ordem, nas duas execuções. O que chamou atenção foi o campo `accesses`: subiu exatamente +1, em todos os dez livros, entre a primeira e a segunda execução.

**Causa raiz:** O `LIST_URL` pede ao site os 10 livros mais acessados (`colunaOrdenar=NU_PAGE_HITS&ordem=desc`), ordenação feita pelo próprio servidor do domínio público. Ao visitar a página de detalhe de cada livro pra coletar metadados, o `01_download.py` conta como um acesso naquela página — incrementando o mesmo campo (`NU_PAGE_HITS`) usado como critério de ordenação da consulta seguinte. A raspagem influencia o próprio ranking que decide quais livros ela vai raspar da próxima vez.

**Evidência observada:** Duas execuções seguidas de `make download` retornaram os mesmos 10 códigos, na mesma ordem, mas com `accesses` incrementado em +1 para cada um dos dez livros na segunda execução.

**Pilares afetados:** Data Quality (reprodutibilidade do conjunto de dados raspado não é garantida ao longo do tempo, mesmo sem nenhuma aleatoriedade explícita no código).

**Solução:** Pular a busca da página de detalhe (`get_download_url_and_metadata`) para códigos que já existem no `metadata.json` salvo de uma execução anterior, reaproveitando os dados já coletados em vez de visitar a página de novo. A listagem (`LIST_URL`) continua sendo buscada a cada execução — ela não afeta o contador de acessos; só a visita à página de detalhe o faz. Essa correção não tem efeito na primeira execução, mas evita o incremento indevido em qualquer execução subsequente: reruns manuais durante desenvolvimento, testes, retomada após falha parcial no meio do processamento.

**Nota de escopo:** essa solução parte da premissa de que este pipeline roda como um instantâneo único, sem execução periódica agendada considerando o escopo do case ("process at least 10 books", sem menção a recorrência). Num cenário de execução periódica real, a resposta correta não seria voltar a sobrescrever o catálogo a cada execução (isso reintroduziria tanto o risco de órfãos quanto a poluição do contador de acessos do site), seria buscar a listagem numa cadência definida e mesclar os códigos novos com o catálogo existente, preservando os registros já conhecidos. (upsert muito provavelmente)