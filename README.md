# Trabalho 2 - E-commerce com Microsserviços, RabbitMQ, REST e SSE

Estudantes: Tainara Novaes e João Pedro Castilho

Evolução do Trab1: o backend continua event-driven via RabbitMQ (com assinatura digital RSA em todas as mensagens), e agora ganha um **frontend web** (HTML + JavaScript), um **API Gateway REST** com notificações em tempo real via **SSE**, um **Mock de Pagamento** externo que responde por **Webhook** e envio de promoções por **e-mail (API Resend)**.

## Arquitetura

```
 Frontend (HTML/JS, :8080)
   │  REST (fetch)          ▲ SSE (EventSource)
   ▼                        │
 MS Principal / API Gateway (:5000) ──REST──► MS Estoque (:5001, SQLite)
   │  publica / consome eventos assinados
   ▼
 RabbitMQ  (exchange Direct "eCommerce")
   ├── MS Estoque     ── reserva/devolve estoque
   ├── MS Pagamento (:5002) ──REST──► Mock de Pagamento (:5003)
   │        ▲                              │ usuário clica Aprovar/Recusar
   │        └────────── Webhook (POST) ────┘
   ├── MS Entrega     ── nota fiscal + envio
   └── MS Promoções   ── inscritos (SQLite) ──REST──► API Resend (e-mail)
```

| Processo | Pasta | Porta | Comunicação |
| --- | --- | --- | --- |
| Frontend | `frontend/` | 8080 | REST + SSE com o Gateway |
| MS Principal (API Gateway) | `principal/` | 5000 | REST, SSE, RabbitMQ |
| MS Estoque | `estoque/` | 5001 | REST (consulta de produtos), RabbitMQ, SQLite |
| MS Pagamento | `pagamento/` | 5002 | Webhook (REST), RabbitMQ, chama o Mock |
| MS Entrega | `entrega/` | — | RabbitMQ |
| MS Promoções | `promocoes/` | — | RabbitMQ, SQLite, API externa de e-mail |
| Mock de Pagamento | `mock_pagamento/` | 5003 | Só HTTP (sistema externo, sem RabbitMQ) |

## Pré-requisitos

- Python 3.10+
- RabbitMQ rodando em `localhost:5672`

## Instalação

```powershell
pip install -r requirements.txt
python scripts/generate_keys.py
copy .env.example .env      # e preencha RESEND_API_KEY (opcional)
```

Sem `RESEND_API_KEY`, o MS Promoções só **mostra no log** o e-mail que seria enviado. Com a chave, envia de verdade pela Resend. Sem domínio verificado, o remetente `onboarding@resend.dev` só entrega no e-mail do dono da conta Resend: usem esse e-mail na demonstração.

O MS Promoções gera uma promoção a cada **2 minutos** (`PROMOCAO_INTERVALO=120`) e envia **no máximo 5 e-mails por pessoa** enquanto estiver rodando (`MAX_EMAILS_POR_PESSOA=5`). A contagem zera quando o serviço é reiniciado. O limite existe para não estourar a cota diária gratuita da Resend: passando dela, a API responde `429 daily_quota_exceeded`.

## Execução

Tudo de uma vez (abre uma janela por processo):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
```

Ou manualmente, um terminal para cada:

```powershell
python estoque/main.py
python pagamento/main.py
python entrega/main.py
python promocoes/main.py
python mock_pagamento/main.py
python principal/main.py
python -m http.server 8080 --directory frontend   # só serve os arquivos estáticos
```

Depois abra **http://localhost:8080**.

Teste automático do fluxo completo (com tudo rodando):

```powershell
python scripts/test_flow.py
```

Para voltar o estoque ao inicial, pare o MS Estoque e apague `estoque/estoque.db`.

## Fluxo de um pedido

1. Frontend → `POST /pedidos` → Gateway responde **201 Created** e publica `pedido.criado`.
2. **Estoque** reserva os itens no SQLite → `pedido.estoque_ok` (com `valor_total`) ou `estoque.indisponivel`.
3. **Pagamento** consome `pedido.estoque_ok`, chama `POST /cobrancas` no Mock passando a sua `webhook_url`, recebe a `checkout_url` e publica `pagamento.link_gerado`.
4. Gateway envia via **SSE** o pedido com o link `pagamento`. O frontend leva para o checkout a aba que abriu ao clicar em "Finalizar pedido" (o botão "Pagar" também fica disponível).
5. No Mock, o usuário clica em **Pagamento Aprovado/Recusado** → o Mock faz `POST /webhook/pagamento` no MS Pagamento → `pagamento.aprovado` ou `pagamento.recusado`.
6. **Entrega** consome `pagamento.aprovado`, simula nota fiscal + despacho → `pedido.enviado`.
7. Em cada evento, o Gateway atualiza o pedido e notifica o cliente via SSE.
8. Estoque indisponível, pagamento recusado ou cancelamento pelo cliente → Gateway publica `pedido.excluido` → Estoque devolve a reserva e Pagamento cancela a cobrança.

## Endpoints REST

### API Gateway (`http://localhost:5000`)

