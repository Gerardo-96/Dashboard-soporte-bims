# 1. Fase de Compilación
FROM node:20-alpine AS builder

WORKDIR /data

# Instalar Git
RUN apk add --no-cache git

# Clonar la versión v2.1.1 oficial de Evolution API
RUN git clone -b v2.1.1 --single-branch https://github.com/EvolutionAPI/evolution-api.git .

# Instalar dependencias exactas usando ci en lugar de install
RUN npm ci

# Compilar proyecto
RUN npm run build

# 2. Fase de Ejecución
FROM node:20-alpine AS runner

WORKDIR /data

COPY --from=builder /data/package*.json ./
COPY --from=builder /data/node_modules ./node_modules
COPY --from=builder /data/dist ./dist

EXPOSE 8080

CMD ["node", "dist/main.js"]
