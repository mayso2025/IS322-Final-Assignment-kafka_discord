from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from confluent_kafka import Producer
from redis import Redis
from tenacity import retry, stop_after_attempt, wait_exponential
import hmac
import hashlib
import requests
import logging
import logging.config
import json
import uuid
from datetime import datetime, timedelta
import asyncio
from urllib.parse import urlencode

class Settings(BaseSettings):
    github_client_id: str
    github_client_secret: str
    github_webhook_secret: str
    callback_url: str
    kafka_bootstrap_servers: str = 'kafka:9092'
    commits_topic: str = 'github_commits'
    repositories_topic: str = 'github_repositories'
    poll_interval: float = 0.1
    redis_host: str = 'redis'
    redis_port: int = 6379
    redis_password: str = None
    jwt_secret_key: str
    jwt_algorithm: str = 'HS256'
    jwt_expiration_minutes: int = 30

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

settings = Settings()

app = FastAPI()
templates = Jinja2Templates(directory="templates")
bearer_auth = HTTPBearer()

with open('logging_config.json', 'r') as config_file:
    logging_config = json.load(config_file)
    logging.config.dictConfig(logging_config)

logger = logging.getLogger(__name__)

producer = Producer({
    'bootstrap.servers': settings.kafka_bootstrap_servers,
    'acks': 'all',
    'retries': 5,
    'retry.backoff.ms': 300
})

redis_client = Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    password=settings.redis_password
)

class CommitData(BaseModel):
    repository: str
    commit_id: str
    message: str
    author: str
    timestamp: str
    added: list
    removed: list
    modified: list

class RepositoryData(BaseModel):
    repository: str
    description: str
    url: str
    stars: int
    forks: int
    language: str

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error(f"HTTP Exception: {exc.detail}")
    return templates.TemplateResponse("error.html", {"request": request, "message": exc.detail}, status_code=exc.status_code)

@app.exception_handler(Exception)
async def exception_handler(request: Request, exc: Exception):
    logger.error(f"Unexpected Exception: {str(exc)}")
    return templates.TemplateResponse("error.html", {"request": request, "message": "Internal Server Error"}, status_code=500)

def generate_token(username: str):
    expiration = datetime.utcnow() + timedelta(minutes=settings.jwt_expiration_minutes)
    token_data = {
        "sub": username,
        "exp": expiration.timestamp()
    }
    token = jwt.encode(token_data, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token

def decode_token(token: str):
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        username = payload["sub"]
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_auth)):
    token = credentials.credentials
    username = decode_token(token)
    return username

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/github/oauth")
async def github_oauth_callback(request: Request, code: str):
    try:
        # Exchange the authorization code for an access token
        token_url = "https://github.com/login/oauth/access_token"
        params = {
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
        }
        response = requests.post(token_url, params=params)
        access_token = response.json()["access_token"]

        # Retrieve the user's GitHub account information
        user_url = "https://api.github.com/user"
        headers = {"Authorization": f"Bearer {access_token}"}
        user_response = requests.get(user_url, headers=headers)
        github_user = user_response.json()
        username = github_user["login"]

        # Generate a JWT token for the user
        token = generate_token(username)

        # Store the access token in Redis for future use
        redis_client.set(f"access_token:{username}", access_token)

        logger.info(f"GitHub account linked successfully for user: {username}")
        return templates.TemplateResponse("success.html", {"request": request, "message": "GitHub account linked successfully", "token": token})
    except Exception as e:
        logger.error(f"Error during GitHub OAuth callback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to link GitHub account")

