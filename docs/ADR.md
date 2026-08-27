# Architecture Decision Records

## ADR 001 — Escolha dos pilares de entrega

**Contexto:**
O desafio exige a escolha de pelo menos 2 das 5 áreas de entrega definidas (Event-Driven Pipeline, Data Architecture, Versioning, Scalability, Data Quality), com avaliação focada em decisões de engenharia, não nos scripts fornecidos. Minha escolha considerou o conhecimento técnico e também alinhamento da vaga de Data Foundry Engineer.

**Decisão:**
Escolhidos os pilares Data Quality e Event-Driven Pipeline. Data Quality foi selecionado por conhecimento e experiencia prévia, assim como alinhamento direto com os requisitos da vaga (deduplicação, normalização, consistência de dados). Event-Driven Pipeline foi selecionado por ser uma oportunidade concreta de aprendizado dentro do escopo do teste e também por representar uma competência explicitamente citada na vaga (orquestração via Airflow/Temporal). Data Architecture foi avaliada e descartada como pilar formal por não haver necessidade real de camadas de agregação neste projeto. Uma separação básica raw/processed será mantida como boa prática independentemente disso.

**Consequências:**
A entrega ganha alinhamento direto com os requisitos técnicos da vaga e demonstra disposição para aprender fora da zona de conforto técnica atual. Em contrapartida, a execução em Event-Driven Pipeline envolve menor domínio prévio, exigindo mais tempo de estudo dentro do prazo do teste.

## ADR 002 — Troca do provedor de LLM: Ollama local → Gemini API hospedada

**Contexto:**
O scaffold do case configura por padrão um LLM local via Ollama, orquestrado pelo `compose.yaml`, que reserva 16GB de RAM só para esse container. A máquina utilizada para desenvolvimento não suporta esse requisito e o próprio enunciado permite a troca de provedor de LLM via variáveis de ambiente (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), sem necessidade de alterar o código dos scripts.

**Decisão:**
Substituído o Ollama local pela API do Gemini (Google AI Studio), usando seu endpoint compatível com o formato OpenAI. O modelo escolhido foi `gemini-3.5-flash-lite`, por oferecer suporte a entrada de imagem (necessário para o script de descrição, que envia páginas de PDF renderizadas) e por ter, entre as opções gratuitas avaliadas, a maior margem de cota disponível para o volume de chamadas do projeto. O serviço `ollama` e sua dependência no `compose.yaml` foram removidos.

**Consequências:**
O pipeline passa a depender de acesso à internet e de uma chave de API externa, em vez de rodar totalmente offline. Em contrapartida, se torna executável em qualquer máquina, independentemente de capacidade de hardware, sem custo dentro do volume esperado (~10 livros).
