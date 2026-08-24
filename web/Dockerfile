# Subsystem M (frontend). Multi-stage: build the static bundle with Node,
# then serve it with nginx -- the final image has no Node runtime at all,
# just the ~few-hundred-KB bundle vite.config.js is deliberately budgeted
# to produce (see that file's own comments on the bundle-shape decisions).
#
# VITE_API_BASE is a BUILD-time argument, not a runtime env var, because
# Vite inlines import.meta.env.VITE_API_BASE into the bundle at build time
# (see src/api/client.js) -- setting it at `docker run` time would do
# nothing. Pass it via docker-compose's `build.args`, or
# `--build-arg VITE_API_BASE=...` if building this image standalone.

FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