| Método | Rota | O que faz | Resposta |
| --- | --- | --- | --- |
| GET | `/` | Ponto de entrada com os links da API (HATEOAS) | 200 |
| GET | `/produtos?categoria=A` | Lista produtos disponíveis (consulta o MS Estoque via REST) | 200 / 503 |
| POST | `/pedidos` | Cria pedido `{"cliente_id", "itens": [{"produto_id", "quantidade"}]}` | **201** + `Location` / 400 / 404 |
| GET | `/pedidos?cliente_id=X` | Pedidos do cliente | 200 |
| GET | `/pedidos/{id}` | Um pedido | 200 / 404 |
| DELETE | `/pedidos/{id}` | Cancela (publica `pedido.excluido`) | **204** / 404 / 409 |
| POST | `/interesses` | Registra interesse `{"email", "categorias": ["A","B"]}` | **202** / 400 |
| DELETE | `/interesses/{email}` | Cancela interesse em todas as categorias | 202 |
| DELETE | `/interesses/{email}/categorias/{cat}` | Cancela interesse em uma categoria | 202 |
| GET | `/eventos?cliente_id=X` | Conexão **SSE** (`text/event-stream`) | stream |

### MS Estoque (`:5001`)
`GET /produtos` (filtros `?categoria=` e `?todos=true`), `GET /produtos/{id}`.

### MS Pagamento (`:5002`)
`POST /webhook/pagamento` com `{"cobranca_id", "pedido_id", "status": "APROVADO"|"RECUSADO"}`.

### Mock de Pagamento (`:5003`)
`POST /cobrancas` (201 + `checkout_url`), `GET /cobrancas/{id}`, `GET /checkout/{id}` (página com os botões).

## Eventos no RabbitMQ (exchange `eCommerce`, Direct)

| Routing key | Publicado por | Consumido por |
| --- | --- | --- |
| `pedido.criado` | Principal | Estoque |
| `pedido.excluido` | Principal | Estoque, Pagamento |
| `interesse.promocao` | Principal | Promoções |
| `pedido.estoque_ok` | Estoque | Pagamento, Principal |
| `estoque.indisponivel` | Estoque | Principal |
| `pagamento.link_gerado` | Pagamento | Principal |
| `pagamento.aprovado` | Pagamento | Entrega, Principal |
| `pagamento.recusado` | Pagamento | Principal |
| `pedido.enviado` | Entrega | Principal |

Toda mensagem vai no envelope `{"data": {...,"origin": "<serviço>"}, "Signature": "<RSA-SHA256 base64>"}`. Quem consome verifica a assinatura com a chave pública do serviço de origem (`shared/events.py`) e descarta a mensagem se ela for inválida.

## RabbitMQ: o que veio do Trab1 e o que mudou

A base de mensageria é a mesma do Trab1. O Trab2 acrescentou a camada web (REST, SSE, Webhook) **por cima** dela, sem trocar a comunicação entre os microsserviços.

**O que continua igual**
- O mesmo exchange **`eCommerce`** do tipo **Direct**, por onde passa todo o fluxo de pedidos.
- As mesmas filas duráveis: `principal_queue`, `estoque_queue`, `pagamento_queue` e `entrega_queue`.
- Os mesmos eventos do pedido: `pedido.criado`, `pedido.excluido`, `pedido.estoque_ok`, `estoque.indisponivel`, `pagamento.aprovado`, `pagamento.recusado` e `pedido.enviado`.
- A mesma **assinatura digital RSA**: o envelope `{"data", "Signature"}`, as chaves geradas por `scripts/generate_keys.py` e a verificação em `shared/events.py`.
- A mesma base em `shared/rabbit.py` (`connect`, `start_consumer`, `publish_signed_event`).

**O que mudou**

| Mudança | Por quê |
| --- | --- |
| Saiu o exchange **`Promocoes`** (Topic) e os consumidores C1/C2 | No Trab2 as promoções chegam ao cliente **por e-mail** (API Resend), não por consumidores do RabbitMQ |
| Novo evento **`interesse.promocao`** e nova fila `promocoes_queue` | O enunciado pede que o Gateway publique o interesse e que o MS Promoções consuma |
| Novo evento **`pagamento.link_gerado`** | O Pagamento usa esse evento para avisar o Gateway da URL do checkout, que segue por SSE até o frontend |
| O Pagamento passou a escutar `pedido.excluido` | Para cancelar a cobrança se o cliente cancelar o pedido antes de pagar (o webhook atrasado recebe 409) |
| Nova função `publish_once` | As rotas REST (Flask) rodam em outras threads, e o canal do pika **não é thread-safe**. Por isso cada publicação feita por uma rota abre a própria conexão |
| O consumidor captura exceções no tratamento | Um erro num evento não derruba mais a thread que consome a fila |

