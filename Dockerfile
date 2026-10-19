FROM evolutionapi/evolution-api:1.8.4

EXPOSE 8080

CMD ["node", "dist/main.js"]
