# Alean AI Assistant

> **MVP monolithic FastAPI application for collecting and analyzing hotel reviews through Telegram bot**

Alean AI Assistant is an intelligent system for collecting feedback from hotel guests using a Telegram bot and AI-powered review analysis. The system automatically analyzes sentiment, relevance, and categorizes reviews to improve service quality.

## Quick Start

### Installation and Setup

#### 1. Clone Repository

```bash
git clone <repository-url>
cd alean/backend
```

#### 2. Environment Configuration

```bash
# Copy configuration template
cp template.env .env

# Edit environment variables
nano .env
```

**All required environment variables are listed in `template.env`. Please review and set them as needed.**

#### 3. Install Dependencies

```bash
# Use uv for fast dependency installation
uv sync
```

#### 4. Database Setup

```bash
# Run PostgreSQL via Docker
docker run --name alean-postgres \
  -e POSTGRES_USER=alean_user \
  -e POSTGRES_PASSWORD=alean_password \
  -e POSTGRES_DB=alean_db \
  -p 5433:5432 \
  -d postgres:15

# Apply migrations
cd database_migrations
python migrate.py
```

#### 5. Run Application

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 
```

#### 6. Configure Telegram Webhook

```bash
# Set webhook for Telegram bot
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/webhook/telegram", "secret_token": "your_webhook_secret"}'
```

## Project Structure

```
backend/
├── app/                           # Main application
│   ├── adapters/                  # External service adapters
│   │   └── telegram/             # Telegram Bot API adapter
│   ├── api/                      # API routes
│   │   └── routes.py            # Main endpoints
│   ├── config/                  # Configuration
│   │   ├── settings.py          # Application settings
│   │   └── messages.py          # Text messages
│   ├── core/                    # Core logic
│   │   ├── db.py               # Database connection
│   │   ├── state.py            # State management
│   │   └── db_middleware.py     # Database middleware
│   ├── models/                  # Data models
│   │   ├── analysis.py         # Analysis models
│   │   ├── admin.py            # Administrative models
│   │   ├── constants.py        # Callback variables for events
│   │   └── manager.py          # Manager models
│   ├── repositories/            # Database repositories
│   ├── services/                # Business logic
│   ├── utils/                   # Utilities
│   ├── workers/                 # Background tasks
│   └── main.py                 # Entry point
├── database_migrations/         # Database migrations
├── load_testing/               # LLM load testing
├── shared_models/              # Shared models between app and database_migrations 
├── Dockerfile                  # Docker configuration
├── pyproject.toml             # Python dependencies
├── uv.lock                    # Python dependencies
├── template.env               # Environment variables template
└── README.md                  # Documentation
```

## API Endpoints

### Main Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health check |
| `POST` | `/webhook/telegram` | Telegram bot webhook |

### Request Examples

#### Service Health Check
```bash
curl http://localhost:8000/health
```

#### Telegram Webhook
```bash
curl -X POST http://localhost:8000/webhook/telegram \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: your_secret" \
  -d '{"update_id": 123, "message": {...}}'
```

## Core Features

### **Telegram Bot**
- **User Registration** — user registration through hotel PMS system
- **Interactive Menus** — navigation through hotels and zones
- **Review Collection** — structured feedback collection
- **QR Codes** — quick access to hotel zones via QR codes
- **Admin Panel** — management interface for administrators
- **Manager Panel** - management interface for managers

### **AI Analysis**
- **Sentiment Analysis** — determining emotional tone of reviews
- **Categorization** — automatic categorization by problem types
- **Relevance Detection** — determining review relevance
- **Entity Extraction** — extracting key points

### **Reporting**
- **Weekly/Monthly/Half-Year/Year Reports** — automatic report generation
- **Data Export** — data export for analysis

## Docker

### Build Image

```bash
docker build -t alean-assistant .
```

### Run Container

```bash
docker run -d \
  --name alean-assistant \
  -p 8000:8000 \
  --env-file .env \
  alean-assistant
```

### Docker Compose

```yaml
version: '3.8'
services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DB_URL=postgresql+asyncpg://alean_user:alean_password@db:5432/alean_db
    depends_on:
      - db
  
  db:
    image: postgres:15
    environment:
      - POSTGRES_USER=alean_user
      - POSTGRES_PASSWORD=alean_password
      - POSTGRES_DB=alean_db
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

### Metrics

- **Prometheus** metrics for performance monitoring
- **Langfuse** for LLM request tracking and analysis
- **Structured logging** for log analysis

## Security

- **Webhook validation** — Telegram secret token verification
- **Rate limiting** — request rate limiting
- **Input validation** — validation of all incoming data
- **SQL injection protection** — ORM usage
- **Environment variables** — storing secrets in environment variables
