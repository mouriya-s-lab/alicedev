SHELL := /bin/sh

# Layout on nekoringo2 (ARCHITECTURE §11):
#   /srv/alicedev/deploy     compose file, Caddyfile, astrbot templates, .env  (IaC: nekoringo-iac)
#   /srv/alicedev/app        alicedev checkout owned by tools/deployctl        (bot/, templates/, build sources)
#   /srv/alicedev/e2e-src    checkout mounted by the resident e2e stack
#   /srv/alicedev/src/paseo  unmodified mouriya-s-lab/paseo source at PASEO_SRC_REF
# Host targets run from /srv/alicedev/app, e.g. `make -C /srv/alicedev/app build`.

DEPLOY_DIR ?= /srv/alicedev/deploy
ENV_FILE ?= $(DEPLOY_DIR)/.env
COMPOSE := docker compose -f $(DEPLOY_DIR)/docker-compose.yml --env-file $(ENV_FILE)
E2E_COMPOSE := docker compose -f deploy/e2e/docker-compose.yml --env-file $(ENV_FILE)
TG_COMPOSE := docker compose -f deploy/tg-cli/docker-compose.yml

HOST ?= root@160.191.41.242
SSH ?= ssh -i $(HOME)/.ssh/dev-dai -o IdentitiesOnly=yes
REPO_URL ?= https://github.com/mouriya-s-lab/alicedev.git
PASEO_REPO ?= $(HOME)/Ext/code/paseo
PASEO_SRC_REF ?= 7ab7c444d

.PHONY: harness harness-clean render-config test-env test-rendered build up down logs \
        workspace-init fixed-main e2e-up tg-up paseo-src bootstrap-app bootstrap-e2e-src

# --- developer machine ----------------------------------------------------------

# Build the omp extension + reply-cli into harness/dist/ (the paseo image builds
# them itself in a multi-stage build; this target is for local development).
harness:
	cd harness && npm ci && npm run build

harness-clean:
	rm -rf harness/dist harness/node_modules

# Ship the unmodified upstream paseo source (PASEO_SRC_REF) to the host.
paseo-src:
	git -C $(PASEO_REPO) archive --format=tar $(PASEO_SRC_REF) | $(SSH) $(HOST) \
	  'set -e; rm -rf /srv/alicedev/src/paseo.new; mkdir -p /srv/alicedev/src/paseo.new; \
	   tar -x -C /srv/alicedev/src/paseo.new; rm -rf /srv/alicedev/src/paseo; \
	   mv /srv/alicedev/src/paseo.new /srv/alicedev/src/paseo'

# Clone the deployctl-owned checkout on the host at COMMIT (GITHUB_TOKEN from the
# host's IaC-provisioned .env; the token never enters argv).
bootstrap-app:
	test -n "$(COMMIT)"
	$(SSH) $(HOST) 'set -a; . /srv/alicedev/deploy/.env; set +a; \
	  python3 - bootstrap --app /srv/alicedev/app --repo-url $(REPO_URL) --commit $(COMMIT) \
	  && chown -R 1000:1000 /srv/alicedev/app' < tools/deployctl

bootstrap-e2e-src:
	test -n "$(COMMIT)"
	$(SSH) $(HOST) 'set -a; . /srv/alicedev/deploy/.env; set +a; \
	  python3 - bootstrap --app /srv/alicedev/e2e-src --repo-url $(REPO_URL) --commit $(COMMIT) \
	  && chown -R 1000:1000 /srv/alicedev/e2e-src' < tools/deployctl

# --- host (run from /srv/alicedev/app) --------------------------------------------

test-env:
	test -s $(ENV_FILE)

render-config: test-env
	set -a; . $(ENV_FILE); set +a; $(DEPLOY_DIR)/astrbot/render-config.sh

test-rendered:
	test -s $(DEPLOY_DIR)/astrbot/cmd_config.rendered.json
	test -s $(DEPLOY_DIR)/astrbot/alicedev_config.rendered.json
	test -s $(DEPLOY_DIR)/astrbot/snowluma_onebot.rendered.json

build: test-env
	$(COMPOSE) --profile build build paseo-base
	$(COMPOSE) build astrbot gateway paseo

up: test-env test-rendered
	$(COMPOSE) up -d

down: test-env
	$(COMPOSE) down

logs: test-env
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

# Seed OpenAlice once (cwd of the single-conversation scenarios).
workspace-init: test-env
	docker exec --user paseo alicedev-paseo sh -c 'test -d /workspace/openalice/.git || git clone https://github.com/TraderAlice/OpenAlice /workspace/openalice'

# Alignment-only fixed-main for the upgrade-bot scenario (never an AI cwd).
fixed-main:
	tools/bootstrap-fixed-main.sh --container alicedev-paseo --workspace /workspace/alicedev --repo-url $(REPO_URL)

# Resident e2e stack (needs the alicedev-e2e network, created by `up`).
e2e-up: test-env
	$(E2E_COMPOSE) up -d --build

tg-up:
	$(TG_COMPOSE) up -d --build
