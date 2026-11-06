# UNVERIFIED (Docker was not available where this project was built).
FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
EXPOSE 3000
CMD ["npm", "start"]
