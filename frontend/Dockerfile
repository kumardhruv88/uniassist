# syntax=docker/dockerfile:1

# ---- build: compile the single-page app -----------------------------------------------------
FROM node:24-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
# Build with VITE_MOCK=1 for a UI that runs on built-in examples and needs no backend:
#   docker build --build-arg VITE_MOCK=1 -t uniassist-ui .
ARG VITE_MOCK=0
ENV VITE_MOCK=${VITE_MOCK}
RUN npm run build

# ---- serve: static files, plus /api proxied to the backend ----------------------------------
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
