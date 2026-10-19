# 1. Fase de Compilación
FROM node:20-alpine AS builder

WORKDIR /data

# Instalar Git
RUN apk add --no-cache git

# Clonar el código fuente oficial directamente
RUN git clone --depth 1 https://github.com/EvolutionAPI/evolution-api.git .

# Instalar dependencias
RUN npm install --legacy-peer-deps

# Compilar omitiendo la verificación estricta de librerías para evitar errores de TypeScript
RUN npx tsc --skipLibCheck

# 2. Fase de Ejecución
FROM node:20-alpine AS runner

WORKDIR /data

COPY --from=builder /data/package*.json ./
COPY --from=builder /data/node_modules ./node_modules
COPY --from=builder /data/dist ./dist

EXPOSE 8080

CMD ["node", "dist/main.js"]
