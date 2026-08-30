# Architecture Decision Records

## ADR 001: Escolha dos pilares de entrega

**Contexto:**
O desafio exige a escolha de pelo menos 2 das 5 áreas de entrega definidas (Event-Driven Pipeline, Data Architecture, Versioning, Scalability, Data Quality), com avaliação focada em decisões de engenharia, não nos scripts fornecidos. Minha escolha considerou o conhecimento técnico e também alinhamento da vaga de Data Foundry Engineer.

**Decisão:**
Escolhidos os pilares Data Quality e Event-Driven Pipeline. Data Quality foi selecionado por conhecimento e experiencia prévia, assim como alinhamento direto com os requisitos da vaga (deduplicação, normalização, consistência de dados). Event-Driven Pipeline foi selecionado por ser uma oportunidade concreta de aprendizado dentro do escopo do teste e também por representar uma competência explicitamente citada na vaga (orquestração via Airflow/Temporal). Data Architecture foi avaliada e descartada como pilar formal por não haver necessidade real de camadas de agregação neste projeto. Uma separação básica raw/processed será mantida como boa prática independentemente disso.

**Consequências:**
A entrega ganha alinhamento direto com os requisitos técnicos da vaga e demonstra disposição para aprender fora da zona de conforto técnica atual. Em contrapartida, a execução em Event-Driven Pipeline envolve menor domínio prévio, exigindo mais tempo de estudo dentro do prazo do teste.

## ADR 002: Troca do provedor de LLM: Ollama local → Gemini API hospedada

**Contexto:**
O scaffold do case configura por padrão um LLM local via Ollama, orquestrado pelo `compose.yaml`, que reserva 16GB de RAM só para esse container. A máquina utilizada para desenvolvimento não suporta esse requisito e o próprio enunciado permite a troca de provedor de LLM via variáveis de ambiente (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), sem necessidade de alterar o código dos scripts.

**Decisão:**
Substituído o Ollama local pela API do Gemini (Google AI Studio), usando seu endpoint compatível com o formato OpenAI. O modelo escolhido foi `gemini-3.5-flash-lite`, por oferecer suporte a entrada de imagem (necessário para o script de descrição, que envia páginas de PDF renderizadas) e por ter, entre as opções gratuitas avaliadas, a maior margem de cota disponível para o volume de chamadas do projeto. O serviço `ollama` e sua dependência no `compose.yaml` foram removidos.

**Consequências:**
O pipeline passa a depender de acesso à internet e de uma chave de API externa, em vez de rodar totalmente offline. Em contrapartida, se torna executável em qualquer máquina, independentemente de capacidade de hardware, sem custo dentro do volume esperado (~10 livros).

## ADR 003: Sinalização de falha em traduções de descrição

**Contexto:**
O script `05_translate_descriptions.py` falha silenciosamente ao estourar o limite de requisições por minuto da API do Gemini (ver DQ-001 em `data-quality-log.md`), deixando traduções ausentes sem nenhuma sinalização de causa. Cada idioma (EN/ES/FR) é uma chamada de LLM independente e pode falhar por motivos diferentes dentro do mesmo livro.

**Decisão:**
Adicionada sinalização de status em dois níveis: um campo `status`/`reason` por idioma, registrando sucesso ou o motivo específico da falha de cada tradução individual, e um campo agregado `translation_complete` no nível do livro, resumindo se todas as traduções daquele registro foram concluídas com sucesso. O nível por idioma serve auditoria e retry preciso; o nível agregado serve como sinal simples de decisão para os estágios seguintes do pipeline, incluindo o pilar Event-Driven.

**Consequências:**
O `description_translations.json` passa a carregar mais estrutura por idioma, exigindo ajuste no formato lido pelo `07_localized_catalog.py`. Em contrapartida, ganha-se rastreabilidade real de falhas e uma base concreta para o pilar Event-Driven decidir quando avançar ou não para a próxima etapa.

## ADR 004: Granularidade do disparo no Event-Driven Pipeline

**Contexto:**
Definida a adoção do pilar Event-Driven Pipeline (ver ADR 001), foi avaliado qual nível de granularidade usar para o disparo entre etapas. Três alternativas foram consideradas: um orquestrador real (Airflow ou Temporal), reatividade em nível de item individual (cada livro dispara a etapa seguinte assim que fica pronto) e reatividade em nível de etapa (a etapa seguinte é disparada quando o arquivo de saída da etapa anterior é atualizado). Um orquestrador real traria escalonamento, retry nativo com backoff e observabilidade prontos, mas exigiria infraestrutura adicional (scheduler, banco de metadados, workers) desproporcional ao volume e ao prazo deste case. A granularidade por item permitiria início mais cedo do processamento posterior e maior paralelismo teórico, mas seus ganhos reais de velocidade, custo e memória dependem de volume alto ou de latência muito variável entre itens; na escala deste case (10 livros, chamadas de LLM com duração similar entre si), esse ganho seria arquitetural e não se traduziria em impacto mensurável.

