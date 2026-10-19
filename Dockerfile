# 1. Fase de Compilación
FROM node:20-alpine AS builder

WORKDIR /data

RUN apk add --no-cache git

# Clonar directamente la rama principal estable sin depender de tags de versiones
RUN git clone --depth 1 https://github.com/EvolutionAPI/evolution-api.git .

# Instalar dependencias
RUN npm install --legacy-peer-deps

# Generar Prisma indicando su ruta exacta en el proyecto
RUN npx prisma generate --schema=./src/database/prisma/schema.prisma

# Compilar TypeScript
RUN npm run build

# 2. Fase de Ejecución
FROM node:20-alpine AS runner

WORKDIR /data

COPY --from=builder /data/package*.json ./
COPY --from=builder /data/node_modules ./node_modules
COPY --from=builder /data/dist ./dist

EXPOSE 8080

CMD ["node", "dist/main.js"]
