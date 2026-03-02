# Airbnb Agentic AI Prototype

A full-stack, cloud-native Airbnb-style rental platform with an AI-powered travel concierge. Built with React, Node.js/Express, Python FastAPI, MySQL, MongoDB, and Redpanda (Kafka). Deployed on AWS EKS.

---

## Architecture Overview

```
                    ┌─────────────────────────────────────────┐
                    │           AWS EKS Cluster               │
                    │                                         │
  Browser ──► ALB ──► Frontend (React/nginx, 2 replicas)     │
                    │       │                                 │
                    │       ├──► Backend (Node.js/Express)    │
                    │       │        │                        │
                    │       │        ├──► MySQL (StatefulSet) │
                    │       │        ├──► MongoDB (StatefulSet)│
                    │       │        ├──► Kafka (StatefulSet) │
                    │       │        └──► AI Service          │
                    │       │                                 │
                    │       └──► AI Service (FastAPI/Gemini)  │
                    └─────────────────────────────────────────┘
```

### Services

| Service | Technology | Port | Purpose |
|---|---|---|---|
| Frontend | React 19 + nginx | 80 | SPA, proxies `/api/` and `/agent/` |
| Backend | Node.js 18 + Express 5 | 3000 | REST API, Kafka producer/consumer |
| AI Service | Python 3.11 + FastAPI | 8000 | Gemini-powered travel concierge |
| MySQL | MySQL 8.0 | 3306 | Relational data (users, bookings, properties) |
| MongoDB | MongoDB 7.0 | 27017 | Session store, document data |
| Redpanda/Kafka | Confluent Kafka 7.5 | 9092 | Async booking event streaming |

---

## Features

- **Property Management** — owners list, edit, and manage rental properties
- **Booking System** — traveler search, booking, and reservation management with Kafka event streaming
- **AI Concierge** — Gemini-powered trip planning with daily itineraries, weather integration, packing checklists, and restaurant recommendations
- **LangGraph Agent** — multi-node workflow graph (StateGraph) with MemorySaver for session-persistent agent orchestration; tool calls routed via conditional edges
- **Streaming Chat** — SSE endpoint streams Gemini tokens token-by-token to the browser via LangGraph `astream_events`
- **Chat History** — persistent AI conversation storage per booking (MySQL)
- **Image Uploads** — property photos via AWS S3 with presigned URLs
- **Favorites** — save and manage preferred properties
- **User Authentication** — bcrypt-hashed passwords, MongoDB-backed sessions

---

## Tech Stack

| Layer | Technologies |
|---|---|
| Frontend | React 19, Redux Toolkit, React Router v7, Bootstrap 5, nginx |
| Backend | Node.js 18, Express 5, Kafka.js, AWS SDK v3 (S3), bcrypt, express-session |
| AI Service | Python 3.11, FastAPI, LangChain, **LangGraph**, Google Gemini (gemini-2.5-flash), SQLAlchemy |
| Databases | MySQL 8.0, MongoDB 7.0, Redpanda/Kafka |
| Infrastructure | Docker, Kubernetes 1.29, AWS EKS, AWS ECR, AWS EBS, AWS ALB, eksctl |

---

## Local Development

### Prerequisites

