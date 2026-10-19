# 1. Fase de Compilación
FROM node:20-alpine AS builder

WORKDIR /data

# Instalar Git para clonar el proyecto oficial
RUN apk add --no-cache git

# Clonar la versión estable v1 de Evolution API
RUN git clone -b 1.8.2 --single-branch https://github.com/EvolutionAPI/evolution-api.git .

# Instalar dependencias y compilar el proyecto TypeScript
RUN npm install --legacy-peer-deps
RUN npm run build

# 2. Fase Final de Ejecución (Imagen Liviana)
FROM node:20-alpine AS runner

WORKDIR /data

# Copiar la aplicación compilada desde la fase anterior
COPY --from=builder /data/package*.json ./
COPY --from=builder /data/node_modules ./node_modules
COPY --from=builder /data/dist ./dist

EXPOSE 8080

CMD ["node", "dist/main.js"]
