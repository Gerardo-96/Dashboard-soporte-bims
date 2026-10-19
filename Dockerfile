# 1. Fase de Compilación
FROM node:20-alpine AS builder

WORKDIR /data

# Instalar Git
RUN apk add --no-cache git

# Clonar la versión estable v2.1.1 de Evolution API
RUN git clone -b v2.1.1 --single-branch https://github.com/EvolutionAPI/evolution-api.git .

# Instalar dependencias exactas
RUN npm ci

# Generar el cliente de Prisma desde su ubicación real en el proyecto
RUN npx prisma generate --schema=./src/database/prisma/schema.prisma

# Compilar NestJS usando su script de build oficial
RUN npm run build

# 2. Fase de Ejecución
FROM node:20-alpine AS runner

WORKDIR /data

COPY --from=builder /data/package*.json ./
COPY --from=builder /data/node_modules ./node_modules
COPY --from=builder /data/dist ./dist

EXPOSE 8080

CMD ["node", "dist/main.js"]
