# syntax=docker/dockerfile:1

# Same pinned AstrBot image as production (deploy/astrbot/Dockerfile) so
# before/after runs exercise the production runtime. The plugin and templates
# are mounted from the e2e checkout at run time, never baked in; the baseline
# plugin requirements are pre-installed here and AstrBot installs any new
# requirement of a candidate when it (re)loads the plugin.
ARG ASTRBOT_IMAGE=soulter/astrbot:latest@sha256:8e9f108c1470e6dd46bbbaad0d01e93d740dd86ee8d1cd767a10106aefd62ecf
FROM ${ASTRBOT_IMAGE}

USER root

COPY bot/requirements.txt /tmp/alicedev-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/alicedev-requirements.txt \
    && rm /tmp/alicedev-requirements.txt

COPY deploy/e2e/seed-data/ /AstrBot/data/

RUN mkdir -p \
      /AstrBot/data/plugin_data/alicedev/images \
      /AstrBot/data/plugin_data/alicedev/reports
