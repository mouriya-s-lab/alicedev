SHELL := /bin/sh

COMPOSE_FILE := deploy/docker-compose.yml
ENV_FILE := deploy/.env
COMPOSE := docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE)

.PHONY: harness harness-clean render-config build up down logs deploy test-env test-rendered

# Build the omp extension + reply-cli into harness/dist/. Needs npm registry access.
harness:
	cd harness && npm ci && npm run build

harness-clean:
	rm -rf harness/dist harness/node_modules

render-config:
	test -s $(ENV_FILE)
	set -a; . $(ENV_FILE); set +a; deploy/astrbot/render-config.sh

test-env:
	test -s $(ENV_FILE)

test-rendered:
	test -s deploy/astrbot/cmd_config.rendered.json
	test -s deploy/astrbot/alicedev_config.rendered.json

build: harness render-config
	$(COMPOSE) --profile build build paseo-base
	$(COMPOSE) build gateway paseo

up: test-env test-rendered
	$(COMPOSE) up -d

down: test-env
	$(COMPOSE) down

logs: test-env
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

deploy: harness render-config
	ssh nekoringo2 'mkdir -p /srv/alicedev'
	rsync -az --exclude '.git/' --exclude 'deploy/.env' ./ nekoringo2:/srv/alicedev/
	ssh nekoringo2 'cd /srv/alicedev && make build && make up'
