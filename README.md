# Trabalho 1 - MOM E-commerce com RabbitMQ

Backend de e-commerce event-driven com 5 microsservicos Python comunicando exclusivamente via RabbitMQ, com assinatura digital RSA nos eventos.

## Arquitetura

- **Exchange `eCommerce`** (Direct): fluxo de pedidos
- **Exchange `Promocoes`** (Topic): promocoes por categoria
- **5 microsservicos**: Principal, Estoque, Pagamento, Entrega, Promocoes
- **2 consumidores**: C1 (categorias A e B), C2 (todas as categorias)

## Pre-requisitos

- Python 3.10+
- RabbitMQ rodando em `localhost:5672`

### Instalar RabbitMQ (Windows)

1. Instale o Erlang: https://www.erlang.org/downloads
2. Instale o RabbitMQ: https://www.rabbitmq.com/download.html
3. Inicie o servico RabbitMQ

## Instalacao

```powershell
cd C:\Users\taina\Desktop\trabalho1_sd
pip install -r requirements.txt
python scripts/generate_keys.py
```

## Execucao

Abra **7 terminais** e execute cada processo:

```powershell
# Terminal 1 - Estoque
python estoque/main.py

# Terminal 2 - Pagamento
python pagamento/main.py

# Terminal 3 - Entrega
python entrega/main.py

# Terminal 4 - Promocoes
python promocoes/main.py

# Terminal 5 - Consumidor C1 (categorias A e B)
python consumidores/c1.py

# Terminal 6 - Consumidor C2 (todas categorias)
python consumidores/c2.py

# Terminal 7 - Principal (interacao com usuario)
python principal/main.py
```

## Fluxo de pedido

1. Usuario cria pedido no **Principal** -> publica `pedido.criado`
2. **Estoque** verifica disponibilidade -> `pedido.estoque_ok` ou `estoque.indisponivel`
3. **Pagamento** processa (aleatorio) -> `pagamento.aprovado` ou `pagamento.recusado`
4. **Entrega** emite nota e envia -> `pedido.enviado`
5. **Principal** atualiza status a cada evento recebido
6. Se estoque indisponivel ou pagamento recusado -> **Principal** publica `pedido.excluido` -> **Estoque** devolve reserva

## Roteiro

1. Mostrar os 7 processos rodando em terminais separados
2. No Principal, listar produtos (opcao 1)
3. Criar um pedido (opcao 2) e acompanhar logs nos outros servicos
4. Consultar status do pedido (opcao 4) ate chegar em "Enviado" ou "Excluido"
5. Mostrar promocoes chegando em C1 (so A/B) e C2 (A/B/C)
6. Explicar assinatura digital: cada evento tem campo `Signature` verificado com chave publica do produtor
7. Demonstrar exclusao manual de pedido (opcao 3)

## Estrutura do projeto

```
trabalho2_sd/
├── shared/           # Modulos compartilhados (crypto, events, rabbit)
├── principal/        # Microsservico Principal + keys/
├── estoque/          # Microsservico Estoque + keys/
├── pagamento/        # Microsservico Pagamento + keys/
├── entrega/          # Microsservico Entrega + keys/
├── promocoes/        # Microsservico Promocoes + keys/
├── consumidores/     # C1 e C2
├── scripts/          # generate_keys.py
└── requirements.txt
```

## Routing keys

| Exchange  | Routing Key          | Publicado por | Consumido por        |
| --------- | -------------------- | ------------- | -------------------- |
| eCommerce | pedido.criado        | Principal     | Estoque              |
| eCommerce | pedido.excluido      | Principal     | Estoque              |
| eCommerce | pedido.estoque_ok    | Estoque       | Pagamento, Principal |
| eCommerce | estoque.indisponivel | Estoque       | Principal            |
| eCommerce | pagamento.aprovado   | Pagamento     | Entrega, Principal   |
| eCommerce | pagamento.recusado   | Pagamento     | Principal            |
| eCommerce | pedido.enviado       | Entrega       | Principal            |
| Promocoes | promocao.categoria.A | Promocoes     | C1, C2               |
| Promocoes | promocao.categoria.B | Promocoes     | C1, C2               |
| Promocoes | promocao.categoria.C | Promocoes     | C2                   |
