FROM node:22-alpine AS dashboard-build
WORKDIR /build/dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKSIGHT_HOST=0.0.0.0 \
    WORKSIGHT_PORT=5001
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements-mcp.txt

COPY . .
COPY --from=dashboard-build /build/dashboard/dist ./dashboard/dist

EXPOSE 5001
VOLUME ["/app/data"]
CMD ["python", "-m", "backend.app"]
