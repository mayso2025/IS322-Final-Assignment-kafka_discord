import uuid
import asyncio
import nextcord
from nextcord.ext import commands, tasks
from confluent_kafka import SerializingProducer, DeserializingConsumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import StringSerializer, StringDeserializer
from pydantic import BaseModel
from pydantic_settings import BaseSettings
import logging
import logging.config
from aiohttp import ClientSession, BasicAuth
from ratelimit import limits, sleep_and_retry
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain.schema.output_parser import StrOutputParser
from langchain.schema.runnable import RunnablePassthrough

# Logging Configuration
logging_config = {
    'version': 1,
    'formatters': {
        'default': {
            'format': '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default',
            'level': 'INFO'
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': 'app.log',
            'formatter': 'default',
            'level': 'DEBUG'
        }
    },
    'loggers': {
        '': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': True
        }
    }
}

# Configure logging
logging.config.dictConfig(logging_config)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    announcement_channel_id: int
    discord_token: str
    kafka_bootstrap_servers: str = 'kafka:9092'
    schema_registry_url: str = 'http://schema-registry:8081'
    commands_topic: str = 'discord_commands'
    responses_topic: str = 'discord_responses'
    minio_url: str = 'http://minio:9000'
    minio_access_key: str = 'minioadmin'
    minio_secret_key: str = 'minioadmin'
    bucket_name: str = 'mybucket'
    openai_api_key: str

    class Config:
        env_file = ".env"

settings = Settings()

class MessageModel(BaseModel):
    uuid: str
    content: str
    author_id: str
    channel_id: str
    guild_id: str = None
    attachments: list = []

intents = nextcord.Intents.default()
intents.messages = True  # This is critical to enable if using message content
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

schema_registry_client = SchemaRegistryClient({"url": settings.schema_registry_url})
logger.debug(f"Initialized SchemaRegistryClient with URL: {settings.schema_registry_url}")

key_serializer = StringSerializer()
logger.debug("Initialized StringSerializer for key serialization")

message_schema = """
{
    "type": "record",
    "name": "Message",
    "fields": [
        {"name": "uuid", "type": "string"},
        {"name": "content", "type": "string"},
        {"name": "author_id", "type": "string"},
        {"name": "channel_id", "type": "string"},
        {"name": "guild_id", "type": ["null", "string"], "default": null},
        {"name": "attachments", "type": {"type": "array", "items": "string"}, "default": []}
    ]
}
"""

avro_serializer = AvroSerializer(schema_str=message_schema, schema_registry_client=schema_registry_client)
logger.debug("Initialized AvroSerializer with Schema Registry URL and Message schema")

def value_serializer(message, ctx):
    # Fixed serialization call
    return avro_serializer.serialize(message, ctx)

producer = SerializingProducer({
    'bootstrap.servers': settings.kafka_bootstrap_servers,
    'key.serializer': key_serializer,
    'value.serializer': lambda message, ctx: value_serializer(message, ctx)
})
logger.debug(f"Initialized SerializingProducer with bootstrap servers: {settings.kafka_bootstrap_servers}")

key_deserializer = StringDeserializer()
logger.debug("Initialized StringDeserializer for key deserialization")

value_deserializer = lambda m: schema_registry_client.decode_message(m)
logger.debug("Initialized value deserializer using schema_registry_client.decode_message")

consumer = DeserializingConsumer({
    'bootstrap.servers': settings.kafka_bootstrap_servers,
    'group.id': 'discord_bot_group',
    'key.deserializer': key_deserializer,
    'value.deserializer': value_deserializer,
    'auto.offset.reset': 'earliest'
})
logger.debug(f"Initialized DeserializingConsumer with bootstrap servers: {settings.kafka_bootstrap_servers}")

consumer.subscribe([settings.responses_topic])
logger.debug(f"Subscribed to Kafka topic: {settings.responses_topic}")

async def send_to_kafka(topic, key, message: MessageModel):
    try:
        logger.debug(f"Sending message to Kafka topic: {topic}, key: {key}")
        producer.produce(topic=topic, key=key, value=message)
        producer.poll(0)
    except Exception as e:
        logger.error(f"Exception in send_to_kafka: {e}")
        raise

async def consume_from_kafka():
    msg = consumer.poll(1.0)
    if msg is None:
        logger.debug("No message received from Kafka consumer")
        return
    if msg.error():
        logger.error(f"Consumer error: {msg.error()}")
        return
    data = msg.value()
    channel = bot.get_channel(int(data['channel_id']))
    if channel:
        logger.info(f"Sending message to Discord channel: {channel.name}")
        await channel.send(data['content'])
    else:
        logger.warning(f"Channel with ID {data['channel_id']} not found")

