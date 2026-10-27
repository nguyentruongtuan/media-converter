# Streaming edge: serves transcoded outputs from MinIO with correct MIME types, CORS and caching,
# plus the demo HLS player. Build from the repository root.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY player/ /usr/share/nginx/html/
ENV MINIO_UPSTREAM=minio:9000 \
    OUTPUT_BUCKET=videos-output \
    NGINX_RESOLVER=127.0.0.11
EXPOSE 8080
