"""
Discord Bot as Kafka Producer

This script sets up a Discord bot that listens to messages on Discord channels and forwards them as messages to a Kafka topic.
This enables asynchronous processing of Discord messages through a distributed messaging system, allowing for decoupled architectures in microservices.

The bot uses the nextcord library to interact with the Discord API and confluent_kafka for Kafka interactions.
It is configured to respond to all messages that are not commands or messages from bots, including its own messages.

Features:
- Configurable through environment variables and a JSON-based logging configuration.
- Sends all messages received on Discord to a specified Kafka topic.
- Acknowledges successful and failed message deliveries to Kafka.
- Provides an announcement in a designated Discord channel upon starting.
- Utilizes asynchronous operations to handle incoming messages and interact with Kafka.
"""

import uuid
import asyncio
import json
import logging
import logging.config
from confluent_kafka import Producer, KafkaError
from pydantic_settings import BaseSettings
import nextcord
from nextcord.ext import commands

class Settings(BaseSettings):
    # Configuration parameters loaded from .env file for flexibility and security
    discord_token: str  # Token for the Discord bot
    kafka_bootstrap_servers: str = 'kafka:9092'  # Kafka cluster address
    commands_topic: str = 'discord_listen'  # Kafka topic to send messages to
    poll_interval: float = 0.1  # Time in seconds to block for message events
    command_prefix: str = '!'  # Prefix used to identify commands directed at the bot
    announcement_channel_id: int  # Discord channel ID to send startup announcements

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

# Configuration loading and logging setup
settings = Settings()
with open('logging_config.json', 'r') as config_file:
    logging_config = json.load(config_file)
    logging.config.dictConfig(logging_config)
logger = logging.getLogger(__name__)

# Kafka producer configuration
producer = Producer({
    'bootstrap.servers': settings.kafka_bootstrap_servers,
    'acks': 'all',  # Ensures delivery acknowledgment from all Kafka replicas
    'retries': 5,  # Retry a failed message send up to 5 times
    'retry.backoff.ms': 300  # Wait 300 ms between retries
})

# Setting up the Discord bot with specified intents and command prefix
intents = nextcord.Intents.default()
intents.messages = True  # Bot needs to listen to message events
bot = commands.Bot(command_prefix=settings.command_prefix, intents=intents, help_command=None)

def acked(err, msg):
    """ Callback for Kafka message delivery reports. Logs success or failure. """
    if err:
        logger.error(f"Message delivery failed: {err}")
    else:
        logger.info(f"Message delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}")

@bot.event
async def on_message(message):
    """ Processes each message sent in Discord channels the bot has access to. """
    if message.author == bot.user or message.author.bot:
        return  # Ignores messages from the bot itself or other bots

    # Preparing message data for Kafka
    message_data = {
        "uuid": str(uuid.uuid4()),  # Unique identifier for each message
        "content": message.clean_content,  # The text content of the message
        "author_id": str(message.author.id),  # Discord ID of the message author
        "channel_id": str(message.channel.id)  # Discord channel ID where the message was sent
    }
    message_json = json.dumps(message_data)  # Convert the message data to JSON format
    try:
        # Produce and send the message to Kafka
        producer.produce(settings.commands_topic, value=message_json, callback=acked)
        producer.poll(settings.poll_interval)  # Wait briefly to allow for message sending
    except KafkaError as e:
        logger.error(f"Kafka exception: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Serialization error: {e}")  # Corrected exception type for serialization errors
    logger.debug(f"Produced message to Kafka: {message_json}")

@bot.event
async def on_ready():
    """ Notifies when the bot logs in and is ready to send messages. """
    logger.info(f'Logged in as {bot.user}')
    announcement_channel = bot.get_channel(int(settings.announcement_channel_id))
    if announcement_channel:
        await announcement_channel.send("I'm online and ready to listen!")
        logger.info("Announcement made in Discord.")
    else:
        logger.warning("Announcement channel not found.")
    await bot.change_presence(activity=nextcord.Game(name="Listening to messages"))

async def start():
    """ Starts the Discord bot. """
    await bot.start(settings.discord_token)

if __name__ == "__main__":
    asyncio.run(start())
