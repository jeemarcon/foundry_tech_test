# Architecture Decision Records

Registro das decisões de arquitetura tomadas durante o desenvolvimento do pipeline, contexto e consequências de cada uma.

## ADR 001: Escolha das target areas de entrega

**Contexto:**
O desafio exige a escolha de pelo menos 2 das 5 target areas definidas (Event-Driven Pipeline, Data Architecture, Versioning, Scalability, Data Quality), com avaliação focada no design do pipeline e nas decisões de engenharia. A escolha também considerou o conhecimento técnico e a experiência prévia com cada área.

**Decisão:**
Escolhidas as target areas Data Quality e Event-Driven Pipeline. A escolha considerou não só o conhecimento técnico prévio de cada área, mas também o conteúdo real do case: as etapas do pipeline, a origem dos dados e a maturidade da base fornecida. Data Quality foi a escolha mais direta, por ser uma das áreas de maior domínio técnico prévio e, ao mesmo tempo, uma das mais evidentes no próprio cenário: dados raspados de uma fonte pública carregam risco concreto de falhas silenciosas. Essa hipótese se confirmou já na primeira execução manual, etapa por etapa, quando ocorreram exatamente falhas silenciosas de tradução (ver DQ-001). Event-Driven Pipeline foi a segunda escolha: uma oportunidade concreta de aprendizado numa lacuna técnica específica (execução orientada a eventos fora do contexto de ferramentas gerenciadas), reforçada pela leitura do `main.py` original, que revelou que a execução do pipeline, em ordem fixa, poderia ser melhor gerenciada e otimizada, em vez de depender de uma ordem estática. Data Architecture foi avaliada e descartada como target area, apesar de ser a opção de maior familiaridade prévia (arquiteturas em camadas): o projeto tem uma única fonte de dados e apenas dois arquivos finais exigidos pelo case, sem múltiplas fontes para integrar nem consumo por BI/analytics que justificasse formalizar camadas adicionais de agregação. Formalizar esse tipo de arquitetura aqui seria emprestar e forçar nomenclatura (uma camada "gold" sem nenhum cálculo ou agregação real) para uma separação de pastas comum, sem necessidade real por trás. Apesar de as escolhas formais terem sido essas duas, o desenvolvimento esbarrou repetidamente em decisões pertencentes a outras target areas (por exemplo, rate limiting em Scalability, ver DQ-001), o que é consequência natural de um projeto de engenharia bem construído, e não uma expansão de escopo não declarada.

**Consequências:**
A execução em Event-Driven Pipeline envolve menor domínio prévio, exigindo mais tempo de estudo dentro do prazo do teste. Em contrapartida, representa uma oportunidade concreta de aprendizado fora da zona de conforto técnica atual.

## ADR 002: Troca do provedor de LLM: Ollama local → Gemini API hospedada

**Contexto:**
O scaffold do case configura por padrão um LLM local via Ollama, orquestrado pelo `compose.yaml`, que reserva 16GB de RAM só para esse container. A máquina utilizada para desenvolvimento não suporta esse requisito e o próprio enunciado permite a troca de provedor de LLM via variáveis de ambiente (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), sem necessidade de alterar o código dos scripts.

**Decisão:**
Substituído o Ollama local pela API do Gemini (Google AI Studio), usando seu endpoint compatível com o formato OpenAI. O modelo escolhido foi `gemini-3.5-flash-lite`, por oferecer suporte a entrada de imagem (necessário para o script de descrição, que envia páginas de PDF renderizadas) e por ter, entre as opções gratuitas avaliadas, a maior margem de cota disponível para o volume de chamadas do projeto. O serviço `ollama` e sua dependência no `compose.yaml` foram removidos.

**Consequências:**
O pipeline passa a depender de acesso à internet e de uma chave de API externa, em vez de rodar totalmente offline. Em contrapartida, se torna executável em qualquer máquina, independentemente de capacidade de hardware, sem custo dentro do volume esperado (~10 livros).

## ADR 003: Granularidade do disparo no Event-Driven Pipeline

**Contexto:**
Definida a adoção da target area Event-Driven Pipeline (ver ADR 001), foi avaliado qual nível de granularidade usar para o disparo entre etapas. Após pesquisa e estudo, três alternativas foram consideradas: um orquestrador real (Airflow ou Temporal), reatividade em nível de item individual (cada livro dispara a etapa seguinte assim que fica pronto) e reatividade em nível de etapa (a etapa seguinte é disparada quando o arquivo de saída da etapa anterior é atualizado). Um orquestrador real traria escalonamento, retry nativo com backoff e observabilidade prontos, mas exigiria infraestrutura adicional (scheduler, banco de metadados, workers) desproporcional ao volume, ao prazo e ao hardware 😅. A granularidade por item permitiria início mais cedo do processamento posterior e maior paralelismo teórico, mas seus ganhos reais de velocidade, custo e memória dependem de volume alto ou de latência muito variável entre itens, na escala deste case (10 livros, chamadas de LLM com duração similar entre si), esse ganho seria arquitetural e não se traduziria em impacto mensurável.

