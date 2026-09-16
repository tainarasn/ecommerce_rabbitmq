import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.catalog import PRODUTOS
from shared.rabbit import PROMOCOES_EXCHANGE, connect, declare_promocoes_exchange, publish_signed_event

SERVICE = "promocoes"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"

CATEGORIAS = ["A", "B", "C"]
DESCONTOS = [10, 15, 20, 25, 30]


def gerar_promocao() -> tuple[str, dict]:
    categoria = random.choice(CATEGORIAS)
    produtos_categoria = [
        {"id": pid, **info} for pid, info in PRODUTOS.items() if info["categoria"] == categoria
    ]
    produto = random.choice(produtos_categoria)
    desconto = random.choice(DESCONTOS)
    routing_key = f"promocao.categoria.{categoria}"

    payload = {
        "produto_id": produto["id"],
        "produto_nome": produto["nome"],
        "categoria": categoria,
        "desconto_percentual": desconto,
        "preco_original": produto["preco"],
        "preco_promocional": round(produto["preco"] * (1 - desconto / 100), 2),
    }
    return routing_key, payload


def main():
    connection, channel = connect()
    declare_promocoes_exchange(channel)
    print(" [*] Microsservico Promocoes iniciado. Publicando promocoes a cada 10s...")

    try:
        while True:
            routing_key, payload = gerar_promocao()
            publish_signed_event(channel, PROMOCOES_EXCHANGE, routing_key, payload, SERVICE, PRIVATE_KEY)
            print(
                f" [x] Promocao publicada ({routing_key}): "
                f"{payload['produto_nome']} - {payload['desconto_percentual']}% OFF"
            )
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n [*] Microsservico Promocoes encerrado.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