**Restos do Trab1 no broker:** o exchange `Promocoes` e as filas `promocoes_c1_queue`/`promocoes_c2_queue` ainda podem existir no RabbitMQ, mas nada usa mais. Dá para apagar pelo painel de gerenciamento (http://localhost:15672), se o plugin estiver ativo.

## Roteiro para a defesa (conceitos da aula)

**REST / métodos HTTP**
- `GET` só consulta e não altera estado (`/produtos`, `/pedidos/{id}`). Os filtros vão na **query string** (`?categoria=A`, `?cliente_id=`).
- `POST /pedidos` cria um recurso subordinado a `/pedidos`, e o servidor devolve a URL do novo recurso no cabeçalho `Location` (`/pedidos/1`). POST não é idempotente: dois POSTs criam dois pedidos.
- `DELETE /pedidos/{id}` aponta o recurso na própria URL, sem corpo, e é **idempotente**: cancelar de novo um pedido já cancelado devolve 204 de novo, sem efeito extra.
- O servidor não guarda sessão (stateless): cada requisição leva tudo o que precisa (`cliente_id`, itens).

**Códigos de status usados**
- `200 OK` em consultas; `201 Created` ao criar pedido/cobrança; `204 No Content` ao cancelar pedido.
- `202 Accepted` em `/interesses`: o Gateway aceitou o pedido, mas quem grava o interesse é o MS Promoções, de forma assíncrona via RabbitMQ.
- `400 Bad Request` em payload inválido; `404 Not Found` para pedido/produto inexistente; `409 Conflict` ao cancelar um pedido já pago ou num webhook de cobrança cancelada; `503` quando o MS Estoque está fora.

**HATEOAS**
- `GET /` devolve só os links de entrada da API.
- Cada pedido vem com `_links` que **dependem do estado**: `cancelar` só aparece enquanto o pedido pode ser cancelado, e `pagamento` só aparece quando está "Aguardando pagamento". O frontend desenha os botões **a partir desses links**, sem URLs fixas. Se a regra de negócio mudar, basta o servidor parar de mandar o link.

**SSE (Server-Sent Events)**
- O frontend abre `new EventSource("/eventos?cliente_id=...")`. É uma conexão HTTP que o servidor mantém aberta e por onde ele empurra mensagens `event: pedido / data: {...}`.
- Comunicação **unidirecional** (servidor → cliente). Ao contrário do polling, o cliente não fica perguntando, e é mais simples que WebSocket, que é bidirecional. O navegador reconecta sozinho se a conexão cair.
- No Gateway, cada conexão SSE tem uma `queue.Queue`. A thread que consome do RabbitMQ coloca o pedido atualizado nas filas do dono, e o generator do Flask entrega ao navegador. A cada 15 s vai um comentário `: keep-alive` para detectar abas fechadas.

**Webhook**
- Inversão da chamada: em vez de o MS Pagamento ficar consultando o provedor, ele informa uma URL (`webhook_url`) e **o provedor chama de volta** quando o usuário decide.
- O webhook é tratado de forma idempotente (o mesmo status repetido só é confirmado), e o de uma cobrança cancelada é recusado com 409.

**Mensageria (Trab1)**
- O Gateway não chama Estoque/Pagamento/Entrega diretamente no fluxo do pedido: só publica e consome eventos, o que desacopla os serviços. A única chamada REST interna é a consulta de produtos ao Estoque, exigida pelo enunciado.
- As rotas Flask publicam com `publish_once` (uma conexão por publicação), porque o canal do pika **não é thread-safe** e a thread consumidora já usa o seu.

## Estrutura do projeto

```
├── frontend/          # index.html, style.css, app.js (REST + SSE)
├── principal/         # API Gateway (REST + SSE + RabbitMQ)
├── estoque/           # REST + SQLite + RabbitMQ
├── pagamento/         # Webhook + RabbitMQ + cliente do Mock
├── entrega/           # RabbitMQ
├── promocoes/         # RabbitMQ + SQLite + Resend
├── mock_pagamento/    # Provedor de pagamento simulado (externo)
├── shared/            # crypto, events, rabbit, config, catalog
├── scripts/           # generate_keys.py, start_all.ps1, test_flow.py
└── requirements.txt
```
