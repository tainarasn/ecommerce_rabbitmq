"""Camada de integração com RabbitMQ: conexão, filas, exchange e publicação/consumo de eventos assinados."""

import traceback
from pathlib import Path

import pika

from shared.events import build_event, parse_event, serialize_event, validate_event

# Exchange único (Direct) por onde trafegam todos os eventos do backend.
ECOMMERCE_EXCHANGE = "eCommerce"

RABBIT_HOST = "localhost"


# Cria a conexão com o broker RabbitMQ e retorna o canal de comunicação.
def connect() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBIT_HOST))
    channel = connection.channel()
    return connection, channel


# Declara o exchange do e-commerce, usado para todos os eventos.
def declare_ecommerce_exchange(channel) -> None:
    channel.exchange_declare(exchange=ECOMMERCE_EXCHANGE, exchange_type="direct", durable=True)


# Cria uma fila durável para receber mensagens do broker.
def declare_queue(channel, queue_name: str) -> None:
    channel.queue_declare(queue=queue_name, durable=True)


# Associa a fila ao exchange e ao routing key desejado.
def bind_queue(channel, queue_name: str, exchange: str, routing_key: str) -> None:
    channel.queue_bind(exchange=exchange, queue=queue_name, routing_key=routing_key)


# Publica um evento assinado no exchange correspondente, preservando a origem e a validação.
def publish_signed_event(
    channel,
    exchange: str,
    routing_key: str,
    data: dict,
    origin: str,
    private_key_path: Path,
) -> None:
    envelope = build_event(data, origin, private_key_path)
    channel.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=serialize_event(envelope),
        properties=pika.BasicProperties(delivery_mode=2),
    )


# Publica um evento abrindo uma conexão própria. Usado pelas rotas HTTP (Flask), que rodam em
# threads diferentes da thread consumidora — o canal do pika não pode ser compartilhado entre threads.
def publish_once(routing_key: str, data: dict, origin: str, private_key_path: Path) -> None:
    connection, channel = connect()
    try:
        declare_ecommerce_exchange(channel)
        publish_signed_event(channel, ECOMMERCE_EXCHANGE, routing_key, data, origin, private_key_path)
    finally:
        connection.close()


# Consome eventos assinados da fila, validando estrutura, assinatura e origem antes de processar.
def consume_signed_events(channel, queue_name: str, keys_dir: Path, callback) -> None:
    def on_message(ch, method, properties, body):
        envelope = parse_event(body)
        if envelope is None:
            print(" [x] Evento invalido (JSON malformado). Descartando.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        data = validate_event(envelope, keys_dir)
        if data is None:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # Um erro no tratamento não pode derrubar a thread consumidora.
        try:
            callback(ch, method, properties, data)
        except Exception:
            print(f" [x] Erro ao tratar evento '{method.routing_key}':")
            traceback.print_exc()
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=on_message)


# Configura um consumidor de eventos do e-commerce para uma fila e seus bindings específicos.
def start_consumer(service_name: str, queue_name: str, bindings: list[tuple[str, str]], keys_dir: Path, handler):
    connection, channel = connect()
    declare_ecommerce_exchange(channel)
    declare_queue(channel, queue_name)

    for exchange, routing_key in bindings:
        bind_queue(channel, queue_name, exchange, routing_key)

    print(f" [*] {service_name} aguardando eventos na fila '{queue_name}'...")
    consume_signed_events(channel, queue_name, keys_dir, handler)
    channel.start_consuming()
