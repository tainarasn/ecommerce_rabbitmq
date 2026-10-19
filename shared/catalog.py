"""Catálogo de produtos e estoque inicial compartilhado por todos os microsserviços."""

# Lista base de produtos do e-commerce.
PRODUTOS = {
    1: {"nome": "Notebook", "categoria": "A", "preco": 3500.0},
    2: {"nome": "Mouse", "categoria": "A", "preco": 80.0},
    3: {"nome": "Camiseta", "categoria": "B", "preco": 59.9},
    4: {"nome": "Tenis", "categoria": "B", "preco": 299.9},
    5: {"nome": "Livro SD", "categoria": "C", "preco": 120.0},
    6: {"nome": "Fone Bluetooth", "categoria": "C", "preco": 199.9},
}

# Quantidade inicial disponível em estoque para cada produto.
ESTOQUE_INICIAL = {
    1: 10,
    2: 50,
    3: 30,
    4: 20,
    5: 15,
    6: 25,
}
