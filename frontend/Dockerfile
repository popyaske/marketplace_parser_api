# Multi-stage build: Node → nginx
FROM node:20-alpine AS builder

WORKDIR /app

# Сначала зависимости (кэшируется)
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

# Затем исходники
COPY . .

# Сборка
RUN npm run build

# Production: nginx
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]