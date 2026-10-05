FROM node:22-slim AS dashboard
WORKDIR /dash
COPY dashboard/package*.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

FROM node:22-slim AS build
WORKDIR /app
COPY cloud/package*.json ./
RUN npm ci --no-audit --no-fund
COPY cloud/tsconfig.json ./
COPY cloud/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production DASHBOARD_DIR=/app/public
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=dashboard /dash/dist ./public
COPY cloud/package.json ./
EXPOSE 8095
CMD ["node", "dist/main.js"]
