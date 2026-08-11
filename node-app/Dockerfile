# Two stages so the runtime image carries no TypeScript compiler and no dev
# dependencies — the app itself has zero runtime dependencies, so the final
# layer is the Node base plus about 20 kB of compiled JavaScript.
FROM node:22-alpine AS build

WORKDIR /app

# Copy the manifests first: this layer only invalidates when dependencies
# change, not on every source edit.
COPY package.json package-lock.json* tsconfig.json ./
RUN npm ci --silent || npm install --silent

COPY src ./src
RUN npx tsc

FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/dist ./dist
COPY public ./public
COPY package.json ./

EXPOSE 8081

# node:http, no framework — nothing to install at runtime.
CMD ["node", "dist/server.js"]
