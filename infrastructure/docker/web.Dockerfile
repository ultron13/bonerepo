# The web interface. Built in a container like everything else, so `make dev`
# needs Docker and nothing else -- no local Node, no version to match.
FROM node:20-alpine AS deps
WORKDIR /srv
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:20-alpine AS build
WORKDIR /srv
COPY --from=deps /srv/node_modules ./node_modules
COPY apps/web ./
# Baked at build time: Next.js inlines NEXT_PUBLIC_* into the client bundle, so
# the browser's view of the API is fixed here rather than at start-up.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /srv
ENV NODE_ENV=production
# The standalone output carries only the files the server actually needs.
COPY --from=build /srv/.next/standalone ./
COPY --from=build /srv/.next/static ./.next/static
COPY --from=build /srv/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
