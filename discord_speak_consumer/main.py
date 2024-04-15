"""
Discord Bot as Kafka Consumer

This script sets up a Discord bot designed to act as a consumer for Kafka messages.
It listens to a specified Kafka topic and broadcasts the received messages to a designated Discord channel.
This setup allows for asynchronous processing and dissemination of information, fitting well into a microservice architecture where Kafka is used as a messaging backbone.

Purpose:
- To relay messages from a Kafka topic directly to a Discord channel, bridging external systems or services with Discord users.
- To facilitate real-time notifications and commands processed elsewhere in the system and reflected in Discord.

How It Works:
- The script configures a Kafka consumer to subscribe to a predefined topic.
- It initializes a Discord bot that listens for messages from this Kafka topic.
- When a message is received, the bot posts it to a specific Discord channel based on the settings.
- The system is designed to handle errors gracefully and ensures the consumer cleanly exits on application termination.

Usage:
- This bot is ideally deployed in environments where Kafka is used to handle inter-service communications.
- It requires proper Kafka settings and Discord credentials, which are fetched from environment variables to ensure security and flexibility.

Setup:
- Configure Kafka connection settings and the Discord bot token through environment variables.
- Specify the target Discord channel ID where messages should be broadcasted.
- Run the script in an environment where Python 3.7+ is available, with required packages installed.
"""

import asyncio
import json
import logging
import logging.config
from confluent_kafka import DeserializingConsumer
from confluent_kafka.serialization import StringDeserializer
from nextcord.ext import commands, tasks
import nextcord
from pydantic_settings import BaseSettings

# Setting configurations using Pydantic for environment management
class Settings(BaseSettings):
    announcement_channel_id: int
    discord_token: str
    kafka_bootstrap_servers: str = 'kafka:9092'
    kafka_topic: str = 'discord_speak'
    group_id: str = 'discord_bot_group'
    auto_offset_reset: str = 'earliest'

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

settings = Settings()

# Load and configure logging
with open('logging_config.json', 'r') as config_file:
    logging_config = json.load(config_file)
    logging.config.dictConfig(logging_config)

logger = logging.getLogger(__name__)

# Initialize the Discord bot with the specified intents
intents = nextcord.Intents.default()
intents.messages = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Configure and initialize Kafka consumer
consumer = DeserializingConsumer({
    'bootstrap.servers': settings.kafka_bootstrap_servers,
    'group.id': settings.group_id,
    'key.deserializer': StringDeserializer(),
    'value.deserializer': lambda m, c: json.loads(m.decode('utf-8')),
    'auto.offset.reset': settings.auto_offset_reset
})
consumer.subscribe([settings.kafka_topic])

@tasks.loop(seconds=1)
async def consume_from_kafka_task():
    """ Consumes messages from Kafka, posting them to a Discord channel. """
    msg = consumer.poll(1.0)
    if msg is None:
        return
    if msg.error():
        logger.error(f"Consumer error: {msg.error()}")
        return
    data = msg.value()
    channel = bot.get_channel(settings.announcement_channel_id)
    if channel:
        await channel.send(data['content'])
        logger.info(f"Sent message to Discord: {data['content']}")
    else:
        logger.warning(f"Channel with ID {settings.announcement_channel_id} not found")

@bot.event
async def on_ready():
    """ Announces bot's presence upon successful login and initialization. """
    logger.info(f'Logged in as {bot.user}')
    channel = bot.get_channel(settings.announcement_channel_id)
    if channel:
        await channel.send("Hello, I'm now connected and ready to relay messages!")
        logger.info("Announcement made in Discord.")
    else:
        logger.warning("Announcement channel not found.")
    consume_from_kafka_task.start()

async def start():
    """ Starts the Discord bot and handles graceful shutdown. """
    try:
        await bot.start(settings.discord_token)
    except asyncio.CancelledError:
        logger.info("Bot stopping...")
    finally:
        consumer.close()
        logger.info("Kafka consumer closed and bot stopped gracefully.")

if __name__ == "__main__":
    asyncio.run(start())
