FROM golang:1.23-alpine AS builder

WORKDIR /app

# Copy source first so go mod tidy can resolve all imports.
COPY . .

# go mod tidy downloads dependencies and generates go.sum.
# Network access is required on first build.
RUN go mod tidy && \
    CGO_ENABLED=0 GOOS=linux go build -trimpath -o /app/producer .

# ---- runtime image ----
FROM gcr.io/distroless/static-debian12

COPY --from=builder /app/producer /producer

EXPOSE 2112

ENTRYPOINT ["/producer"]
