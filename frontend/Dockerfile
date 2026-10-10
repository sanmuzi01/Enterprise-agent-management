# 前端：构建静态资源 → nginx 托管，并把 /api、/health、/metrics 反代到 api 容器。
FROM node:24-alpine AS build
WORKDIR /app
COPY package*.json ./
# NPM_REGISTRY：访问 npm 官方源不稳时指定镜像站，例如 https://registry.npmmirror.com；默认不改。
ARG NPM_REGISTRY=
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi && npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