**Decisão:**
Adotada a granularidade por etapa: cada etapa do pipeline dispara a próxima reagindo à atualização do seu arquivo de saída, e não a um cronograma fixo. A condição de disparo usa os campos `status`/`reason` (ver ADR 003) como critério de decisão, não a simples existência do arquivo, para que a etapa seguinte só avance sobre registros que de fato tiveram sucesso na etapa anterior. Essa abordagem reaproveita diretamente o trabalho já feito no pilar Data Quality e conecta os dois pilares escolhidos em ADR 001.

**Consequências:**
O pipeline ganha reatividade real, sem depender de execução manual ou de um agendamento fixo, e sem a complexidade operacional de um orquestrador completo nem o risco de um modelo por item, que exigiria decisões adicionais sobre o que fazer com um pipeline parcialmente concluído. Em contrapartida, a solução não oferece paralelismo real entre itens dentro da mesma etapa nem os recursos de observabilidade e retry automático de um orquestrador dedicado; caso o volume de dados ou os requisitos de latência do projeto crescessem significativamente, essa decisão precisaria ser revisitada.

## ADR 005: Mecanismo de disparo entre etapas do Event-Driven Pipeline

**Contexto:**
Definida a granularidade por etapa (ver ADR 004), foi necessário escolher o mecanismo técnico para detectar quando uma etapa está pronta para disparar a próxima. Duas alternativas foram avaliadas: um watcher real de sistema de arquivos (biblioteca `watchdog`, citada explicitamente no enunciado do case como um padrão event-driven válido) e um dispatcher orientado ao grafo de dependência declarado em código, que reage à conclusão de cada etapa em vez de a eventos de arquivo. O mapeamento das dependências reais entre os 8 scripts também mostrou que várias etapas (`02_hash`, `03_describe`, `04_translate`, `06_covers`) dependem apenas de `01_download` e não umas das outras, mas o `main.py` original as executava em sequência fixa (1 a 8), sem aproveitar esse paralelismo real e mensurável.

**Decisão:**
Adotado o dispatcher por grafo de dependência, não o watcher de arquivos. No `watchdog`, quem escreve os arquivos observados é o próprio pipeline (os subprocessos que ele mesmo dispara), e cada etapa grava seu output incrementalmente (um registro salvo por vez), gerando múltiplos eventos de modificação por arquivo; usar esses eventos como gatilho exigiria lógica adicional de debounce para não disparar a etapa seguinte de forma prematura ou duplicada, sem ganho real neste projeto, já que não existe nenhum produtor externo verdadeiro escrevendo esses dados. O dispatcher implementado em `main.py` declara o grafo de dependência das 8 etapas e, ao final de cada uma, verifica quais outras já têm todas as dependências satisfeitas, disparando todas de uma vez via `ThreadPoolExecutor`. Etapas cuja dependência falhou (código de saída diferente de zero) ou foi pulada são marcadas como `skipped` em cascata, preservando o modelo de isolamento por status/reason já usado no pilar Data Quality, agora também no nível de etapa.

**Consequências:**
O pipeline ganha paralelismo real entre etapas independentes, confirmado em execução real: `02_hash`, `03_describe`, `04_translate` e `06_covers` dispararam simultaneamente logo após `01_download`, e `08_universal_metadata` disparou assim que `02_hash` e `06_covers` terminaram, sem esperar `03_describe`/`04_translate`/`05_translate_descriptions` concluírem, reduzindo o tempo total de execução. Como efeito colateral positivo, a falha de uma etapa deixou de interromper o pipeline inteiro, comportamento do `main.py` original: agora só as etapas realmente dependentes daquela falha são puladas, e ramos independentes continuam normalmente. Em contrapartida, a solução não generaliza para um cenário em que os dados fossem produzidos por um sistema externo real, fora do controle do próprio pipeline; nesse caso, um watcher de arquivos ou uma fila de mensagens seria a escolha mais adequada.

O paralelismo real trouxe um efeito colateral negativo: como cada etapa roda em sua própria thread/subprocesso, os prints de etapas concorrentes se intercalavam no console sem nenhum controle, dificultando a leitura do log durante a execução (a rastreabilidade nos arquivos gerados nunca foi afetada, cada etapa grava seu próprio JSON de forma independente). Corrigido capturando a saída de cada subprocesso e reimprimindo linha a linha com um prefixo `[etapa]`, em vez de deixar cada subprocesso herdar o console diretamente. *(concluído)*