- Docker Desktop (with Compose v2)
- API keys: Gemini, Tavily, OpenWeather (for AI features)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/aneessaheba/airbnb-agentic-ai-prototype.git
   cd airbnb-agentic-ai-prototype
   ```

2. Create environment files from examples:
   ```bash
   cp Backend/.env.example Backend/.env
   cp agentic/.env.example agentic/.env
   ```

3. Edit `Backend/.env` and `agentic/.env` with your real credentials.

4. Start all services:
   ```bash
   docker compose up -d
   ```

5. Open [http://localhost:3000](http://localhost:3000)

6. Stop services:
   ```bash
   docker compose down
   ```

### Demo Accounts

| Role | Email | Password |
|---|---|---|
| Owner | shao-yu.huang@sjsu.edu | sandy0318 |
| Traveler | taylor.swift@sjsu.edu | demo1234 |

---

## Environment Variables

### Backend (`Backend/.env`)

| Variable | Description |
|---|---|
| `PORT` | Server port (default: `3000`) |
| `NODE_ENV` | Environment (`production` or `development`) |
| `SESSION_SECRET` | Express session secret |
| `MONGODB_URI` | MongoDB connection string |
| `MYSQL_HOST` | MySQL hostname |
| `MYSQL_USER` | MySQL username |
| `MYSQL_PASSWORD` | MySQL password |
| `MYSQL_DATABASE` | MySQL database name |
| `KAFKA_BROKERS` | Kafka broker address |
| `CLIENT_ORIGIN` | Allowed CORS origins |
| `AWS_REGION` | AWS region for S3 (e.g., `us-east-1`) |
| `S3_BUCKET` | S3 bucket name for uploads |
| `S3_PUBLIC_BASE` | Public base URL for S3 objects |
| `AGENT_URL` | AI service URL (e.g., `http://ai-service:8000/ai`) |
| `PEXELS_API_KEY` | Pexels API key for property images |

### AI Service (`agentic/.env`)

| Variable | Description |
|---|---|
| `DATABASE_URL` | MySQL SQLAlchemy URL (`mysql+mysqlconnector://user:pass@host:3306/db`) |
| `GEMINI_API_KEY` | Google Gemini API key |
| `TAVILY_API_KEY` | Tavily search API key |
| `OPENWEATHER_API_KEY` | OpenWeather API key |

---

## AWS Deployment

### Prerequisites

- AWS CLI configured (`aws configure`)
- `eksctl` installed
- `kubectl` installed
- Docker with BuildX

### 1. Build and Push Docker Images to ECR

```bash
cd aws
./build-and-push-ecr.sh
```

Override defaults as needed:
```bash
REGION=us-east-1 TAG=v3 PLATFORM=linux/amd64 ./build-and-push-ecr.sh
```

This automatically creates ECR repositories if they don't exist:
- `airbnb-backend`
- `airbnb-frontend`
- `airbnb-ai-service`

### 2. Update Kubernetes Secrets

Before deploying, update `k8s/secrets/app-secrets.yaml` with base64-encoded production values:

```bash
# Encode a secret value
echo -n "your-actual-value" | base64
```

Required secrets to update:
- `SESSION_SECRET`
- `GEMINI_API_KEY`
- `TAVILY_API_KEY`
- `OPENWEATHER_API_KEY`
- `MYSQL_ROOT_PASSWORD`
- `MYSQL_PASSWORD`
- `PEXELS_API_KEY`

### 3. Deploy to EKS

```bash
cd aws
./deploy-to-eks.sh
```

This script:
1. Creates the EKS cluster if it doesn't exist (`data236-cluster` in `us-east-1`)
2. Installs the EBS CSI driver add-on
3. Applies all Kubernetes manifests (configmaps, secrets, volumes, services, statefulsets, deployments)
4. Updates deployment images to point to your ECR registry

Override defaults:
```bash
CLUSTER_NAME=my-cluster REGION=us-west-2 TAG=v3 ./deploy-to-eks.sh
```

### 4. Get the Public URL

```bash
kubectl get svc frontend-service
```

The `EXTERNAL-IP` column shows your AWS ALB hostname.

### EKS Cluster Configuration

| Setting | Value |
|---|---|
| Cluster name | `data236-cluster` |
| Region | `us-east-1` |
| Kubernetes version | 1.29 |
| Node instance type | `t3.small` |
| Node count | 6 desired, 6 min, 9 max |
| Availability zones | us-east-1a, us-east-1b, us-east-1c |
| Node storage | 100 GiB per node |

### Persistent Storage (EBS)

| Volume | Size | Purpose |
|---|---|---|
| MySQL data | 10 GiB | Relational database |
| MongoDB data | 5 GiB | Document store |
| Backend uploads | 2 GiB | Local file cache |
| Kafka data | 3 GiB | Message log |

---

