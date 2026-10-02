# CareerPilot web app: a standalone Next.js server.
#
# Build from the repository root (npm workspaces):
#   docker build -f apps/web/Dockerfile \
#     --build-arg NEXT_PUBLIC_API_BASE_URL=https://careerpilot.example.com -t careerpilot-web .
#
# NEXT_PUBLIC_API_BASE_URL is compiled into the browser bundle, so it is a build argument.
# Behind the bundled reverse proxy the API shares the site's origin: use the site URL.

FROM node:22-bookworm-slim AS build
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
RUN npm ci --workspace web --include-workspace-root
COPY apps/web apps/web
# public/ may hold nothing but .gitkeep; make sure it exists for the final COPY.
RUN mkdir -p apps/web/public
ARG NEXT_PUBLIC_API_BASE_URL
RUN test -n "$NEXT_PUBLIC_API_BASE_URL" || (echo "Set --build-arg NEXT_PUBLIC_API_BASE_URL" && exit 1)
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_OUTPUT=standalone \
    NODE_ENV=production
RUN npm run build --workspace web

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /repo/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["node", "apps/web/server.js"]