@tasks.loop(seconds=1)
async def consume_from_kafka_task():
    await consume_from_kafka()

async def upload_files(attachments):
    urls = []
    async with ClientSession() as session:
        for attachment in attachments:
            content = await attachment.read()
            url = f"{settings.minio_url}/{settings.bucket_name}/{uuid.uuid4()}-{attachment.filename}"
            auth = BasicAuth(login=settings.minio_access_key, password=settings.minio_secret_key)
            async with session.put(url, data=content, auth=auth) as response:
                if response.status == 200:
                    logger.info(f"File uploaded successfully: {url}")
                    urls.append(url)
                else:
                    logger.error(f"Failed to upload file to MinIO: {response.status}")
    return urls

# Setup LangChain with OpenAI
chat_api = ChatOpenAI(api_key=settings.openai_api_key, model="gpt-3.5-turbo")
logger.info("Chat API initialized with model gpt-3.5-turbo")

@sleep_and_retry
@limits(calls=5, period=60)
async def handle_conversation(user_input):
    era = "ancient Rome"
    prompt_text = f"Imagine you are a history teacher specializing in {era}. Your task is to educate a student about {era}. Respond to the student's inquiry: '{user_input}'"
    prompt = ChatPromptTemplate.from_messages([("system", prompt_text)])
    logger.info("Generating response using Chat API")

    chain = (
        RunnablePassthrough.assign(
            input=lambda x: x["input"]
        )
        | prompt
        | chat_api
        | StrOutputParser()
    )

    try:
        response = await chain.ainvoke({"input": user_input})
        logger.info("Response generated successfully")
        return f"Roman History Teacher: {response}"
    except Exception as e:
        logger.error(f"Failed to generate response: {e}")
        return "Sorry, I encountered an error. Please try asking something else."

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    channel = bot.get_channel(settings.announcement_channel_id)
    if channel:
        logger.info(f"Sending greeting message to channel {settings.announcement_channel_id}")
        await channel.send("Hello! I am your Roman history teacher. Ask me anything about Roman history.")
    else:
        logger.warning(f"Channel with ID {settings.announcement_channel_id} not found.")
    consume_from_kafka_task.start()
    logger.info("consume_from_kafka_task started")

@bot.slash_command(description="Echo the provided message")
async def echo(ctx, *, message: str):
    response_data = MessageModel(
        uuid=str(uuid.uuid4()),
        content=message,
        author_id=str(ctx.author.id),
        channel_id=str(ctx.channel.id),
        guild_id=str(ctx.guild.id) if ctx.guild else None
    )
    await send_to_kafka(settings.responses_topic, str(ctx.author.id), response_data)
    logger.debug(f"Message echoed to Kafka topic: {settings.responses_topic}, key: {str(ctx.author.id)}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        logger.debug("Received message from self, ignoring")
        return

    attachments_urls = await upload_files(message.attachments) if message.attachments else []
    message_data = MessageModel(
        uuid=str(uuid.uuid4()),
        content=message.content,
        author_id=str(message.author.id),
        channel_id=str(message.channel.id),
        guild_id=str(message.guild.id) if message.guild else None,
        attachments=attachments_urls
    )
    await send_to_kafka(settings.commands_topic, str(message.author.id), message_data)
    logger.debug(f"Sent message to Kafka topic: {settings.commands_topic}, key: {str(message.author.id)}")

    logger.info(f"Received message from {message.author}: {message.content}")
    if isinstance(message.channel, nextcord.DMChannel):
        logger.debug("Processing message in DM channel")
    elif isinstance(message.channel, nextcord.abc.GuildChannel):
        logger.debug(f"Processing message in guild channel: {message.channel.guild.name}")
    else:
        logger.warning(f"Unsupported channel type: {type(message.channel)}")

    response = await handle_conversation(message.content)
    await message.channel.send(response)
    logger.info(f"Generated response: {response}")

    # Storing the response in Kafka
    user_id = message.author.id
    channel_id = message.channel.id
    timestamp = message.created_at.isoformat()
    await send_to_kafka(f'discord_history_{user_id}', str(user_id), MessageModel(
        uuid=str(uuid.uuid4()),
        content=response,
        author_id=str(bot.user.id),
        channel_id=str(channel_id),
        timestamp=timestamp
    ))
    logger.debug(f"Stored response in Kafka topic: discord_history_{user_id}, key: {str(user_id)}")

async def start():
    try:
        logger.info("Starting Discord bot...")
        await bot.start(settings.discord_token)
    except Exception as e:
        logger.error(f"Failed to start services due to: {e}")
    finally:
        await stop()

async def stop():
    logger.info("Stopping Discord bot...")
    await bot.close()
    logger.info("Discord bot stopped")

if __name__ == "__main__":
    asyncio.run(start())
