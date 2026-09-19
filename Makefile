SHELL := /bin/sh

COMPOSE_FILE := deploy/docker-compose.yml
ENV_FILE := deploy/.env
COMPOSE := docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE)

.PHONY: harness harness-clean render-config build up down logs workspace-init deploy test-env test-rendered

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

up: test-env render-config test-rendered
	$(COMPOSE) up -d

# Seed the workspace volume: clone OpenAlice once and ensure the alicedev dir exists.
# Runs on the server tree; safe to re-run (clone is skipped when present).
workspace-init: test-env
	$(COMPOSE) run --rm --user paseo paseo sh -c 'test -d /workspace/openalice/.git || git clone https://github.com/TraderAlice/OpenAlice /workspace/openalice; mkdir -p /workspace/alicedev'

down: test-env
	$(COMPOSE) down

logs: test-env
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

deploy: harness render-config
	ssh nekoringo2 'mkdir -p /srv/alicedev'
	rsync -az --delete --exclude '.git/' --exclude 'node_modules/' --exclude 'harness/node_modules/' --exclude 'src/' --exclude 'backups/' --exclude 'deploy/.env' --exclude 'deploy/astrbot/*.rendered.json' ./ nekoringo2:/srv/alicedev/
	ssh nekoringo2 'cd /srv/alicedev && make build && make up'
