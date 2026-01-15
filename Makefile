.PHONY: help build push run run-native run-docker stop restart logs clean prune backup restore

# Docker image configuration
IMAGE_NAME = herpiko/tg-cc
IMAGE_TAG = latest
FULL_IMAGE = $(IMAGE_NAME):$(IMAGE_TAG)

# Docker Compose configuration
COMPOSE_FILE = docker-compose.yml
CONTAINER_NAME = tg-cc-bot

help: ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

build: ## Build the Docker image
	@echo "Building Docker image: $(FULL_IMAGE)"
	docker build -t $(FULL_IMAGE) .

build-no-cache: ## Build the Docker image without cache
	@echo "Building Docker image (no cache): $(FULL_IMAGE)"
	docker build --no-cache -t $(FULL_IMAGE) .

push: ## Push the Docker image to Docker Hub
	@echo "Pushing Docker image: $(FULL_IMAGE)"
	docker push $(FULL_IMAGE)

pull: ## Pull the Docker image from Docker Hub
	@echo "Pulling Docker image: $(FULL_IMAGE)"
	docker pull $(FULL_IMAGE)

run: run-native ## Start the bot natively (alias for run-native)

run-native: ## Start the bot natively using Python
	@echo "Starting tg-cc bot natively..."
	./tg-cc

run-docker: ## Start the bot using Docker Compose
	@echo "Starting tg-cc bot in Docker..."
	docker-compose up -d

run-foreground: ## Start the bot in foreground
	@echo "Starting tg-cc bot (foreground)..."
	docker-compose up

stop: ## Stop the bot
	@echo "Stopping tg-cc bot..."
	docker-compose stop

restart: ## Restart the bot
	@echo "Restarting tg-cc bot..."
	docker-compose restart

down: ## Stop and remove the container
	@echo "Stopping and removing tg-cc bot..."
	docker-compose down

logs: ## Show bot logs (follow mode)
	docker-compose logs -f

logs-tail: ## Show last 100 lines of logs
	docker-compose logs --tail=100

status: ## Show container status
	docker-compose ps

exec: ## Execute shell in the running container
	docker-compose exec tg-cc /bin/bash

clean: ## Remove containers and volumes
	@echo "Removing containers and volumes..."
	docker-compose down -v

prune: ## Remove all unused Docker resources
	@echo "Pruning Docker system..."
	docker system prune -f

prune-all: ## Remove all unused Docker resources including images
	@echo "Pruning Docker system (including images)..."
	docker system prune -a -f

backup: ## Backup the workspace volume
	@echo "Backing up workspace volume..."
	docker run --rm \
		-v tg-cc_workspace:/workspace \
		-v $(PWD):/backup \
		alpine tar czf /backup/workspace-backup-$$(date +%Y%m%d-%H%M%S).tar.gz -C /workspace .
	@echo "Backup complete!"

restore: ## Restore workspace volume from backup (specify BACKUP_FILE=filename)
	@if [ -z "$(BACKUP_FILE)" ]; then \
		echo "Error: Please specify BACKUP_FILE=filename"; \
		exit 1; \
	fi
	@echo "Restoring workspace from $(BACKUP_FILE)..."
	docker run --rm \
		-v tg-cc_workspace:/workspace \
		-v $(PWD):/backup \
		alpine tar xzf /backup/$(BACKUP_FILE) -C /workspace
	@echo "Restore complete!"

update: ## Pull latest image and restart
	@echo "Updating tg-cc bot..."
	docker-compose pull
	docker-compose up -d
	@echo "Update complete!"

rebuild: ## Rebuild and restart the bot
	@echo "Rebuilding and restarting tg-cc bot..."
	docker-compose build --no-cache
	docker-compose up -d
	@echo "Rebuild complete!"

config-test: ## Test configuration syntax
	@echo "Testing configuration..."
	@if [ ! -f .env ]; then \
		echo "Error: .env file not found. Copy .env.example to .env"; \
		exit 1; \
	fi
	@if [ ! -f config.yaml ]; then \
		echo "Error: config.yaml not found"; \
		exit 1; \
	fi
	@echo "Configuration files found!"

setup: ## Initial setup (create .env from example)
	@if [ ! -f .env ]; then \
		echo "Creating .env from .env.example..."; \
		cp .env.example .env; \
		echo "Please edit .env file with your configuration"; \
	else \
		echo ".env file already exists"; \
	fi

stats: ## Show container resource usage
	docker stats $(CONTAINER_NAME) --no-stream

version: ## Show Docker and Docker Compose versions
	@echo "Docker version:"
	@docker --version
	@echo "Docker Compose version:"
	@docker-compose --version

all: build push run ## Build, push, and run the bot
