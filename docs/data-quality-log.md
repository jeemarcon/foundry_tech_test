# Data Quality Log

Registro de problemas de qualidade de dados identificados durante o desenvolvimento do pipeline, causa raiz e solução aplicada.

## DQ-001: Traduções de descrição faltantes para alguns livros

**Identificado em:** execução de `make translate-descriptions`

**Sintoma:** Nem todos os livros tiveram suas descrições traduzidas para EN/ES/FR: alguns registros ficaram com campos de tradução nulos no `description_translations.json`, sem nenhuma sinalização de que houve falha. O mesmo nulo, também sem sinalização, aparecia no `localized_catalog.json`, o output final exigido pelo case.

**Causa raiz:** O script `05_translate_descriptions.py` dispara 3 chamadas ao LLM por livro (uma por idioma) em sequência, sem pausa entre elas. Com 10 livros, isso ultrapassa o limite de 15 requisições/minuto do tier gratuito da Gemini API (erro 429). O script não trata esse erro, descarta a tentativa e segue em frente, deixando o campo daquele idioma nulo tanto no arquivo intermediário quanto, sem tratamento nenhum, no output final.

**Evidência observada:**
```
LLM error: Error code: 429 - You exceeded your current quota [...]
limit: 15, model: gemini-3.5-flash-lite, quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier
Please retry in 49.484914876s.
```

Exemplo do nulo no arquivo intermediário (código `19322`, `description_translations.json`):
```json
"es": {"text": null, "status": "Failed", "reason": "LLM_error"},
"fr": {"text": null, "status": "Failed", "reason": "LLM_error"}
```

**Pilares afetados:** Data Quality (dado incompleto sem sinalização) e Scalability (rate limiting/backpressure ausente).

**Solução (Data Quality, rastreabilidade):** sinalização explícita de status/reason por registro em `03_describe.py`, e status/reason por idioma + `translation_complete` agregado em `05_translate_descriptions.py`, em vez de falha silenciosa. *(concluído)*

**Solução (Data Quality, vazamento das colunas de rastreabilidade pro output final):** revisando o `localized_catalog.json` depois de pronto, notou-se que as próprias colunas de rastreabilidade criadas acima (`status`/`reason`) estavam se espelhando no output final, em vez de ficarem restritas ao arquivo intermediário. `07_localized_catalog.py` copiava `title`/`description` diretamente do JSON intermediário sem extrair o valor de texto de dentro do objeto de status/reason. PT (campo original, sem tradução) permanecia string simples, enquanto EN/ES/FR viravam objetos aninhados no output final, quebrando a consistência de tipo do schema:
```json
"description": {
  "pt": "[...]",
  "en": {"text": "[...]", "status": "Success", "reason": null},
  "es": {"text": null, "status": "Failed", "reason": "LLM_error"},
  "fr": {"text": null, "status": "Failed", "reason": "LLM_error"}
}
```
A lógica é a mesma do DQ-004: o valor ausente (`null`) é aceitável e correto no entregável final, quando a tradução de fato falhou. O  *porquê* da ausência é o que deve ficar registrado só na camada intermediária, não a estrutura de rastreabilidade em si. Corrigido com um helper `extract_text()` em `07_localized_catalog.py`, aplicado à extração de `title` e `description` em EN/ES/FR: retorna `value["text"]` quando o campo é um objeto de status/reason, ou o próprio valor quando já é string/null. `localized_catalog.json` volta a ter só valores de texto ou `null` em cada idioma, consistente com PT, sem carregar a estrutura de rastreabilidade até o entregável final. *(concluído)* Confirmado via reexecução manual: o código `19322` passou a mostrar `"es": null, "fr": null` no output final, em vez dos objetos aninhados.

