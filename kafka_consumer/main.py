"""
Kafka Consumer for Discord Commands

This script is a Kafka consumer that continuously listens for messages on a specified Kafka topic.
It is particularly designed to process commands or messages originated from a Discord bot, acting as part of a larger microservice architecture.
The consumer fetches messages from the Kafka queue, processes them, and performs designated actions based on the message content.

Purpose:
- To decouple the message receiving part (Discord bot) from message processing, enhancing scalability and reliability.
- To facilitate asynchronous processing of commands or data received through Discord, using Kafka as a message queue.

How it Works:
- The script initializes a Kafka consumer with specified settings, subscribes to a topic, and listens for new messages.
- Upon receiving a message, the consumer checks for errors, processes valid messages, and logs relevant information.
- It gracefully handles termination signals to ensure that all resources are properly cleaned up before shutdown.

Usage:
- This script is typically run on a server where it can have uninterrupted access to the Kafka cluster.
- It requires configuration settings such as Kafka bootstrap servers, topic name, and consumer group to be set in an `.env` file.

"""

import json
import logging
import logging.config
from confluent_kafka import Consumer, KafkaError
from pydantic_settings import BaseSettings
import signal
import sys

class Settings(BaseSettings):
    kafka_bootstrap_servers: str = 'kafka:9092'
    commands_topic: str = 'kafka_commands'
    consumer_group: str = 'my_consumer_group'
    auto_offset_reset: str = 'earliest'  # Can be set to 'latest' or 'earliest'

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

settings = Settings()

# Load logging configuration from an external JSON file for better maintainability
with open('logging_config.json', 'r') as config_file:
    logging_config = json.load(config_file)
    logging.config.dictConfig(logging_config)

logger = logging.getLogger(__name__)

# Initialize the Kafka Consumer with settings from the `.env` file
consumer = Consumer({
    'bootstrap.servers': settings.kafka_bootstrap_servers,
    'group.id': settings.consumer_group,
    'auto.offset.reset': settings.auto_offset_reset
})
consumer.subscribe([settings.commands_topic])

def signal_handler(signal, frame):
    """Graceful shutdown of the consumer on receiving SIGINT or SIGTERM."""
    logger.info("Shutting down consumer...")
    consumer.close()
    sys.exit(0)

# Register signal handlers for graceful termination
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def consume_messages():
    """Continuously consume messages from Kafka and process them."""
    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                # Handle errors such as the end of a partition or other Kafka errors
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logger.info(f'End of partition reached {msg.topic()}:{msg.partition()}')
                else:
                    logger.error(f'Error occurred: {msg.error().str()}')
            else:
                # Process the valid message
                message = msg.value().decode('utf-8')
                process_message(json.loads(message))
    except Exception as e:
        logger.error(f"An unexpected exception occurred: {e}")
    finally:
        # Ensure the consumer is properly closed during an unexpected shutdown
        consumer.close()
        logger.info("Consumer closed")

def process_message(message):
    """Log the processing of each message."""
    logger.info(f"Processing message: {message}")

if __name__ == "__main__":
    consume_messages()
