#!/bin/bash

# Navigate to the correct directory
cd /home/richard/code/flight-shuttle-dispatcher

# Pull latest code from your repository
git pull origin main

# Rebuild and restart the container stack using your updated docker-compose file
docker-compose down
docker-compose up --build -d

echo "Deployment complete."