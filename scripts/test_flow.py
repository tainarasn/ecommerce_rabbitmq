"""Script de teste automatizado do fluxo de pedidos."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.rabbit import ECOMMERCE_EXCHANGE, connect, declare_ecommerce_exchange, publish_signed_event

SERVICE = "principal"
KEYS_DIR = ROOT / "principal" / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"


def main():
    connection, channel = connect()
    declare_ecommerce_exchange(channel)

    pedido_id = 999
    print(f"Publicando pedido.criado (id={pedido_id})...")
    publish_signed_event(
        channel,
        ECOMMERCE_EXCHANGE,
        "pedido.criado",
        {"pedido_id": pedido_id, "produtos": [{"id": 1, "quantidade": 1}]},
        SERVICE,
        PRIVATE_KEY,
    )
    print("Evento publicado. Aguardando processamento (5s)...")
    time.sleep(5)
    connection.close()
    print("Teste concluido. Verifique os logs dos microsservicos.")


if __name__ == "__main__":
    main()
