# Usamos la imagen oficial desde Docker Hub
FROM evolutionapi/evolution-api:v1.8.4

EXPOSE 8080

CMD ["node", "dist/main.js"]
