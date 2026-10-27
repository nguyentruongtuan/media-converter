# MinIO no longer publishes freely pullable container images, so build the server from source
# at a pinned release tag. Override with MINIO_IMAGE if you have a mirrored or licensed image.
FROM golang:1.24-alpine AS build
ARG MINIO_RELEASE=RELEASE.2025-04-22T22-12-26Z
RUN apk add --no-cache git make bash
RUN git clone --depth 1 --branch "${MINIO_RELEASE}" https://github.com/minio/minio.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -tags kqueue \
      -ldflags "-s -w -X github.com/minio/minio/cmd.ReleaseTag=${MINIO_RELEASE}" -o /out/minio .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl \
    && adduser -D -u 1000 minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /out/minio /usr/bin/minio
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