**Decisão:**
Adotada a granularidade por etapa: cada etapa do pipeline dispara a próxima reagindo à atualização do seu arquivo de saída, e não a um cronograma fixo. A condição de disparo usa os campos `status`/`reason` (ver DQ-001 em `data-quality-log.md`) como critério de decisão, não a simples existência do arquivo, para que a etapa seguinte só avance sobre registros que de fato tiveram sucesso na etapa anterior. Essa abordagem reaproveita diretamente o trabalho já feito na target area Data Quality e conecta as duas target areas escolhidas em ADR 001.

**Consequências:**
O pipeline ganha reatividade real, sem depender de execução manual ou de um agendamento fixo, e sem a complexidade operacional de um orquestrador completo nem o risco de um modelo por item, que exigiria decisões adicionais sobre o que fazer com um pipeline parcialmente concluído. Em contrapartida, a solução não oferece paralelismo real entre itens dentro da mesma etapa nem os recursos de observabilidade e retry automático de um orquestrador dedicado, caso o volume de dados ou os requisitos de latência do projeto crescessem significativamente, essa decisão precisaria ser revisitada.

## ADR 004: Mecanismo de disparo entre etapas do Event-Driven Pipeline

**Contexto:**
Definida a granularidade por etapa (ver ADR 003), foi necessário escolher o mecanismo técnico para detectar quando uma etapa está pronta para disparar a próxima. Duas alternativas foram avaliadas: um watcher real de sistema de arquivos (biblioteca `watchdog`, citada explicitamente no enunciado do case como um padrão event-driven válido) e um dispatcher orientado ao grafo de dependência declarado em código, que reage à conclusão de cada etapa em vez de a eventos de arquivo. O mapeamento das dependências reais entre os 8 scripts também mostrou que várias etapas (`02_hash`, `03_describe`, `04_translate`, `06_covers`) dependem apenas de `01_download` e não umas das outras, mas o `main.py` original as executava em sequência fixa (1 a 8), sem aproveitar esse paralelismo real e mensurável.

**Decisão:**
Adotado o dispatcher por grafo de dependência, não o watcher de arquivos. No `watchdog`, quem escreve os arquivos observados é o próprio pipeline (os subprocessos que ele mesmo dispara), e cada etapa grava seu output incrementalmente (um registro salvo por vez), gerando múltiplos eventos de modificação por arquivo. O dispatcher implementado em `main.py` declara o grafo de dependência das 8 etapas e, ao final de cada uma, verifica quais outras já têm todas as dependências satisfeitas, disparando todas de uma vez via `ThreadPoolExecutor`. Etapas cuja dependência falhou (código de saída diferente de zero) ou foi pulada são marcadas como `skipped` em cascata, preservando o modelo de isolamento por status/reason já usado na target area Data Quality, agora também no nível de etapa.

**Consequências:**
O pipeline ganha paralelismo real entre etapas independentes, confirmado em execução real: `02_hash`, `03_describe`, `04_translate` e `06_covers` dispararam simultaneamente logo após `01_download`, e `08_universal_metadata` disparou assim que `02_hash` e `06_covers` terminaram, sem esperar `03_describe`/`04_translate`/`05_translate_descriptions` concluírem, reduzindo o tempo total de execução. Como efeito colateral positivo, a falha de uma etapa deixou de interromper o pipeline inteiro: o `main.py` original parava (`break`) no primeiro erro, abandonando inclusive etapas seguintes sem nenhuma dependência real da que falhou; agora só as etapas realmente dependentes daquela falha são puladas, e ramos independentes continuam normalmente. Em contrapartida, a solução não generaliza para um cenário em que os dados fossem produzidos por um sistema externo real, fora do controle do próprio pipeline; nesse caso, um watcher de arquivos ou uma fila de mensagens seria a escolha mais adequada.

O paralelismo real trouxe um efeito colateral negativo: como cada etapa roda em sua própria thread/subprocesso, os prints de etapas concorrentes se intercalavam no console sem nenhum controle, dificultando a leitura do log durante a execução (a rastreabilidade nos arquivos gerados nunca foi afetada, cada etapa grava seu próprio JSON de forma independente). **Corrigido capturando a saída de cada subprocesso e reimprimindo linha a linha com um prefixo `[etapa]`, em vez de deixar cada subprocesso herdar o console diretamente. - (concluído)**