## Backend API Routes

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Register a new user |
| POST | `/api/auth/login` | User login |
| GET | `/api/properties` | List all properties |
| POST | `/api/properties` | Create a property (owner) |
| GET | `/api/bookings` | Get user bookings |
| POST | `/api/bookings` | Create a booking |
| GET | `/api/search` | Search properties |
| GET | `/api/favorites` | Get favorites |
| POST | `/api/uploads` | Upload property images to S3 |
| POST | `/api/ai/concierge` | Proxy to AI concierge service |
| GET | `/api/health` | Health check |

## AI Concierge API Routes

| Method | Path | Description |
|---|---|---|
| POST | `/ai/concierge` | Generate a full structured trip plan (Gemini + Tavily) |
| POST | `/ai/concierge/chat` | Conversational chat — full response (LangGraph agent) |
| POST | `/ai/concierge/chat/stream` | **Streaming SSE** — streams tokens as `data: {"token": "..."}` |
| POST | `/ai/chat` | Legacy chat endpoint (backwards compatible) |
| GET | `/ai/history` | Get chat history by `booking_id` |
| GET | `/ai/health` | Health check |

### LangGraph Agent Architecture

The chat agent is built on a LangGraph `StateGraph` with two nodes and memory:

```
AgentState { messages, context }
        │
   ┌────▼──────────┐   tool_calls present?   ┌──────────────┐
   │  call_model   │ ──────── yes ──────────► │  call_tools  │
   │  (Gemini LLM) │                          │  (weather,   │
   └────┬──────────┘                          │   search...) │
        │ no → END                            └──────┬───────┘
        ▼                                            │ loop
      reply                                    call_model
```

- **MemorySaver** checkpoints state per `thread_id` (booking ID), giving the agent persistent memory across conversation turns
- **Conditional edge** (`_should_continue`) routes to `call_tools` when the LLM emits tool calls, enabling multi-step reasoning
- **Streaming** uses `astream_events(version="v2")` to capture `on_chat_model_stream` events and deliver tokens via SSE
- **Pre-fetch optimization** injects weather and dining data as `SystemMessage`s before graph execution to reduce tool call round-trips and lower latency

---

## Load Testing

JMeter tests are included for backend performance testing:

```bash
cd jmeter-test

# Run load test (20 virtual users, 10s ramp-up)
docker run --rm --network airbnb-agentic-ai-prototype_default \
  -v "$PWD":/test -w /test justb4/jmeter \
  -n -t airbnb_backend_test.jmx \
  -JVUSERS=20 -JRAMP_SECONDS=10 \
  -l jmeter_results.jtl -j jmeter.log

# Generate HTML report
docker run --rm -v "$PWD":/test -w /test justb4/jmeter \
  -g jmeter_results.jtl -o jmeter_report
```

---

## Project Structure

```
airbnb-agentic-ai-prototype/
├── Frontend/               # React SPA
│   ├── src/
│   │   ├── pages/          # Page components
│   │   ├── components/     # Reusable UI components
│   │   ├── store/          # Redux state management
│   │   └── api.js          # API client
│   ├── nginx.conf          # nginx reverse proxy config
│   └── Dockerfile
├── Backend/                # Node.js REST API
│   ├── routes/             # Express route handlers
│   ├── server.js           # Entry point
│   └── Dockerfile
├── agentic/                # Python AI concierge service
│   ├── agent/
│   │   ├── main.py         # FastAPI app
│   │   ├── chat_agent.py   # Chat orchestration
│   │   ├── planner.py      # Gemini plan generation
│   │   ├── tools.py        # Agent tools
│   │   ├── db.py           # Database operations
│   │   └── providers/      # LLM, weather, search clients
│   └── Dockerfile
├── k8s/                    # Kubernetes manifests
│   ├── configmaps/
│   ├── secrets/
│   ├── volumes/
│   ├── services/
│   ├── deployments/
│   └── statefulsets/
├── aws/                    # AWS deployment scripts
│   ├── eksctl-cluster.yaml
│   ├── build-and-push-ecr.sh
│   ├── create-eks-cluster.sh
│   └── deploy-to-eks.sh
├── jmeter-test/            # Performance tests
├── docker-compose.yml      # Local development
└── README.md
```
