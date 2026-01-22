# Docker Compose 설정 수정 사항

## 문제 요약

`docker-compose up -d` 실행 시 여러 컨테이너에서 설정 오류가 발생하여 정상 실행되지 않았음.

---

## 1. Proxy 컨테이너 - Caddyfile 구문 오류

### 오류 메시지

```
Error: adapting config using caddyfile: server block without any key is global configuration, and if used, it must be first
```

### 원인

Caddy 설정에서 global configuration block (`{ }`)은 반드시 파일의 **맨 처음**에 위치해야 하는데, snippet 정의 뒤에 있었음.

### 수정 파일

`apps/proxy/Caddyfile.ce`

### 수정 내용

Global configuration block을 파일 맨 위로 이동:

```caddyfile
# 수정 전
(plane_proxy) {
    ...
}

{
    {$CERT_EMAIL}
    acme_ca ...
}

# 수정 후
{
    {$CERT_EMAIL}
    acme_ca {$CERT_ACME_CA:https://acme-v02.api.letsencrypt.org/directory}
    {$CERT_ACME_DNS}
    servers {
        max_header_size 25MB
        client_ip_headers X-Forwarded-For X-Real-IP
        trusted_proxies static {$TRUSTED_PROXIES:0.0.0.0/0}
    }
}

(plane_proxy) {
    ...
}
```

---

## 2. Proxy 컨테이너 - 환경변수 누락

### 원인

`docker-compose.yml`의 proxy 서비스에 Caddyfile에서 필요한 환경변수가 전달되지 않음.

### 수정 파일

`docker-compose.yml`

### 수정 내용

proxy 서비스에 환경변수 추가:

```yaml
proxy:
  environment:
    FILE_SIZE_LIMIT: ${FILE_SIZE_LIMIT:-5242880}
    BUCKET_NAME: ${AWS_S3_BUCKET_NAME:-uploads}
    SITE_ADDRESS: ":80" # 컨테이너 내부 포트
    CERT_EMAIL: ${CERT_EMAIL:-}
    CERT_ACME_CA: ${CERT_ACME_CA:-}
    CERT_ACME_DNS: ${CERT_ACME_DNS:-}
    TRUSTED_PROXIES: ${TRUSTED_PROXIES:-0.0.0.0/0}
```

---

## 3. Proxy 컨테이너 - 포트 불일치

### 원인

- `.env`의 `SITE_ADDRESS=:8080` → Caddy가 컨테이너 내부 8080 포트에서 리스닝
- `docker-compose.yml` 포트 매핑: `8080:80` (호스트 8080 → 컨테이너 80)
- 컨테이너 내부 80 포트에는 아무것도 리스닝하지 않아 접속 불가

### 수정 내용

`SITE_ADDRESS`를 `:80`으로 고정하여 컨테이너 내부에서 80 포트로 리스닝하도록 변경.

---

## 4. Live 컨테이너 - 환경변수 누락

### 오류 메시지

```
[MISSING_ENV_FILE] missing .env file (/app/.env)
Invalid environment variables: API_BASE_URL: Required, LIVE_SERVER_SECRET_KEY: Required
```

### 원인

live 서비스에 필요한 환경변수가 docker-compose.yml에서 전달되지 않음.

### 수정 파일

`docker-compose.yml`

### 수정 내용

live 서비스에 환경변수 및 의존성 추가:

```yaml
live:
  environment:
    PORT: 3000
    API_BASE_URL: http://api:8000
    WEB_BASE_URL: http://web:3000
    LIVE_BASE_URL: http://live:3000
    LIVE_BASE_PATH: /live
    LIVE_SERVER_SECRET_KEY: ${LIVE_SERVER_SECRET_KEY:-secret-key}
    REDIS_HOST: plane-redis
    REDIS_PORT: 6379
    REDIS_URL: redis://plane-redis:6379/
  depends_on:
    - plane-redis
    - api
```

---

## 5. API 환경변수 - 잘못된 리다이렉트 URL

### 문제

관리자 계정 설정 후 `localhost:3001`로 리다이렉트되어 접속 불가.

### 원인

`apps/api/.env`의 모든 URL이 개별 localhost 포트로 설정되어 있었음:

- `ADMIN_BASE_URL="http://localhost:3001"`
- `APP_BASE_URL="http://localhost:3000"`

Docker 환경에서는 프록시(localhost:8080)를 통해 모든 서비스에 접근해야 함.

### 수정 파일

`apps/api/.env`

### 수정 내용

| 환경변수               | 수정 전                                | 수정 후                   |
| ---------------------- | -------------------------------------- | ------------------------- |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000,3001,3002,3100` | `http://localhost:8080`   |
| `AWS_S3_ENDPOINT_URL`  | `http://localhost:9000`                | `http://plane-minio:9000` |
| `USE_MINIO`            | `0`                                    | `1`                       |
| `WEB_URL`              | `http://localhost:8000`                | `http://localhost:8080`   |
| `ADMIN_BASE_URL`       | `http://localhost:3001`                | `http://localhost:8080`   |
| `SPACE_BASE_URL`       | `http://localhost:3002`                | `http://localhost:8080`   |
| `APP_BASE_URL`         | `http://localhost:3000`                | `http://localhost:8080`   |
| `LIVE_BASE_URL`        | `http://localhost:3100`                | `http://localhost:8080`   |

---

## 적용 방법

환경변수 변경 후 컨테이너 재빌드 필요 (VITE\_ 환경변수는 빌드 시점에 번들에 포함됨):

```bash
docker-compose down
docker-compose build --no-cache web admin space
docker-compose up -d
```

---

## 최종 접속 URL

- 메인 앱: http://localhost:8080
- 관리자 모드: http://localhost:8080/god-mode
- Spaces: http://localhost:8080/spaces