**Solução (Scalability)** (`feature/scalability`): pausa entre chamadas ao LLM (`LLM_CALL_DELAY_SECONDS`, 4.5s) em `03_describe.py`/`04_translate.py`/`05_translate_descriptions.py`, para respeitar o limite de requisições por minuto. *(concluído)* Confirmado via reexecução de `make translate`: o código `18957`, que antes tinha `es` falhando com erro 503, passou a mostrar `es`/`fr` como `"status": "Success"`, e os 10 livros completaram sem nenhum erro de LLM.

Vale uma precisão sobre a causa, para não confundir os dois problemas: a pausa elimina erro 429 (cota excedida, a causa raiz original documentada acima), mas não elimina erro 503 (indisponibilidade momentânea do próprio provedor), que é uma falha diferente, fora do controle do cliente. Isso foi confirmado numa reexecução posterior com 20 livros (teste de escala do pilar Event-Driven): mesmo com a pausa ativa, uma chamada de descrição sofreu um erro 503 isolado. O pipeline isolou essa falha corretamente (o registro ficou `Failed`, e só a tradução de descrição daquele livro específico foi pulada), e uma nova execução completou o processamento sem nenhuma intervenção manual. Por isso a solução aqui não tenta prevenir 100% das falhas de LLM: ela remove a causa que estava sob controle (a cota) e conta com o modelo de isolamento e reprocessamento via status/reason (ver ADR 003) para absorver as falhas que não estão, uma postura mais realista para um serviço externo do que tentar blindar contra toda instabilidade momentânea do provedor.

## DQ-002: Ranking de "mais acessados" é afetado pela própria raspagem (01_download.py)

**Identificado em:** executando `01_download.py` duas vezes seguidas, sem apagar o `catalog.json` gerado pela primeira execução.

**Sintoma:** Nenhum efeito visível ainda: os mesmos 10 livros voltaram, na mesma ordem, nas duas execuções. O que chamou atenção foi o campo `accesses`: subiu exatamente +1, em todos os dez livros, entre a primeira e a segunda execução.

**Causa raiz:** O `LIST_URL` pede ao site os 10 livros mais acessados (`colunaOrdenar=NU_PAGE_HITS&ordem=desc`), ordenação feita pelo próprio servidor do domínio público. Ao visitar a página de detalhe de cada livro pra coletar metadados, o `01_download.py` conta como um acesso naquela página, incrementando o mesmo campo (`NU_PAGE_HITS`) usado como critério de ordenação da consulta seguinte. A raspagem influencia o próprio ranking que decide quais livros ela vai raspar da próxima vez.

**Evidência observada:** Duas execuções seguidas de `make download` retornaram os mesmos 10 códigos, na mesma ordem, mas com `accesses` incrementado em +1 para cada um dos dez livros na segunda execução.

**Pilares afetados:** Data Quality (reprodutibilidade do conjunto de dados raspado não é garantida ao longo do tempo, mesmo sem nenhuma aleatoriedade explícita no código).

**Solução:** Pular a busca da página de detalhe (`get_download_url_and_metadata`) para códigos que já existem no `metadata.json` salvo de uma execução anterior, reaproveitando os dados já coletados em vez de visitar a página de novo. A listagem (`LIST_URL`) continua sendo buscada a cada execução, ela não afeta o contador de acessos, só a visita à página de detalhe o faz. Essa correção não tem efeito na primeira execução, mas evita o incremento indevido em qualquer execução subsequente: reruns manuais durante desenvolvimento, testes, retomada após falha parcial no meio do processamento.

**Nota de escopo:** essa solução parte da premissa de que este pipeline roda como um instantâneo único, sem execução periódica agendada considerando o escopo do case ("process at least 10 books", sem menção a recorrência). Num cenário de execução periódica real, a resposta correta não seria voltar a sobrescrever o catálogo a cada execução (isso reintroduziria tanto o risco de órfãos quanto a poluição do contador de acessos do site), seria buscar a listagem numa cadência definida e mesclar os códigos novos com o catálogo existente, preservando os registros já conhecidos. (upsert muito provavelmente)

## DQ-003: Campo "size" com valor incorreto na origem (01_download.py)

**Identificado em:** revisão manual do `catalog.json` após a raspagem.

