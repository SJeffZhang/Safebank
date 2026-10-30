# SafeBank mobile app + same-origin BFF for the demo stack (live mode).
FROM node:22.11.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_BANK_MODE=live
ENV VITE_BANK_MODE=$VITE_BANK_MODE
RUN npm run build

FROM node:22.11.0-alpine
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY server/bff.mjs ./server/bff.mjs
ENV HOST=0.0.0.0 PORT=3000 NODE_ENV=production
USER node
EXPOSE 3000
CMD ["node", "server/bff.mjs"]
