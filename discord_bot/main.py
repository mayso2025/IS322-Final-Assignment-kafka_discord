import asyncio
import nextcord
from nextcord.ext import commands
from pydantic import BaseModel
from pydantic_settings import BaseSettings
import logging
import logging.config
from aiohttp import ClientSession, BasicAuth
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

import asyncio
import logging
from aiohttp import ClientSession, BasicAuth
from nextcord.ext import commands
from nextcord.ext.commands import Bot
from pydantic import BaseModel, BaseSettings
from langchain import ChatOpenAI, ChatPromptTemplate, RunnablePassthrough, StrOutputParser

logging.basicConfig(level=logging.INFO)

DATA_PATH = "discord_bot\data\stuff\output.md"

# Configure logging
logging.config.dictConfig(logging_config)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    announcement_channel_id: int 
    discord_token: str 
    openai_api_key: str 

    class Config:
        env_file = ".env"

settings = Settings()

class MessageModel(BaseModel):
    content: str
    author_id: str
    channel_id: str
    guild_id: str = None
    attachments: list = []

intents = nextcord.Intents.default()
intents.messages = True  # This is critical to enable if using message content
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Setup LangChain with OpenAI
chat_api = ChatOpenAI(api_key=settings.openai_api_key, model="gpt-3.5-turbo")
logger.info("Chat API initialized with model gpt-3.5-turbo")

async def upload_files(attachments):
    urls = []
    async with ClientSession() as session:
        for attachment in attachments:
            content = await attachment.read()
            url = f"https://minio.example.com/{attachment.filename}"
            auth = BasicAuth(login="minioadmin", password="minioadmin")
            async with session.put(url, data=content, auth=auth) as response:
                if response.status == 200:
                    logger.info(f"File uploaded successfully: {url}")
                    urls.append(url)
                else:
                    logger.error(f"Failed to upload file to MinIO: {response.status}")
    return urls

conversation_state = {}

async def save_to_file(data):
    with open(DATA_PATH, 'a') as file:
        file.write(data + "\n")

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    channel = bot.get_channel(settings.announcement_channel_id)
    if channel:
        logger.info(f"Sending greeting message to channel {settings.announcement_channel_id}")
        await channel.send("Greetings traveler! Welcome to the Steampunk City of London!")
    else:
        logger.warning(f"Channel with ID {settings.announcement_channel_id} not found.")

@bot.slash_command(description="Echo the provided message")
async def echo(ctx, *, message: str):
    await ctx.send(message)

@bot.event #main chatbot function, "starts the adventure" so to speak
async def on_message(message):
    global conversation_state
    if message.author == bot.user: #disregards a message if its a message from itself, returns the main prompt
        logger.debug("Received message from self, ignoring") 
        return

    if not isinstance(message.channel, nextcord.DMChannel):  # Process only in guild channels
        if message.author.id not in conversation_state:
            conversation_state[message.author.id] = {"prompt": None, "last_response": None}

        if conversation_state[message.author.id]["prompt"] is None:
            # Initial prompt
            prompt_text = f"Respond with 'glad to see such enthusiasm!', You are a DND dungeon master, give the player an amazing adventure in a time set where Steampunk machines reigned supreme in the city of London! Give the player a scenario, could be funny, serious or normal. Ask them what they would want to do"
        else:
            # Generate prompt based on user's response
            prompt_text = f"{conversation_state[message.author.id]['prompt']} {message.content}"

        # Create prompt from text
        prompt = ChatPromptTemplate.from_messages([("system", prompt_text)])
        logger.info("Generating response using Chat API")
        print("User's Message:", message.content)
        # Create pipeline
        chain = (
            RunnablePassthrough.assign(
                input=lambda x: x["input"]
            )
            | prompt
            | chat_api
            | StrOutputParser()
        )

        try:
            # Generate response
            response = await chain.ainvoke({"input": message.content})
            logger.info("Response generated successfully")
            response_message = f"The Floating Gear Man: {response}"
            await message.channel.send(response_message)
            logger.info(f"Generated response: {response}")
            # Update conversation state
            conversation_state[message.author.id]["last_response"] = response
            conversation_state[message.author.id]["prompt"] = message.content

            # Save the first user response and the first AI response to a Markdown file
            if conversation_state[message.author.id]["last_response"] and conversation_state[message.author.id]["prompt"]:
                await save_to_file(f"User's first message: {conversation_state[message.author.id]['prompt']}")
                await save_to_file(f"Bot's first response: {conversation_state[message.author.id]['last_response']}")
        except Exception as e:
            logger.error(f"Failed to generate response: {e}")
            await message.channel.send("Sorry, I encountered an error. Please try asking something else.")

    if isinstance(message.channel, nextcord.DMChannel):
        logger.debug("Processing message in DM channel")
    elif isinstance(message.channel, nextcord.abc.GuildChannel):
        logger.debug(f"Processing message in guild channel: {message.channel.guild.name}")
    else:
        logger.warning(f"Unsupported channel type: {type(message.channel)}")

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