**Sintoma:** O livro de código `19322` ("Populações meridionais do Brasil") aparece com `size: "0.00 KB"` no `catalog.json`, apesar de ter sido baixado com sucesso (`downloaded: true`).

**Evidência observada:**

Entrada em `catalog.json`:
```json
{
  "code": "19322",
  "title": "Populações meridionais do Brasil",
  "author": "Oliveira Viana",
  "source": "[sf] Senado Federal",
  "format": ".pdf",
  "size": "0.00\r\n              KB",
  "accesses": "9,469",
  "download_url": "https://dominiopublico.mec.gov.br/pesquisa/DetalheObraDownload.do?select_action=&co_obra=19322&co_midia=2",
  "downloaded": true
}
```

Tamanho real do arquivo baixado, verificado via PowerShell:
```
Get-Item "data\pdfs\19322.pdf" | Select-Object Name, Length

Name       Length
----       ------
19322.pdf 1356796
```

1.356.796 bytes (~1,3 MB), muito distante do "0.00 KB" reportado pelo site.

**Causa raiz:** O campo `size` do `catalog.json` vem direto da coluna de tamanho da página de listagem do site (`parse_listing`, `cells[6]`), sem nenhuma validação. É um valor exibido pelo próprio domínio público, e está incorreto na origem pra esse registro específico, as não reflete o arquivo real.

**Pilares afetados:** Data Quality (dado presente e com formato válido, porém numericamente incorreto -> diferente de dado ausente). Também relevante porque `universal_metadata.json`, output final exigido pelo case, inclui "file size" como campo obrigatório: esse erro poderia vazar pro entregável final sem essa correção.

**Solução:** Não implementada, por decisão consciente. Investiguei o caminho até o output final (`universal_metadata.json`) e foi confirmado que o campo com defeito no `catalog.json` não é propagado, a etapa de montagem final recalcula `size_bytes` a partir do `hashes.json` (ou, na ausência, do tamanho real do arquivo em disco), evidenciado por `"size_bytes": 1356796` no output final, batendo exatamente com o tamanho real medido do arquivo. Como o raio de impacto está comprovadamente contido antes de chegar ao entregável exigido, optamos por documentar o achado sem investir tempo corrigindo o campo `size` do `catalog.json` diretamente. Priorização consciente diante do prazo do case, não uma omissão.

## DQ-004: Campo "year" ausente para livros não-teses (01_download.py)

**Identificado em:** revisão do `universal_metadata.json`, notando `year: null` em todos os 10 registros.

**Sintoma:** 100% dos livros no `universal_metadata.json` têm `year: null`.

**Causa raiz:** O `parse_detail_page` procura o rótulo "Ano da Tese" (Year of the Thesis) pra popular o campo `year`. Esse rótulo aparece no template da página de detalhe independente do tipo de obra, mas só é preenchido de fato pra teses acadêmicas. Verificado manualmente no site (código 15713 e outros): o rótulo aparece, mas o valor ao lado está em branco na própria origem: confirma que não é falha de extração, é ausência real de dado na fonte, pra esse tipo de acervo ("História").

**Pilares afetados:** Data Quality (avaliar se um dado ausente é defeito ou característica real da fonte, antes de tentar "corrigir").

**Solução:** Mantido `null` no output final (`universal_metadata.json`), decisão respaldada por pesquisa de boas práticas (ver fontes), que desaconselha preencher ausência real com um placeholder artificial. Adicionado, porém, um sinal leve e escopado no `metadata.json` intermediário (produzido pelo `01_download.py`): quando `year` não é extraído, `parse_detail_page` registra `year_status` como `"empty_in_source"` (rótulo "Ano da Tese" encontrado, valor em branco na origem, o caso confirmado aqui) ou `"label_not_found"` (rótulo nem apareceu, sinal de possível problema de extração real). Esse sinal não é propagado ao output final, servindo só como diagnóstico interno pra
evitar repetir a investigação manual no site caso o mesmo padrão apareça de novo.

