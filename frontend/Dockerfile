ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/node:20-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci && ls node_modules/.bin/tsc && echo "=== npm ci OK ==="
COPY . .
ENV PATH="/app/node_modules/.bin:$PATH"
RUN tsc -b && vite build

FROM ${REGISTRY}/library/nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 443
CMD ["nginx", "-g", "daemon off;"]
