# Design Notes

Registro cronológico de observações, ideias e conexões entre os pilares durante o desenvolvimento: o "porquê" que antecede uma decisão formal. Itens aqui podem evoluir para uma entrada no ADR (quando a decisão amadurece) ou ficar registrados como ideia explorada e adiada/descartada.

## 2026-08-27: Falha na descrição deveria impedir a tentativa de tradução

**Contexto/gatilho:** Ao desenhar a sinalização de falha para `05_translate_descriptions.py` (Data Quality), percebi que uma descrição ausente ou malformada na etapa anterior (`03_describe.py`) torna a tradução impossível/sem sentido: não é um erro novo, é uma consequência direta de um erro anterior não propagado.

**Ideia:** Usar o mesmo campo de status/sinalização (Data Quality) como condição de disparo para o Event-Driven Pipeline: só processar, por exemplo, a tradução de descrição de um livro se a etapa de descrição daquele livro tiver sido concluída com sucesso. Isso conecta diretamente os dois pilares escolhidos: o sinal de qualidade de um estágio vira o gatilho de execução do próximo.

**Status:** Capturado, ainda não desenhado em detalhe; retomar na branch `feature/event-driven-pipeline`.

## 2026-08-27: Granularidade da sinalização de falha em traduções

**Contexto/gatilho:** Ao desenhar o campo de sinalização de falha para `05_translate_descriptions.py`, surgiu a dúvida sobre o nível de detalhe: cada idioma (EN/ES/FR) é uma chamada de LLM independente e pode falhar por motivos diferentes dentro do mesmo livro. Um único status por livro perderia essa granularidade; um status só por idioma exigiria que qualquer consumidor downstream entendesse a estrutura interna só para saber se aquele livro está completo ou não.

**Discussão:** Considerada a opção de manter só um nível (por idioma OU agregado por livro). Optou-se por manter os dois em camadas: detalhe por idioma para auditoria/retry preciso, e um campo agregado (`translation_complete`) para decisão simples de fluxo.

**Status:** Decidido → ver ADR 003 em `ADR.md`.

## 2026-08-27: Título (04_translate.py) tem o mesmo risco de falha silenciosa

**Contexto/gatilho:** Ao desenhar status/reason para `05_translate_descriptions.py`, percebi que `04_translate.py` (tradução de título) segue exatamente o mesmo padrão: chamadas de LLM independentes por idioma, sem tratamento de erro nem sinalização de falha.

**Ideia:** Aplicar a mesma sinalização (status/reason por idioma) depois de fechar a descrição e a tradução de descrição, por consistência entre as duas etapas de tradução.

**Status:** Capturado, adiado; retomar depois de `03_describe.py` e
`05_translate_descriptions.py` estarem prontos.