**Fontes:**
- https://sqlpad.io/tutorial/fill-missing-values-sql-coalesce-window-functions/
- https://dqops.com/common-data-quality-issues/

## DQ-005: Conteúdo do PDF não validado antes de ser gravado em disco (01_download.py)

**Identificado em:** revisão de código, numa varredura sistemática por pontos de fragilidade de Data Quality no pipeline (não foi um incidente observado rodando o pipeline -> ver nota abaixo).

**Sintoma:** `download_pdf` valida só `status_code == 200` e `len(resp.content) > 1000` antes de gravar `{code}.pdf` e marcar `downloaded: True`. Não existe checagem de que o conteúdo baixado é, de fato, um PDF.

**Causa raiz:** o site tem proteção anti-bot, tratada em `fetch_page` (checagem de "challenge" no início do HTML), mas essa mesma proteção não tem equivalente em `download_pdf`. Se o endpoint de download responder com uma página de erro ou captcha em HTML, com status 200 e mais de 1000 bytes, esse conteúdo passa pelas duas validações existentes, é gravado como `{code}.pdf`, e o registro fica marcado como baixado com sucesso.

**Nota sobre a natureza do achado:** diferente de DQ-001 a DQ-004, esse não é um problema observado numa execução real do pipeline. Foi encontrado por revisão do código (com apoio do Claude Code), procurando deliberadamente por lacunas de validação antes de fechar o pilar de Data Quality. Registrado aqui como achado preventivo, não como incidente.

**Pilares afetados:** Data Quality (arquivo corrompido/inválido indistinguível de um arquivo saudável no restante do pipeline).

**Propagação (se não corrigido):** alta. O arquivo inválido seria hasheado normalmente em `02_hash.py` (hash de um HTML, não do livro), poderia gerar erro silencioso ou descrição de baixa qualidade em `03_describe.py`/`06_covers.py` (o `fitz` pode falhar ao abrir ou renderizar lixo), e em `08_universal_metadata.py` teria `document_hash` e `size_bytes` preenchidos normalmente, parecendo um registro saudável no output final exigido pelo case.

**Solução:** `download_pdf` passou a checar a assinatura do arquivo (`resp.content[:5] == b"%PDF-"`, os primeiros bytes de qualquer PDF válido) antes de gravar. Se a assinatura não bater, o conteúdo é descartado (nada é escrito em disco) e a função retorna `False`, deixando `downloaded: False` no `catalog.json`. Nenhum campo novo de status/reason foi necessário aqui: o mecanismo de retry em `main()` já decide se baixa de novo checando `pdf_path.exists()`, então bastou fazer essa checagem receber uma informação verdadeira. Antes, ela recebia um falso positivo. *(concluído)*

## DQ-006: Cache de capa trata falha como definitiva, sem status/reason (06_covers.py)

**Identificado em:** revisão de código, na mesma varredura sistemática do DQ-005 (achado preventivo, não incidente observado: conferido em `covers.json` real, os 10 livros extraíram capa com sucesso).

**Sintoma:** `06_covers.py` usa `if code in covers: continue` para decidir se já processou aquele livro. Quando `extract_cover` falha, o código grava `covers[code] = None`, que já é uma chave presente no dicionário. Na prática, uma falha vira permanente: nenhuma execução futura tenta extrair aquela capa de novo, mesmo que a causa tenha sido transitória (ou corrigida, como no caso do DQ-005).

**Causa raiz:** o mesmo padrão já corrigido em `04_translate.py` e presente originalmente em `05_translate_descriptions.py` antes do DQ-001: usar presença de chave como sinônimo de sucesso, em vez de checar o resultado da tentativa anterior. Aqui é mais pobre ainda, porque a falha nem carrega `status`/`reason`, só um `None` sem contexto.

**Pilares afetados:** Data Quality (retry quebrado e ausência de rastreabilidade sobre por que uma capa não foi extraída).

