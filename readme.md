# Kafka-Discord Integration System

This system integrates Kafka with Discord, facilitating message transmission between a Kafka cluster and Discord channels through two distinct Python scripts: one acting as a producer and the other as a consumer. This setup is ideal for asynchronous processing and dissemination of information within a microservice architecture.

## Components

The system comprises four main components:

1. **Kafka Producer**: Sends messages to a Kafka topic.
2. **Kafka Consumer**: Receives messages from a Kafka topic.
3. **Discord Listener Producer**: Listens to messages from Discord and sends them to a Kafka topic.
4. **Discord Speak Consumer**: Fetches messages from a Kafka topic and posts them to a Discord channel.

### 1. Kafka Producer

This component is a Python script designed to run within a microservice architecture. It takes messages from command-line input or default messages, encodes them into JSON format, and sends them to a designated Kafka topic.

**Features**:
- Configurable Kafka server settings including acknowledgments and retries for robust message delivery.
- Dynamic message content through command-line arguments.
- Continuous asynchronous message production.

**Dependencies**:
- `confluent_kafka`
- `pydantic_settings`
- `asyncio`
- `argparse`

### 2. Kafka Consumer

A Python script that continuously listens for messages on a specified Kafka topic, processes them, and performs designated actions based on the content.

**Purpose**:
- Decouples message receiving from processing, enhancing scalability and reliability.
- Facilitates asynchronous processing of data received through Kafka.

**Operation**:
- Initializes a Kafka consumer with specified settings, subscribes to a topic, and listens for new messages.
- Upon receiving a message, checks for errors, processes valid messages, and logs information.

### 3. Discord Listener Producer

A Discord bot that listens to all messages sent in specified channels, formats them, and forwards them to a Kafka topic. This allows external processing of Discord messages and acts as a bridge between Discord users and backend services.

**Functionality**:
- Listens to real-time messages in Discord.
- Filters and formats messages before sending them to Kafka.
- Utilizes environment variables for configuration to maintain security and flexibility.

### 4. Discord Speak Consumer

This Discord bot acts as a consumer for Kafka messages, taking data from Kafka and posting it directly into a Discord channel. It acts as the voice of the system, communicating processed results or alerts back to Discord.

**Workflow**:
- Subscribes to a Kafka topic and listens for messages.
- Posts each received message to a designated Discord channel.
- Provides real-time updates and interactions within the Discord environment.

## Setup and Configuration

### Requirements

- Python 3.7+
- Discord Bot Token
- Kafka Cluster

### Installation

1. **Install Dependencies**:
pip install nextcord confluent_kafka pydantic_settings

sql
Copy code

2. **Configuration Files**:
- Ensure all components are configured via `.env` files for environment-specific settings.
- Logging configurations are managed through `logging_config.json` for each component.

DISCORD_TOKEN=your_discord_bot_token_here
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
COMMANDS_TOPIC=your_kafka_topic_here
ANNOUNCEMENT_CHANNEL_ID=your_discord_channel_id_here
