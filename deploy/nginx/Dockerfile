# Streaming edge: serves transcoded outputs from MinIO with correct MIME types, CORS and caching,
# plus the web app and HLS player. Build from the repository root.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=web /web/dist/ /usr/share/nginx/html/
ENV MINIO_UPSTREAM=minio:9000 \
    OUTPUT_BUCKET=videos-output \
    NGINX_RESOLVER=127.0.0.11 \
    PUBLIC_API_BASE_URL=http://localhost:8000
EXPOSE 8080