@app.post("/github/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        # Verify the webhook signature
        signature = request.headers.get("X-Hub-Signature")
        secret = settings.github_webhook_secret
        body = await request.body()
        expected_signature = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        if not hmac.compare_digest(f"sha1={expected_signature}", signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

        # Process the webhook payload
        payload = await request.json()
        repository = payload["repository"]["full_name"]
        commits = payload["commits"]

        for commit in commits:
            commit_data = CommitData(
                repository=repository,
                commit_id=commit["id"],
                message=commit["message"],
                author=commit["author"]["name"],
                timestamp=commit["timestamp"],
                added=commit["added"],
                removed=commit["removed"],
                modified=commit["modified"]
            )

            # Publish the commit data to Kafka in the background
            background_tasks.add_task(send_to_kafka, settings.commits_topic, commit_data.json())

        logger.info(f"Received webhook for repository: {repository}")
        return {"message": "Webhook received"}
    except Exception as e:
        logger.error(f"Error processing GitHub webhook: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process webhook")

@app.get("/github/subscribe", response_class=HTMLResponse)
async def subscribe_form(request: Request):
    return templates.TemplateResponse("subscribe.html", {"request": request})

@app.post("/github/subscribe")
async def subscribe_to_repository(request: Request, background_tasks: BackgroundTasks, access_token: str = None, current_user: str = Depends(get_current_user)):
    try:
        form_data = await request.form()
        repository_url = form_data.get("repository_url")

        if not access_token:
            access_token = redis_client.get(f"access_token:{current_user}")
            if not access_token:
                raise HTTPException(status_code=401, detail="Access token not found")

        # Extract the repository owner and name from the URL
        owner, repo = extract_owner_and_repo(repository_url)

        # Import the repository data
        repository_data = await import_repository_data(owner, repo, access_token)

        # Publish the repository data to Kafka in the background
        background_tasks.add_task(send_to_kafka, settings.repositories_topic, repository_data.json())

        # Create a new webhook using the GitHub API
        webhook_url = f"https://api.github.com/repos/{owner}/{repo}/hooks"
        payload = {
            "name": "web",
            "active": True,
            "events": ["push"],
            "config": {
                "url": f"{settings.callback_url}/github/webhook",
                "content_type": "json"
            }
        }
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.post(webhook_url, json=payload, headers=headers)

        if response.status_code == 201:
            webhook_id = response.json()["id"]
            # Store the webhook ID and repository information in your system
            # ...
            logger.info(f"Repository subscribed to commit changes: {repository_url}")
            return templates.TemplateResponse("success.html", {"request": request, "message": "Repository subscribed to commit changes"})
        else:
            logger.error(f"Failed to create webhook for repository: {repository_url}")
            raise HTTPException(status_code=400, detail="Failed to create webhook")
    except Exception as e:
        logger.error(f"Error subscribing to repository: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to subscribe to repository")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def import_repository_data(owner: str, repo: str, access_token: str):
    try:
        # Retrieve the repository data using the GitHub API
        repo_url = f"https://api.github.com/repos/{owner}/{repo}"
        headers = {"Authorization": f"Bearer {access_token}"}
        repo_response = requests.get(repo_url, headers=headers)
        repo_data = repo_response.json()

        repository_data = RepositoryData(
            repository=f"{owner}/{repo}",
            description=repo_data.get("description", ""),
            url=repo_data["html_url"],
            stars=repo_data["stargazers_count"],
            forks=repo_data["forks_count"],
            language=repo_data.get("language", "")
        )

        # Retrieve the commit history using the GitHub API with pagination
        commits_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        page = 1
        per_page = 100
        all_commits = []

        while True:
            params = {
                "page": page,
                "per_page": per_page
            }
            commits_response = requests.get(commits_url, headers=headers, params=params)
            commits_data = commits_response.json()

            if not commits_data:
                break

            all_commits.extend(commits_data)
            page += 1

        for commit in all_commits:
            commit_data = CommitData(
                repository=f"{owner}/{repo}",
                commit_id=commit["sha"],
                message=commit["commit"]["message"],
                author=commit["commit"]["author"]["name"],
                timestamp=commit["commit"]["author"]["date"],
                added=[file["filename"] for file in commit["files"] if file["status"] == "added"],
                removed=[file["filename"] for file in commit["files"] if file["status"] == "removed"],
                modified=[file["filename"] for file in commit["files"] if file["status"] == "modified"]
            )

            # Publish the commit data to Kafka in the background
            await send_to_kafka(settings.commits_topic, commit_data.json())

        logger.info(f"Imported repository data for {owner}/{repo}")
        return repository_data
    except Exception as e:
        logger.error(f"Error importing repository data: {str(e)}")
        raise

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def send_to_kafka(topic, message_json):
    message = {
        "uuid": str(uuid.uuid4()),
        "content": message_json,
        "timestamp": datetime.now().isoformat()
    }
    try:
        producer.produce(topic, json.dumps(message).encode('utf-8'), on_delivery=delivery_report)
        producer.poll(settings.poll_interval)
    except Exception as e:
        logger.error(f"Kafka exception occurred: {e}")
        raise
    logger.debug(f"Sent message to {topic}: {message}")

def delivery_report(err, msg):
    if err:
        logger.error(f"Message delivery failed: {err}")
    else:
        logger.info(f"Message delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}")

def extract_owner_and_repo(repository_url: str):
    # Extract the repository owner and name from the URL
    parts = repository_url.strip("/").split("/")
    owner = parts[-2]
    repo = parts[-1]
    return owner, repo

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)