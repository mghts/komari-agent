FROM golang:1.25-alpine@sha256:1ae0735f00daffa3aaf1363a5184c0d2dc55c78e3db4ec70241cdac97bf84b59 AS source
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY . .

FROM source AS test
RUN CGO_ENABLED=0 go test ./...

FROM source AS build
ARG VERSION=0.0.0-dev
ARG REVISION=unknown
RUN CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="-s -w -X github.com/komari-monitor/komari-agent/update.CurrentVersion=${VERSION}" -o /out/komari-agent .

FROM alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d
RUN apk add --no-cache ca-certificates tzdata && touch /.komari-agent-container
WORKDIR /app
ARG VERSION=0.0.0-dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/mghts/komari-agent" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="MIT"
COPY --from=build /out/komari-agent /app/komari-agent
ENTRYPOINT ["/app/komari-agent"]
CMD ["--help"]
