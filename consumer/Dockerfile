FROM golang:1.23-alpine AS builder

WORKDIR /app

COPY . .

RUN go mod tidy && \
    CGO_ENABLED=0 GOOS=linux go build -trimpath -o /app/consumer .

# ---- runtime image ----
FROM gcr.io/distroless/static-debian12

COPY --from=builder /app/consumer /consumer

EXPOSE 2112

ENTRYPOINT ["/consumer"]
