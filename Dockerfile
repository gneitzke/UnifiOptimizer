# Stage 1: Build React frontend
FROM node:20-alpine AS frontend
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Python backend + built frontend
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ api/
COPY core/ core/
COPY utils/ utils/
COPY data/ data/
COPY server/ server/
COPY optimizer.py regenerate_report.py ./

# Copy built frontend from stage 1
COPY --from=frontend /app/web/dist web/dist/

EXPOSE 8000

ENV JWT_SECRET=""
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