**Propagação:** alta e direta. `08_universal_metadata.py:48-49` faz `cover.get("path") if cover else None`, com `cover` igual a `None`, tanto `cover_path` quanto `cover_hash` viram `null` no `universal_metadata.json`, dois campos exigidos pelo case, indistinguíveis de "nunca foi tentado corretamente".

**Solução:** a condição de skip passou a checar `(covers.get(code) or {}).get("status") == "Success"`, em vez de só a presença da chave. Sucesso e falha agora gravam um objeto com `status` (`"Success"` ou `"Failed"`, com `reason: "extraction_error"` no segundo caso), no mesmo formato usado em `03_describe.py`/`04_translate.py`/`05_translate_descriptions.py`. Entradas antigas do formato anterior (sem `status`) são reprocessadas uma vez e migram sozinhas pro novo formato. `08_universal_metadata.py` não precisou de nenhuma mudança: o `cover` de uma falha continua sendo um dicionário truthy com `path`/`hash` em `None`, produzindo o mesmo `null` de antes no output final. *(concluído)*

## DQ-007: "Success" definido só por resposta não vazia, sem validar o conteúdo (03_describe.py)

**Identificado em:** revisão de código (achado preventivo: conferindo as 10 descrições reais em `descriptions.json`, nenhuma aciona o definido abaixo).

**Sintoma:** `descriptions[code]["status"] = "Success"` era decidido só por `if description:` (string não vazia), sem checar se o conteúdo faz sentido. Uma recusa da LLM de visão (ex.: "não consigo analisar esta imagem") ou uma resposta anormalmente curta contam como sucesso.

**Causa raiz:** a validação existente é de forma (a chamada retornou algo), não de conteúdo. Como `05_translate_descriptions.py` só olha `status == "Success"` da etapa anterior, uma descrição ruim seria traduzida para EN/ES/FR normalmente, multiplicando o dado de baixa qualidade em 4 idiomas no `localized_catalog.json`, todos marcados como sucesso do início ao fim.

**Pilares afetados:** Data Quality (validação de conteúdo, não só de forma, antes de propagar um dado como confiável).

**Escopo consciente:** validar semanticamente se uma descrição está correta não é viável no prazo do case (isso seria, na prática, outro classificador). A solução aqui é uma heurística barata, não uma prova de qualidade: tamanho mínimo de 40 caracteres e uma lista curta de frases de recusa conhecidas em PT/EN. Reduz o risco, não elimina.

**Decisão de design:** o achado da heurística vira um campo novo e separado, `quality_flag` (`"too_short"` ou `"possible_refusal"`), em vez de rebaixar `status` para `"Failed"`. `status` continua significando "a chamada ao LLM funcionou tecnicamente", `quality_flag` sinaliza "o conteúdo pode não ser confiável", sem misturar os dois conceitos. Consequência: uma descrição flagada ainda é traduzida em `05` e ainda aparece no `localized_catalog.json` normalmente, o sinal fica só em `descriptions.json`, para revisão manual (viável com 10 livros).

**Solução:** adicionada a função `looks_suspicious()`, aplicada a toda descrição gerada com sucesso. Quando aciona, grava `quality_flag` junto do registro. Nenhuma das 10 descrições reais aciona a heurística hoje. *(concluído)*

**Achado relacionado, mesmo arquivo:** a checagem de resume em `03_describe.py` (`if code in descriptions: continue`) tinha o mesmo defeito do DQ-006/DQ-001 antes da correção: não checava `status`, então uma descrição `"Failed"` (`render_failed` ou `llm_error`) ficava congelada para sempre. Corrigido para `descriptions.get(code, {}).get("status") == "Success"`, no mesmo padrão já usado em `04`/`05`/`06`. Corrigido também um efeito colateral encontrado ao ler o código: quando `render_failed` ocorria, um `continue` pulava o salvamento em disco daquela iteração, se fosse o último PDF do loop, a falha nunca era persistida. As duas correções foram unificadas num único ponto de salvamento por iteração.